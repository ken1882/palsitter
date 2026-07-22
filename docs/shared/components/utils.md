# Shared Component: Utils

- The Utils view is a two-column layout with utility actions on the left and a live
  developer Log on the right.
- The action column displays `Raise exception`, `Force restart`, `Shutdown Palsitter`,
  `Run all instances`, `Stop all instances`, and `Kill all instances` in that order.
- `Raise exception` captures a diagnostic stack trace in the developer Log without
  breaking the GUI session.
- `Force restart` first opens a detailed confirmation popup. The popup explains that
  every active managed server is saved before its supervisor is handed off, and that
  active PalServer processes remain online while the GUI child is replaced. Cancel
  closes the popup; Continue starts the persisted restart workflow.
- Force Restart performs an all-or-nothing save preflight. If any managed save fails,
  no managed server is stopped or killed and the GUI is not restarted. A dismissable
  failure overlay reports the result.
- After saves, each managed supervisor hands off its detached Windows PalServer agent.
  Palsitter verifies the agent and PalServer PID, creation time, session ID, and
  configured executable before detaching external watchers. If handoff or verification
  fails, the current GUI remains alive, external watchers remain attached, and every
  successfully handed-off agent is reconnected where possible; no replacement GUI is
  spawned.
- During a successful workflow, the full-screen progress overlay cannot be dismissed,
  cancelled, or bypassed. It shows the persisted phase and per-instance results while
  the GUI supervisor saves, detaches managed supervisors without stopping PalServer,
  restarts the GUI child, and adopts managed instances again. External instances are
  only detached and reattached; they are never stopped or killed.
- Managed restore connects to an existing agent and requests only `ping`/`status`. It
  never creates a missing restore agent and never sends `start`; an existing idle agent
  remains idle until the user explicitly starts it after reconnect.
- `Shutdown Palsitter` opens a confirmation popup, then uses a shared full-screen
  stopping overlay. It immediately exposes the stopping state, saves active instances,
  requests graceful shutdown, and closes the GUI after every lifecycle instance stops.
  The overlay enables `Force Shutdown (5)` after five seconds, counts down once per
  second, removes the counter at zero, and force-kills managed instances only when the
  operator clicks it. The same workflow is used by the Windows Electron tray exit.
- Restart state is stored atomically under the configured data directory. Refreshing or
  reconnecting reconstructs the active overlay. The initiating browser connection is
  expected to drop while the GUI child is replaced. A completion or failure overlay is
  dismissable and remains after refresh until dismissed.
- A Force Restart request is idempotent while restart state exists: another click
  renders the existing overlay and does not create another worker. Multi-tab live
  synchronization is explicitly unsupported; tabs synchronize only after refresh or
  reconnect.
- Run, Stop, and Kill open a modal listing every configured instance. Each instance has
  an HTML checkbox, all checkboxes are selected by default, the list scrolls when it is
  too tall, and only selected instances are acted on after confirmation.
- Run starts inactive supported instances, Stop terminates running supported instances
  normally, and Kill immediately terminates running supported instances. Unsupported
  placeholders are skipped and named in the result.
- The developer Log refreshes every second, retains the latest 500 entries, and has an
  Auto Scroll ON/OFF toggle.
- `Run Code` is hidden by default and appears only when localStorage key
  `DANGER_ENABLE_EVAL` exactly equals
  `DO_NOT_PASTE_ANY_CODE_HERE_UNLESS_YOU_KNOW_WHAT_YOU_ARE_DOING`.
- Run Code remembers the last submitted code in localStorage key `_last_exec` and
  reports execution errors in the developer Log.
- The utility action buttons are rounded (`4px`) blue primary buttons (`#375a7f`); the
  developer Log uses square-bordered dark panels and monospace log styling like the rest
  of the GUI.
- The developer Log's `Auto Scroll ON`/`OFF` control is a square purple/gray toggle
  (Nechouli's on/off toggle style), not a rounded button.
- Utils has no dedicated action icons; its actions are text buttons.
