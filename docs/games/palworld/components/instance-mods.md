# Palworld Component: Mods

Reached from the Palworld instance menu after World Settings. This page is not exposed by
unsupported game adapters.

- The page uses sibling `Upload mod`, `UE4SS mod loader`, `Lua mods (UE4SS)`, `Pak
  mods`, and `PalSchema mods` cards with a right-side navigator. Native Linux omits the
  unsupported Lua card and navigator item but retains Upload mod for Pak files.

## Upload mod

- Native browser controls accept multiple files or one decompressed folder without
  exposing absolute client paths. Supported inputs are ZIP, 7z, TAR, TAR.GZ/TGZ,
  standalone GZ, Pak, UTOC, and UCAS, with a 200 MiB batch limit.
- Upload opens a non-dismissible progress dialog that inventories every selection before
  installation, then processes items sequentially. Unsupported items fail with manual
  extraction guidance while later selections continue.
- Recognized content includes official server `Info.json` packages, UE4SS Lua/C++ mods,
  PalSchema folders, and Pak groups. An unmarked Pak pauses for a choice between the Paks
  root, `LogicMods`, and `~mods`.
- PalSchema uploads require the PalSchema runtime to be installed first. If it is absent,
  Palsitter warns the operator and rejects the PalSchema item without creating its mod
  directory.
- Existing destination names are listed before installation and require one overwrite
  confirmation. Folder mods are replaced as a whole; Pak companion files sharing the
  package stem are replaced together. Installation ignores the current server state and
  successful batches remind the operator to restart the server.
- Cancel cooperatively stops the current item before commit when possible, skips pending
  items, and preserves prior successes. The service stages and rolls back each item on
  failure and removes temporary content after success, failure, cancellation, or page
  navigation.
- Native Linux accepts legacy Pak content but reports UE4SS Lua/C++, PalSchema, and
  official Windows-only server packages as unsupported. Archive paths, links, encryption,
  collisions, member counts, and expanded sizes are validated before installation.

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

- Lua mod folders from the active UE4SS Mods directory are shown with Enabled and Delete
  actions; the bundled `shared` directory is omitted. Enable state prefers the presence
  of `<mod>/enabled.txt`, then falls back to matching `mods.txt`/`mods.json` entries.
  Toggling synchronizes those config entries and the marker file. Built-in UE4SS mods
  are hidden from the table and remain managed by UE4SS.
- Confirmed deletion removes a user Lua mod folder. UE4SS built-in folders cannot be
  deleted through Palsitter.
- `.pak` files directly under `Pal/Content/Paks` and its `LogicMods` and `~mods` children
  are shown in a separate table on Windows and native Linux. Game-owned `Pal-*` archives
  are omitted.
- PalSchema mod folders under the active UE4SS `Mods/PalSchema/mods` directory are shown
  in a separate table after Pak mods. Enable/disable moves a complete folder between
  `mods` and the sibling `disabled-mods` quarantine so the PalSchema loader does not scan
  disabled content; Delete removes the selected folder after confirmation.
- Each Pak row has a native checkbox derived from its filename. Unticking renames `.pak`
  to `.pak.disabled`; ticking renames it back to `.pak`. The Enabled and Delete columns
  have fixed widths so the table does not shift when the suffix changes. A separate
  confirmed Delete action removes only that Pak file.
- Each table has a folder icon that opens its directory in the host operating system's
  default file browser when the browser is connected through localhost or the trusted
  desktop executable session. The icon is hidden for remote browser sessions. Lua rows
  remain read-only.

**Tests:** Service tests fake the fixed release download and use temporary Palworld
installations. Upload tests use generated local archives and folder selections. The GUI
test clicks the real Mods page path, exercises upload progress and choices, installs a
fake fixed UE4SS release, checks both lists and folder buttons, and confirms removal
without contacting GitHub release APIs or running a real server.
