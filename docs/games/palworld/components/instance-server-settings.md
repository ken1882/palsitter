# Palworld Component: Server Settings

Reached from the instance navigation menu on [Instance Overview](./instance-overview.md);
embeds the profile form directly in the content area (no modal).

- The panel title is `Settings`; it does not repeat the selected instance name.

- The form includes `Query port`; newly created or cloned instances are assigned
  non-colliding ports automatically (see [Port Allocation](../features/port-allocation.md)).
  The player-facing game port, REST port, and REST password are edited only in [World
  Settings](./instance-world-settings.md). Palsitter's REST client always connects to
  `localhost` with the fixed account `admin`.
- Backup directory, interval, retention, and backup actions live on the separate
  `Saves & Backups` instance tab beneath World Settings.
- The server working directory and executable are fixed under
  `profile/<name>/steamcmd/steamapps/common/PalServer`; they are not separate editable
  settings.
- The backup source is fixed to
  `profile/<name>/steamcmd/steamapps/common/PalServer/Pal/Saved/SaveGames/0`.
- New profiles default `Backup dir` to `profile/<name>/backups`.
- Every new profile receives a random 32-character uppercase alphanumeric `Dedicated
  server name`. The editable field accepts exactly `[0-9A-Z]{32}` and is synchronized
  to `Pal/Saved/Config/WindowsServer/GameUserSettings.ini` on Windows and
  `Pal/Saved/Config/LinuxServer/GameUserSettings.ini` on native Linux as
  `DedicatedServerName=...` under `[/Script/Pal.PalGameLocalSettings]`, preserving the
  section's other settings.
- The Steam app id is fixed in code to the Palworld dedicated server app id (`2394010`);
  it is not a separate editable setting.
- The Installation section includes `Update on start` (default On), `Auto update`
  (default On), and `Idle shutdown for update (minutes)` (default 30). Auto update is
  disabled when Update on start is Off; the idle field is disabled when Auto update is
  Off. The values remain saved while their controls are disabled.
- The SteamCMD row is not an editable path. It checks
  `profile/<name>/steamcmd/steamcmd.exe` on Windows and
  `profile/<name>/steamcmd/steamcmd` on Linux. If present, `Show` opens its folder with
  the OS file explorer; otherwise it reports Missing. Installation, update, and
  Validate/Repair actions live in Overview's Operations card and follow
  [Installation & Updates](../features/installation-and-updates.md).
- Every field label has a small circular `[i]` help icon (`.field-help`). Hovering or
  focusing it shows tooltip text sourced from `settings.help.<profile field>` locale
  keys and describing the behavior implemented by the code path for that field.
- The Launch Options section has typed controls for `-useperfthreads`,
  `-NoAsyncLoadingThread`, `-UseMultithreadForDS`, `-NumberOfWorkerThreadsServer`,
  `-enable-gamedata-api`, `-publiclobby`, and `-logformat`, plus an Advanced extra-
  arguments list. The `Enable Game Data API` control is below `Worker threads`. The extra
  arguments use one text input per argument, with add and remove icon buttons. New profiles
  enable `-useperfthreads` and `-UseMultithreadForDS`, leave `-NoAsyncLoadingThread`
  disabled, and set `-NumberOfWorkerThreadsServer` to the detected logical CPU count
  minus one (minimum 1). Existing profiles retain their saved values. Tooltips describe
  these defaults and link to the official launch-argument guidance.
- Enabled typed launch options are prepended to the extra-arguments list as disabled,
  read-only rows. Their input and remove button show `Controlled by other options` on
  hover. The controlled rows update immediately when their source toggle changes or the
  worker-thread number is edited. Advanced arguments preserve order and casing for
  unrecognized values. Save rejects a recognized option repeated there, case-insensitively,
  so one setting cannot produce conflicting arguments. The only editable row has no remove
  button until a second row is added.
- Schema-v2 migration parses recognized legacy executable arguments into typed fields and
  retains all unrecognized arguments in their original order. Migration is atomic and
  does not change an existing profile's effective command line.
- Crash, memory, and planned restart settings live only on the separate
  [Auto Restart](./instance-auto-restart.md) page.
- Editing the form reveals a floating unsaved-changes bar pinned above the bottom of the
  viewport, with `Reset` and `Save` actions. It is hidden while the form is clean.
- `Save` is the green/purple primary-save action; `Reset` is a neutral button that
  reloads the form from the saved profile, discarding unsaved edits. There is no `Back`
  button.
- `Save` validates field types and checks every configured path field exists with the
  expected kind (folder or file). Invalid fields receive a red border and an inline error
  message; the profile is not saved until highlighted errors are fixed.
- Save validates the Server Settings fields and structured/Advanced argument conflicts;
  automatic restart validation belongs to the Auto Restart page.
- Leaving the page through in-app navigation while the form has unsaved edits opens an
  unsaved-changes dialog with `Save and leave`, `Discard changes`, and `Cancel`. Browser
  refresh/reload is not intercepted.
- A horizontal rule separates the form from a red `Delete instance` button beneath it;
  Delete remains in the document and is not part of the floating action bar.
- `Delete instance` opens a confirmation modal; it does not delete immediately.
- The confirmation modal shows a warning that only the profile reference is removed, an
  input box, a `Wipe data` checkbox, and a red `Yes, delete` button that stays disabled
  until the input exactly matches the instance's displayed name (e.g. `default` for the
  default instance).
- When `Wipe data` is checked, clicking `Yes, delete` opens a second confirmation modal;
  confirming it permanently removes the instance directory, including server files,
  save games, backups, and profile data. Canceling leaves the instance unchanged.
- Confirming deletes only the profile reference (its `<name>.json`); the server working
  directory, save games, and backups on disk are left intact.
- After a successful delete the modal closes, the instance is removed from the sidebar,
  and the Home view is shown.
