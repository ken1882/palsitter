# Shared Component: Settings

The Home secondary menu contains `Home`, `Updater`, `Settings`, and `Utils`.

- Settings persists the web UI bind address in `config/webui/settings.json`.
- The address list includes localhost (`127.0.0.1`), all interfaces (`0.0.0.0`),
  and detected local IPv4 interface addresses. Localhost is the default.
- Basic Auth is disabled by default. When enabled, username and password are required
  on every save; only a salted password hash is persisted.
- Selecting a non-localhost address while authentication is disabled requires an
  explicit `Save anyway` confirmation warning.
- The firewall action performs a read-only TCP check for the Palsitter web port.
- Saved settings take effect through the shared restart confirmation workflow.
- Global web authentication events are stored under `config/webui/` and appear in
  instance Audit tabs when the selected game provides an Audit page. They are not
  copied into instance audit files.
- Static assets remain public; the control-panel page and websocket require auth.

CLI host arguments take precedence over `PALSITTER_HOST`, which takes precedence over
the saved Settings value. No proxy forwarding header is trusted for source-IP audit
records.

**Tests:** Settings navigation, validation, exposure warning, firewall status, restart
flow, and authentication are covered by GUI and focused service tests.
