from __future__ import annotations
import threading
import time
import traceback
from pywebio.exceptions import SessionException
from pywebio.input import textarea
from pywebio.output import clear, close_popup, popup, put_button, put_row, put_scope, put_text, put_warning, toast, use_scope
from pywebio.session import local, register_thread
from module.games import get_game
from module.instances import list_instances
from module.webui.i18n import t
from module.webui.restart import load_state, render_overlay, start_workflow
from module.webui.shutdown_workflow import render_overlay as render_shutdown_overlay
from module.webui.shutdown_workflow import stop_gui_only
from module.webui.shutdown_workflow import start_workflow as start_shutdown_workflow
from module.webui.session import page_context, register_page_stop_event, run_if_current
from module.webui.assets import client_call, client_query, put_asset_widget
from module.webui.checkbox_groups import mount_checkbox_group

def _home(*args, **kwargs):
    from module.webui.pages.home import _home as implementation
    return implementation(*args, **kwargs)

def _manager(*args, **kwargs):
    from module.webui.instance import _manager as implementation
    return implementation(*args, **kwargs)

def _menu_button(*args, **kwargs):
    from module.webui.instance import _menu_button as implementation
    return implementation(*args, **kwargs)

def _profile_label(*args, **kwargs):
    from module.webui.instance import _profile_label as implementation
    return implementation(*args, **kwargs)

def _set_frame(*args, **kwargs):
    from module.webui.instance import _set_frame as implementation
    return implementation(*args, **kwargs)

def _run_navigation(*args, **kwargs):
    from module.webui.instance import _run_navigation as implementation
    return implementation(*args, **kwargs)

def _updater(*args, **kwargs):
    from module.webui.pages.updater import _updater as implementation
    return implementation(*args, **kwargs)

UTIL_LOGS: list[str] = []

UTIL_LOGS_LOCK = threading.Lock()

def _append_util_log(message: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    with UTIL_LOGS_LOCK:
        UTIL_LOGS.append(f"{stamp} {message}")
        if len(UTIL_LOGS) > 500:
            del UTIL_LOGS[:-500]

def _select_instances(action: str) -> None:
    instances = [
        {"name": record.name, "label": _profile_label(record.name)}
        for record in list_instances()
    ]
    with popup(t(f"utils.select_{action}"), closable=True) as scope:
        put_asset_widget(
            "shared.instance_selection",
            {
                "instances": instances,
                "empty": t("utils.no_instances"),
                "select_all": t("common.select_all"),
                "select_none": t("common.select_none"),
            },
            scope=scope,
        )
        mount_checkbox_group("utils-instance-selection")
        put_button(t("common.confirm"), onclick=lambda: _execute_bulk_action(action), color="primary")


def _selected_instance_names() -> set[str]:
    selected = client_query("utils.selectedInstances")
    return {str(name) for name in (selected or [])}


def _run_all_instances() -> None:
    _select_instances("start")


def _stop_all_instances() -> None:
    _select_instances("stop")


def _kill_all_instances() -> None:
    _select_instances("kill")


def _execute_bulk_action(action: str) -> None:
    selected = _selected_instance_names()
    close_popup()
    if action == "start":
        _run_selected_instances(selected)
    elif action == "stop":
        _stop_selected_instances(selected)
    else:
        _kill_selected_instances(selected)


def _run_selected_instances(selected: set[str]) -> None:
    started = []
    skipped = []
    for record in list_instances():
        name = record.name
        if name not in selected:
            continue
        if not get_game(record.game).capabilities.lifecycle:
            skipped.append(_profile_label(name))
            continue
        manager = _manager(name)
        if manager.active:
            continue
        manager.start()
        started.append(_profile_label(name))
    message = "\n".join(started) if started else t("utils.all_running")
    if skipped:
        message += "\n\n" + t("utils.skipped_unsupported", instances="\n".join(skipped))
    popup(t("utils.started"), message)
    _append_util_log(t("utils.started_result", instances=message))


def _stop_selected_instances(selected: set[str]) -> None:
    stopped = []
    skipped = []
    for record in list_instances():
        name = record.name
        if name not in selected:
            continue
        if not get_game(record.game).capabilities.lifecycle:
            skipped.append(_profile_label(name))
            continue
        manager = _manager(name)
        if not manager.active:
            continue
        manager.stop()
        stopped.append(_profile_label(name))
    message = "\n".join(stopped) if stopped else t("utils.none_running")
    if skipped:
        message += "\n\n" + t("utils.skipped_unsupported", instances="\n".join(skipped))
    popup(t("utils.stopped"), message)
    _append_util_log(t("utils.stopped_result", instances=message))


def _kill_selected_instances(selected: set[str]) -> None:
    killed = []
    skipped = []
    for record in list_instances():
        name = record.name
        if name not in selected:
            continue
        if not get_game(record.game).capabilities.lifecycle:
            skipped.append(_profile_label(name))
            continue
        manager = _manager(name)
        if not manager.active:
            continue
        manager.kill()
        killed.append(_profile_label(name))
    message = "\n".join(killed) if killed else t("utils.none_running")
    if skipped:
        message += "\n\n" + t("utils.skipped_unsupported", instances="\n".join(skipped))
    popup(t("utils.killed"), message)
    _append_util_log(t("utils.killed_result", instances=message))

def _raise_diagnostic_exception() -> None:
    def raise_exception(depth: int) -> None:
        if depth:
            raise_exception(depth - 1)
        raise RuntimeError("quq")

    try:
        raise_exception(3)
    except RuntimeError as e:
        _append_util_log(traceback.format_exc().rstrip())
        toast(t("utils.exception_captured"), color="error")

def _force_restart() -> None:
    existing = load_state()
    if existing is not None:
        render_overlay()
        return
    with popup(t("utils.restart_title"), closable=True):
        put_warning(t("utils.restart_warning"))
        put_text(t("utils.restart_details"))
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(t("utils.restart_continue"), onclick=_confirm_force_restart, color="danger"),
            ],
            size="auto auto",
        )


def _confirm_force_restart() -> None:
    close_popup()
    start_workflow()
    render_overlay()


def _shutdown_palsitter() -> None:
    with popup(t("utils.shutdown_title"), closable=True):
        put_warning(t("utils.shutdown_warning"))
        put_text(t("utils.shutdown_details"))
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(t("utils.shutdown_gui_only"), onclick=_confirm_gui_only, color="primary"),
                put_button(t("utils.shutdown_continue"), onclick=_confirm_shutdown, color="danger"),
            ],
            size="auto auto",
        )


def _confirm_gui_only() -> None:
    close_popup()
    stop_gui_only()


def _confirm_shutdown() -> None:
    close_popup()
    start_shutdown_workflow()
    render_shutdown_overlay()

def _run_utility_code() -> None:
    last_exec = client_query("storage.get", key="_last_exec") or ""
    code = textarea(
        t("utils.code_edit"),
        code={"mode": "python", "theme": "darcula"},
        value=last_exec,
    )
    client_call("storage.set", key="_last_exec", value=code or "")
    namespace = getattr(local, "utils_exec_namespace", None)
    if namespace is None:
        namespace = {"_append_util_log": _append_util_log}
        local.utils_exec_namespace = namespace
    try:
        exec(str(code or ""), namespace, namespace)
    except Exception:
        _append_util_log(traceback.format_exc().rstrip())

@use_scope("log_scroll_btn", clear=True)
def _update_utils_scroll_button() -> None:
    enabled = bool(getattr(local, "utils_keep_bottom", True))
    put_button(
        t("log.auto_scroll") if enabled else t("log.auto_scroll_off"),
        onclick=_toggle_utils_scroll,
        color="success" if enabled else "secondary",
    )

def _toggle_utils_scroll() -> None:
    local.utils_keep_bottom = not bool(getattr(local, "utils_keep_bottom", True))
    _update_utils_scroll_button()

def _update_utils_log(lines: tuple[str, ...]) -> None:
    content = "\n".join(lines) if lines else t("log.empty")
    client_call(
        "utils.updateLog",
        content=content,
        keepBottom=bool(getattr(local, "utils_keep_bottom", True)),
    )

def _start_utils_updates() -> None:
    stop_event = threading.Event()
    register_page_stop_event(stop_event)
    context = page_context()

    def update_log() -> None:
        last_lines: tuple[str, ...] | None = None
        try:
            while not stop_event.is_set():
                with UTIL_LOGS_LOCK:
                    lines = tuple(UTIL_LOGS)
                if lines != last_lines:
                    run_if_current(context, lambda: _update_utils_log(lines))
                    last_lines = lines
                stop_event.wait(1)
        except SessionException:
            return

    thread = threading.Thread(target=update_log, daemon=True)
    register_thread(thread)
    thread.start()

def _utils() -> None:
    return _run_navigation(_render_utils)

def _render_utils() -> None:
    if _set_frame(t("nav.utils"), "Home") is None:
        return
    clear("menu")
    with use_scope("menu"):
        _menu_button(t("nav.home"), _home)
        _menu_button(t("nav.updater"), _updater)
        _menu_button(t("nav.utils"), _utils, True)
    clear("content")
    with use_scope("content"):
        put_scope("overview", [put_scope("util-buttons"), put_scope("logs")])
        client_call("dom.addClasses", scope="overview", classes=["overview"])
        with use_scope("util-buttons"):
            put_button(t("utils.raise_exception"), onclick=_raise_diagnostic_exception)
            put_button(t("utils.force_restart"), onclick=_force_restart)
            put_button(t("utils.shutdown"), onclick=_shutdown_palsitter)
            put_button(t("utils.run_all"), onclick=_run_all_instances)
            put_button(t("utils.stop_all"), onclick=_stop_all_instances)
            put_button(t("utils.kill_all"), onclick=_kill_all_instances)
            enable_eval = client_query("storage.get", key="DANGER_ENABLE_EVAL") or ""
            if enable_eval == "DO_NOT_PASTE_ANY_CODE_HERE_UNLESS_YOU_KNOW_WHAT_YOU_ARE_DOING":
                put_button(t("utils.run_code"), onclick=_run_utility_code)
        with use_scope("logs"):
            put_scope(
                "log-bar",
                [
                    put_scope(
                        "log-title",
                        [
                            put_asset_widget(
                                "shared.section_title",
                                {"classes": "utils-log-title", "title": t("log.title")},
                            ),
                            put_scope("log-title-btns", [put_scope("log_scroll_btn")]),
                        ],
                    ),
                    put_asset_widget("shared.horizontal_rule", {"classes": "hr-group"}),
                ],
            )
            put_scope(
                "dev-log",
                [put_asset_widget("shared.log_output", {"id": "utils-log-output"})],
            )
    local.utils_keep_bottom = True
    _update_utils_scroll_button()
    _start_utils_updates()
