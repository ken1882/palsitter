# Palworld Feature: World Settings

Every Palworld instance has its own game *world* settings - difficulty, rates, PvP,
base camp limits, server name/password, RCON/REST toggles, and so on - roughly 100
fields, all originally sourced from Palworld's `OptionSettings=(...)` struct in
`PalWorldSettings.ini`. These are separate from Palsitter's own per-instance `Profile`
fields (backup schedule, crash-restart, ...), except for network values needed by
Palsitter itself. `PublicPort` is synchronized to the internal launch/status game port;
`RESTAPIPort` and `AdminPassword` are synchronized to Palsitter's REST client, which
always connects to `localhost` using the fixed account `admin`.

The `Server Admin & Network` category also contains the launch-only `Enable Game Data
API` switch. When enabled (the default), Palsitter appends `-enable-gamedata-api` to
PalServer's launch command, enabling the REST `/game-data` world actor snapshot API.
This switch is stored in the Palsitter profile and is not written into
`PalWorldSettings.ini` or `WorldOption.sav`.

The full field list, grouped by category, lives in `module/worldsettings/schema.py` as a
single data-driven table (key, category, type, default, i18n key) - that table is the
only place field metadata is defined; launch-only fields are marked non-persisted, and
the INI codec and the [World Settings
page](../components/instance-world-settings.md) both iterate it rather than hand-coding
each field.

## On-disk format

- **`PalWorldSettings.ini`** - lives at
  `<workdir>/Pal/Saved/Config/WindowsServer/PalWorldSettings.ini` on Windows and
  `<workdir>/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini` on native Linux.
  Existing files are preserved when only the other platform's path exists. Palsitter
  parses and rewrites only the single
  `OptionSettings=(...)` line, preserving every other line and the file's existing
  newline style untouched. This is the only format exposed by the World Settings page
  and the only format written for an active managed world.

## Imported save migration

When importing a world containing `WorldOption.sav`, Palsitter decodes its option values
into the new profile's `PalWorldSettings.ini`, replaces `PublicPort`, `RESTAPIPort`, and
the REST admin password with the newly allocated profile values, and removes the active
SAV override. A malformed or undecodable SAV aborts the import without changing the
source. If no SAV is present, a companion server INI is imported as a fallback.

Every successful save also stores the normalized settings dictionary in the Palsitter
profile as a synchronized fallback copy. If neither target file exists, the World
Settings page loads this profile copy before falling back to schema defaults.

`CrossplayPlatforms` is edited as Steam/Xbox/PS5/Mac checkboxes. The profile copy stores
the selected values as a list, while Palworld's INI receives the required parenthesized
representation such as `(Steam,PS5,Mac)`.

## Guided recovery

- If `PalWorldSettings.ini` cannot be parsed, the page reports the concrete parse error
  instead of silently loading defaults. With the server inactive, a confirmed recovery
  makes a timestamped sibling copy, regenerates the managed `OptionSettings=(...)` line
  from the synchronized profile copy or schema defaults, and preserves other readable
  lines. Failure leaves the original path unchanged.
There is no active-world SAV write or SAV recovery action in the World Settings page.

**Tests:** `tests/test_worldsettings_ini_codec.py` (parse/serialize round-trip,
unknown-key preservation, newline preservation), `tests/test_worldsettings_sav_codec.py`
(import decoder), `tests/test_worldsettings_service.py` (INI loading and SAV-to-INI
migration), and `tests/test_gui_playwright.py` (INI-only page, filters, dirty-state
preservation, field types, import migration, recovery, and help-tooltip content).

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
