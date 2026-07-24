# Palworld Component: Players

Reached from the Palworld instance menu directly after [Overview](./instance-overview.md).
It is a dedicated REST-backed administration page; the Overview keeps a compact roster.

- Each online-player row shows name, level, whole-millisecond ping (rendered with
  `int(ping)`), PalDB in-game coordinates, and building count when the REST response
  supplies them. Palpagos and The World Tree use their map-specific coordinate transforms;
  unrecognized numeric positions retain their formatted REST values. IP addresses are never
  displayed.
- The page separates current REST players from offline players retained in the per-instance
  cache. Offline rows keep their last known player details and have no live kick/ban actions.
  They omit ping and label cached coordinates as `Last location`.
- Each cached row records the last login in `yyyy/mm/dd hh:mm:ss` format and total play time;
  total play time is shown in hours and increases by the REST players-poll interval while the
  player is online.
- User ids are displayed as `stem_****` initially, preserving the prefix before the first
  underscore, and the eye control reveals or hides the final component. The control is
  icon-only with a localized accessible label and appears immediately before Copy ID.
  Copy retains its existing behavior and copies the full id without revealing every other row.
- Kick and Ban use vertically centered boot and prohibited-circle SVG icon buttons with
  localized text tooltips on hover or keyboard focus. Their dialogs show the selected
  player, accept an optional message, and send no request until confirmed. The boot glyph
  is rotated 30 degrees counter-clockwise while its square button remains upright.
  Successful player-list responses are upserted into the
  instance's player cache; every refreshed row receives an `updated_at` timestamp.
- A Banned players section at the bottom reads IDs from PalServer's
  `Pal/Saved/SaveGames/banlist.txt`, creating the file and its parent directory if absent.
  Its ID and cached player name columns are followed by an icon-only Unban action. A
  successful Unban is reflected from the server-maintained file. The page has no manual
  Unban or broadcast input.
- The page checks the shared REST cache once per second, updates the DOM only when that
  snapshot changes, and never issues its own `/players` or `/metrics` request. The cache's
  API refresh remains every three seconds. Existing rows remain
  visible while loading and after a cache refresh failure, with a visible stale/error
  state; leaving the page stops its renderer but not the instance cache. The cache polls
  only while the exact instance process is running and its configured REST TCP endpoint
  is open. An unavailable instance replaces every loading placeholder with an explicit
  unavailable state without attempting HTTP requests.
- The unavailable state is rendered independently in the online and offline list scopes,
  so the same localized message can appear more than once. Tests must scope the locator
  to the list or section being verified.
- Existing keyed player rows are patched in place when cached fields change. Only joined
  or departed players add or remove row nodes, so a revealed user id stays revealed while
  that player remains online.
- Below 600 px, player-row content/actions stack without horizontal page overflow.

**Tests:** `tests/test_gui_playwright.py` verifies the action SVGs, hover tooltips, and
vertical centering, then clicks the eye/copy controls, Kick, Ban, and cached-player Unban through
the real page. It also verifies stable row identity and revealed-id state across a cache
update, the removed controls and stale refreshes, and proves
polling stops on navigation. Storage tests verify cache upserts, timestamps, login/play-time
tracking, name lookup,
and missing-banlist creation. REST tests assert method, URL, Basic Auth, bodies, successful
parsing, and HTTP failures against a fake server.
