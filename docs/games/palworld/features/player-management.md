# Palworld Feature: Player Management

The dedicated [Players page](../components/instance-players.md) and compact Overview
roster use the per-instance REST cache. Palsitter does not add an RCON command console;
RCON is deprecated upstream in favor of the REST API.

- `GET /players` identifies each connected player with camel-case `userId`; optional
  name, level, ping, coordinates, and building-count values are normalized without
  rejecting a partial row.
- `GET /metrics` supplies `currentplayernum`, `maxplayernum`, FPS, uptime, and day values.
- `POST /announce` sends `{"message": ...}`.
- `POST /kick` and `POST /ban` send `{"userid": ..., "message": ...}`; the roster's
  `userId` is mapped to lowercase `userid` only at the request boundary.
- `POST /unban` sends `{"userid": ...}`. Because the official API has no banned-player
  listing, the Players page reads PalServer's `Pal/Saved/SaveGames/banlist.txt` as the
  source of truth and creates an empty file when it does not yet exist.
- Every successful `GET /players` response upserts rows into the per-instance
  `players.json` cache without deleting offline players. Each row seen in that response
  receives a UTC `updated_at`, an `online` state, and a `total_play_time_seconds` increase
  equal to the configured REST poll interval. `last_login` is set when a player first appears
  or returns after being offline; banned-player names are resolved from these cached rows.
- Kick and Ban confirmations may send an empty message but never an empty user id.
- The client omits IP addresses from its UI model and reports HTTP, authentication, and
  decode failures without clearing the last successful roster.
- Before a read poll, the cache requires the configured PalServer executable to be
  running from the exact instance path and the configured REST TCP endpoint to accept
  connections. A failed readiness check sends no HTTP request. Administrative REST
  requests retain the same readiness gate in `PalRestClient`.

**Tests:** `tests/test_rest_cache.py` covers session identity, one-shot info caching,
three-second dynamic polling, readiness, and stale-value retention.
`tests/test_rest_client.py` covers every method, URL, Basic Auth header, body,
optional field, success parse, and failure. `tests/test_players_cache.py` covers persistent
upserts, name lookup, and ban-list file handling. `tests/test_gui_playwright.py` covers the
complete click paths described by the Players component.
