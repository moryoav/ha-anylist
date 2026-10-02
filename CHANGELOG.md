# Changelog

## 0.8.0 - 2026-10-02

- Honour the list's **Insert New Items** setting when Home Assistant adds an
  item, so new items land at the top of lists set to **At Top**, as they do in
  the AnyList app ([#5](https://github.com/moryoav/ha-anylist/issues/5)).
- Add **Item sort order** and **Insert new items** select entities for each
  shopping list. They read and change the same list settings as the AnyList
  app.
- Support reordering items by dragging them in the Home Assistant to-do list,
  on manually sorted lists.
- Fix renaming an item from Home Assistant, which was previously ignored.
- Show items of alphabetically sorted lists in alphabetical order, matching
  the AnyList app.
- Give each shopping list its own device holding the list and its settings.
  Existing todo entities keep their entity IDs and names and move to the new
  list devices. Devices of deselected or deleted lists are removed.

## 0.7.1 - 2026-09-29

- Add automated HACS ZIP releases containing the AnyList integration files at
  the archive root.
- Run HACS and Hassfest checks on main pushes, pull requests targeting main,
  and release workflow calls while keeping the daily HACS check.

## 0.7.0 - 2026-09-22

- Add a configurable polling interval from 60 to 3600 seconds during setup and
  in integration options, keeping the 60-second default. Thanks to
  [@kevdliu](https://github.com/kevdliu) for [PR #4](https://github.com/moryoav/ha-anylist/pull/4).
- Preserve selected shopping lists when changing options while AnyList is
  unavailable, and safely handle invalid saved polling intervals.
- Include the polling interval in diagnostics and update the configuration and
  shopping list synchronization guides.

## 0.6.0 - 2026-09-20

- Add `anylist.search_recipes` with local word matching, Unicode normalization,
  minor typo tolerance, and optional ingredient-name matching.
- Return ranked, compact candidates with exact IDs, match explanations, a
  configurable limit, returned count, and an indicator of omitted candidates.
- Document the search-then-read workflow for conversation agents while keeping
  `get_recipes` and `get_recipe` behavior unchanged.

## 0.5.1 - 2026-09-20

- Add an optional `image_url` to `anylist.update_recipe` to add or replace a
  recipe image using AnyList's image import flow.
- Preserve existing images when `image_url` is omitted, along with recipe
  metadata such as notes, source, rating, and creation date during updates.

## 0.5.0 - 2026-09-20

- Add an optional `image_url` to `anylist.create_recipe`. AnyList downloads and
  stores the image before the recipe is created.
- Return AnyList-hosted image URLs for recipes with uploaded photos.
- Fix recipe deletion by sending the recipe and its recipe data ID in the
  format expected by AnyList.

## 0.4.9 - 2026-09-13

- Add a guide for synchronizing Alexa and AnyList shopping lists with a kitchen
  dashboard card, including complete YAML examples and screenshots.

## 0.4.8 - 2026-09-13

- Fix items assigned to newly created AnyList categories appearing under
  Uncategorized, including completed items, while preserving built-in category
  grouping.

## 0.4.7 - 2026-08-10

- Make manual refresh failures visible to Home Assistant instead of reporting
  stale coordinator data as a successful refresh.
- Mark a todo entity unavailable when its specific AnyList shopping list is
  absent from refreshed data, while keeping valid empty lists available.

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
