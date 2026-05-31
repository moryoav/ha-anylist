# AnyList for Home Assistant
[![HACS][hacs-badge]][hacs-url] [![release][release-badge]][release-url] ![downloads][downloads-badge] [![hassfest][hassfest-badge]][hassfest-url] [![validate][validate-badge]][validate-url] [![license](https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square)](LICENSE)

A Home Assistant custom integration for [AnyList](https://www.anylist.com/) shopping lists, recipes, and meal planning.

AnyList is a shared grocery list and meal planning service. This integration brings selected AnyList shopping lists into Home Assistant as todo entities, exposes a meal plan iCalendar URL for the built-in iCal integration, and provides recipe actions for automations and scripts.

## Features

- **Shopping lists** as todo entities: view, add, check off, and remove items.
- **Shopping list change signatures** for automations that need to detect renamed, added, removed, checked, or unchecked items.
- **Meal plan iCalendar URL** as a diagnostic sensor when the option is enabled.
- **Recipe actions** to search recipes, fetch one recipe, create/update/delete recipes, and add recipe ingredients to shopping lists.
- **Polling sync fallback** with a safe cloud polling interval.

## Installation

### HACS

[![Open the AnyList HACS repository](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=moryoav&repository=ha-anylist&category=integration)

1. Open HACS in Home Assistant.
2. Add `https://github.com/moryoav/ha-anylist` as an integration custom repository until this repository is accepted as a HACS default.
3. Search for **AnyList** and install it.
4. Restart Home Assistant.
5. Add the integration from **Settings** -> **Devices & services** -> **Add integration** -> **AnyList**.

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

Use **Configure** on the integration entry to change selected lists or meal plan URL exposure. Use **Reconfigure** to update account credentials.

## Supported Functionality

### Todo Entities

Each selected AnyList shopping list appears as a todo entity. You can:

- View items on the list.
- Add new items.
- Check off and uncheck items.
- Remove one or more items.

Todo entities expose these state attributes for content-change detection:

- `items_signature`: a SHA256 hash of current item names and completion states.
- `items_signature_raw`: the normalized source string used to build the hash.

### Meal Plan iCalendar URL Sensor

If the meal plan option is enabled, the integration creates a diagnostic sensor named **Meal plan iCalendar URL**. Use that URL with Home Assistant's built-in [iCal integration](https://www.home-assistant.io/integrations/ical/) to display AnyList meal planning events as a calendar.

### Service Actions

Recipe support is exposed through Home Assistant actions for automations, scripts, and Node-RED flows:

- `anylist.refresh`
- `anylist.get_recipes`
- `anylist.get_recipe`
- `anylist.add_recipe_to_list`
- `anylist.create_recipe`
- `anylist.update_recipe`
- `anylist.delete_recipe`

See [custom_components/anylist/services.yaml](custom_components/anylist/services.yaml) for field descriptions and examples.

## Data Updates

The integration uses Home Assistant's `DataUpdateCoordinator` and polls AnyList every 60 seconds. Mutations such as adding, checking, deleting, or recipe-to-list actions request an immediate refresh after the AnyList operation completes.

If AnyList is unavailable, entities are marked unavailable through the coordinator until the next successful refresh.

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

Fetch recipes whose names contain `pasta`:

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

The integration polls AnyList every 60 seconds. Local mutations request a refresh immediately, but changes made in another AnyList client may take up to one polling interval to appear.

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

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square
[hacs-url]: https://github.com/hacs/integration
[release-badge]: https://img.shields.io/github/v/release/moryoav/ha-anylist?style=flat-square
[release-url]: https://github.com/moryoav/ha-anylist/releases
[downloads-badge]: https://img.shields.io/github/downloads/moryoav/ha-anylist/total?style=flat-square
[hassfest-badge]: https://img.shields.io/github/actions/workflow/status/moryoav/ha-anylist/hassfest.yaml?branch=main&style=flat-square&label=hassfest
[hassfest-url]: https://github.com/moryoav/ha-anylist/actions/workflows/hassfest.yaml
[validate-badge]: https://img.shields.io/github/actions/workflow/status/moryoav/ha-anylist/validate.yaml?branch=main&style=flat-square&label=validate
[validate-url]: https://github.com/moryoav/ha-anylist/actions/workflows/validate.yaml
[license-badge]: https://img.shields.io/github/license/moryoav/ha-anylist?style=flat-square
[license-url]: https://github.com/moryoav/ha-anylist/blob/main/LICENSE
