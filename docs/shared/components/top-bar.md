# Shared Component: Top Bar

- `#pywebio-scope-header` is a full-width, 3rem-high flex row.
- Its content order is: logo image, `Palsitter` brand text, `header_status`, then the
  current page title.
- The logo and `Palsitter` brand are left-aligned.
- The current page title is centered and changes with the selected view, such as `Home`,
  `Updater`, `Utils`, `Overview`, or `Settings`.
- `header_status` is empty when no server instance is selected.
- Selecting a server instance displays exactly one status indicator and its matching text:
  - `Running`: green `spinner-border` with the unfilled border style.
  - `Inactive`: gray `spinner-border` with the fully bordered fill style.
  - `Warning`: yellow `spinner-grow`.
  - `Updating`: green `spinner-grow`.
- The brand, status indicator, and status text have visible spacing and never overlap.
- Returning to a Home view clears the selected-instance status.
