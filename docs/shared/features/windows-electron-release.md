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

Tray Exit is a full graceful shutdown. It saves active instances, requests graceful
shutdown through every active supervisor and agent, waits for game servers and agents to
exit, stops the PyWebIO server, and then quits Electron. If any component does not stop
within 60 seconds, Palsitter stays open and reports the affected instance. Exit never
calls the force-kill path automatically.

The desktop backend control endpoint is loopback-only and authenticated with the
per-launch `PALSITTER_DESKTOP_TOKEN`:

```text
POST /desktop/shutdown
X-Palsitter-Token: <token>
```

The portable archive is built by the Windows GitHub Actions workflow and includes a
SHA-256 checksum. It does not include an installer or automatic updater.
