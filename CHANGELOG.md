# Changelog

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
