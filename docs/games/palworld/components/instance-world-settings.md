# Palworld Component: World Settings

Reached from the instance navigation menu on [Instance Overview](./instance-overview.md),
directly beneath [`Server Settings`](./instance-server-settings.md); embeds the form
directly in the content area (no modal). Behavior for how the underlying data is loaded
and saved is described in [World Settings](../features/world-settings.md); this page is
about layout only.

- The panel title is `World Settings`; it does not repeat the selected instance name.
- A sticky filter row below the title contains `All`, one option per schema
  category, a search input, and `Changed only`. Search matches localized labels and raw
  setting keys case-insensitively; search and Changed only apply within the selected
  category.
- Filtering hides the existing field wrappers in the DOM instead of rebuilding inputs,
  preserving unsaved values, password visibility, validation errors, and dirty state.
  An empty result shows a localized message without hiding the filters.
- The roughly 100 settings remain grouped under plain category headings inside one flat,
  scrollable `.panel`; headings with no visible fields are hidden. There are no
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
  admin password, and their REST API toggle defaults to On.
- Every field label has a small circular `(?)` help icon immediately after it
  (`.field-help`). Hovering (or focusing via keyboard) shows a tooltip explaining what
  the setting does and, for enum fields, what each choice means — sourced from official
  and community documentation rather than guessed from the field's name (see [World
  Settings](../features/world-settings.md#help-tooltips) for sourcing and confidence
  notes on specific fields).
- Editing the form reveals the same viewport-bottom floating unsaved-changes bar as
  Server Settings, with a changed-field count, `Reset`, and `Save`; it is hidden while
  clean. `Reset` reloads the form from disk, re-running the same auto-detect as opening
  the page. There is no `Back` button and no `Delete instance` button on this page.
- `Save` validates field types before writing. Invalid numeric fields receive a red
  border and inline error message, and the world settings are not saved until highlighted
  errors are fixed.
- Leaving the page through in-app navigation while the form has unsaved edits opens an
  unsaved-changes dialog with `Save and leave`, `Discard changes`, and `Cancel`. Browser
  refresh/reload is not intercepted.
- A malformed `PalWorldSettings.ini` shows its parse error and a recovery action. Recovery
  is disabled while the server is active; after confirmation it makes a timestamped copy
  and regenerates only Palsitter-managed defaults.

The instance navigation places [`Saves & Backups`](./instance-saves-backups.md) directly
beneath World Settings.

**Tests:** `tests/test_gui_playwright.py` clicks category, search, Changed only, password,
Reset/Save, navigation guards, and INI recovery through the real page. Tests verify
filtering never loses edits and recovery is unavailable while active.
