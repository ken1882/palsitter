from __future__ import annotations
import threading
from pywebio.exceptions import SessionException
from pywebio.output import clear, close_popup, popup, put_button, put_loading, put_row, put_scope, put_text, put_warning, toast, use_scope
from pywebio.pin import pin, put_checkbox, put_input
from pywebio.session import local, register_thread
from module.games import get_game
from module.instances import delete_instance, list_instances, load_instance
from module.webui.i18n import LANGUAGES, set_language, t
from module.webui.process_manager import ProcessManager
from module.webui.theme import save_preferred_theme
from module.webui.game_ui import get_game_ui
from module.webui.session import (
    begin_page_navigation,
    navigation_transaction,
    page_context,
    register_page_stop_event,
    request_navigation,
    run_if_current,
)
from module.webui.assets import client_call, put_asset_icon, put_asset_widget

def _add_server(*args, **kwargs):
    from module.webui.add_instance import _add_server as implementation
    return implementation(*args, **kwargs)

def _asset_icon(*args, **kwargs):
    return put_asset_icon(*args, **kwargs)

def _clear_dirty_form(*args, **kwargs):
    from module.webui.forms import _clear_dirty_form as implementation
    return implementation(*args, **kwargs)

def _guard_unsaved_navigation(*args, **kwargs):
    from module.webui.forms import _guard_unsaved_navigation as implementation
    return implementation(*args, **kwargs)

def _home(*args, **kwargs):
    from module.webui.pages.home import _home as implementation
    return implementation(*args, **kwargs)

def _updater(*args, **kwargs):
    from module.webui.pages.updater import _updater as implementation
    return implementation(*args, **kwargs)

def _utils(*args, **kwargs):
    from module.webui.pages.utils import _utils as implementation
    return implementation(*args, **kwargs)

def _manager(name: str) -> ProcessManager:
    return ProcessManager.get(name)

def _dashboard_row(name: str) -> list[str]:
    record = load_instance(name)
    adapter = get_game(record.game)
    manager = _manager(name) if adapter.capabilities.lifecycle else None
    data = adapter.status_summary(record, manager.resource_usage() if manager else None)
    return [
        name,
        data["server_name"],
        manager.display_state if manager else "unsupported",
        str(data.get("players", "-")),
        str(data.get("fps", "-")),
        str(data.get("uptime", "-")),
        str(data.get("memory", "-")),
        str(data.get("latest_backup", "-")),
        str(data.get("days", "-")),
        str(data.get("cpu", "-")),
        str(data.get("game_version", "-")),
        str(data.get("palbox", "-")),
    ]

def _format_uptime(value) -> str:
    try:
        total_seconds = max(0, int(value))
    except (TypeError, ValueError):
        return "-"
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}d {hours:02d}h {minutes:02d}m {seconds:02d}s"

def _profile_label(name: str) -> str:
    return name

def _put_loading_text(text: str, shape: str = "border", color: str = "dark", fill: bool = False) -> None:
    put_row(
        [
            put_asset_widget(
                "shared.loading_indicator",
                {"shape": shape, "fill": fill, "indicator": put_loading(shape=shape, color=color)},
            ),
            None,
            put_text(text),
        ],
        size="auto 2px 1fr",
    )

def _status_code(active: str) -> int:
    if active == "Home" or active == "Add":
        return 0
    record = load_instance(active)
    if not get_game(record.game).capabilities.lifecycle:
        return 5
    manager = _manager(record.name)
    if manager.display_state == "running":
        return 1
    if manager.state in ("stopping", "killing"):
        return 6
    if manager.state in ("installing", "updating", "booting"):
        return 4
    if manager.state == "warning":
        return 3
    return 2


def _run_navigation(render):
    with navigation_transaction() as request:
        if request is None:
            return None
        previous = getattr(local, "navigation_request_override", None)
        local.navigation_request_override = request
        try:
            return render()
        finally:
            local.navigation_request_override = previous

@use_scope("header_status", clear=True)
def _set_status(state: int) -> None:
    if state == 1:
        _put_loading_text(t("status.running"), color="success")
    elif state == 2:
        _put_loading_text(t("status.inactive"), color="secondary", fill=True)
    elif state == 3:
        _put_loading_text(t("status.warning"), shape="grow", color="warning")
    elif state == 4:
        _put_loading_text(t("status.updating"), shape="grow", color="success")
    elif state == 5:
        put_text(t("status.unsupported"))
    elif state == 6:
        _put_loading_text(t("status.stopping"), shape="grow", color="secondary")

def _set_frame(title: str, active: str = "Home"):
    _clear_dirty_form()
    request = getattr(local, "navigation_request_override", None)
    if request is None:
        request = request_navigation()
    context = begin_page_navigation(request)
    if context is None:
        return None
    clear("ROOT")
    with use_scope("ROOT"):
        put_scope(
            "header",
            [
                put_scope("brand", [
                    put_asset_widget("shared.brand"),
                    put_scope("header_status"),
                ]),
                put_scope("title", [put_text(title)]),
            ],
        )
        put_scope(
            "contents",
            [
                put_scope("aside"),
                put_scope("menu"),
                put_scope("main", [put_scope("content")]),
            ],
        )
    client_call("page.begin", generation=context.generation)
    client_call("dom.resetContent")
    _set_status(_status_code(active))
    _render_aside(active)
    return context

def _render_aside(active: str) -> None:
    clear("aside")
    with use_scope("aside"):
        _rail_button(_rail_icon("develop"), t("nav.home"), lambda: _home(), active == "Home")
        for record in list_instances():
            _rail_button(_rail_icon("run"), _profile_label(record.name), lambda n=record.name: open_instance(n), active == record.name)
        _rail_button(_rail_icon("add"), t("nav.add"), _add_server, active == "Add")

def _rail_icon(name: str):
    return _asset_icon(name)

def _rail_button(icon, label: str, onclick, active: bool = False) -> None:
    cls = "rail-button rail-active" if active else "rail-button"
    put_asset_widget(
        "shared.navigation_item",
        {"classes": cls, "icon": [icon], "label": label},
    ).onclick(
        lambda: _guard_unsaved_navigation(onclick)
    )

def _menu_button(label: str, onclick, active: bool = False) -> None:
    cls = "menu-button menu-active" if active else "menu-button"
    put_asset_widget(
        "shared.navigation_item",
        {"classes": cls, "icon": [], "label": label},
    ).onclick(lambda: _guard_unsaved_navigation(onclick))

def _render_home_menu() -> None:
    clear("menu")
    with use_scope("menu"):
        _menu_button(t("nav.home"), _home, True)
        _menu_button(t("nav.updater"), _updater)
        _menu_button(t("nav.utils"), _utils)

def _render_instance_menu(name: str, active: str = "overview") -> None:
    clear("menu")
    record = load_instance(name)
    pages = get_game_ui(record.game).pages
    with use_scope("menu"):
        for index, page in enumerate(pages):
            if index == 1:
                put_asset_widget("shared.menu_divider", {"label": _profile_label(name)})
            _menu_button(
                t(page.label_key),
                lambda page_id=page.id: open_instance(name, page_id),
                active == page.id,
            )
    if get_game(record.game).capabilities.lifecycle and active != "overview":
        _mount_instance_header_status(name)

def _mount_instance_header_status(name: str) -> None:
    stop_event = threading.Event()
    register_page_stop_event(stop_event)
    context = page_context()
    manager = _manager(name)

    def refresh() -> None:
        previous = _status_code(name)
        try:
            while not stop_event.wait(1):
                current = _status_code(name)
                if current != previous:
                    run_if_current(context, lambda: _set_status(current))
                    previous = current
        except (SessionException, FileNotFoundError):
            return

    thread = threading.Thread(target=refresh, daemon=True)
    register_thread(thread)
    thread.start()

def open_instance(name: str, page_id: str = "overview") -> None:
    return _run_navigation(lambda: _open_instance(name, page_id))


def _open_instance(name: str, page_id: str = "overview") -> None:
    record = load_instance(name)
    webui = get_game_ui(record.game)
    try:
        page = next(page for page in webui.pages if page.id == page_id)
    except StopIteration as exc:
        raise KeyError(f"unknown {record.game} page: {page_id}") from exc
    if _set_frame(t(page.title_key), name) is None:
        return
    _render_instance_menu(name, page.id)
    clear("content")
    page.render(name)


def _instance(name: str) -> None:
    open_instance(name)


def _players_page(name: str) -> None:
    open_instance(name, "players")


def _settings(name: str) -> None:
    open_instance(name, "server_settings")


def _auto_restart(name: str) -> None:
    open_instance(name, "auto_restart")


def _world_settings(name: str) -> None:
    open_instance(name, "world_settings")


def _mods(name: str) -> None:
    open_instance(name, "mods")


def _backups(name: str) -> None:
    open_instance(name, "backups")

def _change_language(language: str) -> None:
    selected = set_language(language)
    _home()
    toast(t("home.language_selected", name=LANGUAGES[selected]))

def _change_theme(theme: str) -> None:
    selected = save_preferred_theme(theme)
    client_call("dom.setTheme", theme=selected)
    toast(t("home.theme_selected", theme=t(f"home.{selected}")))

def _delete_instance(name: str) -> None:
    label = _profile_label(name)
    with popup(t("settings.delete"), closable=True) as scope:
        put_asset_widget(
            "shared.quiet_text",
            {"text": t("settings.delete_warning")},
            scope=scope,
        )
        put_input("delete_confirm_name", label=t("settings.delete_confirm_label", name=label), scope=scope)
        put_checkbox(
            "delete_wipe_data",
            options=[{"label": t("settings.delete_wipe_data"), "value": "wipe"}],
            value=[],
            scope=scope,
        )
        put_scope("delete_error", scope=scope)
        put_scope("delete_confirm", scope=scope)
        with use_scope("delete_confirm"):
            put_button(
                t("settings.delete_yes"),
                onclick=lambda: _confirm_delete_instance(name),
                color="danger",
                disabled=True,
            )
    client_call("instance.configureDeleteConfirmation", target=label)

def _confirm_delete_instance(name: str) -> None:
    label = _profile_label(name)
    if str(pin.delete_confirm_name or "").strip() != label:
        clear("delete_error")
        put_warning(t("settings.delete_mismatch"), scope="delete_error")
        return
    if bool(pin.delete_wipe_data):
        close_popup()
        with popup(t("settings.delete_wipe_title"), closable=True) as scope:
            put_warning(t("settings.delete_wipe_warning"), scope=scope)
            put_scope("delete_wipe_error", scope=scope)
            put_row(
                [
                    put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                    None,
                    put_button(
                        t("settings.delete_wipe_yes"),
                        onclick=lambda: _delete_instance_now(
                            name, wipe_data=True, error_scope="delete_wipe_error"
                        ),
                        color="danger",
                    ),
                ],
                size="auto .5rem auto",
            )
        return
    _delete_instance_now(name)


def _delete_instance_now(
    name: str, *, wipe_data: bool = False, error_scope: str = "delete_error"
) -> None:
    label = _profile_label(name)
    try:
        delete_instance(name, wipe_data=wipe_data)
    except Exception as exc:
        clear(error_scope)
        put_warning(t("settings.delete_failed", error=exc), scope=error_scope)
        return
    close_popup()
    toast(t("settings.deleted", name=label))
    _home()


confirm_delete_instance = _delete_instance

__all__ = ["confirm_delete_instance", "open_instance"]
