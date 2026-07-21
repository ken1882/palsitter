# Palworld Feature: World Settings

Every Palworld instance has its own game *world* settings - difficulty, rates, PvP,
base camp limits, server name/password, RCON/REST toggles, and so on - roughly 100
fields, all originally sourced from Palworld's `OptionSettings=(...)` struct in
`PalWorldSettings.ini`. These are separate from Palsitter's own per-instance `Profile`
fields (backup schedule, crash-restart, ...), except for network values needed by
Palsitter itself. `PublicPort` is synchronized to the internal launch/status game port;
`RESTAPIPort` and `AdminPassword` are synchronized to Palsitter's REST client, which
always connects to `localhost` using the fixed account `admin`.

The full field list, grouped by category, lives in `module/worldsettings/schema.py` as a
single data-driven table (key, category, type, default, i18n key) - that table is the
only place field metadata is defined; the ini codec, the `.sav` codec, and the
[World Settings page](../components/instance-world-settings.md) all iterate it rather
than hand-coding each field.

## Two on-disk formats

- **`PalWorldSettings.ini`** - lives at
  `<workdir>/Pal/Saved/Config/WindowsServer/PalWorldSettings.ini` on Windows and
  `<workdir>/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini` on native Linux.
  Existing files are preserved when only the other platform's path exists. Palsitter
  parses and rewrites only the single
  `OptionSettings=(...)` line, preserving every other line and the file's existing
  newline style untouched.
- **`WorldOption.sav`** - a binary Unreal Engine save file. When present, it overrides
  `PalWorldSettings.ini` entirely. It lives inside the world's own save folder under
  `profile.backup_source` at
  `Pal/Saved/SaveGames/0/<DedicatedServerName>/WorldOption.sav`.
  Palsitter reads and writes it using the `palworld-save-tools` library.

## Auto-detect on load, explicit choice on save

Opening the World Settings page checks the save folder named by the profile's stable
`DedicatedServerName` for `WorldOption.sav`. If it exists, its values are loaded and the format selector defaults
to `WorldOption.sav`; otherwise the ini is loaded and the selector defaults to
`PalWorldSettings.ini`. The user can switch the selector to the other format at any
time - `Save` always writes to whichever format is currently selected, independent of
which one was auto-detected. Saving as `WorldOption.sav` creates the dedicated save
folder when necessary; saving as ini always works regardless.

Every successful save also stores the normalized settings dictionary in the Palsitter
profile as a synchronized fallback copy. If neither target file exists, the World
Settings page loads this profile copy before falling back to schema defaults.

`CrossplayPlatforms` is edited as Steam/Xbox/PS5/Mac checkboxes. The profile copy stores
the selected values as a list, while Palworld's INI and SAV formats receive the required
parenthesized representation such as `(Steam,PS5,Mac)`.

## Valid stale-`.sav` warning

Because a `WorldOption.sav` overrides the ini unconditionally, switching the format
selector to `PalWorldSettings.ini` and saving does **not** make the ini take effect while
a `WorldOption.sav` still exists - the game keeps reading the `.sav`. The page shows a
warning banner explaining this whenever a `WorldOption.sav` was loaded. Palsitter never
deletes or otherwise touches that file automatically; per this project's
destructive-action conventions, that stays a manual, deliberate action for the operator
outside of Palsitter (the ini edit is still saved to disk, so it's ready and correct for
whenever the `.sav` is removed).

## Guided recovery

- If `PalWorldSettings.ini` cannot be parsed, the page reports the concrete parse error
  instead of silently loading defaults. With the server inactive, a confirmed recovery
  makes a timestamped sibling copy, regenerates the managed `OptionSettings=(...)` line
  from the synchronized profile copy or schema defaults, and preserves other readable
  lines. Failure leaves the original path unchanged.
- If `WorldOption.sav` cannot be decoded, Palsitter never applies a template over it.
  With the server inactive, a separate confirmed recovery atomically renames it with a
  timestamped `.disabled` suffix and reloads INI mode. Failure leaves the override in
  place and reports the error.
- A valid `WorldOption.sav` has no in-app disable/delete action. Normal successful SAV
  writes retain the mandatory safety-backup rule below.

## Safety backup before writing `.sav`

A `WorldOption.sav` write is a save-data write, not a config edit, so every `.sav` save
first runs [`BackupService.create_backup()`](./scheduled-backups.md) - the same rule that
governs the crash self-heal rollback. Saving as ini never triggers a backup (it isn't
save data). When saving as `.sav` for a world that has never had one, Palsitter starts
from a bundled template GVAS structure (`module/games/palworld/worldsettings/template/`) and applies
only the edited option values on top of it, rather than fabricating a save file from
scratch.

**Tests:** `tests/test_worldsettings_ini_codec.py` (parse/serialize round-trip,
unknown-key preservation, newline preservation), `tests/test_worldsettings_sav_codec.py`
(GVAS wrapper with faked and real `palworld-save-tools` calls),
`tests/test_worldsettings_service.py` (auto-detect, format selection, backup-before-`.sav`
rule, timestamped recovery, failure preservation), `tests/test_gui_playwright.py` (menu
placement, filters and dirty-state preservation, field types round-tripping to ini,
auto-detect, recovery, stale-`.sav` warning behavior, and help-tooltip content).

## Help tooltips

Every field on the [World Settings page](../components/instance-world-settings.md) has a
`(?)` icon whose tooltip text (`world.field_help.<key>` in both locale files) explains
what the setting does and, for enum fields, what each choice means. This text was
written from real fetched documentation (official Palworld docs, `palworld.wiki.gg`,
and several hosting-provider guides), not guessed from the field's literal name -
per this project's convention that a setting's behavior must be sourced, not assumed.

A handful of fields have weaker or conflicting sourcing and should get a second look if
precision matters here: `Difficulty` (community sources disagree on whether the
Casual/Normal/Hard preset still does anything on a dedicated server),
`CollectionObjectRespawnSpeedRate` (multiple hosting guides, but no official doc, claim
this rate is inverted relative to every other rate field), `bIsMultiplay` (conflicting
reports on whether it has any effect on a true dedicated server),
`bDisplayPvPItemNumOnWorldMap_Player` / `_BaseCamp` (thinly documented; the "PvP item
num" concept is pieced together rather than spelled out anywhere), and
`PhysicsActiveDropItemMaxNum` (official Palworld 1.0 docs describe it as the maximum
number of dropped items that can use physics behavior, but do not document its default
value).
