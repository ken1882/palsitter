# Palworld Component: World Settings

Reached from the instance navigation menu on [Instance Overview](./instance-overview.md),
directly beneath [`Server Settings`](./instance-server-settings.md); embeds the form
directly in the content area (no modal). Behavior for how the underlying data is loaded
and saved is described in [World Settings](../features/world-settings.md); this page is
about layout only.

- The panel title is `World Settings`; it does not repeat the selected instance name.
- Each schema category is a sibling section card and right-side navigator target. A sticky
  filter row below the title contains search and `Changed only`; category chips are
  replaced by the navigator. Search matches localized labels and raw setting keys
  case-insensitively.
- Filtering hides the existing field wrappers in the DOM instead of rebuilding inputs,
  preserving unsaved values, password visibility, validation errors, and dirty state.
  An empty result shows a localized message without hiding the filters.
- The roughly 100 settings remain mounted under their category cards; cards and navigator
  entries with no visible fields are hidden. There are no
  collapsible/accordion sections and no sliders or hard ranges without an authoritative
  range source.
- Every boolean field is the same square on/off toggle used on Server Settings; enum
  fields (e.g. `Difficulty`, `DeathPenalty`, `LogFormatType`) are `<select>` dropdowns;
  `CrossplayPlatforms` is a four-choice checkbox group for Steam, Xbox, PS5, and Mac;
  everything else is a plain text/number input. Numeric fields have a working
  increment/decrement spinner: rate/multiplier (float) fields step by `0.1`, other
  numeric (int) fields step by `1`; free-text fields (e.g. `RandomizerSeed`,
  `ServerName`) have no spinner since they aren't numeric.
- `Server password` and `Admin password` are masked inputs with eye buttons that toggle
  visibility. New instances receive a random eight-character lowercase alphanumeric
  admin password and their REST API toggle defaults to On. `Enable Game Data API` is a
  launch-only control on Server Settings, not a World Settings field; it adds
  `-enable-gamedata-api` to PalServer and is not written to the world INI.
- Every field label has a small circular `[i]` help icon immediately after it
  (`.field-help`). Hovering (or focusing via keyboard) shows a tooltip explaining what
  the setting does and, for enum fields, what each choice means — sourced from official
  and community documentation rather than guessed from the field's name (see [World
  Settings](../features/world-settings.md#help-tooltips) for sourcing and confidence
  notes on specific fields).
- Editing the form reveals the same viewport-bottom floating unsaved-changes bar as
  Server Settings, with a changed-field count, `Reset`, and `Save`; it is hidden while
  clean. Boolean toggle changes are included in the count. Save and Reset disable
  immediately when clicked to reject duplicate actions, then unlock after validation
  failure. `Reset` updates the mounted controls from disk, re-running the same
  auto-detect as opening the page without repainting the form. There is no `Back`
  button and no `Delete instance` button on this page.
- `Save` validates field types before writing. Invalid numeric fields receive a red
  border and inline error message, and the world settings are not saved until highlighted
  errors are fixed. After the INI is successfully written, an active
  `WorldOption.sav` is renamed to `WorldOption.sav.disabled` so it cannot override the
  saved INI values.
- Saving with `RESTAPIEnabled` Off or an empty admin password opens a warning that most
  Palsitter management features will become unavailable. `Cancel` keeps the edits
  unsaved; `Save anyway` persists them. The same confirmation interrupts `Save and
  leave`, then resumes navigation only after the operator confirms and the save succeeds.
- Leaving the page through in-app navigation while the form has unsaved edits opens an
  unsaved-changes dialog with `Save and leave`, `Discard changes`, and `Cancel`. Browser
  refresh/reload is not intercepted.
- A malformed `PalWorldSettings.ini` shows its parse error and a recovery action. Recovery
  is disabled while the server is active; after confirmation it makes a timestamped copy
  and regenerates only Palsitter-managed defaults.

The instance navigation places [`Saves & Backups`](./instance-saves-backups.md) directly
beneath World Settings.

**Tests:** `tests/test_gui_playwright.py` clicks category, search, Changed only, boolean
toggles, password, Reset/Save, navigation guards, and INI recovery through the real
page. Tests verify filtering never loses edits, action buttons lock immediately, and
recovery is unavailable while active.
