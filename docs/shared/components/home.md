# Shared Component: Home

`Home`, `Updater`, and `Utils` are sibling tabs reached from the same secondary menu once
the `Home` item is active in the [Left Sidebar](./left-sidebar.md); see
[Updater](./updater.md) and [Utils](./utils.md) for those tabs.

- The landing Home tab shows a responsive card grid containing every stored instance,
  followed by the language selector (see [Language / i18n](../features/i18n.md)), theme
  selector, project description, repository link, and the browser URL for the running UI.
- A fresh installation shows an empty-state explanation and an `Add instance` action
  that opens the normal [Add Instance](./add-instance.md) flow.
- Cards render a loading placeholder immediately, then refresh through the selected game
  adapter every five seconds. At most one summary request may be in flight per instance;
  a slow or failed refresh leaves the last successful values visible and marks them stale.
- A supported card can show lifecycle/ownership state, players, FPS, process CPU and RSS
  memory, endpoint states, game/build version, latest and next backup, and current
  install/update progress when the adapter supplies those fields. Missing optional fields
  render as `-`; shared Home code never obtains game-specific values directly.
- Cards have distinct `Unsupported`, `Installing`, `Failed`, `Inactive`, Palsitter-owned
  `Running`, and externally attached states. Operation failures include the adapter's
  actionable message and Retry action when one is available.
- Clicking a supported instance card opens its Overview. Clicking an unsupported card
  opens its game-owned placeholder Overview.
- Below 600 px, cards and their actions stack vertically without horizontal page
  overflow.

**Tests:** `tests/test_gui_playwright.py` covers empty startup, asynchronous/stale card
updates, navigation from supported and unsupported cards, operation progress/Retry, and
the 390 px layout. Adapter tests prove Satisfactory summaries do not call Palworld
runtime, REST, SteamCMD, port, or backup services.
