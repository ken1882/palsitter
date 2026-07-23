# Palworld Component: Instance Overview

Reached by selecting a Palworld server in the [Left Sidebar](../../../shared/components/left-sidebar.md).
The instance menu contains `Overview`, [`Players`](./instance-players.md), [`Server
Settings`](./instance-server-settings.md), [`Auto Restart`](./instance-auto-restart.md),
[`World Settings`](./instance-world-settings.md), and [`Saves & Backups`](./instance-saves-backups.md).
It also contains [`Game Map`](./instance-map.md), [`Audit`](./instance-audit.md), and
[`Tools`](./instance-tools.md).
REST Actions is not a separate item.

Satisfactory does not inherit this menu or any component on this page; its complete
contract is the [Placeholder Overview](../../satisfactory/components/instance-overview.md).

## Overview operations

There is no persistent `instance_actions` strip. Basic lifecycle state is shown once in
the top-bar `header_status`, and logs are shown once in Overview's live Log panel. A
lightweight watcher keeps the header current on non-Overview instance pages without
adding lifecycle controls to those pages.

- Overview's `Start` action updates or installs PalServer before launch. It applies the
  `Validate server files` option from Server Settings. The Operations card does not expose
  separate Check update, Update, Validate/Repair, Retry, or operation-progress controls.
- On Windows, Start first launches or connects to the instance's detached agent and then
  sends its explicit `start` command. Agent startup alone never launches PalServer. After
  GUI replacement, restore uses only `connect_existing` and `ping`/`status`; it does not
  create a missing agent or start an idle one.
- On native Linux, Start launches PalServer directly under the supervisor with `Popen`;
  Stop, KILL, adoption, and process matching use the exact Linux server executable path
  and `psutil` process supervision.
- The primary action row is `Start`/`Stop`, `Save`, and `Backup`, with the lifecycle
  action on the left and Save/Backup grouped on the right. `Save` remains visible but is
  disabled unless the server is running. No Restart button is shown.
- For a Palsitter-owned running instance, Stop requests graceful REST shutdown with
  `{"waittime": 5, "message": "Server will shutdown immediately"}`. The top-bar status
  changes to `Stopping` while shutdown is in progress. While its process tree remains
  alive, `Stop` changes to `KILL`, which force-terminates that owned process tree.
- For an agent-managed Windows instance, Stop/KILL are sent to the detached agent. The
  agent terminates PalServer through its Job Object and then exits; an unexpected agent
  exit closes that Job Object and terminates PalServer descendants.
- A server detected by an exact configured executable-name and executable-path match is
  displayed as Running after a GUI restart. Opening Overview automatically starts the
  Palsitter supervisor in external-watch mode, without updating or launching PalServer,
  and exposes REST `Stop`, `Save`, `Backup`, and `Detach` without requiring a Start
  click. It never exposes KILL or managed Restart for that external server.
- Intentional Stop, KILL, and Detach are not crash-restart events.
- Save and graceful Stop use Palworld's REST API only while the matching process is
  running and the configured REST TCP endpoint is open.
- Installation, update, validation, update checks, and progress behavior is defined in
  [Installation & Updates](../features/installation-and-updates.md).

## Operations and reliability (left column)

- The Operations card shows lifecycle/ownership state and UDP/REST/RCON endpoint states,
  including each endpoint's configured port. Endpoint status retries once per second for up to ten seconds while
  a newly detected process binds UDP and REST, then refreshes independently every ten
  seconds.
- Process identity is checked before any endpoint probe. When the matching executable is
  absent, endpoint states are closed/disabled without attempting UDP, REST, or RCON
  connections.
- UDP is Open when the configured game UDP port is locally bound. REST is Open when its
  configured TCP endpoint accepts a connection. RCON is Disabled when its effective world
  setting is off, otherwise Closed or Open according to its configured TCP port.
- Automatic restart settings, next planned restart, and restart history live only on the
  separate Auto Restart page.
- The compact Players card reads the shared REST cache every three seconds, retains the
  last successful online roster, and links to the dedicated [Players page](./instance-players.md). It
  contains only vertically centered boot-icon Kick and prohibited-circle-icon Ban actions,
  each with a localized hover/focus tooltip, and has no Unban control. Existing player
  rows and action nodes are updated in place by stable user id; only joins and departures
  add or remove row nodes.

## Log and metrics (right column)

- Opening Overview displays the Log panel and a localized loading placeholder immediately;
  logs never wait for REST, metrics, status, or update checks.
- The placeholder is replaced by current output or a localized empty state. Output
  refreshes at least once per second, retains only the latest 300 lines, appends without
  rebuilding retained text, and preserves an active text selection.
- On Windows, a standard SteamCMD installation is launched through Palworld's console
  server binary with Unreal stdout logging, `-stdout`, `-FullStdOutLogOutput`, and
  `-FORCELOGFLUSH` enabled. The detached agent reads ConPTY output continuously, preserves
  raw chunks including ANSI/carriage returns/partial lines, flushes the file immediately
  and with Windows `FlushFileBuffers`, and redirects both PalServer output streams to a
  persistent per-instance raw log. This allows the supervisor to detach and a fresh
  supervisor to adopt the same process after GUI replacement. Native
  server lines are prefixed with `PalServer:` and appended to the instance Overview log.
  The Windows smoke diagnostic can query the agent's `job_status` response to enumerate
  the PalServer root and descendants and fails if any process is outside the Job Object.
  When UE4SS is installed in either supported layout, its `UE4SS.log` is tailed into the
  same Overview log with an `UE4SS:` prefix. Managed launches skip stale PalServer bytes;
  managed adoption resumes from its persisted cursor and replays at most the latest 300
  missed lines. Raw PalServer output is stored in `logs/palserver-yyyymmdd.log`, and the
  supervisor log is stored in `logs/overview-yyyymmdd.log`; both writers switch to the
  next day's file while a server remains running. Dated log files are retained for 30
  calendar days. Missing logs are awaited silently, and UE4SS output does not contribute
  to PalServer crash diagnostics.
- On native Linux, a standard SteamCMD installation launches
  `Pal/Binaries/Linux/PalServer-Linux-Shipping` directly and captures stdout/stderr
  through the supervisor-managed process log. UE4SS log tailing remains unavailable
  because UE4SS management is unsupported on native Linux.
- Auto Scroll keeps the newest line visible while On; when Off, appends preserve the
  user's manual scroll position.
- A Check update button immediately left of Filter starts a forced SteamCMD update check;
  the check runs while a managed server is active, writes its result to the Overview log,
  and is unavailable for external or uninstalled servers. A Filter button immediately
  left of Auto Scroll opens a popup with checked native HTML
  checkboxes for `Palsitter`, `PalServer`, `SteamCMD`, and `UE4SS`; there is no aggregate
  `All` option. `PalServer:`, `SteamCMD:`, and `UE4SS:` lines, including supervisor lines
  carrying an instance-name prefix, use their matching type and every other line uses
  `Palsitter`. Checkbox changes apply immediately for the current
  Overview visit. The browser retains the full latest-300-line model, but the log view
  renders only matching rows so filtered entries do not leave blank vertical space.
  Filtered rows are detached from the view and cached by stable row identity; when
  re-enabled, the same nodes are reused. New rows inherit the active filter before
  insertion, and reopening Overview resets all four types to visible.
- The console runs on Enter as well as its Run button. Focusing the field opens a
  filterable autocomplete containing every supported command: REST-backed announce,
  player moderation, info, players, metrics, save, and shutdown operations, plus
  Palsitter's backup and lifecycle commands. Player-context-only in-game commands,
  credentials, actor snapshots, and force-stop shortcuts are not exposed. Up/Down and
  Tab/Shift+Tab move the active suggestion; Enter fills an active suggestion without
  running it, Enter with no active suggestion runs the input and clears the field, and
  Escape closes the list. REST-backed command hints match their request shape and describe
  the corresponding Palworld server operation documented in the [official command guide](https://docs.palworldgame.com/settings-and-operation/commands/):
  `announce <message>`, `kick <user_id> [message]`, `ban <user_id> [message]`,
  `unban <user_id>`, and `shutdown <waittime> [message]`; save, info, players,
  metrics, and stop take no arguments. Save and shutdown append their localized
  requested message before waiting for the corresponding REST response. Every graceful
  shutdown sends Palworld's `Save` request before its `Shutdown` request because the
  official API documents those as separate operations and does not promise an implicit
  save during shutdown. If the save request fails, the shutdown request is not sent.
- Metrics start as placeholders and read the shared REST cache every three seconds: current and
  average FPS, uptime, day, PalServer process CPU, PalServer process-tree RSS memory, and
  game version. The Palbox metric shows the REST `basecampnum` count as
  `<current> / <world-settings BaseCampMaxNum>`. When a check reports an available update,
  `<b>(↑)</b>` follows the game
  version and its hover/focus tooltip displays the localized `update available: {current_version}
  → {new_version}` message. CPU and memory use the exact instance executable path and remain available
  for a detected external server before Palsitter attaches its watcher. Slow or failed REST
  calls leave the last value visible and marked stale.
- Metric values patch their existing text nodes; periodic refreshes do not rebuild the
  metric cards.
- For each detected PalServer process session, the shared REST cache requests `/info`
  once after REST opens and retains the last successful response after the server stops,
  so the cached Game Version remains visible while inactive. It requests `/players` and
  `/metrics` together every three seconds while that process is running and REST remains
  open. Overview, Home, Players, and the read-only console commands render those cached
  results and never issue their own requests to these endpoints.
- Leaving Overview stops its background renderers so they cannot modify another page or
  instance.
- Below 1100 px the two Overview columns stack; below 600 px maintenance controls and
  roster rows stack without horizontal page overflow.

**Tests:** `tests/test_gui_playwright.py` proves the persistent action scope and obsolete
scheduler operations are absent, verifies the Start/Save/Backup action row and disabled
Save state, Start update/validation, Stopping status, shutdown payload, status recovery,
readiness gating, KILL only after owned graceful Stop, external attach/Detach, the live
PalServer and UE4SS Overview logs, and responsive layout. Lifecycle tests fake SteamCMD,
PalServer, process state, REST, and network probes.
