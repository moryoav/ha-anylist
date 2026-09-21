# AnyList for Home Assistant
[![HACS][hacs-badge]][hacs-url] [![release][release-badge]][release-url] [![hassfest][hassfest-badge]][hassfest-url] [![validate][validate-badge]][validate-url] [![license](https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square)](LICENSE)

---

## Support me on Ko-fi

If this project is useful to you, you can support its continued development:

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/Y5B124NZ2L)

---

A Home Assistant custom integration for [AnyList](https://www.anylist.com/) shopping lists, recipes, and meal planning.

AnyList is a shared grocery list and meal planning service. This integration brings selected AnyList shopping lists into Home Assistant as todo entities, exposes a meal plan iCalendar URL for the built-in iCal integration, and provides recipe actions for automations and scripts.

## Features

- **Shopping lists** as todo entities: view, add, check off, and remove items.
- **Automatic AnyList categories** when adding known items from Home Assistant.
- **Shopping list change signatures** for automations that need to detect renamed, added, removed, checked, or unchecked items.
- **Meal plan iCalendar URL** as a diagnostic sensor when the option is enabled.
- **Recipe actions** to search recipes, fetch one recipe, create/update/delete recipes, and add recipe ingredients to shopping lists.
- **Polling sync fallback** with a configurable cloud polling interval.

## Installation

### HACS

[![Open the AnyList HACS repository](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=moryoav&repository=ha-anylist&category=integration)

AnyList is available in the default HACS catalog, so no custom repository setup is required.

1. Select the button above, or open HACS and search for **AnyList** under **Integrations**.
2. Select **AnyList** and choose **Download**.
3. Restart Home Assistant.
4. Add the integration from **Settings** -> **Devices & services** -> **Add integration** -> **AnyList**.

### Manual

1. Copy `custom_components/anylist` to `config/custom_components/anylist`.
2. Restart Home Assistant.
3. Add the integration from **Settings** -> **Devices & services** -> **Add integration** -> **AnyList**.

## Configuration

[![Add the AnyList integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=anylist)

The integration is configured through the Home Assistant UI.

Installation parameters:

- **Email**: your AnyList account email address.
- **Password**: your AnyList account password.

Configuration options:

- **Shopping Lists**: choose which AnyList shopping lists should be exposed as Home Assistant todo entities. If no explicit list selection is stored, all lists are exposed.
- **Enable Meal Plan Calendar URL**: creates a diagnostic sensor containing the AnyList meal plan iCalendar URL.
- **Polling Interval**: how often AnyList is polled for changes, in seconds. Defaults to 60. Higher values reduce cloud requests but delay changes made by other AnyList clients.

Use **Configure** on the integration entry to change selected lists, meal plan URL exposure, or the polling interval. Use **Reconfigure** to update account credentials.

## Supported Functionality

### Todo Entities

Each selected AnyList shopping list appears as a todo entity. You can:

- View items on the list.
- Add new items.
- Check off and uncheck items.
- Remove one or more items.

When Home Assistant adds an item that AnyList already knows how to categorize
for that list, the integration sends the same category assignment metadata used
by the AnyList app, so known grocery items land in their usual categories.

Todo entities expose these state attributes for content-change detection:

- `items_signature`: a SHA256 hash of current item names and completion states.
- `items_signature_raw`: the normalized source string used to build the hash.

### Meal Plan iCalendar URL Sensor

If the meal plan option is enabled, the integration creates a diagnostic sensor named **Meal plan iCalendar URL**. Use that URL with Home Assistant's built-in [iCal integration](https://www.home-assistant.io/integrations/ical/) to display AnyList meal planning events as a calendar.

### Service Actions

Recipe support is exposed through Home Assistant actions for automations, scripts, and Node-RED flows:

- `anylist.refresh`
- `anylist.get_recipes`
- `anylist.search_recipes`
- `anylist.get_recipe`
- `anylist.add_recipe_to_list`
- `anylist.create_recipe`
- `anylist.update_recipe`
- `anylist.delete_recipe`

See [custom_components/anylist/services.yaml](custom_components/anylist/services.yaml) for field descriptions and examples.

## Data Updates

The integration uses Home Assistant's `DataUpdateCoordinator` and polls AnyList every 60 seconds by default. Set **Polling Interval** in the integration options to configure the polling interval to a custom value. Mutations such as adding, checking, deleting, or recipe-to-list actions request an immediate refresh after the AnyList operation completes.

If AnyList is unavailable, entities are marked unavailable through the coordinator until the next successful refresh.

## Alexa Shopping List and Dashboard Sync

See the [Alexa, AnyList, and dashboard synchronization guide](docs/shopping-list-sync.md)
for a complete shopping list sync automation, a category-based kitchen dashboard
card, screenshots, and setup instructions.

## Examples

Trigger an automation whenever the actual contents of a shopping list change:

```yaml
trigger:
  - platform: state
    entity_id: todo.groceries
    attribute: items_signature
```

Add a recipe's ingredients to a shopping list:

```yaml
action: anylist.add_recipe_to_list
data:
  recipe_name: Weeknight Pasta
  list_name: Groceries
  scale_factor: 2
```

Create a recipe with an image:

```yaml
action: anylist.create_recipe
data:
  name: Tomato Soup
  ingredients:
    - name: Tomatoes
      quantity: 500 g
    - name: Water
      quantity: 250 ml
  preparation_steps:
    - Simmer the tomatoes and water until soft.
    - Blend until smooth.
  image_url: "https://example.com/tomato-soup.jpg"
response_variable: created_recipe
```

Replace the example image URL with a publicly accessible HTTP or HTTPS URL
pointing directly to an image. AnyList downloads and stores the image, so local
Home Assistant URLs and images requiring a login will not work. The action waits
for the image to become available before creating the recipe; if importing the
image fails, the action reports an error and does not create the recipe.
`image_url` is optional. The response includes the
stored image URL in `created_recipe.recipe.photo_urls`.

To add or replace an image on an existing recipe, use `anylist.update_recipe`:

```yaml
action: anylist.update_recipe
data:
  recipe_name: Tomato Soup
  name: Tomato Soup
  ingredients:
    - name: Tomatoes
      quantity: 500 g
    - name: Water
      quantity: 250 ml
  preparation_steps:
    - Simmer the tomatoes and water until soft.
    - Blend until smooth.
  image_url: "https://example.com/tomato-soup.jpg"
response_variable: updated_recipe
```

Provide either `recipe_id` or the exact current `recipe_name`. The action still
requires the complete replacement `name`, `ingredients`, and `preparation_steps`.
Omitting `image_url` keeps the existing image. Providing it adds an image to a
recipe without one, or replaces its existing image. Other metadata, including
notes, source, rating, and creation date, is preserved. If importing the image
fails, the recipe is not updated.

### Search recipes, then read the selected recipe

Use `anylist.search_recipes` to find compact candidates for a conversation agent:

```yaml
action: anylist.search_recipes
data:
  query: pink horseradish salmon
  limit: 15
  include_ingredients: false
response_variable: recipe_candidates
```

`query` is required and must contain at least one letter or number. `limit` is
a whole number from 1 to 50, defaulting to 15. `include_ingredients` defaults to
`false`; enabling it searches ingredient names with less weight than titles.
With multiple loaded AnyList accounts, supply `config_entry_id` using the same
config entry ID field as the other recipe actions, and use that entry for the
subsequent read.

Matching runs locally after the integration retrieves recipes through its
existing AnyList client. It needs no LLM or external search service. It normalizes
Unicode, capitalization, accents, punctuation and whitespace, and matches distinct
words in any order. Extra title words do not reduce relevance: the query above
matches **Pink Horseradish & Dill Salmon**. Minor typos in words of at least four
characters allow one insertion, deletion, substitution or adjacent transposition.

Exact normalized titles rank first, followed by titles containing all query words,
then partial and typo matches. Title matches carry more weight than ingredient
matches. Candidates must match at least half the distinct query words, so unrelated
recipes are excluded rather than added to fill the limit. This is word matching;
synonyms and broader culinary meaning are left to the conversation agent.

The response has this shape (the ID below is illustrative; real results preserve
the exact AnyList recipe ID):

```yaml
recipes:
  - id: recipe_123
    name: Pink Horseradish & Dill Salmon
    score: 90.0
    match_explanation: All query words in title
count: 1
has_more: false
```

`count` is the number returned. `has_more` is true only when additional matching
candidates were omitted by the limit. Search never returns ingredient lists or
preparation instructions. Scores express ranking relevance, **not calibrated
confidence**: exact normalized titles score 100, all-word title matches score 90,
and partial/typo results score below 80. Ties sort by normalized title and then
exact recipe ID, so duplicate or ambiguous titles remain separate candidates.

The conversation agent should use context to select a candidate, or ask the user
to clarify ambiguous matches before editing. It should then call
`anylist.get_recipe` with that candidate's `id` as `recipe_id` to read full details:

```yaml
action: anylist.get_recipe
data:
  recipe_id: recipe_123 # Use the exact ID selected from the search response.
response_variable: selected_recipe
```

Ingredients and preparation steps are included by default by `get_recipe`.
Search does not select or edit a recipe automatically. Avoid automatically taking
the first candidate for an edit when the user's intent is ambiguous.

### List recipes with a substring filter

`anylist.get_recipes` retains its case-insensitive substring behavior and response
format. `anylist.get_recipe` still accepts an exact ID or exact name. For example,
fetch recipes whose names contain `pasta`:

```yaml
action: anylist.get_recipes
data:
  query: pasta
  include_ingredients: true
  include_steps: false
response_variable: anylist_recipes
```

## Known Limitations

- This is an unofficial integration and AnyList does not publish a public API.
- The local client implements only the AnyList API subset needed by this integration.
- Realtime websocket sync is intentionally disabled; polling is the reliable update path.
- The integration supports AnyList cloud accounts, not local devices.
- Recipe import from external websites is not implemented.
- Meal plan calendar support exposes the iCalendar URL; calendar entities are provided by Home Assistant's iCal integration.

## Troubleshooting

### Authentication Fails

Confirm that the email and password work in the official AnyList app or website. If credentials changed, use **Reconfigure** on the integration entry.

### Lists Do Not Update Immediately

The integration polls AnyList every 60 seconds by default. Local mutations request a refresh immediately, but changes made in another AnyList client may take up to one polling interval to appear.

### Missing Shopping List

Open the integration options and confirm that the list is selected. If the list was created after setup, reload the integration or revisit options after the next refresh.

### Meal Plan Sensor Is Missing

Enable **Meal Plan Calendar URL** in the integration options and reload the integration. AnyList may require a paid feature tier for meal planning calendar export.

## Removal

1. In Home Assistant, go to **Settings** -> **Devices & services**.
2. Open the **AnyList** integration entry.
3. Delete the integration entry.
4. If installed manually, remove `custom_components/anylist`.
5. Restart Home Assistant.

## Requirements

This integration includes a local pure-Python AnyList client. No external AnyList client package or Rust extension is required.

## Security And Privacy

The integration stores AnyList credentials in the Home Assistant config entry store, as is typical for UI-configured integrations. Diagnostics redact credentials, tokens, and private URLs.

## Acknowledgments

The local client follows the protobuf-over-multipart AnyList API shape documented by the excellent [anylist_rs](https://github.com/phildenhoff/anylist_rs) project by [@phildenhoff](https://github.com/phildenhoff).

## Disclaimer

This project is unofficial and is not affiliated with or endorsed by AnyList or Purple Cover, Inc. Use it at your own risk and in accordance with AnyList's terms of service.

## License

MIT

[hacs-badge]: https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=flat-square
[hacs-url]: https://github.com/hacs/integration
[release-badge]: https://img.shields.io/github/v/release/moryoav/ha-anylist?style=flat-square
[release-url]: https://github.com/moryoav/ha-anylist/releases
[hassfest-badge]: https://img.shields.io/github/actions/workflow/status/moryoav/ha-anylist/hassfest.yaml?branch=main&style=flat-square&label=hassfest
[hassfest-url]: https://github.com/moryoav/ha-anylist/actions/workflows/hassfest.yaml
[validate-badge]: https://img.shields.io/github/actions/workflow/status/moryoav/ha-anylist/validate.yaml?branch=main&style=flat-square&label=validate
[validate-url]: https://github.com/moryoav/ha-anylist/actions/workflows/validate.yaml
[license-badge]: https://img.shields.io/github/license/moryoav/ha-anylist?style=flat-square
[license-url]: https://github.com/moryoav/ha-anylist/blob/main/LICENSE
