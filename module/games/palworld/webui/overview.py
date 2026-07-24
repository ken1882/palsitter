from __future__ import annotations
import datetime as dt
import json
import re
import shutil
import threading
from pathlib import Path
from pywebio.exceptions import SessionException
from pywebio.input import input
from pywebio.output import (
    clear,
    close_popup,
    popup,
    put_button,
    put_row,
    put_scope,
    put_text,
    put_warning,
    toast,
    use_scope,
)
from pywebio.pin import pin, put_input
from pywebio.session import local, register_thread
from module.games.palworld.backup import BackupService
from module.instances import load_instance, load_runtime
from module.games.palworld.config import (
    fixed_executable_path,
    fixed_palserver_dir,
    load_profile,
)
from module.games.palworld.server import PalRestClient, get_pal_rest_cache
from module.games.palworld.audit import AuditEvent, AuditStore, utc_now
from module.games.palworld.server.status import endpoint_ports, endpoint_status, instance_is_running
from module.webui.i18n import t
from module.webui.session import page_context, register_page_cleanup, register_page_stop_event, run_if_current
from module.webui.assets import client_call, client_query, put_asset_widget
from module.webui.checkbox_groups import mount_checkbox_group


def _open_folder(*args, **kwargs):
    from module.games.palworld.webui.server_settings import _open_folder as implementation

    return implementation(*args, **kwargs)

def _action_detach(name: str) -> None:
    if _manager(name).detach():
        toast(t("actions.detached", name=name))
    _update_scheduler_controls(name)
    _set_status(_status_code(name))

def _dashboard_row(*args, **kwargs):
    from module.webui.instance import _dashboard_row as implementation
    return implementation(*args, **kwargs)

def _delete_instance(*args, **kwargs):
    from module.webui.instance import _delete_instance as implementation
    return implementation(*args, **kwargs)

def _manager(*args, **kwargs):
    from module.webui.instance import _manager as implementation
    return implementation(*args, **kwargs)

def _render_backup_files(*args, **kwargs):
    from module.games.palworld.webui.backups import _render_backup_files as implementation
    return implementation(*args, **kwargs)

def _render_backup_now_button(*args, **kwargs):
    from module.games.palworld.webui.backups import _render_backup_now_button as implementation
    return implementation(*args, **kwargs)

def _render_instance_menu(*args, **kwargs):
    from module.webui.instance import _render_instance_menu as implementation
    return implementation(*args, **kwargs)

def _render_players(*args, **kwargs):
    from module.games.palworld.webui.players import _render_players as implementation
    return implementation(*args, **kwargs)

def _save_world_now(name: str) -> None:
    if bool(getattr(local, "scheduler_save_busy", False)):
        return
    local.scheduler_save_busy = True
    _update_scheduler_save(name)

    def save() -> None:
        try:
            toast(t("actions.save_sent"))
            PalRestClient(load_profile(name)).save()
        except Exception as exc:
            toast(t("action.failed", action=t("actions.save_world"), error=exc), color="error")
        finally:
            local.scheduler_save_busy = False
            try:
                if client_query("dom.scopeExists", scope="scheduler_save"):
                    _update_scheduler_save(name)
            except SessionException:
                return

    thread = threading.Thread(target=save, daemon=True)
    register_thread(thread)
    thread.start()

def _set_frame(*args, **kwargs):
    from module.webui.instance import _set_frame as implementation
    return implementation(*args, **kwargs)

def _set_status(*args, **kwargs):
    from module.webui.instance import _set_status as implementation
    return implementation(*args, **kwargs)

def _status_code(*args, **kwargs):
    from module.webui.instance import _status_code as implementation
    return implementation(*args, **kwargs)

CONSOLE_COMMANDS = (
    ("announce <message>", "console.hint.announce"),
    ("ban <user_id> [message]", "console.hint.ban"),
    ("backup", "console.hint.backup"),
    ("info", "console.hint.info"),
    ("kick <user_id> [message]", "console.hint.kick"),
    ("metrics", "console.hint.metrics"),
    ("players", "console.hint.players"),
    ("restart", "console.hint.restart"),
    ("save", "console.hint.save"),
    ("shutdown <waittime> [message]", "console.hint.shutdown"),
    ("start", "console.hint.start"),
    ("stop", "console.hint.stop"),
    ("unban <user_id>", "console.hint.unban"),
)

LOG_TYPES = ("palsitter", "palserver", "steamcmd", "ue4ss")
LOW_DISK_SPACE_BYTES = 10 * 1024 * 1024 * 1024 # 10GB
_LOG_SOURCE_PATTERN = re.compile(
    r"^(?:\d{2}:\d{2}:\d{2} )?(?:\[[^\]\r\n]+\] )?(PalServer|SteamCMD|UE4SS):"
)
_LOG_SOURCE_TYPES = {
    "PalServer": "palserver",
    "SteamCMD": "steamcmd",
    "UE4SS": "ue4ss",
}


def _log_type(line: str) -> str:
    match = _LOG_SOURCE_PATTERN.match(line)
    return _LOG_SOURCE_TYPES.get(match.group(1), "palsitter") if match else "palsitter"

def render(name: str) -> None:
    local.overview_keep_bottom = True
    _auto_attach_running_server(name)
    with use_scope("content"):
        put_scope("overview", [
            put_scope("scheduler"),
            put_scope("scheduler_log"),
        ])
        client_call("dom.addClasses", scope="overview", classes=["overview"])
        client_call("dom.addClasses", scope="scheduler_log", classes=["scheduler-log"])
        _render_scheduler(name)
        _render_players(name)
        _render_log_area(name)
    _start_overview_updates(name)
    register_page_cleanup(lambda: client_call("palworld.overview.destroy"))


def _auto_attach_running_server(name: str) -> None:
    manager = _manager(name)
    if manager.active or manager.operation_busy:
        return
    if instance_is_running(load_profile(name)):
        # A GUI replacement has no live ProcessManager state.  Preserve the
        # ownership recorded by the previous supervisor so a running agent is
        # adopted (and its raw output tailer is started) instead of being
        # treated as an external watcher.
        runtime = load_runtime(name) or {}
        manager.start(
            update=False,
            reason="automatic attach",
            adopt_managed=runtime.get("ownership") == "managed",
        )


def _instance(name: str) -> None:
    from module.webui.instance import open_instance
    open_instance(name)

def _render_scheduler(name: str) -> None:
    profile = load_profile(name)
    clear("scheduler")
    with use_scope("scheduler"):
        folder_button = put_asset_widget(
            "palworld.backup_icon_button",
            {"label": t("scheduler.open_folder"), "glyph": "📁", "folder": True},
        ).onclick(lambda: _open_scheduler_folder(name))
        put_scope(
            "scheduler_panel",
            [
                put_scope(
                    "scheduler_title_row",
                    [put_row(
                        [
                            put_asset_widget("shared.panel_title", {"title": t("scheduler.title")}),
                            folder_button,
                            None,
                        ],
                        size="auto auto 1fr",
                    )],
                ),
                put_row(
                    [
                        put_scope("scheduler_toggle"),
                        put_scope("scheduler_save"),
                        put_scope("scheduler_backup"),
                    ],
                    size="1fr auto auto",
                ),
                put_scope("scheduler_maintenance"),
                put_scope("scheduler_endpoints"),
            ],
        )
        client_call("dom.addClasses", scope="scheduler_panel", classes=["panel", "scheduler-actions"])
    _update_scheduler_controls(name)
    _update_scheduler_backup(name)
    _update_scheduler_endpoints(name)


def _open_scheduler_folder(name: str) -> None:
    folder = fixed_palserver_dir(name)
    if not folder.is_dir() or not fixed_executable_path(name).is_file():
        toast(t("scheduler.server_not_installed"), color="error")
        return
    try:
        _open_folder(folder)
    except Exception as exc:
        toast(t("scheduler.open_failed", error=exc), color="error")

@use_scope("scheduler_backup", clear=True)
def _update_scheduler_backup(name: str, disabled: bool = False) -> None:
    put_button(t("scheduler.backup"), onclick=lambda: _backup_now(name), disabled=disabled)

@use_scope("scheduler_save", clear=True)
def _update_scheduler_save(name: str) -> None:
    busy = bool(getattr(local, "scheduler_save_busy", False))
    put_button(
        t("scheduler.save"),
        onclick=lambda: _save_world_now(name),
        disabled=busy or _manager(name).state != "running",
    )

@use_scope("scheduler_toggle", clear=True)
def _update_scheduler_toggle(name: str) -> None:
    manager = _manager(name)
    if manager.operation_busy:
        if _can_stop_update(manager):
            put_button(t("scheduler.stop"), onclick=lambda: _toggle_server(name), color="danger")
        else:
            put_button(t("scheduler.start"), onclick=lambda: None, disabled=True)
    elif manager.state in ("stopping", "killing") and manager.ownership == "managed":
        put_button(t("scheduler.kill"), onclick=lambda: _toggle_server(name), color="danger")
    elif manager.state in ("stopping", "killing"):
        put_button(
            t("scheduler.stop"),
            onclick=lambda: None,
            disabled=True,
            color="danger",
        )
    elif manager.active:
        put_button(t("scheduler.stop"), onclick=lambda: _toggle_server(name), color="danger")
    else:
        put_button(t("scheduler.start"), onclick=lambda: _toggle_server(name), color="success")


def _can_stop_update(manager) -> bool:
    operation = manager.operation_progress
    return (
        manager.state in ("installing", "updating")
        and operation is not None
        and operation.kind in ("install", "update", "validate")
        and operation.phase not in ("preparing", "complete", "failed")
    )


@use_scope("scheduler_maintenance", clear=True)
def _update_scheduler_maintenance(name: str) -> None:
    manager = _manager(name)
    buttons = []
    if manager.state == "running" and manager.ownership == "external":
        buttons.append(
            put_button(
                t("actions.detach"),
                onclick=lambda: _action_detach(name),
                color="secondary",
            )
        )
    if buttons:
        put_row(buttons, size=" ".join("auto" for _ in buttons))

@use_scope("scheduler_endpoints", clear=True)
def _update_scheduler_endpoints(name: str, statuses: dict[str, str] | None = None) -> None:
    values = statuses or {"udp": "unknown", "rest": "unknown", "rcon": "unknown"}
    ports = endpoint_ports(load_profile(name))
    put_asset_widget(
        "palworld.scheduler_endpoints",
        {
            "endpoints": [
                {
                    "key": key,
                    "label": t(f"scheduler.{key}_status"),
                    "status": t(f"endpoint.{values[key]}"),
                    "port": ports[key],
                }
                for key in ("udp", "rest", "rcon")
            ]
        },
    )

def _update_scheduler_controls(name: str) -> None:
    _update_scheduler_toggle(name)
    _update_scheduler_save(name)
    _update_scheduler_maintenance(name)

def _toggle_server(name: str) -> None:
    manager = _manager(name)
    if manager.state in ("stopping", "killing"):
        manager.kill()
        toast(t("action.killed", name=name))
    elif manager.active:
        manager.stop()
        toast(t("action.stop_requested", name=name))
    else:
        _confirm_low_disk_start(name)
    _update_scheduler_controls(name)
    _set_status(_status_code(name))


def _disk_usage_path(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _available_disk_space(path: Path) -> int | None:
    try:
        return shutil.disk_usage(_disk_usage_path(path)).free
    except OSError:
        return None


def _start_server(name: str) -> None:
    _manager(name).start()
    toast(t("action.started", name=name))


def _continue_low_disk_start(name: str) -> None:
    close_popup()
    _start_server(name)
    _update_scheduler_controls(name)
    _set_status(_status_code(name))


def _confirm_low_disk_start(name: str) -> None:
    available = _available_disk_space(fixed_palserver_dir(name))
    if available is None or available >= LOW_DISK_SPACE_BYTES:
        _start_server(name)
        return
    with popup(t("scheduler.low_disk_title"), closable=True):
        put_warning(t("scheduler.low_disk_warning"))
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(
                    t("scheduler.low_disk_continue"),
                    onclick=lambda: _continue_low_disk_start(name),
                    color="danger",
                ),
            ],
            size="auto auto",
        )

def _render_metrics(name: str, row: list[str] | None = None):
    update_info = _manager(name).update_info
    update_available = update_info.status == "update_available"
    update_tooltip = (
        t(
            "metrics.update_available",
            current_version=update_info.installed_build_id,
            new_version=update_info.available_build_id,
        )
        if update_available
        else ""
    )
    values = {
        "fps": row[4] if row else "-",
        "uptime": row[5] if row else "-",
        "memory": row[6] if row else "-",
        "days": row[8] if row else "-",
        "cpu": row[9] if row else "-",
        "game-version": row[10] if row else "-",
        "palbox": row[11] if row else "-",
    }
    items = [
        ("fps", t("metrics.fps")),
        ("uptime", t("metrics.uptime")),
        ("days", t("metrics.days")),
        ("cpu", t("metrics.cpu")),
        ("memory", t("metrics.memory")),
        ("game-version", t("metrics.game_version")),
        ("palbox", t("metrics.palbox")),
    ]
    return put_asset_widget(
        "palworld.metrics",
        {
            "metrics": [
                {
                    "key": key,
                    "value": str(values[key]),
                    "label": label,
                    "updateAvailable": key == "game-version" and update_available,
                    "updateTooltip": update_tooltip if key == "game-version" else "",
                }
                for key, label in items
            ]
        },
    )

def _render_log_area(name: str) -> None:
    clear("scheduler_log")
    with use_scope("scheduler_log"):
        put_scope(
            "log_bar",
            [
                put_row(
                    [
                        put_asset_widget("shared.panel_title", {"title": t("log.title")}),
                        put_row(
                            [
                                put_scope("overview_log_check_update_btn"),
                                put_scope("overview_log_filter_btn"),
                                put_scope("overview_log_scroll_btn"),
                            ],
                            size="auto auto auto",
                        ),
                    ],
                    size="1fr auto",
                ),
                put_scope("metrics", [_render_metrics(name)]),
            ],
        )
        put_scope(
            "log_output",
            [put_asset_widget("palworld.log_box", {"loading": t("log.loading")})],
        )
        put_scope(
            "console_bar",
            [
                put_scope(
                    "console_input",
                    [
                        put_input("console_command", placeholder=t("console.placeholder")),
                        put_asset_widget(
                            "palworld.console_autocomplete",
                            {
                                "commands": [
                                    {
                                        "index": index,
                                        "verb": command.split(" ", 1)[0],
                                        "command": command,
                                        "hint": t(hint_key),
                                    }
                                    for index, (command, hint_key) in enumerate(CONSOLE_COMMANDS)
                                ]
                            },
                        ),
                    ],
                ),
                put_button(t("console.run"), onclick=lambda: _run_console(name)),
            ],
        )
        client_call("dom.addClasses", scope="log_bar", classes=["panel"])
        client_call("dom.addClasses", scope="console_bar", classes=["panel", "console-row"])
        client_call("dom.addClasses", scope="console_input", classes=["console-input"])
        _enable_console_input()
    _reset_overview_log_filter()
    _render_overview_check_update_button(name)
    _render_overview_log_filter_button()
    _update_overview_scroll_button()


def _reset_overview_log_filter() -> None:
    context = page_context()
    client_call(
        "palworld.overview.mountLog",
        types=list(LOG_TYPES),
        emptyText=t("log.empty"),
        generation=context.generation if context else None,
    )


@use_scope("overview_log_filter_btn", clear=True)
def _render_overview_log_filter_button() -> None:
    put_button(t("log.filter"), onclick=_open_overview_log_filter, color="secondary")


@use_scope("overview_log_check_update_btn", clear=True)
def _render_overview_check_update_button(name: str) -> None:
    manager = _manager(name)
    disabled = manager.operation_busy or manager.ownership == "external" or not manager.is_installed
    put_button(
        t("actions.check_update"),
        onclick=lambda: _check_update(name),
        color="secondary",
        disabled=disabled,
    )


def _check_update(name: str) -> None:
    manager = _manager(name)
    if manager.check_update(force=True):
        toast(t("actions.check_started"))
    _render_overview_check_update_button(name)


def _open_overview_log_filter() -> None:
    with popup(t("log.filter_title"), closable=True):
        put_asset_widget(
            "palworld.log_filter",
            {
                "select_all": t("common.select_all"),
                "select_none": t("common.select_none"),
                "types": [
                    {"key": log_type, "label": label}
                    for log_type, label in (
                        ("palsitter", "Palsitter"),
                        ("palserver", "PalServer"),
                        ("steamcmd", "SteamCMD"),
                        ("ue4ss", "UE4SS"),
                    )
                ]
            },
        )
        put_button(t("common.close"), onclick=close_popup, color="secondary")
    context = page_context()
    client_call("palworld.overview.mountFilter", generation=context.generation if context else None)
    mount_checkbox_group("overview-log-filter")


def _enable_console_input() -> None:
    context = page_context()
    client_call("palworld.overview.mountConsole", generation=context.generation if context else None)

@use_scope("overview_log_scroll_btn", clear=True)
def _update_overview_scroll_button() -> None:
    enabled = bool(getattr(local, "overview_keep_bottom", True))
    put_button(
        t("log.auto_scroll") if enabled else t("log.auto_scroll_off"),
        onclick=_toggle_overview_scroll,
        color="success" if enabled else "secondary",
    )

def _toggle_overview_scroll() -> None:
    enabled = not bool(getattr(local, "overview_keep_bottom", True))
    local.overview_keep_bottom = enabled
    _update_overview_scroll_button()
    if enabled:
        client_call("palworld.overview.scrollBottom")

def _update_log_output(lines: tuple[str, ...]) -> None:
    client_call(
        "palworld.overview.setLog",
        items=[{"text": line, "type": _log_type(line)} for line in lines],
        emptyText=t("log.empty"),
        keepBottom=bool(getattr(local, "overview_keep_bottom", True)),
    )

def _append_log_output(lines: tuple[str, ...], dropped_lines: int = 0) -> None:
    if not lines and dropped_lines <= 0:
        return
    client_call(
        "palworld.overview.appendLog",
        items=[{"text": line, "type": _log_type(line)} for line in lines],
        droppedLines=dropped_lines,
        keepBottom=bool(getattr(local, "overview_keep_bottom", True)),
    )

def _update_metrics_output(name: str, row: list[str]) -> None:
    update_info = _manager(name).update_info
    update_available = update_info.status == "update_available"
    update_tooltip = (
        t(
            "metrics.update_available",
            current_version=update_info.installed_build_id,
            new_version=update_info.available_build_id,
        )
        if update_available
        else ""
    )
    values = {
        "fps": row[4],
        "uptime": row[5],
        "memory": row[6],
        "days": row[8],
        "cpu": row[9],
        "game-version": row[10],
        "palbox": row[11],
    }
    client_call(
        "palworld.overview.updateMetrics",
        values={key: str(value) for key, value in values.items()},
        updateAvailable=update_available,
        updateTooltip=update_tooltip,
    )

def _start_overview_updates(name: str) -> None:
    stop_event = threading.Event()
    register_page_stop_event(stop_event)
    context = page_context()
    manager = _manager(name)

    def update_log() -> None:
        last_lines: tuple[str, ...] | None = None
        last_state: str | None = None
        last_operation: tuple | None = None
        try:
            if stop_event.wait(0.25):
                return
            while not stop_event.is_set():
                lines = tuple(manager.logs[-300:])
                if lines != last_lines:
                    overlap = 0
                    if last_lines is not None:
                        for size in range(min(len(last_lines), len(lines)), 0, -1):
                            if last_lines[-size:] == lines[:size]:
                                overlap = size
                                break
                    if last_lines is None or (last_lines and not overlap):
                        run_if_current(context, lambda: _update_log_output(lines))
                    else:
                        run_if_current(
                            context,
                            lambda: _append_log_output(lines[overlap:], len(last_lines) - overlap),
                        )
                    last_lines = lines
                current_state = manager.display_state
                if current_state != last_state:
                    run_if_current(
                        context,
                        lambda: (
                            _set_status(_status_code(name)),
                            _update_scheduler_controls(name),
                        ),
                    )
                    last_state = current_state
                operation = manager.operation_progress
                operation_signature = (
                    manager.state,
                    manager.ownership,
                    manager.operation_busy,
                    manager.is_installed,
                    getattr(operation, "kind", None),
                    getattr(operation, "phase", None),
                    getattr(operation, "percent", None),
                    getattr(operation, "message", None),
                    getattr(operation, "error", None),
                )
                if operation_signature != last_operation:
                    run_if_current(
                        context,
                        lambda: (
                            _update_scheduler_controls(name),
                            _render_overview_check_update_button(name),
                        ),
                    )
                    last_operation = operation_signature
                stop_event.wait(1)
        except SessionException:
            return

    def update_metrics() -> None:
        try:
            while not stop_event.is_set():
                row = _dashboard_row(name)
                if stop_event.is_set():
                    return
                run_if_current(context, lambda: _update_metrics_output(name, row))
                stop_event.wait(3)
        except SessionException:
            return

    def update_endpoints() -> None:
        startup_probes_remaining = 10
        try:
            while not stop_event.is_set():
                profile = load_profile(name)
                running = instance_is_running(profile)
                statuses = endpoint_status(
                    profile,
                    process_running=running,
                )
                if stop_event.is_set():
                    return
                run_if_current(context, lambda: _update_scheduler_endpoints(name, statuses))
                ready = statuses.get("udp") == "open" and statuses.get("rest") == "open"
                if not running:
                    startup_probes_remaining = 10
                elif ready:
                    startup_probes_remaining = 0
                delay = 1 if running and startup_probes_remaining > 0 else 10
                if delay == 1:
                    startup_probes_remaining -= 1
                if stop_event.wait(delay):
                    return
        except SessionException:
            return

    for target in (update_log, update_metrics, update_endpoints):
        thread = threading.Thread(target=target, daemon=True)
        register_thread(thread)
        thread.start()

def _run_console(name: str) -> None:
    command = str(pin.console_command or "").strip()
    if not command:
        return
    client_call("palworld.overview.clearConsole")
    manager = _manager(name)
    manager.append_log(f"> {command}")
    try:
        AuditStore(name).append(
            AuditEvent(utc_now(), "palsitter_command", f"Executed: {command}")
        )
    except OSError as exc:
        manager.append_log(f"Could not persist audit event: {exc}")
    verb, _, arg = command.partition(" ")
    verb = verb.lower()
    client = PalRestClient(load_profile(name))
    rest_snapshot = get_pal_rest_cache(name).snapshot()
    try:
        if verb == "announce":
            if not arg:
                manager.append_log(t("console.usage", usage="announce <message>"))
                return
            manager.append_log(t("console.announcement_sent"))
            client.announce(arg)
        elif verb in {"kick", "ban"}:
            userid, _, message = arg.partition(" ")
            if not userid:
                manager.append_log(t("console.usage", usage=f"{verb} <user_id> [message]"))
                return
            manager.append_log(t("action.sent", action=t(f"players.{verb}")))
            getattr(client, verb)(userid, message)
        elif verb == "unban":
            if not arg:
                manager.append_log(t("console.usage", usage="unban <user_id>"))
                return
            manager.append_log(t("action.sent", action=t("players.unban")))
            client.unban(arg)
        elif verb == "info":
            manager.append_log(
                json.dumps(rest_snapshot.info or {}, ensure_ascii=False, sort_keys=True)
            )
        elif verb == "metrics":
            manager.append_log(
                json.dumps(rest_snapshot.metrics or {}, ensure_ascii=False, sort_keys=True)
            )
        elif verb == "players":
            result = rest_snapshot.players or {"players": []}
            safe_players = [
                {
                    key: player[key]
                    for key in ("name", "userId", "playerId", "level", "ping")
                    if key in player
                }
                for player in result.get("players", [])
                if isinstance(player, dict)
            ]
            manager.append_log(json.dumps({"players": safe_players}, ensure_ascii=False, sort_keys=True))
        elif verb == "save":
            manager.append_log(t("console.save_requested"))
            client.save()
        elif verb == "shutdown":
            waittime_text, _, message = arg.partition(" ")
            if not waittime_text:
                manager.append_log(t("console.usage", usage="shutdown <waittime> [message]"))
                return
            try:
                waittime = int(waittime_text)
            except ValueError:
                manager.append_log(t("console.usage", usage="shutdown <waittime> [message]"))
                return
            manager.append_log(t("console.shutdown_requested"))
            client.shutdown(waittime=waittime, message=message or "Server will shutdown immediately")
        elif verb == "stop":
            _stop(name)
            return
        elif verb == "backup":
            _backup_now(name)
            return
        elif verb == "start":
            _start(name)
            return
        elif verb == "restart":
            _restart(name)
            return
        else:
            manager.append_log(t("console.unknown", command=verb))
    except Exception as exc:
        manager.append_log(t("console.failed", error=exc))

def _start(name: str) -> None:
    _manager(name).start()
    toast(t("action.started", name=name))
    _instance(name)

def _stop(name: str) -> None:
    _manager(name).stop()
    toast(t("action.stopped", name=name))
    _instance(name)

def _restart(name: str) -> None:
    _manager(name).restart()
    toast(t("action.restarted", name=name))
    _instance(name)

def _backup_now(name: str) -> None:
    from module.webui.shutdown import is_shutting_down

    if is_shutting_down():
        _manager(name).append_log("Backup rejected: Palsitter is shutting down")
        return
    if client_query("dom.scopeExists", scope="scheduler_backup"):
        _update_scheduler_backup(name, disabled=True)
    if client_query("dom.scopeExists", scope="backup_now_button"):
        _render_backup_now_button(name, disabled=True)
    task = threading.Thread(target=lambda: _run_backup_now(name), daemon=True)
    register_thread(task)
    task.start()

def _run_backup_now(name: str) -> None:
    from module.webui.shutdown import is_shutting_down

    if is_shutting_down():
        return
    profile = load_profile(name)
    try:
        result = BackupService(
            profile,
            logger=_manager(name).append_log,
            rest_client=PalRestClient(profile),
        ).create_backup_with_flush()
        backup = result.backup
        if result.status == "skipped" or backup is None or backup.path is None:
            toast(t("action.backup_skipped"))
        elif result.may_be_stale:
            toast(
                t(
                    "action.backup_created_stale",
                    name=backup.path.name,
                    error=result.flush_error or "unknown",
                ),
                color="warning",
            )
        else:
            toast(t("action.backup_created", name=backup.path.name))
    except Exception as exc:
        toast(t("action.backup_failed", error=exc), color="error")
    if client_query("dom.scopeExists", scope="scheduler_panel"):
        _update_scheduler_backup(name)
    if client_query("dom.scopeExists", scope="backup_settings_panel"):
        _render_backup_now_button(name)
        _render_backup_files(name)

def _rest_action(name: str, action: str) -> None:
    profile = load_profile(name)
    try:
        client = PalRestClient(profile)
        getattr(client, action)()
        toast(t("action.sent", action=action))
    except Exception as exc:
        toast(t("action.failed", action=action, error=exc), color="error")
    _instance(name)

def _announce(name: str) -> None:
    message = input(t("dialog.announcement_message"), required=True)
    profile = load_profile(name)
    try:
        PalRestClient(profile).announce(message)
        toast(t("action.announcement_sent"))
    except Exception as exc:
        toast(t("action.announcement_failed", error=exc), color="error")
    _instance(name)

def _shutdown(name: str) -> None:
    profile = load_profile(name)
    try:
        PalRestClient(profile).shutdown()
        toast(t("action.shutdown_requested"))
    except Exception as exc:
        toast(t("action.shutdown_failed", error=exc), color="error")
    _instance(name)
