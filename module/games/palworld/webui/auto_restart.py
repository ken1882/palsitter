from __future__ import annotations

import datetime as dt
import json
import threading

from pywebio.exceptions import SessionException
from pywebio.output import (
    clear,
    put_button,
    put_row,
    put_scope,
    put_table,
    put_text,
    put_warning,
    toast,
    use_scope,
)
from pywebio.pin import pin
from pywebio.session import local, register_thread

from module.games.palworld.config import PalworldProfile, load_profile, save_profile
from module.games.palworld.server import LifecycleEvent, RestartHistoryStore, TerminationInfo
from module.games.palworld.webui.forms import (
    _settings_field,
    _settings_select,
    _settings_toggle,
    _validate_settings_form,
)
from module.webui.forms import _clear_dirty_form, _register_dirty_form, _settings_label
from module.webui.i18n import t
from module.webui.session import register_stop_event
from module.webui.assets import client_call, put_asset_widget


Profile = PalworldProfile
AUTO_RESTART_FIELDS = {
    "memory_restart_mb",
    "memory_restart_countdown_minutes",
    "self_heal_trigger_frame_minutes",
    "self_heal_trigger_crash_times",
    "crash_restart_limit_per_hour",
    "planned_restart_mode",
    "planned_restart_interval_hours",
    "planned_restart_daily_time",
    "planned_restart_countdown_minutes",
}


def _manager(name: str):
    from module.webui.instance import _manager as implementation

    return implementation(name)


def _settings_pin(key: str) -> str:
    return f"settings_{key}"


def _category(label: str) -> None:
    put_asset_widget("shared.settings_category", {"label": label})


def render(name: str) -> None:
    profile = load_profile(name)
    local.settings_toggles = {
        "restart_on_crash": profile.restart_on_crash,
        "self_heal_enabled": profile.self_heal_enabled,
    }
    clear("content")
    with use_scope("content"):
        put_scope(
            "auto_restart_panel",
            [
                put_asset_widget("shared.panel_title", {"title": t("auto_restart.title")}),
                put_asset_widget("shared.quiet_paragraph", {"text": t("auto_restart.description")}),
                put_scope("auto_restart_form"),
                put_scope("auto_restart_actions"),
            ],
        )
        put_scope("restart_history")
        client_call("dom.addClasses", scope="auto_restart_panel", classes=["panel"])
        client_call("dom.addClasses", scope="auto_restart_form", classes=["settings-view"])
        client_call("dom.addClasses", scope="auto_restart_actions", classes=["settings-actions"])
        with use_scope("auto_restart_form"):
            _category(t("auto_restart.category_crash"))
            _settings_toggle(
                _settings_label("restart_on_crash"),
                "restart_on_crash",
                escape_label=False,
            )
            _settings_toggle(
                _settings_label("self_heal_enabled"),
                "self_heal_enabled",
                escape_label=False,
            )
            _settings_field(
                _settings_label("self_heal_trigger_frame_minutes"),
                "self_heal_trigger_frame_minutes",
                profile.self_heal_trigger_frame_minutes,
                type="number",
                escape_label=False,
            )
            _settings_field(
                _settings_label("self_heal_trigger_crash_times"),
                "self_heal_trigger_crash_times",
                profile.self_heal_trigger_crash_times,
                type="number",
                escape_label=False,
            )
            _settings_field(
                _settings_label("crash_restart_limit_per_hour"),
                "crash_restart_limit_per_hour",
                profile.crash_restart_limit_per_hour,
                type="number",
                escape_label=False,
            )
            _category(t("auto_restart.category_memory"))
            _settings_field(
                _settings_label("memory_restart_mb"),
                "memory_restart_mb",
                profile.memory_restart_mb,
                type="number",
                escape_label=False,
            )
            _settings_field(
                _settings_label("memory_restart_countdown_minutes"),
                "memory_restart_countdown_minutes",
                profile.memory_restart_countdown_minutes,
                type="number",
                escape_label=False,
            )
            _category(t("auto_restart.category_planned"))
            _settings_select(
                _settings_label("planned_restart_mode"),
                "planned_restart_mode",
                profile.planned_restart_mode,
                [
                    {"label": t("settings.restart_off"), "value": "off"},
                    {"label": t("settings.restart_interval"), "value": "interval"},
                    {"label": t("settings.restart_daily"), "value": "daily"},
                ],
                escape_label=False,
            )
            _settings_field(
                _settings_label("planned_restart_interval_hours"),
                "planned_restart_interval_hours",
                profile.planned_restart_interval_hours,
                type="float",
                escape_label=False,
            )
            _settings_field(
                _settings_label("planned_restart_daily_time"),
                "planned_restart_daily_time",
                profile.planned_restart_daily_time,
                escape_label=False,
            )
            _settings_field(
                _settings_label("planned_restart_countdown_minutes"),
                "planned_restart_countdown_minutes",
                profile.planned_restart_countdown_minutes,
                type="number",
                escape_label=False,
            )
        with use_scope("auto_restart_actions"):
            put_row(
                [
                    put_asset_widget("shared.strong_text", {"text": t("form.unsaved_bar")}),
                    None,
                    put_button(
                        t("common.reset"),
                        onclick=lambda: _auto_restart(name),
                        color="secondary",
                    ),
                    put_button(
                        t("common.save"),
                        onclick=lambda: _save_auto_restart(name),
                        color="success",
                    ),
                ],
                size="auto 1fr auto auto",
            )
    _register_dirty_form(
        "pywebio-scope-auto_restart_panel",
        lambda: _save_auto_restart(name, rerender=False),
    )
    _render_restart_history(name)
    _start_history_updates(name)


def _auto_restart(name: str) -> None:
    from module.webui.instance import open_instance

    open_instance(name, "auto_restart")


def _save_auto_restart(name: str, *, rerender: bool = True) -> bool:
    profile = load_profile(name)
    data = profile.to_dict()
    for key in AUTO_RESTART_FIELDS:
        data[key] = getattr(pin, _settings_pin(key))
    if not _validate_settings_form(data, AUTO_RESTART_FIELDS):
        toast(t("validation.fix_errors"), color="error")
        return False
    data["restart_on_crash"] = bool(local.settings_toggles["restart_on_crash"])
    data["self_heal_enabled"] = bool(local.settings_toggles["self_heal_enabled"])
    if not data["restart_on_crash"]:
        data["self_heal_enabled"] = False
    data["name"] = name
    try:
        updated = Profile.from_dict(data)
        updated.to_game_config()
    except (TypeError, ValueError):
        toast(t("validation.fix_errors"), color="error")
        return False
    save_profile(updated)
    _clear_dirty_form()
    toast(t("settings.saved"))
    if rerender:
        _auto_restart(name)
    return True


def _next_restart(name: str, profile: Profile) -> dt.datetime | None:
    manager = _manager(name)
    scheduled = manager.next_scheduled_restart
    if scheduled is not None or not manager.active:
        return scheduled
    now = dt.datetime.now()
    if profile.planned_restart_mode == "interval" and manager.backup_schedule_started_at:
        return dt.datetime.fromtimestamp(manager.backup_schedule_started_at) + dt.timedelta(
            hours=float(profile.planned_restart_interval_hours)
        )
    if profile.planned_restart_mode == "daily":
        hour, minute = (int(part) for part in profile.planned_restart_daily_time.split(":"))
        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return scheduled + dt.timedelta(days=1) if scheduled <= now else scheduled
    return None


def _termination_label(info: TerminationInfo | None) -> str:
    if info is None:
        return "-"
    label = t(f"restart_history.cause.{info.summary_code}")
    codes = []
    if info.symbol:
        codes.append(info.symbol)
    if info.normalized_code:
        codes.append(info.normalized_code)
    if info.raw_exit_code is not None and str(info.raw_exit_code) != info.normalized_code:
        codes.append(str(info.raw_exit_code))
    return f"{label} ({' / '.join(codes)})" if codes else label


def _launch_error_text(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    summary = str(value.get("summary_code", "os_error"))
    os_error = value.get("os_error") or {}
    if not isinstance(os_error, dict):
        os_error = {}
    parts = [t(f"restart_history.cause.{summary}")]
    if os_error.get("winerror") is not None:
        parts.append(f"WinError {os_error['winerror']}")
    elif os_error.get("errno") is not None:
        parts.append(f"errno {os_error['errno']}")
    if os_error.get("filename"):
        parts.append(str(os_error["filename"]))
    return " · ".join(parts)


def _event_details(event: LifecycleEvent):
    detail = event.detail
    lines: list[str] = []
    if event.reason == "memory_threshold" and detail:
        lines.append(
            t(
                "restart_history.memory_detail",
                observed=detail.get("observed_rss_mb", "-"),
                threshold=detail.get("threshold_mb", "-"),
                samples=detail.get("sustained_samples", "-"),
            )
        )
    if event.reason == "planned_restart" and detail.get("due_at"):
        lines.append(
            t(
                "restart_history.planned_detail",
                mode=detail.get("mode", "-"),
                due=detail.get("due_at", "-"),
            )
        )
    if detail.get("rollback"):
        lines.append(t("restart_history.rollback_detail", detail=detail["rollback"]))
    if detail.get("save_outcome"):
        lines.append(t("restart_history.save_detail", detail=detail["save_outcome"]))
    launch_error = _launch_error_text(detail.get("restart_error"))
    if launch_error:
        lines.append(launch_error)
    output = ""
    if event.termination and event.termination.diagnostic_output:
        output = "\n".join(event.termination.diagnostic_output)
    return put_asset_widget(
        "palworld.restart_event_details",
        {
            "lines": [{"text": line} for line in lines],
            "has_output": bool(output),
            "output_label": t("restart_history.final_output"),
            "output": output,
            "empty": not lines and not output,
        },
    )


@use_scope("restart_history", clear=True)
def _render_restart_history(name: str) -> None:
    profile = load_profile(name)
    manager = _manager(name)
    next_restart = _next_restart(name, profile)
    put_asset_widget("shared.panel_title", {"title": t("restart_history.title")})
    put_asset_widget(
        "palworld.restart_history_intro",
        {
            "limitations": t("restart_history.limitations"),
            "next_label": t("reliability.next_restart"),
            "next": next_restart.strftime("%Y-%m-%d %H:%M:%S") if next_restart else "-",
        },
    )
    if manager.ownership == "external" and profile.planned_restart_mode != "off":
        put_warning(t("reliability.external_skip"))
    try:
        events = RestartHistoryStore(name).load()
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        put_warning(t("restart_history.load_failed", error=exc))
        client_call("dom.addClasses", scope="restart_history", classes=["panel", "restart-history"])
        return
    if not events:
        put_text(t("restart_history.empty"))
    else:
        put_table(
            [
                [
                    event.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    t(f"restart_history.trigger.{event.reason}"),
                    _termination_label(event.termination),
                    t(f"restart_history.outcome.{event.outcome}"),
                    _event_details(event),
                ]
                for event in reversed(events)
            ],
            header=[
                t("restart_history.timestamp"),
                t("restart_history.trigger"),
                t("restart_history.cause"),
                t("restart_history.outcome"),
                t("restart_history.details"),
            ],
        )
    client_call("dom.addClasses", scope="restart_history", classes=["panel", "restart-history"])


def _start_history_updates(name: str) -> None:
    stop_event = threading.Event()
    register_stop_event(stop_event)
    path = RestartHistoryStore(name).path

    def refresh() -> None:
        previous = None
        try:
            while not stop_event.wait(1):
                try:
                    stat = path.stat()
                    file_signature = (stat.st_mtime_ns, stat.st_size)
                except OSError:
                    file_signature = None
                manager = _manager(name)
                signature = (
                    file_signature,
                    manager.next_scheduled_restart,
                    manager.ownership,
                    manager.active,
                )
                if signature != previous:
                    _render_restart_history(name)
                    previous = signature
        except (SessionException, FileNotFoundError):
            return

    thread = threading.Thread(target=refresh, daemon=True)
    register_thread(thread)
    thread.start()


__all__ = ["render"]
