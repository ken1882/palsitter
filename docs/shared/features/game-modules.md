# Shared Feature: Game Modules

Profiles are stored as a game-neutral `name`, stable `game` id, and nested `game_config`.
The registry supplies each game's defaults, cloning rules, typed configuration, optional
capabilities, typed status summary, backup integration, and ordered instance-page
manifest.

- `OperationProgress` carries operation kind, phase, optional percentage, message, and
  optional error.
- `UpdateInfo` carries installed and available build ids, check time, and status.
- `InstanceStatusSummary` carries the server name and optional player counts, FPS,
  uptime/day, process CPU/RSS memory, game version, backup times, and endpoint states.
- Lifecycle, update, backup, players, world-settings, and save-import capabilities are
  optional. Shared Home, Utils, and Add Instance code dispatch only through advertised
  capabilities and render absent fields/actions as unsupported rather than importing a
  game's services.
- An adapter that supports installation owns `is_installed`, cached `check_update`, and
  `install_or_update` operations and reports progress through `OperationProgress`.

Palworld is the fully supported module. It owns its server manager, REST/status clients,
backup rules, paths, Steam application id, world-settings codecs, and seven instance
pages. Shared PTY dispatch and SteamCMD archive downloading remain game-neutral process
utilities.

Satisfactory is intentionally a selectable placeholder. Its template is an empty object,
its menu contains only Overview, and it has no runtime, ports, settings, or backup service.
Bulk lifecycle actions skip it explicitly. The placeholder can be deleted through the same
confirmed reference-only deletion flow as supported instances. Its status summary is
`Unsupported` and must not instantiate or probe a Palworld service.

A fresh installation creates no instance. Legacy flat profiles are interpreted as Palworld
and atomically converted to the nested schema. Names are unique case-insensitively across
all games, while Palworld port allocation examines only Palworld records.

The process manager is the thread-safe source of lifecycle ownership, current operation
progress, update information, and the latest 20 in-session lifecycle events. A supervised
child process launches and monitors only the selected adapter's server; installation and
updates run before the supervisor starts. Managed Palworld launches persist process
identity and file-backed output metadata so a GUI replacement can detach and later adopt
the same server without launching a duplicate.

On Windows, one detached agent owns each managed Palworld server's ConPTY, persistent raw
output log, and kill-on-job-close Job Object containing its descendants. The agent never
starts PalServer merely because Palsitter reconnects: an explicit user Start launches or
connects to the agent and sends `start`. Stop/KILL send commands to the agent, while
Palsitter continues to own backups, SteamCMD, memory restart, planned restart, crash
self-heal, and Overview tailing. The stable version-1 named pipe is restricted to the
owning user and validated against the WTS session; its UUID `session_id` is an application
session identifier stored separately from that WTS session ID. A Windows named mutex
serializes launches and enforces one live agent per instance before `agent-state.json` is
published.

On native Linux, managed Palworld servers are supervised directly by the adapter with
`Popen` and `psutil`; the detached agent, ConPTY handoff, and Job Object ownership rules
do not apply.

Force Restart is coordinated by the shared web UI but dispatches save and lifecycle work
through the selected adapter and process manager. It atomically records an operation and
its active-instance manifest, saves every active managed server in parallel, and aborts
without detaching anything if a save fails. After successful saves, managed supervisors
are handed off without stopping PalServer, external ownership is detached, and the GUI
child is replaced. The new child validates persisted agent/server identity, adopts managed
servers with `update=False` and status-only restore, and resumes the raw log from its
persisted cursor with at most a 300-line replay. It does not launch a second server; an
idle existing agent stays idle. The browser connection may
drop during replacement; refresh or reconnect reconstructs the persisted progress or
terminal overlay. Live multi-tab synchronization is intentionally unsupported.

Each adapter also names a lazily imported Web UI module. Its `GameWebUI` manifest owns
the ordered instance pages and their renderers; shared navigation does not hardcode page
ids for Palworld or any other game. A game may provide an Add Instance extension for
game-specific fields and finalization, while the shared modal continues to own game/name
selection and same-game template/clone behavior.
