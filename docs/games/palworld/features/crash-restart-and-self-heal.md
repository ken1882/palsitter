# Palworld Feature: Reliability and Restart Policies

Configured per instance in [Auto Restart](../components/instance-auto-restart.md).
The supervisor distinguishes unexpected exits from intentional Stop, KILL, Restart, and
Detach using an explicit requested-operation state set before process termination.

## Crash restart and self-heal

- `Restart on crash` defaults On. An unexpected owned PalServer exit is eligible for an
  automatic relaunch; intentional operations and externally attached servers are not.
- `Crash restart limit per hour` defaults to 5 and counts unexpected exits in a rolling
  one-hour window. The cap is checked before any restore or relaunch. Reaching it leaves
  the instance in Warning with no owned process; only an explicit manual Start clears the
  window.
- `Self-heal` defaults On but is disabled in the UI and has no effect while Restart on
  crash is Off. Its trigger frame defaults to 30 minutes and its trigger count defaults
  to 2 crashes. Eligible crashes are counted in that configurable rolling frame.
- When the configured count is reached, self-heal selects the newest managed backup whose
  modification time is strictly before the triggering frame's start, takes a safety
  backup of the current save, restores, and relaunches. The incident crash list resets
  after the attempt.
- Restore is allowed only after the safety backup succeeds. If no suitable old backup
  exists or the safety backup fails, Palsitter keeps the current world and performs the
  same bounded normal restart; reaching the crash cap still prevents that restart.
- Automatic restore and restart outcomes are recorded in a persisted, per-instance list
  of the newest 20 decisions, including skipped/failed safety backups and cap exhaustion.
- Unexpected process exits retain the raw exit code. Windows exception values are mapped
  to known NTSTATUS names, POSIX negative return codes are mapped to signals, and launch
  `OSError` details are retained when an automatic relaunch cannot start.
- The final five non-empty combined stdout/stderr lines are optional diagnostic context,
  not a claimed root cause. Palsitter does not configure crash dumps or system event logs.

## Process-memory restart

- `Process memory restart MiB` defaults to 0, which disables the policy. A positive value
  compares only the combined RSS of the owned PalServer process tree, not whole-machine
  memory or unrelated instances.
- The threshold must be exceeded in three consecutive supervisor samples. A sample below
  or equal to the threshold resets the consecutive count.
- After triggering, Palsitter broadcasts the configured countdown, including each
  remaining minute, invokes REST Save, gracefully restarts, and does not run SteamCMD.
- Legacy percentage-only profiles migrate to
  `ceil(total physical memory MiB × percentage / 100)` so their approximate threshold is
  preserved while changing the measurement to PalServer RSS. Migration is atomic.

## Planned restart

- Mode `Off` disables the policy. `Interval` schedules positive whole hours from the last
  owned launch/restart. `Daily` schedules the next host-local `HH:MM`; crossing midnight
  selects the following day.
- The default countdown is ten minutes. Palsitter broadcasts the countdown, including
  each remaining minute, invokes REST Save, gracefully restarts, and never updates files.
- A missed daily time while Palsitter was inactive schedules the next occurrence instead
  of immediately restarting. Only one planned restart may be in flight.
- Externally attached servers never run planned or memory restarts; the reliability card
  identifies the policy as skipped for external ownership.
- Restart History retains the newest 20 automatic restart decisions across GUI restarts,
  each with timestamp, trigger, detected cause, outcome, and details. Manual restarts and
  routine schedule-created events are excluded.

**Tests:** `tests/test_server_manager.py` fakes process trees, RSS, clocks, REST, backups,
and exits to cover sustained thresholds, reset samples, planned schedules/countdowns,
intentional operations, external attachment, rolling crash caps, manual clearing,
configurable self-heal frames/counts, strict rollback boundaries, and safety failures.
Config tests cover defaults and both migrations;
`tests/test_gui_playwright.py` covers field dependencies, validation, next-run/event
rendering, and external skip messaging.
