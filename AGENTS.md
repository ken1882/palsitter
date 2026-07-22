# AGENTS.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- Use shared abstractions when behavior is reused or should stay consistent across pages.
  Put shared widget behavior in `module/webui`; keep game pages focused on data and labels.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.
- When implementing new code, check if existing code or workflow covers it, point out and don't do it twice.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.


## Game Notes

### Shared / Multi-game
- Generic storage, navigation, and process coordination must not assume Palworld fields or
  capabilities. Resolve behavior through the selected game adapter.
- Every user-triggered and supervisor-backed instance operation that emits operator-visible
  output must route it through the instance `ProcessManager` log and stream it to the Overview
  log; do not leave output only on stdout or in an operation result.

### Palworld
- Official documentation: https://docs.palworldgame.com

### Satisfactory
- Satisfactory is currently a UI/storage placeholder only. Do not add guessed ports,
  paths, Steam app ids, settings, or runtime behavior without an explicit feature spec.

## Testing Requirements

### Shared / Multi-game
- Every GUI feature must have a corresponding Playwright test in `tests/test_gui_playwright.py`.
- Playwright tests must click the real UI path for the feature, not only assert that text exists after page load.
- For layout changes, test geometry and user state: no page-level horizontal overflow, popovers stay
  in the viewport, table content scrolls inside its shell, and navigation does not move the outer page.
- PyWebIO server errors must fail Playwright tests. The GUI test harness must capture stdout/stderr and assert there is no `Unhandled error in pywebio app` and no `Traceback`.
- External dependencies must be mocked or faked.
- Config tests must use `PALSITTER_CONFIG_DIR` with a temporary directory and must not read or mutate the real local profiles.
- Multi-game tests must cover empty startup, case-insensitive global names, same-game cloning,
  adapter dispatch after multiprocessing spawn, and unsupported-game bulk-action skipping.

### Palworld
- Tests must never require real SteamCMD, a real Palworld server, or a live Palworld REST API.
- Server lifecycle tests must mock SteamCMD, `PalServer.exe`, process state, virtual memory, and REST calls.
- REST tests must assert method, URL, Basic Auth, request bodies, successful parsing, and HTTP failure handling.
- Backup tests must use temporary directories and assert included files, excluded nested `backup` folders, retention deletion, and failure/retry behavior when relevant.

### Satisfactory
- Playwright must verify its only page is the unsupported Overview, exposes no runtime
  actions/settings, reports `Unsupported`, and retains confirmed reference-only deletion.
- Tests must prove placeholder creation and navigation do not invoke Palworld defaults,
  process managers, REST clients, SteamCMD, port allocation, or backup services.

## Commands
- Run all tests (serial unit/timing-sensitive phases, then two parallel Playwright workers): `python test.py`
- Run all tests serially: `python -m pytest -q`
- Run only parallel-safe Playwright GUI tests (two worker processes): `python -m pytest -q -n 2 --dist=load -m "playwright and not serial_playwright"`
- Debug a timing/order-sensitive failure serially: `python -m pytest -q -n 0 <test-path>`
- Compile check: `python -m compileall -q .`

### Start And Stop The Local GUI
- Start in the foreground:
  ```powershell
  python gui.py --host 127.0.0.1 --port 22368
  ```
- Open `http://127.0.0.1:22368/`.
- Stop a foreground server with `Ctrl+C`.
- Start in the background and retain its process ID:
  ```powershell
  $process = Start-Process -FilePath python `
      -ArgumentList @('gui.py', '--host', '127.0.0.1', '--port', '22368') `
      -WorkingDirectory $PWD -WindowStyle Hidden -PassThru
  $process.Id
  ```
- Stop that background server:
  ```powershell
  Stop-Process -Id $process.Id
  ```
- If the saved process ID is unavailable, inspect the process listening on port `22368`:
  ```powershell
  $listener = Get-NetTCPConnection -State Listen -LocalPort 22368
  $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
  $owner | Select-Object ProcessId, CommandLine
  ```
- Only after confirming that `CommandLine` is Palsitter's `gui.py`, stop it:
  ```powershell
  Stop-Process -Id $listener.OwningProcess
  ```

## Specs
Detailed, testable specs for the GUI live under [`docs/`](docs/README.md), split from
this file for readability — every component must still have a corresponding Playwright
test that verifies its behavior, regardless of which doc describes it:

- [`docs/shared/`](docs/shared/README.md) — application-wide conventions and multi-game
  components/features: Top Bar, Left Sidebar, Add Instance, Home, Updater, Utils,
  Game Modules, Language/i18n, and File Browser.
- [`docs/games/palworld/`](docs/games/palworld/README.md) — Palworld-only Overview,
  Server Settings, World Settings, ports, lifecycle/self-heal, backups, players, and
  world-settings behavior.
- [`docs/games/satisfactory/`](docs/games/satisfactory/README.md) — the Satisfactory
  placeholder Overview and its explicit lack of runtime capabilities.

Documentation ownership rules:
- Put shell behavior or deliberately reusable multi-game behavior under `docs/shared`.
- Put game-specific UI, configuration, paths, ports, processes, APIs, backups, and
  lifecycle behavior under `docs/games/<game>`.
- A new game starts with its own README and only the component/feature specs it actually
  implements. Never treat Palworld behavior as an implicit default for another game.
- Keep component bullets independently testable and update the corresponding real-path
  Playwright test whenever a GUI specification changes.

Frontend maintainability rules:
- Production Python must not embed HTML, JavaScript, SVG, style blocks, inline handlers,
  or fixed `.style()` strings. Use PyWebIO outputs for ordinary controls.
- Register CSS, JavaScript, and Mustache templates in `assets/gui/manifest.json`. Render
  custom DOM through `put_asset_widget()` and SVG through `put_asset_icon()`.
- `client_call()` and `client_query()` are the only production Python/browser bridge.
  Browser APIs live under `window.Palsitter`; do not add private window globals.
- Stateful widgets implement repeatable `mount`, `update`, and `destroy` operations and
  release timers, observers, and listeners during page cleanup.
- Any page callback or worker that can outlive navigation must capture `page_context()`
  before blocking or asynchronous work and guard every later UI mutation (`use_scope`,
  `clear`, PyWebIO output, `toast`, or `client_call`) with `run_if_current()`. The
  operation may finish and write intentional persistent instance logs, but stale UI
  results must never append to the replacement page.
- Keep shared assets under shared ownership and game-specific assets under their game.
  Preserve manifest load order and never use inline-style marker hacks.
- Match existing shared UI styling before adding new styles. Reuse standard buttons and table tokens
  from established pages such as Overview and Backups.
- Prefer responsive shared behavior over page-specific constants: popovers must stay inside the viewport,
  tables must scroll internally when needed, and pagination must not cause page jumps.
- Shared tables should derive initial column widths from content and let the final column use spare space.
  Do not add per-page width constants unless a spec explicitly requires them.
- Architecture tests enforce asset registration, passive autoescaped templates, and the
  Python/frontend boundary. Playwright must fail on page errors and failed GUI assets.
- GUI tests for delayed operations must click the real action, navigate away before the
  result returns, and assert that no result, error, popup, toast, or status rows appear
  on the replacement page.

