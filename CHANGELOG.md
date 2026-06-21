# Changelog

## 0.4.1 - 2026-06-21

- Reuse known AnyList categories when Home Assistant adds items through the
  exposed todo entities.
- Include the category match identifier in add-item payloads so AnyList can
  associate the new item with the resolved category.
