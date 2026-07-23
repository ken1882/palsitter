# Palworld Component: Tools

The Tools page is reached from the Palworld instance menu immediately after Audit.
The first tool checks firewall coverage for the configured PalServer executable and game
UDP port. Windows uses Windows Firewall executable and UDP rules. Native Linux probes
installed command backends for an active firewall in this order: firewalld, UFW, then
iptables. If none reports active, it falls back to the first installed backend in that
same order. Linux backends check and repair the configured UDP port; executable-path
rules are not available through these portable command interfaces.

The page also exposes Palworld player-ID migration for imported local worlds:

- Start the imported world once, let the player create a new character, and stop the
  server before using the migration tool.
- Select the original player `.sav` and the newly generated player `.sav`. The tool
  requires both IDs to already be referenced by `Level.sav`. If the selector only shows
  GUID filenames, use `Build player name cache` first.
- `Build player name cache` reads the player entries in `Level.sav` and writes the
  generated `.palsitter-player-names.json` ID-to-name cache beside `Level.sav`. The
  selector uses names as labels while retaining the GUID filename as the actual value.
- A safety backup is created before the tool swaps the player documents and all matching
  player GUID references in `Level.sav`. The server must remain stopped during the
  operation.
- The migration button is disabled while the server is running. After confirmation, an
  undismissable progress dialog reports the safety backup, each save unpacked (including
  player `_dps.sav` sidecars when present), save-data updates, and each file repacked.
- The current Oodle-capable `palsav-flex` codec is required for current `PlM1` saves;
  the older `palworld-save-tools` dependency alone cannot decode them.

- The page shows the resolved executable path and configured game UDP port.
- On Windows, `Check` inspects enabled inbound Allow rules. The check passes when either
  a rule matches the executable path or a UDP rule matches the configured port.
- On Linux, `Check` inspects the selected firewalld, UFW, or iptables rules. The check
  passes when the configured UDP port is allowed. Matching deny/drop/reject rules take
  precedence, and executable matching is shown as not applicable.
- Matching enabled inbound Block rules take precedence and make the result blocked.
- If no matching Allow rule exists and no third-party Block rule prevents safe repair, the
  page asks for confirmation before launching a narrowly scoped administrator repair.
- Repair creates a Palsitter-owned executable rule by default on Windows, removes only a
  matching Palsitter-owned Block rule, and rechecks the firewall afterward. On Linux it
  creates an inbound UDP-port allow rule in the selected backend. If Linux elevation is
  denied, the page displays the exact password-free sudo command being retried, asks for
  the root password, and sends it only to that one-time sudo retry; it is never included
  in a rule payload, command argument, instance profile, or log. Third-party Block rules
  are reported for manual removal.
- Check and repair results are also written to the instance Overview log. Results are not
  persisted as Audit events.
- If Check or Fix completes after the operator navigates away, its persistent log entry may
  remain, but its status, error, toast, popup, and result rows are discarded and never
  appended to the replacement page.

**Tests:** firewall matching and repair tests use fake command runners. Playwright follows
the instance menu to Tools and uses a fake firewall state/helper to exercise Check, the
repair confirmation, successful repair, and the final Open state.
