# Shared Component: Add Instance

Opened from the `Add` item in the [Left Sidebar](./left-sidebar.md).

## User flow

- The modal contains a game selector, profile name field, source-profile selector, and
  `Cancel` and `Confirm` actions.
- Cancel, the header close (×), and Escape dismiss the modal without creating anything.
  The previously selected sidebar page remains active.
- Confirm is the last action and uses the primary blue button style. Once creation
  starts, both actions are disabled and an undismissible `Creating profile #<name>`
  modal remains visible until completion or an error.
- Automatic names use the game id (`palworld`, `palworld2`, `satisfactory`, ...), with
  case-insensitive uniqueness across all games. Changing the selected game updates an
  untouched automatic name but never overwrites a manually edited name.
- The game selector lists Palworld and Satisfactory, defaulting to Palworld. Satisfactory
  creates a non-runnable placeholder and does not guess any server configuration.
- The source selector lists `template` followed only by profiles for the selected game.
  Changing games resets the source to `template`; cross-game cloning is rejected again
  when confirming.

Successful creation closes the modal, adds the new instance to the sidebar, and opens
its Overview.

## Palworld save import

- Palworld adds an optional `Level.sav file` field and `Browse` action. Leaving it empty
  creates or clones normally; selecting a valid save imports its containing world
  folder. There is no separate import checkbox.
- The Palworld file browser shows folders and only the exact `Level.sav` file.
- The selected `Level.sav` must be inside a 32-character world-ID folder. A bare
  `/mnt/Level.sav` or another source without that parent shape is rejected because the
  companion world files cannot be identified safely.
- A path under `Pal/Saved/SaveGames/<SteamID64>/<WORLD_ID>/` is treated as a local
  single-player or co-op world. A path under
  `Pal/Saved/SaveGames/0/<WORLD_ID>/` is treated as a dedicated-server world.
- A local world containing only
  `Players/00000000000000000000000000000001.sav` is classified as single-player.
  Multiple or non-host player saves are classified as co-op. Files alone cannot
  distinguish a host-only co-op world from single-player.
- A sibling `WorldOption.sav` is decoded into the new profile's
  `PalWorldSettings.ini`, then removed from the managed copy. Newly allocated network
  ports and the REST secret replace imported values.
- If `WorldOption.sav` cannot be decoded or parsed, it is removed only from the managed
  copy and import continues with the companion server INI or template settings. The
  source save remains unchanged.
- When no `WorldOption.sav` exists, the companion WindowsServer or LinuxServer
  `PalWorldSettings.ini` is imported when present. Otherwise template settings remain.
- Nested `backup` folders are excluded. Satisfactory exposes no import fields.
- Local single-player and co-op worlds are imported, but the resulting warning explains
  that player identity migration may still be required before the original characters
  can be used on the dedicated server.

## Safety and failures

- Confirming an import creates a normal managed Palworld profile with newly allocated
  ports and secrets, copies only the selected world through a staging directory, and
  atomically activates it. It never changes or deletes the source and never adopts an
  external server binary or configuration in place.
- An import collision or copy failure removes only the incomplete profile and staging
  data, leaves the source untouched, and keeps the modal open with an actionable error.

## Presentation

- The modal uses a `#191d21` shell, `#2f3136` body, subtle light border, and 0.3rem
  corner radius.
- Its title and labels are light gray, while its close icon is white at reduced opacity.
- Input and select controls are compact, transparent, square, and use a purple bottom
  border.
- The backdrop is black at 50% opacity.

## Verification focus

Playwright covers cancel, create, clone, and Palworld import through the real modal.
Focused tests cover source preservation, world detection, staging cleanup, collisions,
symbolic-link exclusion, and adapter isolation.
