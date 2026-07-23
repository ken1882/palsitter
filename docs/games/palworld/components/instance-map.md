# Palworld Component: Game Map

The Game Map page is a full-size Palpagos Islands or The World Tree map reached
from the Palworld instance menu. It uses locally captured PalDB map tiles and
displays Fast Travel, Watchtower, and cached Palbox markers.

- The map selector switches between Palpagos Islands and The World Tree.
- POI names follow the selected UI language for Palpagos Islands; World Tree
  labels fall back to English when a localized label is unavailable.
- The map supports mouse-wheel zoom, `+`/`−` zoom buttons, drag panning, and
  camera-bound clamping.
- Online players come from the shared `PalRestCache`; the page does not issue its
  own REST requests. Each valid player position is an 8px-radius red dot, or,
  when Game Data API data is available, a stable shared color for its `GuildID`
  from the v1 palette red/blue/green/yellow/purple/teal/gray/orange.
- When `Enable Game Data API` is enabled, Palbox actors come from the same cache's
  `/v1/api/game-data` snapshot. Each valid `Type: "Palbox"` position uses the shared
  `home.webp` icon and hovers as `Palbox: <GuildName>`.
- The translucent Players control opens a list containing only players detected
  on the selected map. Selecting a player recenters the client-side camera
  without sending an API request. Players without coordinates are omitted.
- The map page owns its refresh timer and restores normal content scrolling when
  navigation leaves the page.

Player coordinates use Palworld REST `location_x`/`location_y` (or camel-case
variants). The horizontal map coordinate is derived from world Y and the vertical
map coordinate is derived from inverted world X using the selected map's PalDB
bounds. Map membership is detected from the coordinate bounds before rendering.

**Tests:** `tests/test_palworld_map.py` verifies both coordinate transforms and
invalid data handling. `tests/test_gui_playwright.py` covers the real map
navigation path, map switching, selected-map player filtering, Palbox rendering,
overlays, player selection, zoom/drag behavior, responsive layout, and cache-only
player updates.
