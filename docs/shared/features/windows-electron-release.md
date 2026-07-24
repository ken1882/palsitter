# Windows Electron Release

The Windows release is a portable extracted directory. `Palsitter.exe` loads
application-owned Electron JavaScript from `resources/app`, Python backend source from
`resources/backend`, and the standalone Python runtime from `resources/python`.

The application is intentionally packaged with `asar: false`. Editing Palsitter-owned
JavaScript, Python, or GUI assets takes effect on the next launch without rebuilding.

The release also contains the repository's full `.git` history under
`resources/backend/.git` and a portable Git runtime under `resources/git`. This keeps the
Updater history, fetch, and pull operations functional without requiring Git to be
installed separately.

Packaged runtime data is stored beside `Palsitter.exe` in the portable release's `data`
directory instead of `%APPDATA%`: configuration is under `data/config`, instance state
under `data/profile`, and logs under `data/logs`. The extracted release directory must be
writable by the current user.

Tray Exit requests the shared Home → Utils shutdown workflow. Its dialog offers `Cancel`,
`GUI only`, and `Stop all`. `GUI only` stops the PyWebIO server and quits Electron while
leaving active agents and game servers running. `Stop all` makes the web UI immediately
show an undismissable stopping dialog, saves active instances, requests graceful shutdown
through every active supervisor and agent, waits for game servers and agents to exit,
stops the PyWebIO server, and then quits Electron. The dialog enables `Force Shutdown`
after five seconds; that explicit action kills managed processes instead of waiting for
graceful shutdown. If any component does not stop within 60 seconds, the dialog remains
available so the operator can force shutdown.

The desktop backend control endpoint is loopback-only and authenticated with the
per-launch `PALSITTER_DESKTOP_TOKEN`:

```text
POST /desktop/shutdown
X-Palsitter-Token: <token>
```

The GUI-only path uses the same token:

```text
POST /desktop/gui-only
X-Palsitter-Token: <token>
```

The executable starts the shared workflow through the authenticated control endpoint.
The explicit force path uses the same token and shared workflow:

```text
POST /desktop/force-shutdown
X-Palsitter-Token: <token>
```

When the updater restart is confirmed, the backend exits with the shared restart exit code.
Electron recognizes that intentional exit, starts a fresh backend using the updated source,
and reloads the BrowserWindow. Only that intentional exit code triggers this replacement
path.

At startup, if the preferred web or control port is already in use, the executable asks
whether to stop the process listening on that port. If stopping it does not free the port,
or the operator declines, it asks whether to choose another available loopback port. Declining
that second prompt exits without starting Palsitter. Startup prompt text follows the system
locale for English, Traditional Chinese, and Japanese.

The portable archive is built by the Windows GitHub Actions workflow and includes a
SHA-256 checksum. It does not include an installer or automatic updater.
