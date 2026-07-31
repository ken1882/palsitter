# Palworld Component: Tools

The Tools page is reached from the Palworld instance menu immediately after Audit.
Firewall, Instance, and Palworld player ID migration are sibling cards in that
navigator order.
The first tool checks firewall coverage for the configured PalServer executable and game
UDP port. Windows uses Windows Firewall executable and UDP rules. Native Linux probes
installed command backends for an active firewall in this order: firewalld, UFW, then
iptables. If none reports active, it falls back to the first installed backend in that
same order. Linux backends check and repair the configured UDP port; executable-path
rules are not available through these portable command interfaces.

The page also owns Palworld instance management:

- `Rename profile` moves the managed instance directory and updates the profile name and
  derived Palworld paths. Existing save games, backups, logs, and configuration remain
  with the renamed instance.
- Rename is disabled while PalServer, a lifecycle supervisor, or an externally detected
  server is running. An idle detached Windows agent is stopped as part of confirmation
  before the profile directory is moved.
- `Delete instance` uses the shared exact-name confirmation flow. Without `Wipe data`,
  it removes only the profile reference; selecting `Wipe data` requires a second
  confirmation before permanently deleting the managed instance directory.
- Delete is disabled while PalServer, a lifecycle supervisor, or an externally detected
  server is running. The confirmation rechecks that state before removing the profile.

The page also exposes Palworld player-ID migration for imported local worlds:

- Start the imported world once, let the player create a new character, and stop the
  server before using the migration tool.
- Select the original player `.sav` and the newly generated player `.sav`. The tool
  requires both IDs to already be referenced by `Level.sav`. If the selector only shows
  GUID filenames, use `Build player name cache` first.
- `Build player name cache` reads the player and personally owned Pal entries in
  `Level.sav`, plus each player's `_dps.sav` Dimensional Pal Storage sidecar, and writes
  `.palsitter-player-names.json` beside `Level.sav`. The selector shows
  `<name> (owned Pals: <count>) — <GUID>.sav` while retaining the GUID filename as the
  actual value. The count excludes nil-owner guild/base Pals and cross-world Global
  Palbox data.
- A safety backup is created before the tool swaps the player documents and all matching
  player GUID references in `Level.sav`. The server must remain stopped during the
  operation. While the migration documents are decoded, the tool also rebuilds the player
  cache without unpacking those files a second time. Once decoding completes, the
  pre-migration cache is retained after cancellation or failure; a successful save
  transaction includes the migrated cache. The player selectors are refreshed after
  every outcome.
- The migration button is disabled while the server is running. After confirmation, an
  undismissable dialog remains open while it transitions between identity warnings,
  owned-Pal warnings, and progress. Progress reports the safety backup, each save unpacked
  (including player `_dps.sav` sidecars when present), save-data updates, and each file
  repacked. Consecutive unpack operations share one progress row whose filename updates
  to the file currently being unpacked.
- The current Oodle-capable `palsav-flex` codec is required for current `PlM1` saves;
  the older `palworld-save-tools` dependency alone cannot decode them.

- The page shows the resolved executable path and configured game UDP port.
- On Windows, `Check` inspects enabled inbound Allow rules. The check passes when either
  a rule matches the executable path or a UDP rule matches the configured port.
- On Linux, `Check` inspects the selected firewalld, UFW, or iptables rules. The check
  passes when the configured UDP port is allowed. Matching deny/drop/reject rules take
  precedence, and executable matching is shown as not applicable.
- Matching enabled inbound Block rules take precedence and make the result blocked.
- If no matching Allow rule exists, the page asks for confirmation before launching a
  narrowly scoped administrator repair. The Fix Firewall confirmation identifies any
  detected matching Block rules that will be removed.
- Repair creates a Palsitter-owned inbound UDP-port allow rule on Windows and Linux,
  using the shared firewall service. On Windows the effective PalServer executable is
  supplied so matching executable-scoped Block rules are also removed. All detected
  Block rules are removed before the allow rule is replaced, and the firewall is then
  rechecked. If Linux elevation is
  denied, the page displays the exact password-free sudo command being retried, asks for
  the root password, and sends it only to that one-time sudo retry; it is never included
  in a rule payload, command argument, instance profile, or log. Third-party Block rules
  are included in the repair confirmation instead of requiring manual removal.
- During player-ID migration, after `Level.sav` is decoded and before any save is
  modified, the source and destination names are compared with each other and with the
  names selected from the player-name cache. If either identity differs, the page asks
  whether to continue; cancelling leaves the save documents unchanged.
- After the name check is accepted, the decoded personally owned Pal totals are compared,
  including Dimensional Pal Storage. If the destination owns more Pals than the source,
  a second confirmation warns that the selections may be reversed. Cancelling leaves the
  save documents unchanged.
- Check and repair results are also written to the instance Overview log. Results are not
  persisted as Audit events. Raw administrator-command stdout/stderr and exit codes from
  repair are streamed to the same Overview log.
- If Check or Fix completes after the operator navigates away, its persistent log entry may
  remain, but its status, error, toast, popup, and result rows are discarded and never
  appended to the replacement page.

## Verification focus

Focused tests cover atomic profile/data renaming and derived paths. Playwright follows
the instance menu to Tools to exercise stopped-instance rename, running-instance disable,
reference-only/wipe deletion, firewall repair, and player migration.
