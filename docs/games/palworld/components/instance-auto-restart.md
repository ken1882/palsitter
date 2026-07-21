# Palworld Component: Auto Restart

Reached from the Palworld instance menu immediately after Server Settings. This page is
Palworld-specific and is not exposed by unsupported game placeholders.

- The panel title is `Auto Restart`; it does not repeat the selected instance name.
- The embedded settings panel contains Crash recovery, Process-memory restart, and
  Planned restart groups. These fields do not also appear in Server Settings.
- Crash recovery includes `Self-heal trigger frame (minutes)` (default 30) and
  `Self-heal trigger crash times` (default 2). Both accept positive whole numbers and
  control the rolling incident window and threshold used by the supervisor.
- The page uses the standard square toggles, validation, floating Reset/Save bar, and
  unsaved-navigation guard. Self-heal is disabled while Restart on crash is Off.
- Restart History is a separate panel below the form. It displays the newest 20 persisted
  automatic-restart decisions in reverse chronological order with timestamp, trigger,
  detected cause, outcome, and expandable details. Manual restarts and routine schedule
  creation are excluded.
- History is stored atomically at `profile/<name>/logs/restart-history.json` and survives
  GUI restarts. Missing history shows an empty state; unreadable history shows a warning
  without affecting lifecycle behavior.
- Windows native exception codes and POSIX terminating signals are shown by symbolic name
  and numeric code. Automatic relaunch failures retain their OS exception details.
- The final five non-empty PalServer output lines may be shown as escaped diagnostic
  context. The page explains that an OS termination type is not a definitive root cause
  and that faulting modules, functions, mods, or addresses require a dump or application
  log.
- The next planned restart and externally-managed skip warning live on this page and
  update while it remains open. Leaving the page stops its renderer.

**Tests:** `tests/test_gui_playwright.py` opens the real menu path, edits and validates
settings, exercises toggle dependencies, renders persisted crash classifications and
escaped output, and verifies Overview no longer owns the history card.
