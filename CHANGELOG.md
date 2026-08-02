# Changelog

## 0.4.6 - 2026-08-02

- Simplify HACS installation instructions now that AnyList is available in the
  default HACS catalog.

## 0.4.5 - 2026-07-06

- Expose AnyList todo items grouped by their native AnyList categories for
  dashboard aisle displays.

## 0.4.4 - 2026-06-21

- Fix the options flow for Home Assistant 2026.6.
- Translate todo mutation and manual refresh failures.
- Expand Home Assistant integration tests above the Gold quality-scale coverage
  threshold.
- Remove unused realtime sync scaffolding; polling remains the supported update
  path.

## 0.4.3 - 2026-06-21

- Document automatic AnyList category reuse for items added from Home Assistant.

## 0.4.2 - 2026-06-21

- Use AnyList's learned per-list item category assignments when adding todo
  items from Home Assistant.
- Send full category assignment metadata with added items so items such as
  `milk` and `cucumbers` land in the same categories as the AnyList app.

## 0.4.1 - 2026-06-21

- Reuse known AnyList categories when Home Assistant adds items through the
  exposed todo entities.
- Include the category match identifier in add-item payloads so AnyList can
  associate the new item with the resolved category.
