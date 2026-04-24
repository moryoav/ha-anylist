# AnyList for Home Assistant

A Home Assistant custom integration for [AnyList](https://www.anylist.com/) shopping lists and meal planning.

## Features

- **Shopping Lists** as Todo entities - view, add, check off, and remove items
- **Meal Plan iCalendar URL** - exposes your AnyList meal plan URL for use with Home Assistant's iCal integration
- **Recipe services** - search recipes, fetch one recipe, create/update/delete recipes, and add recipe ingredients to shopping lists from automations or Node-RED
- **Real-time sync** - changes sync automatically via WebSocket

## Installation

### HACS (Recommended)

1. Add this repository as a custom repository in HACS
2. Search for "AnyList" and install
3. Restart Home Assistant
4. Add the integration via Settings → Devices & Services → Add Integration → AnyList

### Manual Installation

1. Copy `custom_components/anylist` to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant
3. Add the integration via Settings → Devices & Services → Add Integration → AnyList

## Configuration

The integration is configured via the UI. You'll need your AnyList email and password.

## Entities

### Todo Entities

Each AnyList shopping list appears as a todo entity. You can:
- View items on the list
- Add new items
- Check off items
- Remove items

### Meal Plan iCalendar URL Sensor

If you enable the meal plan option during setup, a diagnostic sensor is created containing your AnyList iCalendar URL. You can use this URL with Home Assistant's built-in [iCal integration](https://www.home-assistant.io/integrations/ical/) to display your meal plan as a calendar:

1. Go to Settings → Devices & Services → Add Integration
2. Search for "iCal" and add it
3. Copy the URL from the AnyList "Meal Plan iCalendar URL" sensor
4. Paste it into the iCal integration setup

## Service Actions

Recipe support is currently exposed through AnyList service actions for automations, scripts, and Node-RED flows:

- `anylist.get_recipes`
- `anylist.get_recipe`
- `anylist.add_recipe_to_list`
- `anylist.create_recipe`
- `anylist.update_recipe`
- `anylist.delete_recipe`

## Requirements

This integration requires the [pyanylist](https://github.com/ozonejunkieau/pyanylist) library, which is installed automatically.

**Note:** The pyanylist library is a Rust extension. Pre-built wheels are available for:
- Linux (x86_64, aarch64)
- macOS (Intel, Apple Silicon)

## Acknowledgments

This integration is built on top of the excellent [anylist_rs](https://github.com/phildenhoff/anylist_rs) Rust library by [@phildenhoff](https://github.com/phildenhoff), which provides the underlying AnyList API implementation.

## Disclaimer

This is an unofficial integration and is not affiliated with or endorsed by AnyList. Use at your own risk and in accordance with AnyList's terms of service.

## License

MIT
