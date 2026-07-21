# Palworld Component: Saves & Backups

Reached from the Palworld instance menu beneath [World Settings](./instance-world-settings.md).
Its internal route id remains `backups` so existing links and navigation state continue to
work.

- The panel title is `Saves & Backups`; it does not repeat the selected instance name.

## Managed worlds

- The page lists direct world folders beneath
  `Pal/Saved/SaveGames/0` that contain `Level.sav`; symbolic links are ignored. Each row
  shows folder id, active marker, total size, modification time, and player-save count.
- The active id is the profile's `DedicatedServerName`. Switching to another managed
  world is disabled while the instance is running or externally attached and requires
  confirmation.
- A switch first creates a mandatory safety backup, then atomically updates the profile
  id and `GameUserSettings.ini`. If synchronization fails, both values are rolled back and
  the original world remains active.
- Dedicated-save import is initiated from [Add Instance](../../../shared/components/add-instance.md),
  not this page; imported worlds follow the same managed-world and active-id rules.

## Backup settings and files

- Settings include the safe backup-directory picker, interval in minutes, maximum files
  (default 20), and default-On `Skip scheduled backup with no players`, plus `Backup now`.
- Above the `Managed backup files` table, a separate table lists only the active world's
  Palworld snapshots from `backup/world/<snapshot>`. Each row shows snapshot name,
  modification time, size, and Rollback; world and type columns are omitted because both
  are fixed by the active-world view.
- The built-in backup heading has a folder icon that opens the active world's
  `backup/world` folder, matching the managed-backup folder shortcut.
- Backup now first invokes REST Save when REST is available. If flushing fails, Palsitter
  warns that disk state may be older and requires `Create anyway` before archiving; it
  never silently reports the archive as fully flushed.
- Each archive row shows filename, timestamp, size, Restore, and confirmed Delete.
  Deletion affects only the selected managed archive.
- Managed and built-in Rollback both require confirmation and a successful managed safety
  backup. They record whether the instance was inactive, Palsitter-owned running, or
  externally attached, stop any reachable server, and abort before replacing files unless
  process/endpoint checks confirm it is stopped. A built-in rollback may restore only a
  snapshot beneath the active world's `backup/world` directory. After restoring, only the
  previously owned running instance restarts; initially inactive and externally attached
  instances remain stopped.
- While backup, switch, or restore is running, conflicting save/lifecycle actions are
  disabled and progress/errors update without rebuilding the file table.

**Tests:** `tests/test_gui_playwright.py` clicks world switching, Backup now flush failure,
Create anyway, verifies Test schedule is absent, restore ownership states, and confirmed deletion.
Focused tests use temporary folders and fake REST/process state to verify metadata,
full-world archive contents, built-in-backup and symbolic-link exclusion, built-in backup
listing, switch rollback, mandatory safety backups, and state-preserving restore.
