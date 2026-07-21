# Palworld Component: Players

Reached from the Palworld instance menu directly after [Overview](./instance-overview.md).
It is a dedicated REST-backed administration page; the Overview keeps a compact roster.

- Each online-player row shows name, level, whole-millisecond ping (rendered with
  `int(ping)`), coordinates, and building count when the REST response supplies them. IP
  addresses are never displayed.
- User ids are masked initially in a fixed-width region. Reveal/Hide and Copy retain
  stable positions for both masked and full values. Reveal changes only that row; Copy
  copies the full id without revealing every other row.
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
- Existing keyed player rows are patched in place when cached fields change. Only joined
  or departed players add or remove row nodes, so a revealed user id stays revealed while
  that player remains online.
- Below 600 px, player-row content/actions stack without horizontal page overflow.

**Tests:** `tests/test_gui_playwright.py` verifies the action SVGs, hover tooltips, and
vertical centering, then clicks reveal/copy, Kick, Ban, and cached-player Unban through
the real page. It also verifies stable row identity and revealed-id state across a cache
update, the removed controls and stale refreshes, and proves
polling stops on navigation. Storage tests verify cache upserts, timestamps, name lookup,
and missing-banlist creation. REST tests assert method, URL, Basic Auth, bodies, successful
parsing, and HTTP failures against a fake server.
