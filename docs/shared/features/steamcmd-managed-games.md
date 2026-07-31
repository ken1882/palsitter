# Shared Feature: SteamCMD-Managed Games Without an API

This specification defines the minimum reusable integration for a dedicated server that
is installed by SteamCMD and exposes no supported management API. It is a porting
contract, not a claim that SteamCMD standardizes server paths, launch arguments, save
data, ports, or shutdown behavior.

New games must use shared services or services owned by their own adapter. They must
never import Palworld configuration, process managers, REST clients, backup services,
paths, ports, or UI pages as defaults.

## Required adapter contract

Before implementation, the game integration must provide authoritative values for:

- Steam application id, branch selection, and login policy.
- The per-platform installation directory, executable path, working directory, base
  launch argument vector, and any required environment.
- Exact process-identity validation and the process tree Palsitter is permitted to own.
- The server output source: captured stdout/stderr, an explicitly named log file, or no
  game output.
- The profile-owned paths that reference deletion and separately confirmed data wiping
  may affect.
- The launch arguments that operators may configure and the adapter-controlled arguments
  with which they must not conflict.

The app id, installation directory, executable, working directory, and base command are
adapter-owned values. They are not free-form operator settings. Launch commands are
argument vectors and must never be stored or executed as shell command strings.

An adapter may expose an ordered list of additional launch arguments. It must validate
each argument, preserve order and casing, and reject adapter-controlled arguments
duplicated through that list.

## Baseline instance navigation

A runnable no-API SteamCMD integration exposes these pages in this order:

1. `Overview`
2. `Runtime Settings`
3. `Reliability`
4. `Audit`
5. `Manage`

Game-owned pages may be added only after their data source and operations have an explicit
feature specification. The baseline does not include Players, Saves & Backups, Map, Mods,
World Settings, firewall/ports, a remote console, or game-specific tools.

### Overview

Overview provides the process-level functionality that does not require a game API:

- Install, update, validate, and forced update-check operations dispatch through the
  selected game adapter and report `OperationProgress` and `UpdateInfo`.
- Start installs or updates according to `Update on start`, then launches the server.
  SteamCMD must finish successfully before the managed process is launched.
- SteamCMD install, update, and validation never run while the configured server process
  is active.
- Stop sends the adapter's configured OS termination request, waits for its bounded grace
  period, and force-kills the validated managed process tree only if it remains alive.
  Kill skips the grace period.
- State distinguishes installing/updating, inactive, warning, stopping/killing, and
  running under validated Palsitter ownership.
- CPU and RSS memory are calculated from only the validated instance process tree.
- Operator-visible output from install, update, lifecycle, adoption, and failure paths is
  written through the instance `ProcessManager` log and streamed to Overview.
- Captured stdout/stderr is appended when the server exposes it. File tailing is optional
  and may be enabled only for a path declared by the adapter. When neither is available,
  the log still shows Palsitter and SteamCMD operations.
- The log retains the established shared Auto Scroll, clear-view, bounded-history, and
  page-cleanup behavior. A game may add log categories only when it owns their source.

No Save, Backup, player roster, endpoint, game metric, announcement, or remote-console
control is inferred from a running process.

### Runtime Settings

The common page exposes only:

- `Update on start`, default On.
- `Validate server files on start`, with its default chosen explicitly by the game
  integration.
- Ordered additional launch arguments approved by the adapter.

Read-only installation details may be displayed for diagnosis. Game settings, ports,
credentials, save paths, and arbitrary environment or command editing require a
game-owned specification and UI.

### Reliability

The common process-only policies are:

- Restart on unexpected crash, default On.
- Process-tree RSS restart threshold, default disabled.
- Planned interval/daily restart, default Off.
- Persisted restart history containing trigger, detected process outcome, action,
  timestamp, and failure details.

Every automatic restart uses the same bounded OS terminate-then-kill contract as manual
Stop. The page must state that a no-API integration cannot promise an in-game save,
announcement, countdown broadcast, or rollback before restarting.

Automatic crash, RSS, and planned restarts honor `Update on start`. SteamCMD begins only
after the previous validated process tree is fully stopped. A failed update leaves the
instance in Warning and does not launch potentially partial files.

Self-heal, restore, backup selection, player-idle decisions, and broadcast countdowns are
not part of this baseline.

### Audit

The shared Audit page is a read-only projection of:

- Common application audit events currently emitted by Palsitter:
  `web_login_success` and `web_login_failure`.
- Audited events recorded for the selected instance through its adapter.

It must not include events belonging to another instance. The shared page does not invent
an event taxonomy for a game, and it does not promise a web-logout event that Palsitter
does not currently emit. Game adapters may define additional event types only for events
they can observe authoritatively.

Search, type/time filters, paging, responsive table behavior, and global-auth-event
projection use the existing shared audit and pagination patterns.

### Manage

Manage owns generic instance administration:

- Rename is allowed only after lifecycle and operation checks prove the instance is idle.
  It preserves the instance profile, logs, and adapter-declared profile-owned data.
- Delete reference uses exact-name confirmation and removes only the profile reference.
- `Wipe data` is separately confirmed and may remove only adapter-declared,
  profile-owned paths.

Detecting or adopting an independently started process does not make its executable,
working directory, save data, or other external paths owned by Palsitter and never grants
permission to delete them.

Firewall repair is not a common Manage action. It becomes available only after a game
specifies its required ports, protocols, executable rules, and verification behavior.

## Process identity, adoption, and application lifecycle

An adapter detects an existing server only by an exact normalized absolute executable
path. A process-name or port match alone is insufficient.

Before automatic adoption, the adapter validates and persists the process id, creation
time, executable path, and process-tree ownership boundary. Successful adoption:

- Marks the server managed by Palsitter.
- Starts supervision without SteamCMD or a second launch.
- Enables normal Stop, Kill, reliability, shutdown, and handoff behavior.
- Writes adoption output to Overview and an instance audit event when the adapter audits
  lifecycle events.

Failed or ambiguous validation leaves the instance in Warning, performs no update or
launch, and enables no destructive lifecycle action.

GUI-only exit leaves managed and adopted servers running. Force Restart hands off and
reconnects them without a save preflight. A platform adapter that cannot preserve and
revalidate ownership across GUI replacement must report that limitation and make Force
Restart unavailable while its instance is active.

`Stop all` explicitly warns that no game save can be requested, then performs the normal
bounded terminate-then-kill operation. Explicit Force Shutdown remains immediate and
targets only validated managed process trees.

## Shared module boundaries

The reusable areas and their current limits are:

| Area | Shared responsibility | Game responsibility |
| --- | --- | --- |
| Profiles | Game-neutral instance record, global names, templates, cloning, atomic storage, logs | Typed `game_config`, defaults, derived paths, profile-owned data |
| SteamCMD | Download, archive validation, safe extraction, host platform selection | App id, branch/login, install layout, executable verification, update cache |
| Process I/O | Cross-platform PTY abstraction and output decoding | Launch command, output prefixes/categories, optional declared file tail |
| Coordination | `ProcessManager` queues, progress, logs, operation exclusion, Home/Utils dispatch | Adapter lifecycle implementation, identity, adoption, force-stop, supervision |
| Process utilities | Exact process resource accounting and bounded tree termination primitives | Which executable/tree belongs to the instance and which signal starts termination |
| Web UI | Navigation manifest, page cleanup, forms, tables, file browser, i18n, Home and Utils | Page labels/data, approved fields, game-owned operations |
| Audit | Common web-auth store and shared table/filter patterns | Selected-instance event store, types, parsing, and messages |
| Archives | Safe archive creation/extraction and retention are candidates for extraction | Authoritative save roots, quiescing, exclusions, restore ownership and restart rules |

The current `GameAdapter` and Palworld services still contain Palworld-specific dispatch.
A new integration must not call those Palworld branches. Generalization requires a
deliberate shared extraction or a separate game-owned adapter implementation.

## Palworld component portability

| Palworld component | Common result | Excluded or adapter-owned behavior |
| --- | --- | --- |
| Overview | Lifecycle, SteamCMD operations, ownership, process CPU/RSS, progress and logs form the common Overview | REST Save/shutdown, endpoints, players, Palbox/game metrics, REST console and UE4SS log handling |
| Players | Row/table/cache presentation is reusable UI infrastructure | Roster data, identity fields, Kick/Ban/Unban, ban files, coordinates and play-time semantics |
| Server Settings | Form validation, dirty state, help, category navigation and ordered argument widgets are reusable | Palworld app/path rules, launch switches, query port, Engine.ini and REST-log settings |
| Auto Restart | Crash/RSS/planned policies and restart-history presentation form common Reliability | REST save/announce, player-idle behavior, self-heal and backup rollback |
| World Settings | Schema-driven form presentation is reusable | Palworld fields, defaults, INI/SAV codecs, password/API settings and recovery |
| Mods | File tables, toggles, confirmation and localhost folder shortcuts are reusable patterns | UE4SS release/layout rules, Lua/Pak discovery, enable/disable and removal semantics |
| Saves & Backups | Archive, retention, confirmation and table patterns are extraction candidates | Save roots, flush, world discovery/switching, built-in snapshots, safety restore and restart ownership |
| Map | Generic browser widget lifecycle and responsive behavior only | Map assets, coordinate transforms, player/Palbox data and markers |
| Audit | Common-auth projection, table, filters and paging become shared | Palworld log parsing, player/command events and Palworld event taxonomy |
| Tools | Rename and Delete/Wipe move to common Manage; firewall UI is conditionally reusable | Palworld player migration, save codecs, game paths and guessed firewall requirements |

Reusable presentation does not advertise a capability. A page appears only when the
baseline requires it or the selected adapter has an explicit feature specification.

## Required porting decisions

Before adding a new game, confirm rather than infer:

- Steam app id, public/beta branch behavior, login requirements, and validation policy.
- Supported operating systems, executable paths, working directories, environment, and
  required launch arguments.
- Process identity, adoption, termination signal, grace period, force-stop boundary, and
  whether ownership can survive GUI replacement.
- Whether raw output comes from stdout/stderr or a declared file, including encoding and
  log retention expectations.
- Operator-approved extra arguments and conflicts with adapter-controlled arguments.
- Which paths are owned by the profile and what reference deletion and Wipe data mean.
- Whether the game has authoritative ports, settings, save roots, backups, an API,
  players, mods, or other pages. Each addition needs its own game specification.

SteamCMD usage alone is never evidence for a port, setting, save path, server API,
graceful command, or file ownership rule.

## Verification requirements for a future port

- Fake SteamCMD, server processes, clocks, process identity, resource usage, and output.
  Tests must never require a real game installation.
- Assert SteamCMD arguments, app/branch/login, install directory, update parsing,
  cancellation, validation, and executable verification.
- Cover exact-path identity, persisted pid/creation-time validation, automatic adoption,
  ambiguous identity failure, and duplicate-launch prevention.
- Prove install/update/validation cannot run while the process is active and that an
  automatic restart waits for complete termination before honoring update-on-start.
- Verify manual and automatic terminate-to-kill timing, process-tree targeting, crash
  defaults, disabled RSS/planned defaults, and update failure behavior.
- Cover GUI handoff/reconnection, unavailable Force Restart when ownership cannot be
  preserved, GUI-only exit, unsaved `Stop all`, and bounded Force Shutdown.
- Prove every operator-visible operation writes to the instance `ProcessManager` log.
- Audit tests merge common authentication events with only the selected instance's
  audited events.
- Playwright clicks the real path through all five baseline pages and checks navigation,
  controls, async cleanup, operation state, responsive layout, and absence of unsupported
  pages/actions.
