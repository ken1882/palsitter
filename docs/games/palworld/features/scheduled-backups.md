# Palworld Feature: Scheduled Backups

Each Palsitter-owned running instance backs up its fixed save root
`Pal/Saved/SaveGames/0` according to the settings on
[Saves & Backups](../components/instance-saves-backups.md).

- An interval of 0 or less disables scheduling. Starting the supervisor begins the
  interval wait; it does not create an immediate backup. Stopping/detaching ends that
  instance's schedule without a separate backup process or start/stop control.
- A backup zip contains every regular file in each managed world folder, including nested
  player and metadata files, while excluding Palworld's nested `backup` folders and all
  symbolic links. If no eligible save files exist, the attempt is Skipped and no empty
  archive is created.
- Retention deletes oldest managed archives first after a successful creation until no
  more than `Max backup files` remain; the default is 20.
- Scheduled attempts default to Skipped when REST metrics report zero online players.
  Manual Backup now is never skipped for player count.
- A failed attempt uses the existing retry/backoff behavior, reports its reason, and does
  not advance retention or delete an older archive.
- Automatic self-heal, world switching, WorldOption writes, and manual Restore use the
  same backup service for mandatory safety archives but are not subject to the
  no-player skip.

**Tests:** `tests/test_backup_service.py` uses temporary directories to cover contents,
nested-backup and symbolic-link exclusion, empty skips, retention, safety archives,
failure preservation, and retry/backoff. Supervisor tests cover schedule start/stop and
Playwright covers the real backup-settings and manual-backup path.
