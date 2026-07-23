from __future__ import annotations

import json
import re
from urllib.parse import quote

from pywebio.output import put_button, put_scope, use_scope
from pywebio.session import local

from module.games.palworld.map import (
    MAP_NAMES,
    MAP_SIZE,
    load_manifest,
    load_marker_labels,
    game_data_player_guilds,
    map_name_for_coordinates,
    player_map_row,
    world_to_map_pixel,
)
from module.games.palworld.server import get_pal_rest_cache
from module.webui.assets import client_call, put_asset_widget
from module.webui.i18n import get_language, t
from module.webui.session import page_context, register_page_cleanup, run_if_current


_TILE_NAME = re.compile(r"^z4x(\d+)y(\d+)\.webp$")
_FALLBACK_TILE_NAME = re.compile(r"^z1x(\d+)y(\d+)\.webp$")
_INITIAL_SCALE = 0.125


def _asset_url(map_name: str, path: str) -> str:
    return f"/static/gui/map/{quote(map_name)}/" + "/".join(
        quote(part) for part in path.split("/")
    )


def _shared_asset_url(path: str) -> str:
    return "/static/gui/map/" + "/".join(quote(part) for part in path.split("/"))


def _tile_data(map_name: str, filename: str) -> dict | None:
    match = _TILE_NAME.match(filename)
    if match is None:
        return None
    x, y = (int(value) for value in match.groups())
    return {
        "src": _asset_url(map_name, "tiles/" + filename),
        "left": x * 512,
        "top": y * 512,
    }


def _fallback_tile_data(map_name: str, filename: str, tile_size: int) -> dict | None:
    match = _FALLBACK_TILE_NAME.match(filename)
    if match is None:
        return None
    x, y = (int(value) for value in match.groups())
    return {
        "src": _asset_url(map_name, "tiles/" + filename),
        "left": x * tile_size,
        "top": y * tile_size,
        "width": tile_size,
        "height": tile_size,
    }


def _marker_data(
    map_name: str,
    marker: dict,
    localized_label: str | None = None,
) -> dict | None:
    marker_type = str(marker.get("type", ""))
    if marker_type not in ("Fast Travel", "Watchtower"):
        return None
    try:
        x = float(marker["x"])
        y = float(marker["y"])
    except (KeyError, TypeError, ValueError):
        return None
    label = re.sub(
        r"\s[-+]?\d+\s*,\s*[-+]?\d+\s*$",
        "",
        localized_label or str(marker.get("label") or marker_type),
    ).strip() or marker_type
    icon = "fast-travel.webp" if marker_type == "Fast Travel" else "watchtower.webp"
    return {
        "type": marker_type,
        "label": label,
        "left": f"{x:g}",
        "top": f"{y:g}",
        "src": _shared_asset_url(icon),
    }


def _map_data() -> dict:
    language = get_language()
    layers = []
    for map_name in MAP_NAMES:
        manifest = load_manifest(map_name)
        localized_markers = load_marker_labels(map_name).get("markers", {})
        empty_tiles = set(manifest.get("empty_tiles", []))
        tiles = [
            data
            for filename in manifest.get("tiles", [])
            if str(filename) not in empty_tiles
            if (data := _tile_data(map_name, str(filename))) is not None
        ]
        fallback = manifest.get("fallback", {})
        fallback_tile_size = int(fallback.get("tile_size", 0) or 0)
        fallback_tiles = [
            data
            for filename in fallback.get("tiles", [])
            if fallback_tile_size > 0
            if (data := _fallback_tile_data(map_name, str(filename), fallback_tile_size))
            is not None
        ]
        markers = []
        for marker_type, rows in manifest.get("markers", {}).items():
            localized_rows = localized_markers.get(marker_type, [])
            for index, row in enumerate(rows if isinstance(rows, list) else []):
                if not isinstance(row, dict):
                    continue
                localized_row = localized_rows[index] if index < len(localized_rows) else {}
                localized_label = (
                    localized_row.get(language) or localized_row.get("en-US")
                    if isinstance(localized_row, dict)
                    else None
                )
                data = _marker_data(
                    map_name,
                    {**row, "type": marker_type},
                    localized_label,
                )
                if data is not None:
                    markers.append(data)
        layers.append(
            {
                "name": map_name,
                "hidden": map_name != "palpagos",
                "tiles": tiles,
                "fallback_tiles": fallback_tiles,
                "markers": markers,
            }
        )
    return {
        "layers": layers,
        "map_aria": t("map.map_aria"),
        "map_select": t("map.map_select"),
        "palpagos": t("map.palpagos"),
        "world_tree": t("map.world_tree"),
        "legend": t("map.legend"),
        "fast_travel": t("map.fast_travel"),
        "watchtower": t("map.watchtower"),
        "fast_travel_icon": _shared_asset_url("fast-travel.webp"),
        "watchtower_icon": _shared_asset_url("watchtower.webp"),
        "palbox_icon": _shared_asset_url("home.webp"),
        "players": t("map.players", count=0),
        "player_list": t("map.player_list"),
        "zoom_controls": t("map.zoom_controls"),
        "zoom_in": t("map.zoom_in"),
        "zoom_out": t("map.zoom_out"),
    }


def render(name: str) -> None:
    context = page_context()
    local.map_snapshot_signature = None
    get_pal_rest_cache(name).ensure_started()
    with use_scope("content"):
        put_scope(
            "map_page",
            [
                put_asset_widget("palworld.map", _map_data()),
                put_scope("map_refresh", [put_button("", onclick=lambda: _refresh(name, context))]),
            ],
        )
    client_call("dom.addClasses", scope="content", classes=["map-content"])
    client_call(
        "palworld.map.mount",
        mapSize=MAP_SIZE,
        initialScale=_INITIAL_SCALE,
        palboxIcon=_shared_asset_url("home.webp"),
        generation=context.generation,
        labels={
            "player_count": t("map.players", count="{count}"),
            "player_aria": t("map.player_aria", name="{name}"),
            "no_players": t("map.no_players"),
            "no_position": t("map.no_position"),
            "live": t("map.live"),
            "stale": t("map.stale"),
            "unavailable": t("map.unavailable"),
            "palbox": t("map.palbox"),
        },
    )
    client_call("palworld.map.startRefresh")
    register_page_cleanup(lambda: client_call("palworld.map.destroyPage"))


def _refresh(name: str, context=None) -> None:
    context = context or page_context()
    snapshot = get_pal_rest_cache(name).snapshot()
    game_data = snapshot.game_data
    guilds = game_data_player_guilds(game_data)
    result = snapshot.players if isinstance(snapshot.players, dict) else {}
    rows = result.get("players", []) if isinstance(result, dict) else []
    players = []
    for player in rows if isinstance(rows, list) else []:
        if not isinstance(player, dict):
            continue
        map_name = map_name_for_coordinates(
            player.get("location_x", player.get("locationX")),
            player.get("location_y", player.get("locationY")),
        )
        if map_name is None:
            continue
        mapped = player_map_row(player, map_name)
        if mapped is not None:
            mapped["map"] = map_name
            if game_data is not None:
                guild_id = str(
                    player.get("GuildID")
                    or player.get("guildId")
                    or guilds.get(mapped["userId"], "")
                ).strip()
                if guild_id:
                    mapped["guildId"] = guild_id
            players.append(mapped)
    actors = game_data.get("ActorData", []) if isinstance(game_data, dict) else []
    palboxes = []
    for actor in actors if isinstance(actors, list) else []:
        if not isinstance(actor, dict) or str(actor.get("Type", "")).casefold() != "palbox":
            continue
        world_x = actor.get("LocationX", actor.get("location_x"))
        world_y = actor.get("LocationY", actor.get("location_y"))
        map_name = map_name_for_coordinates(world_x, world_y)
        point = world_to_map_pixel(world_x, world_y, map_name) if map_name else None
        if point is None:
            continue
        palboxes.append(
            {
                "map": map_name,
                "x": point[0],
                "y": point[1],
                "label": f"{t('map.palbox')}: {actor.get('GuildName', '')}",
            }
        )
    state = "live" if snapshot.players is not None and snapshot.players_error is None else "stale"
    if snapshot.players is None and not snapshot.session_active:
        state = "unavailable"
    signature = (
        json.dumps(players, sort_keys=True),
        json.dumps(palboxes, sort_keys=True),
        state,
    )
    run_if_current(
        context,
        lambda: _apply_map_players(
            signature,
            players,
            palboxes,
            state,
            context.generation if context else None,
        ),
    )


def _apply_map_players(
    signature, players: list, palboxes: list, state: str, generation=None
) -> None:
    if signature == getattr(local, "map_snapshot_signature", None):
        return
    local.map_snapshot_signature = signature
    client_call(
        "palworld.map.pushPlayers",
        players=players,
        palboxes=palboxes,
        state=state,
        generation=generation,
    )


__all__ = ["render"]
