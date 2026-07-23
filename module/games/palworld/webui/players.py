from __future__ import annotations
import datetime as dt
import hashlib
import json
from pywebio.output import clear, close_popup, popup, put_button, put_row, put_scope, put_table, put_text, put_warning, toast, use_scope
from pywebio.pin import pin, put_input
from pywebio.session import local
from module.games.palworld.audit import AuditEvent, AuditStore, utc_now
from module.games.palworld.config import load_profile
from module.games.palworld.map import map_name_for_coordinates, world_to_game_coord
from module.games.palworld.players_cache import PalworldBanList, PlayerCache
from module.games.palworld.server import PalRestClient, get_pal_rest_cache
from module.webui.i18n import t
from module.webui.session import page_context, register_page_cleanup, run_if_current
from module.webui.assets import client_call, client_query, put_asset_icon, put_asset_widget

def _render_instance_menu(*args, **kwargs):
    from module.webui.instance import _render_instance_menu as implementation
    return implementation(*args, **kwargs)

def _set_frame(*args, **kwargs):
    from module.webui.instance import _set_frame as implementation
    return implementation(*args, **kwargs)


def _manager(*args, **kwargs):
    from module.webui.instance import _manager as implementation
    return implementation(*args, **kwargs)


def _player_action_button(name: str, userid: str, action: str):
    label = t(f"players.{action}")
    button = put_asset_widget(
        "palworld.player_action_button",
        {"action": action, "label": label, "icon": put_asset_icon(f"player-{action}")},
    )
    return button.onclick(
        lambda: _confirm_player_action(name, userid, action)
    )


def _player_name(name: str, userid: str) -> str:
    snapshot = get_pal_rest_cache(name).snapshot()
    rows = snapshot.players.get("players", []) if snapshot.players else []
    return next(
        (
            str(player.get("name") or "-")
            for player in rows
            if isinstance(player, dict) and str(player.get("userId") or "") == userid
        ),
        PlayerCache(name).names().get(userid, "-"),
    )


def _format_last_login(value: object) -> str:
    try:
        timestamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "-"
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
    return timestamp.astimezone(dt.timezone.utc).strftime("%Y/%m/%d %H:%M:%S")


def _player_activity_values(player: dict) -> dict[str, str]:
    try:
        seconds = max(0.0, float(player.get("total_play_time_seconds", 0)))
    except (TypeError, ValueError):
        seconds = 0.0
    return {
        "last_login": t(
            "players.last_login", value=_format_last_login(player.get("last_login"))
        ),
        "play_time": t("players.play_time", value=f"{seconds / 3600:.1f}"),
    }


def _player_groups(name: str, players: list) -> tuple[list[dict], list[dict]]:
    live = {
        str(player.get("userId")): player
        for player in players
        if isinstance(player, dict) and str(player.get("userId") or "")
    }
    cached = {
        str(player.get("userId")): player
        for player in PlayerCache(name).rows()
        if isinstance(player, dict) and str(player.get("userId") or "")
    }
    online = []
    for userid, player in live.items():
        merged = dict(cached.get(userid, {}))
        merged.update(player)
        merged["userId"] = userid
        merged["online"] = True
        online.append(merged)
    offline = [
        {**player, "online": False}
        for userid, player in cached.items()
        if userid not in live
    ]
    return online, offline


def _render_players(name: str) -> None:
    context = page_context()
    local.players_compact_rows = set()
    local.players_compact_list_initialized = False
    local.players_compact_snapshot_signature = None
    with use_scope("scheduler"):
        put_scope(
            "players_panel",
            [
                put_scope(
                    "players_title",
                    [put_asset_widget("palworld.players_title", {"title": t("players.title", online="-", limit="-")})],
                ),
                put_scope(
                    "players_auto_refresh",
                    [put_button("Auto refresh players", onclick=lambda: _refresh_players(name, context))],
                ),
                put_scope("players_list", [put_text(t("players.loading"))]),
            ],
        )
        client_call("dom.addClasses", scope="players_panel", classes=["panel"])
    client_call("palworld.players.mountCompact", interval=3000, generation=context.generation)
    register_page_cleanup(lambda: client_call("palworld.players.destroyCompact"))

def _refresh_players(name: str, context=None) -> None:
    context = context or page_context()
    return run_if_current(context, lambda: _refresh_players_current(name))

def _refresh_players_current(name: str) -> None:
    profile = load_profile(name)
    snapshot = get_pal_rest_cache(name).snapshot()
    result = snapshot.players or {}
    players = result.get("players", []) if isinstance(result, dict) else []
    metrics = snapshot.metrics or {}
    signature = (
        json.dumps(result, sort_keys=True, default=str),
        json.dumps(metrics, sort_keys=True, default=str),
    )
    if signature == getattr(local, "players_compact_snapshot_signature", None):
        return
    local.players_compact_snapshot_signature = signature
    online = metrics.get("currentplayernum", len(players))
    limit = metrics.get(
        "maxplayernum", profile.world_settings.get("ServerPlayerMaxNum", "-")
    )
    if not client_query("dom.scopeExists", scope="players_panel"):
        return
    client_call("palworld.players.updateTitle", value=t("players.title", online=online, limit=limit))
    _update_players_list(name, players)


def _compact_player_row_scope(userid: str) -> str:
    digest = hashlib.sha256(userid.encode("utf-8")).hexdigest()[:16]
    return f"players_compact_row_{digest}"


def _put_compact_player_row(name: str, player: dict) -> None:
    userid = str(player.get("userId", ""))
    player_name = str(player.get("name", "-"))
    level = str(player.get("level", "-"))
    with use_scope("players_list"):
        put_scope(
            _compact_player_row_scope(userid),
            [
                put_row(
                    [
                        put_asset_widget(
                            "palworld.player_compact_identity",
                            {"name": f"{player_name} (Lv: {level})", "userid": userid},
                        ),
                        _player_action_button(name, userid, "kick"),
                        _player_action_button(name, userid, "ban"),
                    ],
                    size="1fr auto auto",
                )
            ],
        )


def _update_players_list(name: str, players: list) -> None:
    if not bool(getattr(local, "players_compact_list_initialized", False)):
        clear("players_list")
        local.players_compact_list_initialized = True
    valid_players = [
        player
        for player in players
        if isinstance(player, dict) and str(player.get("userId") or "")
    ]
    previous = set(getattr(local, "players_compact_rows", set()))
    current = {str(player["userId"]) for player in valid_players}
    for userid in previous - current:
        client_call("palworld.players.removeRows", scopes=[_compact_player_row_scope(userid)])
    if current:
        client_call("palworld.players.removeEmpty", scope="players_compact_empty")
    elif not client_query("dom.scopeExists", scope="players_compact_empty"):
        with use_scope("players_list"):
            put_scope("players_compact_empty", [put_text(t("players.empty"))])
    for player in valid_players:
        userid = str(player.get("userId", ""))
        player_name = str(player.get("name", "-"))
        level = str(player.get("level", "-"))
        if userid in previous:
            client_call(
                "palworld.players.updateRow",
                scope=_compact_player_row_scope(userid),
                values={"name": f"{player_name} (Lv: {level})"},
            )
        else:
            _put_compact_player_row(name, player)
    if valid_players:
        client_call(
            "palworld.players.orderRows",
            containerScope="players_list",
            scopes=[_compact_player_row_scope(str(player["userId"])) for player in valid_players],
        )
    local.players_compact_rows = current

def _confirm_player_action(name: str, userid: str, action: str) -> None:
    player_name = _player_name(name, userid)
    with popup(t(f"players.confirm_{action}_title"), closable=True):
        put_text(t(f"players.confirm_{action}", name=player_name, userid=userid))
        put_input(
            "player_action_message",
            label=t("players.action_message"),
            placeholder=t("players.action_message_placeholder"),
        )
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup),
                put_button(
                    t(f"players.confirm_{action}_button"),
                    onclick=lambda: _execute_player_action(name, userid, action),
                    color="danger" if action == "ban" else "warning",
                ),
            ],
            size="1fr auto",
        )

def _execute_player_action(name: str, userid: str, action: str) -> None:
    message = str(getattr(pin, "player_action_message", "") or "").strip()
    player_name = _player_name(name, userid)
    result = "fail"
    close_popup()
    try:
        client = PalRestClient(load_profile(name))
        getattr(client, action)(userid, message)
        result = "success"
        toast(t("action.sent", action=t(f"players.{action}")))
    except Exception as exc:
        toast(t("action.failed", action=t(f"players.{action}"), error=exc), color="error")
    audit_message = (
        f"Executed: {action} {player_name} ({userid}) (result: {result})"
    )
    manager = _manager(name)
    manager.append_log(audit_message)
    try:
        AuditStore(name).append(
            AuditEvent(utc_now(), "palsitter_command", audit_message)
        )
    except OSError as exc:
        manager.append_log(f"Could not persist audit event: {exc}")
    if client_query("dom.scopeExists", scope="players_detail_panel"):
        _refresh_players_page(name, force=True)
    else:
        _refresh_players(name)

def render(name: str) -> None:
    """Render the full Palworld REST-backed player administration page."""
    local.players_detail_has_roster = False
    local.players_detail_rows = set()
    local.players_offline_rows = set()
    local.players_detail_list_initialized = False
    local.players_offline_list_initialized = False
    local.players_detail_snapshot_signature = None
    local.players_banned_signature = None
    context = page_context()
    with use_scope("content"):
        put_scope(
            "players_detail_panel",
            [
                put_asset_widget("shared.panel_title", {"title": t("players.page_title")}),
                put_scope("players_detail_error"),
                put_scope(
                    "players_detail_auto_refresh",
                    [put_button(t("players.refresh"), onclick=lambda: _refresh_players_page(name, context))],
                ),
                put_asset_widget("palworld.players_section_title", {"title": t("players.online_title")}),
                put_scope("players_detail_list", [put_text(t("players.loading"))]),
                put_asset_widget("palworld.players_section_title", {"title": t("players.offline_title")}),
                put_scope("players_offline_list", [put_text(t("players.loading"))]),
                put_asset_widget("palworld.players_section_title", {"title": t("players.banned_title")}),
                put_scope("players_banned_list", [put_text(t("players.loading"))]),
            ],
        )
        client_call("dom.addClasses", scope="players_detail_panel", classes=["panel", "players-detail"])
    client_call("palworld.players.mountDetail", interval=1000, generation=context.generation)
    register_page_cleanup(lambda: client_call("palworld.players.destroyDetail"))


def _players_page(name: str) -> None:
    from module.webui.instance import open_instance
    open_instance(name, "players")

def _refresh_players_page(name: str, context=None, *, force: bool = False) -> None:
    context = context or page_context()
    return run_if_current(
        context,
        lambda: _refresh_players_page_current(name, force=force),
    )


def _refresh_players_page_current(name: str, *, force: bool = False) -> None:
    snapshot = get_pal_rest_cache(name).snapshot()
    signature = (
        snapshot.session_active,
        snapshot.rest_open,
        json.dumps(snapshot.players, sort_keys=True, default=str),
        json.dumps(PlayerCache(name).rows(), sort_keys=True, default=str),
        snapshot.players_error,
    )
    if (
        not force
        and signature == getattr(local, "players_detail_snapshot_signature", None)
    ):
        return
    local.players_detail_snapshot_signature = signature
    if not snapshot.session_active or not snapshot.rest_open:
        _show_players_page_unavailable(name, t("players.unavailable"))
        return
    result = snapshot.players
    players = result.get("players", []) if isinstance(result, dict) else None
    if not client_query("dom.scopeExists", scope="players_detail_panel"):
        return
    with use_scope("players_detail_error", clear=True):
        errors = {value for value in (snapshot.players_error,) if value}
        if errors:
            put_warning(t("players.refresh_failed", error="; ".join(errors)))
    if players is not None:
        online, offline = _player_groups(name, players)
        _update_players_page_list(name, online, offline)
        local.players_detail_has_roster = True
    elif not bool(getattr(local, "players_detail_has_roster", False)):
        with use_scope("players_detail_list", clear=True):
            put_text(t("players.unavailable"))
        with use_scope("players_offline_list", clear=True):
            put_text(t("players.unavailable"))
    _update_banned_players(name)


def _show_players_page_unavailable(name: str, error: str) -> None:
    if not client_query("dom.scopeExists", scope="players_detail_panel"):
        return
    with use_scope("players_detail_error", clear=True):
        put_warning(t("players.refresh_failed", error=error))
    cached_players = [
        {**player, "online": False} for player in PlayerCache(name).rows()
    ]
    if cached_players:
        _update_players_page_list(name, [], cached_players)
        local.players_detail_has_roster = True
    elif not bool(getattr(local, "players_detail_has_roster", False)):
        with use_scope("players_detail_list", clear=True):
            put_text(t("players.unavailable"))
        with use_scope("players_offline_list", clear=True):
            put_text(t("players.unavailable"))
    _update_banned_players(name)

def _player_row_scope(userid: str) -> str:
    digest = hashlib.sha256(userid.encode("utf-8")).hexdigest()[:16]
    return f"players_detail_row_{digest}"


def _player_row_values(player: dict) -> dict[str, str]:
    player_name = str(player.get("name", "-"))
    level = str(player.get("level", "-"))
    online = bool(player.get("online", True))
    ping = player.get("ping", None)
    ping = f"{int(ping)}ms" if online and isinstance(ping, (int, float)) else ""
    world_x = player.get("location_x", player.get("locationX"))
    world_y = player.get("location_y", player.get("locationY"))
    map_name = map_name_for_coordinates(world_x, world_y)
    game_coord = world_to_game_coord(world_x, world_y, map_name) if map_name else None
    if game_coord is not None:
        x, y = (str(value) for value in game_coord)
    else:
        x = str(round(world_x, 2)) if isinstance(world_x, (int, float)) else "-"
        y = str(round(world_y, 2)) if isinstance(world_y, (int, float)) else "-"
    buildings = player.get("building_count", player.get("buildingCount"))
    return {
        "name": f"{player_name} (Lv: {level})",
        "ping": t("players.ping", value=ping),
        "coordinates": t(
            "players.coordinates" if online else "players.last_location", x=x, y=y
        ),
        "buildings": f" · {t('players.buildings', count=buildings)}" if buildings is not None else "",
        **_player_activity_values(player),
    }


def _player_details(player: dict) -> object:
    userid = str(player.get("userId", ""))
    values = _player_row_values(player)
    return put_asset_widget(
        "palworld.player_details",
        {
            **values,
            "userid": userid,
            "online": bool(player.get("online", True)),
            "show": t("players.reveal_id"),
            "hide": t("players.hide_id"),
            "copy": t("players.copy_id"),
        },
    )


def _player_activity(player: dict) -> object:
    return put_asset_widget(
        "palworld.player_activity",
        _player_activity_values(player),
    )


def _put_player_detail_row(name: str, player: dict, *, online: bool) -> None:
    userid = str(player.get("userId", ""))
    container = "players_detail_list" if online else "players_offline_list"
    actions = (
        [_player_action_button(name, userid, "kick"), _player_action_button(name, userid, "ban")]
        if online
        else []
    )
    with use_scope(container):
        put_scope(
            _player_row_scope(userid),
            [
                put_row(
                    [
                        _player_details(player),
                        _player_activity(player),
                        *actions,
                    ],
                    size="minmax(0, 1fr) auto auto auto" if online else "minmax(0, 1fr) auto",
                )
            ],
        )


def _update_player_detail_row(player: dict) -> None:
    userid = str(player.get("userId", ""))
    client_call(
        "palworld.players.updateRow",
        scope=_player_row_scope(userid),
        values=_player_row_values(player),
    )


def _update_player_list(
    name: str,
    players: list[dict],
    *,
    online: bool,
    list_scope: str,
    empty_scope: str,
    empty_key: str,
    rows_attribute: str,
    initialized_attribute: str,
) -> None:
    if not bool(getattr(local, initialized_attribute, False)):
        clear(list_scope)
        setattr(local, initialized_attribute, True)
    valid_players = [
        player
        for player in players
        if isinstance(player, dict) and str(player.get("userId") or "")
    ]
    previous = set(getattr(local, rows_attribute, set()))
    current = {str(player["userId"]) for player in valid_players}
    for userid in previous - current:
        client_call("palworld.players.removeRows", scopes=[_player_row_scope(userid)])
    if current:
        client_call("palworld.players.removeEmpty", scope=empty_scope)
    elif not client_query("dom.scopeExists", scope=empty_scope):
        with use_scope(list_scope):
            put_scope(empty_scope, [put_text(t(empty_key))])
    for player in valid_players:
        userid = str(player.get("userId", ""))
        if userid in previous:
            _update_player_detail_row(player)
        else:
            _put_player_detail_row(name, player, online=online)
    if valid_players:
        client_call(
            "palworld.players.orderRows",
            containerScope=list_scope,
            scopes=[_player_row_scope(str(player["userId"])) for player in valid_players],
        )
    setattr(local, rows_attribute, current)


def _update_players_page_list(name: str, online: list[dict], offline: list[dict]) -> None:
    online_ids = {str(player.get("userId")) for player in online}
    offline_ids = {str(player.get("userId")) for player in offline}
    previous_online = set(getattr(local, "players_detail_rows", set()))
    previous_offline = set(getattr(local, "players_offline_rows", set()))
    moving_online = previous_online & offline_ids
    moving_offline = previous_offline & online_ids
    for userid in moving_online | moving_offline:
        # PyWebIO requires every scope id to be unique while a transition is
        # being rendered, and both sections intentionally share row scopes.
        client_call("palworld.players.removeRows", scopes=[_player_row_scope(userid)])
    local.players_detail_rows = previous_online - moving_online
    local.players_offline_rows = previous_offline - moving_offline

    _update_player_list(
        name,
        offline,
        online=False,
        list_scope="players_offline_list",
        empty_scope="players_offline_empty",
        empty_key="players.offline_empty",
        rows_attribute="players_offline_rows",
        initialized_attribute="players_offline_list_initialized",
    )
    _update_player_list(
        name,
        online,
        online=True,
        list_scope="players_detail_list",
        empty_scope="players_detail_empty",
        empty_key="players.empty",
        rows_attribute="players_detail_rows",
        initialized_attribute="players_detail_list_initialized",
    )

def _update_banned_players(name: str) -> None:
    userids = PalworldBanList(name).ids()
    names = PlayerCache(name).names()
    signature = tuple(
        (userid, names.get(userid) or names.get(f"steam_{userid}") or "-")
        for userid in userids
    )
    if signature == getattr(local, "players_banned_signature", None):
        return
    local.players_banned_signature = signature
    with use_scope("players_banned_list", clear=True):
        if not userids:
            put_text(t("players.banned_empty"))
            return
        rows = []
        for userid in userids:
            label = t("players.unban")
            button = put_asset_widget(
                "palworld.player_unban_button", {"label": label}
            ).onclick(lambda userid=userid: _unban_listed_player(name, userid))
            player_name = names.get(userid)
            if player_name is None and not userid.startswith("steam_"):
                player_name = names.get(f"steam_{userid}")
            rows.append([userid, player_name or "-", button])
        put_table(rows, header=[t("players.id"), t("players.name"), ""])


def _unban_listed_player(name: str, userid: str) -> None:
    try:
        PalRestClient(load_profile(name)).unban(userid)
        toast(t("action.sent", action=t("players.unban")))
        _update_banned_players(name)
    except Exception as exc:
        toast(t("action.failed", action=t("players.unban"), error=exc), color="error")
