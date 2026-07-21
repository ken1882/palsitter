from __future__ import annotations
from pywebio.output import clear, close_popup, popup, put_button, put_loading, put_scope, put_text, put_warning, toast, use_scope
from pywebio.pin import pin, pin_on_change, pin_update, put_input, put_select
from pywebio.session import local
from module.games import list_games
from module.instances import create_instance, list_instances, next_instance_name
from module.webui.game_ui import get_game_ui
from module.webui.i18n import t
from module.webui.assets import client_call

def _instance(*args, **kwargs):
    from module.webui.instance import _instance as implementation
    return implementation(*args, **kwargs)

def _profile_label(*args, **kwargs):
    from module.webui.instance import _profile_label as implementation
    return implementation(*args, **kwargs)

def _add_server(values: dict[str, str] | None = None) -> None:
    values = values or {}
    with popup(t("add.title"), closable=True) as scope:
        game = values.get("game", "palworld")
        suggestion = _next_profile_name(game)
        local.add_auto_name = suggestion
        put_select(
            "add_server_game",
            label=t("add.game"),
            options=[{"label": item.display_name, "value": item.id} for item in list_games()],
            value=game,
            scope=scope,
        )
        pin_on_change("add_server_game", onchange=_add_server_game_changed, clear=True)
        put_input(
            "add_server_name",
            label=t("add.profile_name"),
            value=values.get("name", suggestion),
            scope=scope,
        )
        put_scope("add_server_source", scope=scope)
        _render_add_server_source(game, origin=values.get("origin", "template"))
        put_scope("add_server_import", scope=scope)
        _render_add_server_import(game)
        put_scope("add_server_error", scope=scope)
        put_scope("add_server_confirm", scope=scope)
        with use_scope("add_server_confirm"):
            put_button(t("common.confirm"), onclick=lambda: _confirm_add_server(scope))

def _render_add_server_source(game: str, *, origin: str = "template") -> None:
    origins = [{"label": "template", "value": "template"}]
    origins.extend(
        {"label": _profile_label(record.name), "value": record.name}
        for record in list_instances()
        if record.game == game
    )
    clear("add_server_source")
    put_select(
        "add_server_origin",
        label=t("add.copy_from"),
        options=origins,
        value=origin,
        scope="add_server_source",
    )

def _render_add_server_import(game: str) -> None:
    clear("add_server_import")
    creation = get_game_ui(game).creation
    if creation is not None:
        creation.render_fields()

def _add_server_game_changed(game: str) -> None:
    game = str(game or "palworld")
    previous = str(getattr(local, "add_auto_name", ""))
    suggestion = _next_profile_name(game)
    if str(pin.add_server_name or "") == previous:
        pin_update("add_server_name", value=suggestion)
    local.add_auto_name = suggestion
    _render_add_server_source(game)
    _render_add_server_import(game)


def reopen_add_server() -> None:
    values = getattr(local, "add_server_reopen_values", {})
    local.add_server_reopen_values = {}
    _add_server(values=dict(values))

def _next_profile_name(game: str = "palworld") -> str:
    return next_instance_name(game)

def _confirm_add_server(scope: str) -> None:
    name = str(pin.add_server_name or "").strip()
    game = str(pin.add_server_game or "palworld")
    origin = str(pin.add_server_origin or "template")
    if getattr(local, "add_server_busy", False):
        return
    local.add_server_busy = True
    client_call(
        "dom.setControlDisabled",
        selector="#pywebio-scope-add_server_confirm button",
        disabled=True,
    )
    with popup(t("add.creating", name=name), closable=False, implicit_close=False):
        put_loading(shape="border", color="primary")
        put_text(t("add.creating", name=name))
    try:
        creation = get_game_ui(game).creation
        if creation is None:
            create_instance(name, game, origin)
        else:
            if creation.create(name, origin) is False:
                local.add_server_busy = False
                return
        local.add_server_busy = False
        close_popup()
        toast(t("add.created", name=name))
        _instance(name)
    except FileExistsError:
        _restore_add_server_after_error(name, game, origin, t("add.error_exists"))
    except ValueError:
        _restore_add_server_after_error(name, game, origin, t("add.error_invalid"))
    except FileNotFoundError:
        _restore_add_server_after_error(name, game, origin, t("add.error_source"))
    except Exception as exc:
        _restore_add_server_after_error(name, game, origin, t("add.error_unknown", error=exc))


def _restore_add_server_after_error(name: str, game: str, origin: str, message: str) -> None:
    local.add_server_busy = False
    close_popup()
    _add_server(values={"name": name, "game": game, "origin": origin})
    clear("add_server_error")
    put_warning(message, scope="add_server_error")
