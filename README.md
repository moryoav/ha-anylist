# AnyList for Home Assistant

A Home Assistant custom integration for [AnyList](https://www.anylist.com/) shopping lists and meal planning.

## Features

- **Shopping Lists** as Todo entities - view, add, check off, and remove items
- **Meal Plan Calendar** - view your AnyList meal plan in Home Assistant
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

### Calendar Entity

Your AnyList meal plan calendar is available as a calendar entity, showing planned meals.

## Requirements

This integration requires the [pyanylist](https://github.com/ozonejunkieau/pyanylist) library, which is installed automatically.

**Note:** The pyanylist library is a Rust extension. Pre-built wheels are available for:
- Linux (x86_64, aarch64)
- macOS (Intel, Apple Silicon)

## Disclaimer

This is an unofficial integration and is not affiliated with or endorsed by AnyList. Use at your own risk and in accordance with AnyList's terms of service.

## License

MIT
