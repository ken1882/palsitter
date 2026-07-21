# Shared Feature: Pagination Table

The reusable pagination table is a browser-rendered widget under `module/webui`.

- Callers provide typed column descriptors and mapping rows.
- The first render sizes columns from their headers and content, then gives remaining
  space to the final column. Users can still resize any resizable column.
- Every table must provide a `timestamp` column with `datetime` type.
- The widget supports configurable page sizes, text search, tag checkboxes, custom
  datetime bounds, and quick relative time windows.
- Search, filter, and time controls use the existing shared button and table styles.
- Popovers use one shared viewport-aware pattern. They are bounded by the viewport,
  reposition on resize or scroll, and flip above the trigger when needed.
- The table shell fills the available viewport space and scrolls its content internally.
  The footer stays anchored at the bottom, so changing pages does not move the outer page.
- The toolbar search input is a single-line control aligned with the other toolbar controls.
- Pagination is centered and provides first/previous/next/last controls, numbered page
  buttons, ellipses for skipped ranges, and an editable current-page number.
- Filtering and pagination reset to the first page and are applied without rebuilding the
  surrounding page.
