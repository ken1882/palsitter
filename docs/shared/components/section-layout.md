# Shared Component: Section Layout

Long multi-section pages use a shared desktop layout with sibling `.panel` section scopes
in an independently scrolling content column and a right-side section navigator.

- The page heading, page-wide warnings, filters, and floating form actions remain outside
  the section cards.
- Navigator buttons use localized section labels and scroll only the section column to
  their stable target scope. Filtering hides navigator buttons whose target section has
  no visible content.
- The outer page does not scroll horizontally. At 1100 px or narrower the navigator is
  hidden and the section column uses the full available width.
- The section column ends after its final card; it does not add synthetic trailing
  scroll space to force a short final card to the top.
- Mounting is repeatable. Navigation cleanup releases listeners and observers and restores
  the content container's normal overflow behavior.
- Single-purpose full-width pages such as a map or audit table do not add a redundant
  one-item navigator.

The layout is shared by Home, global Settings, Utils, and applicable Palworld instance
pages. Specialized full-width pages retain their own layouts.

**Tests:** `tests/test_gui_playwright.py` follows real page navigation, verifies section
and navigator order, clicks navigator targets, and checks desktop/mobile geometry and
the final-card trailing space.
