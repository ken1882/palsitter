# Shared Component: Add Instance

Opened from the `Add` item in the [Left Sidebar](./left-sidebar.md).

- The modal contains a game selector, profile name field, source-profile selector, and a
  single `Confirm` action; it has no Cancel button and is dismissed with the header close
  (×) icon.
- The game selector lists Palworld and Satisfactory, defaulting to Palworld. Satisfactory
  creates a non-runnable placeholder and does not guess any server configuration.
- The source selector lists `template` followed only by profiles for the selected game.
  Changing games resets the source to `template`; cross-game cloning is rejected again
  when confirming.
- Palworld adds an optional file browser for `Level.sav`; leaving it empty creates a new
  instance, while selecting a valid save automatically imports its containing world folder.
  Sources under `Pal/Saved/SaveGames/<SteamID64>/<WORLD_ID>/` are detected as local
  single-player or co-op saves; sources under `Pal/Saved/SaveGames/0/<WORLD_ID>/` are
  detected as dedicated-server saves. A local save with only
  `Players/00000000000000000000000000000001.sav` is treated as single-player; multiple
  or non-host player saves are treated as co-op. A host-only co-op save cannot be
  distinguished from single-player using files alone.
  A sibling `WorldOption.sav` inside the selected
  `Pal/Saved/SaveGames/<SAVE_ROOT>/<WORLD_ID>/` is decoded into the new profile's
  `PalWorldSettings.ini` and removed from the managed copy when present. The imported
  world settings receive the profile's newly allocated network ports and REST secret.
  If no SAV is present, a companion `PalWorldSettings.ini` from
  `Pal/Saved/Config/WindowsServer/PalWorldSettings.ini` or
  `Pal/Saved/Config/LinuxServer/PalWorldSettings.ini` is imported as a fallback.
  Nested `backup` folders are excluded from the managed copy. A bare `/mnt/Level.sav`
  (or any `Level.sav` whose parent is not a 32-character world-ID folder) is rejected
  without creating an instance because its world identity and companion save files are
  unavailable.
  Satisfactory exposes no import fields.
- The Palworld file browser shows folders and only the exact `Level.sav` file.
- Local single-player and co-op worlds are imported, but the resulting warning explains
  that player identity migration may still be required before the original characters
  can be used on the dedicated server.
- Confirming an import creates a normal managed Palworld profile with newly allocated
  ports and secrets, copies only the selected world through a staging directory, and
  atomically activates it. It never changes or deletes the source and never adopts an
  external server binary or configuration in place.
- After `Confirm` starts profile creation, the confirm control is disabled and an
  undismissable `Creating profile #<name>` modal remains visible until the operation
  completes or reports an error.
- An import collision or copy failure removes only the incomplete profile and staging
  data, leaves the source untouched, and keeps the modal open with an actionable error.
- Automatic names use the game id (`palworld`, `palworld2`, `satisfactory`, ...), checking
  case-insensitive uniqueness across every game. A manually edited name is not overwritten
  when the game selection changes.
- The modal uses a `#191d21` shell, `#2f3136` body, subtle light border, and 0.3rem
  corner radius.
- Its title and labels are light gray, while its close icon is white at reduced opacity.
- Input and select controls are compact, transparent, square, and use a purple bottom
  border.
- Confirm is a blue primary button; the modal has no Cancel button.
- The backdrop is black at 50% opacity.
- Closing the modal with the close (×) icon restores the previously active sidebar item.
- Successfully creating or importing a server closes the modal, adds the new instance to
  the sidebar, and opens that instance's Overview.

**Tests:** `tests/test_gui_playwright.py` clicks the new, clone, and Palworld import paths
and verifies successful navigation. Focused tests use temporary folders to cover direct
and containing-folder scans, symbolic-link exclusion, preview metadata, staging cleanup,
source preservation, collisions, and adapter isolation.
