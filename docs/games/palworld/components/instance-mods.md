# Palworld Component: Mods

Reached from the Palworld instance menu after World Settings. This page is not exposed by
unsupported game adapters.

## UE4SS

- The panel reports UE4SS installation state and the version recorded by Palsitter.
  Manually installed copies are detected from either the flat or nested `ue4ss/` layout
  and display an unknown version.
- The selector offers the fixed `experimental-palworld` release from
  `Okaetsu/RE-UE4SS`, which contains the Palworld-specific UE4SS runtime required by
  current Palworld versions. Palsitter does not live-fetch UE4SS release metadata.
- Only the non-development `UE4SS-Palworld.zip` archive is offered. Install, reinstall,
  and confirmed removal require a fully stopped Windows Palworld server.
- Native Linux shows the UE4SS summary as unavailable with a Linux-specific explanation.
  UE4SS release/install/remove controls and the Lua (UE4SS) section are hidden. Palsitter
  does not install, remove, or manage UE4SS Lua/C++ mods on native Linux until a stable
  native Linux UE4SS runtime exists.
- Installation validates and stages the archive before merging it into
  `Pal/Binaries/Win64`, preserves user mod folders while changing UE4SS layouts, and sets
  `bUseUObjectArrayCache = false` in `UE4SS-settings.ini`.
- Removal deletes the tracked UE4SS loader files but preserves the Lua `Mods` folder so
  switching UE4SS versions does not require reinstalling Lua mods. It does not delete Pak
  mods. PalDefender is not installed or managed by Palsitter.

## Installed mod lists

- Lua mod folders from the active UE4SS Mods directory are shown in a read-only table;
  the bundled `shared` directory is omitted.
- `.pak` files directly under `Pal/Content/Paks` and its `LogicMods` and `~mods` children
  are shown in a separate table on Windows and native Linux. Game-owned `Pal-*` archives
  are omitted.
- Each Pak row has a native checkbox derived from its filename. Unticking renames `.pak`
  to `.pak.disabled`; ticking renames it back to `.pak`. The Enabled and Delete columns
  have fixed widths so the table does not shift when the suffix changes. A separate
  confirmed Delete action removes only that Pak file.
- Each table has a folder icon that opens its directory in the host operating system's
  default file browser. Lua rows remain read-only, and the page does not provide browser
  upload controls.

**Tests:** Service tests fake the fixed release download and use temporary Palworld
installations. `tests/test_gui_playwright.py` clicks the real Mods page, installs a fake
fixed release, checks both lists and folder buttons, and confirms removal without
contacting GitHub release APIs or running a real server.
