# Shared Component: Settings

The Home secondary menu contains `Home`, `Updater`, `Settings`, and `Utils`.

- Settings uses the shared section layout in this order: `Network` and
  `HTTP authentication`. The page title, validation messages, and one shared
  Save/Reset bar remain outside those sibling cards.
- Save and Reset lock immediately in the browser after either action is clicked. A
  validation failure or confirmation prompt unlocks them; successful Save and Reset
  clear the dirty state.

## Network access

- Settings persists the web UI bind address in `config/webui/settings.json`.
- Settings includes an off-by-default `Automatic update` toggle. When enabled, Palsitter
  checks for application updates every six hours and shows a persistent notification when
  an update is available; clicking it opens Updater.
- The address list includes localhost (`127.0.0.1`), all interfaces (`0.0.0.0`),
  and detected local IPv4 interface addresses. Localhost is the default.
- Selecting a specific local IPv4 address listens on that address and localhost;
  selecting localhost or all interfaces uses its normal single listener.
- Selecting a non-localhost address while authentication is disabled requires an
  explicit `Save anyway` confirmation warning.
- The firewall action reports that no configuration is needed while localhost is selected.
  For another bind address, it checks the Palsitter web port over TCP and, when the port
  is blocked or has no matching allow rule, offers to remove matching block rules and
  create an allow rule with administrator approval.
- The game-neutral firewall service provides port checks, executable checks on Windows,
  and port repair. A repair may receive an executable path so its matching Block rules
  are removed before the port allow rule is replaced.
- Saved settings take effect through the shared restart confirmation workflow.
- Save and Reset update the mounted controls in place without repainting the Settings
  page.

## HTTP authentication

- Basic Auth is deliberately the simple HTTP authentication option for this
  self-hosted control panel. It is disabled by default and uses the standard square
  `On`/`Off` toggle.
- When enabled, username and password are required on every save; only a salted password
  hash is persisted. The credential fields are disabled while authentication is Off.
- Place any remotely reachable Palsitter machine behind a trusted intranet or VPN. Keep
  the panel bound to localhost when remote access is not required. Basic Auth controls
  access; it is not a substitute for a trusted network boundary.
- Static assets remain public; the control-panel page and websocket require auth.
- Global web authentication events are stored under `config/webui/` and appear in
  instance Audit tabs when the selected game provides an Audit page. They are not
  copied into instance audit files.

CLI host arguments take precedence over `PALSITTER_HOST`, which takes precedence over
the saved Settings value. No proxy forwarding header is trusted for source-IP audit
records.

## Verification focus

GUI and focused service tests cover Settings navigation, required credentials, the
unauthenticated exposure warning, firewall status, persistence, and restart flow.
