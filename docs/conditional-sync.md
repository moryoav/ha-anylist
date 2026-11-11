# Conditional synchronization

Shopping lists keep their configurable polling interval, including the
60-second default. The integration requests changed resources using AnyList's
timestamp protocol. It does not download the recipe library as part of a
shopping-list poll.

## Verified protocol

I verified these request formats against the schema and request code embedded
in the [official AnyList web client](https://www.anylist.com/static/webapp/js/app.min.js)
on 2026-11-11. The served bundle's `Last-Modified` was
2026-11-10 22:31:15 UTC, and its SHA-256 was
`e25bf32bc6a8df21f9cab2d9bea7de580a125ff6643d91cd47898656273dd6f5`.

The web client contains newer fields than the reference schema in
`anylist_rs`. These are observed wire formats used by the official client;
AnyList does not publish a third-party API compatibility guarantee.

| Resource | Endpoint | Binary multipart parts |
| --- | --- | --- |
| Shopping lists | `/data/shopping-lists/all` | `timestamps`: `PBTimestampList`; `logical_timestamps`: `PBLogicalTimestampList` |
| Recipes and collections | `/data/user-recipe-data/all` | `timestamp`: `PBTimestamp` |
| Favourites, recent items, starter lists | `/data/starter-lists/all-v2` | `user_lists_timestamps`, `recent_item_lists_timestamps`, `favorite_item_lists_timestamps`: `PBTimestampList` |
| Legacy per-user list settings | `/data/list-settings/all` | `timestamp`: `PBTimestamp` |

Ordinary shopping-list timestamps come from each `ShoppingList` identifier and
timestamp. Logical metadata cursors use the identifier
`ALShoppingListLogicalTimestampID-<list ID>`, the list ID as their description,
and the last `PBListResponse.logicalTimestamp`.

Both cursor types are needed. Omitting logical timestamps can cause unchanged
categories, stores, and categorization rules to be sent again. The client also
retains cursors for lists that are not selected as Home Assistant entities:
omitting a list tells the server that it is missing from the client cache.
Records without a list name retain their cursors but are skipped by the list
parser until a later update supplies a name.

HTTP 304 is a successful no-change result when a complete snapshot is already
cached. A 304 without a prior snapshot is an error. A partial response is merged
before the new cursors are saved:

- New and modified lists replace only the corresponding cached list records.
  Unmodified lists remain available; `unknownIds` removes deleted lists.
- Metadata has its own full-sync flag and explicit category, group, rule,
  store, and filter deletion fields. A metadata-only response retains items.
- Recipe updates retain unchanged recipes and opaque protobuf fields. The
  all-recipes membership removes deleted recipes; collection membership is
  replaced only when the server marks its ID list authoritative.
- Favourites batches retain unchanged lists and apply explicit deletions or an
  authoritative full batch, including an empty batch.

If an incremental response fails validation, its resource snapshot and cursors
are discarded and one full resource fetch is attempted. A failed recovery
stops that poll; the next poll or restart can fetch without the rejected cursor.
Other resources retain their snapshots.

## Freshness and persistence

Shopping-list reads revalidate with the server. Overlapping reads share an
in-flight fetch. Recipes and favourites reuse a snapshot for up to five minutes;
recipes are fetched only when requested. Legacy list settings use the same
five-minute cache and are loaded only for lists without native sort settings.

Writes mark the affected cache dirty before the request, including when its
outcome could be ambiguous. Shopping-list actions still await immediate
conditional readback, preserving consecutive add/check/delete behavior.
Metadata-preserving recipe edits revalidate before building their write payload.
Adding a recipe sends all non-heading ingredients in one operations request,
preserving scaling, notes, and insertion order. A deadline cannot interrupt it
between separate ingredient writes. An already sent request can still have an
unknown outcome after a network timeout.
`anylist.refresh` makes loaded secondary caches revalidate on their next read.

Raw protobuf snapshots are retained so fields unknown to this version survive
later updates. Cached account data and its derived cursors are stored together
using Home Assistant's private atomic storage. Authentication tokens and
passwords are excluded. On restart, every restored resource revalidates before
its snapshot is considered current. Storage belongs to the verified AnyList
user ID, so changing the account email retains its cache and cooldown. Removing
the integration clears that account's snapshots. A private lookup between hashed
email and account keys retains cooldown discovery before login, including after
unfinished setup or removal. The lookup contains no email address or snapshots.

## Rate limits and diagnostics

HTTP 429 reads `retry_after` from the JSON body and `Retry-After` from the HTTP
headers, including HTTP-date headers. When both specify a delay, the longer
delay wins. If the server supplies no usable delay, the fallback is one hour.
This fallback follows the restriction duration described by AnyList; it is not
a published request allowance.

The account cooldown applies to polls, manual refreshes, actions, calendar
requests, and token renewal. Configuration and setup check the saved deadline
before attempting login. A server restriction is not treated as a bad password.
No mutation rejected during a cooldown is queued for automatic replay.
Login rechecks the gate inside its executor worker and records a late 429 even
if its caller has timed out. Queued actions carry the caller's deadline and
cancellation state, preventing new requests after timeout or cancellation. A failed
cooldown save is logged while the original 429 and in-memory gate are retained.

Diagnostics include request count, response-body bytes, no-change responses,
cache hits, per-resource snapshot sizes, dirty flags, and the retry deadline.
They do not include raw cached recipes, list items, credentials, or tokens.

The regression tests use synthetic protobuf records and mocked HTTP. They cover
request counts, both timestamp layers, partial updates and deletions, raw field
preservation, concurrent reads, restart recovery, server cooldowns, and actual
Home Assistant todo service sequences.
