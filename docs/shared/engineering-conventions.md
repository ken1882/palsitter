# Shared Implementation Conventions

- Manager/service classes (`PalServerManager`, `BackupService`, `ProcessManager`) accept
  their external dependencies as constructor parameters with real defaults
  (`popen_factory`, `run_command`, `virtual_memory`, `sleep`, `now`, `rest_client`,
  `stop_requested`, `backup_service`, ...), never call them as bare module-level functions
  inline. This is what makes them fakeable in unit tests without mocking libraries — match
  this style for any new time-based, process, or network dependency.
- A page or panel that performs a REST call or other remote I/O when it first loads must
  do so from a background thread registered with `register_thread` (the pattern already
  used for the Log, Metrics, and Players panels), never inline in the synchronous render
  path. A slow or hanging call must never delay another panel's initial paint — most
  notably the Log panel's immediate loading placeholder.
- Deleting a profile removes only its `<name>.json` reference; it must never delete the
  instance's working directory, save games, or backups on disk.
- New game-specific profile fields get sensible defaults and are merged by that game's
  typed configuration loader.
- Generic storage uses `InstanceRecord(name, game, game_config)`. Game modules convert the
  nested payload into their own typed configuration; generic code must not inspect
  game-specific fields.
- Profile names are globally unique by `casefold()`, independent of filesystem behavior.
  Legacy flat Palworld profiles are migrated with a same-directory temporary file and
  `os.replace`, never `os.rename`.
- Multiprocessing targets receive only primitive identifiers, queues, and events. A child
  reloads the instance and resolves its adapter after startup so Windows `spawn` and POSIX
  start methods follow the same path.
- Application-wide PyWebIO framing, navigation, forms, file browsing, and Home/Updater/Utils
  pages live under `module.webui`; they must not import a specific game's services.
- A game adapter identifies its lazily imported Web UI module with `webui_module`. That
  module returns a validated `GameWebUI` containing unique page ids, an `overview` page,
  render callables, and an optional Add Instance extension.
- Game page renderers receive an instance name and render only into the content scope.
  Shared navigation owns the frame, menu, status watcher, and page cleanup lifecycle.
- Background page work registers stop events or cleanup callbacks with
  `module.webui.session`; adding a page must not add another page-specific global stop list.
- Dirty forms register their save callback with the shared form guard. Shared navigation
  must never branch on game-specific form kinds.

## Frontend asset ownership

- Production Python composes ordinary controls with PyWebIO outputs. It must not embed
  HTML, JavaScript, SVG, style blocks, inline event handlers, or fixed `.style()` strings.
- Custom semantic DOM lives in autoescaped Mustache templates registered in
  `assets/gui/manifest.json` and is rendered with `put_asset_widget()`. Icons are SVG
  assets rendered with `put_asset_icon()`.
- Browser behavior lives in registered JavaScript under `window.Palsitter`. Python may
  cross the browser boundary only through `client_call()` and `client_query()`.
- Stateful widgets expose `mount`, `update`, and `destroy` operations. Mounting must be
  repeatable; timers, observers, and listeners must be released by page cleanup or an
  abortable `destroy` operation.
- CSS is registered once in manifest order. Shared shell/component rules stay in shared
  files; game-owned selectors stay in game files. Do not use inline-style marker hacks.
- Every CSS, JavaScript, and HTML asset must be registered exactly once. Templates remain
  passive and autoescaped: no scripts, style blocks, inline styles/events, or raw Mustache
  interpolation except PyWebIO's output placeholder.
- Playwright exercises the real UI path and treats page errors or failed `/static/gui/`
  requests as test failures. Repeated navigation tests cover identity, focus, selection,
  scroll position, timers, observers, and event-listener cleanup where relevant.
