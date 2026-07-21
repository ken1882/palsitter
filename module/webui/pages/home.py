from __future__ import annotations
import datetime as dt
import threading
from pywebio.exceptions import SessionException
from pywebio.output import clear, put_button, put_buttons, put_markdown, put_scope, put_text, use_scope
from pywebio.session import local, register_thread
from module.games import get_game
from module.instances import list_instances, load_instance
from module.webui.i18n import language_options, t
from module.webui.session import register_stop_event
from module.webui.assets import client_call, client_query, put_asset_widget

def _add_server(*args, **kwargs):
    from module.webui.add_instance import _add_server as implementation
    return implementation(*args, **kwargs)

def _change_language(*args, **kwargs):
    from module.webui.instance import _change_language as implementation
    return implementation(*args, **kwargs)

def _change_theme(*args, **kwargs):
    from module.webui.instance import _change_theme as implementation
    return implementation(*args, **kwargs)

def _instance(*args, **kwargs):
    from module.webui.instance import _instance as implementation
    return implementation(*args, **kwargs)

def _manager(*args, **kwargs):
    from module.webui.instance import _manager as implementation
    return implementation(*args, **kwargs)

def _profile_label(*args, **kwargs):
    from module.webui.instance import _profile_label as implementation
    return implementation(*args, **kwargs)

def _render_home_menu(*args, **kwargs):
    from module.webui.instance import _render_home_menu as implementation
    return implementation(*args, **kwargs)

def _safe_dom_id(*args, **kwargs):
    from module.webui.forms import _safe_dom_id as implementation
    return implementation(*args, **kwargs)

def _set_frame(*args, **kwargs):
    from module.webui.instance import _set_frame as implementation
    return implementation(*args, **kwargs)

def _home() -> None:
    _set_frame(t("nav.home"), "Home")
    _render_home_menu()
    clear("content")
    with use_scope("content"):
        put_scope(
            "home_page",
            [
                put_scope(
                    "home_preferences",
                    [
                        put_asset_widget("shared.panel_title", {"title": t("home.preferences")}),
                        put_text(t("home.select_language")),
                        put_buttons(language_options(), onclick=_change_language),
                        put_text(t("home.change_theme")),
                        put_buttons(
                            [
                                {"label": t("home.light"), "value": "light", "color": "light"},
                                {"label": t("home.dark"), "value": "dark", "color": "dark"},
                            ],
                            onclick=_change_theme,
                        ),
                        put_markdown(
                            f"""
{t("home.description")}

{t("home.repository")}: `https://github.com/ken1882/palsitter.git`
                            """,
                        ),
                    ],
                ),
                put_asset_widget(
                    "shared.section_title",
                    {"classes": "home-section-title", "title": t("home.instances")},
                ),
                put_scope("home_instances"),
            ],
        )
        client_call("dom.addClasses", scope="home_page", classes=["home-dashboard"])
        client_call("dom.addClasses", scope="home_instances", classes=["home-grid"])
        client_call(
            "dom.addClasses",
            scope="home_preferences",
            classes=["panel", "home-preferences"],
        )
    _render_home_instances()
    _start_home_updates()

def _home_card_scope(name: str) -> str:
    return f"home_instance_{_safe_dom_id(name)}"

def _render_home_instances() -> None:
    records = list_instances()
    clear("home_instances")
    if not records:
        with use_scope("home_instances"):
            put_scope(
                "home_empty",
                [
                    put_asset_widget("shared.panel_title", {"title": t("home.empty_title")}),
                    put_text(t("home.empty_message")),
                    put_button(t("home.add_instance"), onclick=_add_server),
                ],
            )
            client_call(
                "dom.addClasses",
                scope="home_empty",
                classes=["panel", "home-empty"],
            )
        return
    with use_scope("home_instances"):
        for record in records:
            put_scope(
                _home_card_scope(record.name),
                [
                    put_asset_widget(
                        "shared.home_loading_card",
                        {
                            "name": record.name,
                            "label": _profile_label(record.name),
                            "loading": t("home.loading_status"),
                        },
                    )
                ],
            )


def _summary_attr(summary, key: str, default=None):
    if hasattr(summary, key):
        return getattr(summary, key)
    if isinstance(summary, dict):
        return summary.get(key, default)
    return default


def _home_card_data(name: str) -> dict:
    record = load_instance(name)
    adapter = get_game(record.game)
    if not adapter.capabilities.lifecycle:
        return {
            "name": name,
            "label": _profile_label(name),
            "state": "unsupported",
            "state_label": t("status.unsupported"),
            "summary": adapter.display_name,
            "unsupported": True,
            "unsupported_message": t("home.unsupported_message"),
        }

    manager = _manager(name)
    summary = manager.status_summary(rest_timeout=1)
    state = summary.state
    ownership = str(getattr(manager, "ownership", "owned" if manager.active else "none"))
    operation = getattr(manager, "operation_progress", None)
    update_info = getattr(manager, "update_info", None)
    installed = adapter.is_installed(record)
    endpoints = dict(_summary_attr(summary, "endpoint_states", {}) or {})
    endpoint_text = " · ".join(
        f"{key.upper()} {t(f'endpoint.{value}')}" for key, value in endpoints.items()
    ) or t("endpoint.unknown")
    build_id = (
        getattr(update_info, "installed_build_id", None)
        if update_info is not None
        else _summary_attr(summary, "installed_build_id")
    )
    available_id = getattr(update_info, "available_build_id", None) if update_info else None
    build_text = str(build_id or t("updater.unavailable"))
    if build_id and available_id and str(build_id) != str(available_id):
        build_text = t("home.update_available", installed=build_id, available=available_id)
    progress = False
    if operation is not None and (manager.operation_busy or getattr(operation, "error", None)):
        percent = getattr(operation, "percent", None)
        progress = {
            "kind": str(getattr(operation, "kind", "")),
            "phase": str(getattr(operation, "phase", "")),
            "percent": f" {float(percent):.0f}%" if percent is not None else "",
            "message": str(getattr(operation, "error", None) or getattr(operation, "message", "") or ""),
        }
    ownership_key = (
        "external"
        if ownership == "external" or (state == "running" and not manager.active)
        else "owned"
        if ownership in ("managed", "owned") and state == "running"
        else "none"
    )
    install_text = t("home.installed") if installed else t("home.not_installed")
    next_backup = _summary_attr(summary, "next_backup_at")
    next_backup = (
        next_backup.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(next_backup, dt.datetime)
        else "-"
    )
    return {
        "name": name,
        "label": _profile_label(name),
        "state": state,
        "state_label": t(f"status.{state}"),
        "summary": f"{adapter.display_name} · {t(f'home.ownership_{ownership_key}')} · {install_text}",
        "unsupported": False,
        "players_label": t("metrics.players"),
        "fps_label": t("metrics.fps"),
        "cpu_label": t("metrics.cpu"),
        "memory_label": t("metrics.memory"),
        "players": summary["players"],
        "fps": summary["fps"],
        "cpu": summary["cpu"],
        "memory": summary["memory"],
        "endpoints": endpoint_text,
        "version": f"{t('metrics.game_version')}: {summary['game_version']} · {t('home.build')}: {build_text}",
        "backup": (
            f"{t('scheduler.latest_backup')}: {summary['latest_backup']} · "
            f"{t('scheduler.next_backup')}: {next_backup}"
        ),
        "progress": progress,
    }


def _render_home_card(name: str, stop_event: threading.Event | None = None) -> None:
    scope = _home_card_scope(name)
    if not client_query("dom.scopeExists", scope=scope):
        return
    data = _home_card_data(name)
    if stop_event is not None and stop_event.is_set():
        return
    if not client_query("dom.scopeExists", scope=scope):
        return
    if client_query("home.isRendered", scope=scope):
        client_call("home.updateCard", scope=scope, data=data)
        return
    with use_scope(scope, clear=True):
        put_asset_widget("shared.home_instance_card", data).onclick(lambda: _instance(name))

def _start_home_updates() -> None:
    if not list_instances():
        return
    for record in list_instances():
        stop_event = threading.Event()
        register_stop_event(stop_event)

        def refresh(name=record.name, event=stop_event) -> None:
            try:
                while not event.is_set():
                    _render_home_card(name, event)
                    if event.wait(5):
                        return
            except (SessionException, FileNotFoundError):
                return

        thread = threading.Thread(target=refresh, daemon=True)
        register_thread(thread)
        thread.start()
