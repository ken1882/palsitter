from __future__ import annotations
import re
from pywebio.output import put_button, put_row, put_scope, use_scope
from pywebio.pin import pin, put_input, put_select, put_textarea
from pywebio.session import local
from module.games.palworld.config import DEDICATED_SERVER_NAME_RE
from module.webui.i18n import t
from module.webui.assets import client_call

def _browse_normalize_path(*args, **kwargs):
    from module.webui.file_browser import _browse_normalize_path as implementation
    return implementation(*args, **kwargs)

def _clear_field_errors(*args, **kwargs):
    from module.webui.forms import _clear_field_errors as implementation
    return implementation(*args, **kwargs)

def _field_error_scope(*args, **kwargs):
    from module.webui.forms import _field_error_scope as implementation
    return implementation(*args, **kwargs)

def _mark_dirty_form(*args, **kwargs):
    from module.webui.forms import _mark_dirty_form as implementation
    return implementation(*args, **kwargs)

def _open_browse(*args, **kwargs):
    from module.webui.file_browser import open_browser as implementation
    return implementation(*args, **kwargs)

def _settings_field_row(*args, **kwargs):
    from module.webui.forms import _settings_field_row as implementation
    return implementation(*args, **kwargs)

def _settings_pin(key: str) -> str:
    return f"settings_{key}"

def _show_field_error(*args, **kwargs):
    from module.webui.forms import _show_field_error as implementation
    return implementation(*args, **kwargs)

SETTINGS_TOGGLE_DEPENDENTS = {"restart_on_crash": ["self_heal_enabled"]}
SETTINGS_TOGGLE_PARENT = {
    "self_heal_enabled": "restart_on_crash",
    "auto_update": "update_on_start",
}
SETTINGS_FIELD_PARENT = {"auto_update_idle_minutes": "auto_update"}

SETTINGS_NUMERIC_FIELDS = {
    "query_port": int,
    "backup_interval_minutes": float,
    "backup_retention_count": int,
    "memory_restart_mb": int,
    "memory_restart_countdown_minutes": int,
    "self_heal_trigger_frame_minutes": int,
    "self_heal_trigger_crash_times": int,
    "crash_restart_limit_per_hour": int,
    "planned_restart_interval_hours": float,
    "planned_restart_countdown_minutes": int,
    "auto_update_idle_minutes": int,
}

BROWSE_FIELD_MODES = {
    "backup_dir": "dir",
}

def _settings_field(label: str, key: str, value, *, escape_label: bool = True, **kwargs) -> None:
    _settings_field_row(
        label,
        put_input(_settings_pin(key), value=value, **kwargs),
        error_scope=_field_error_scope(_settings_pin(key)),
        escape_label=escape_label,
    )

def _settings_textarea(label: str, key: str, value: str, *, escape_label: bool = True) -> None:
    _settings_field_row(
        label,
        put_textarea(_settings_pin(key), value=value, rows=4),
        error_scope=_field_error_scope(_settings_pin(key)),
        escape_label=escape_label,
    )

def _settings_select(
    label: str,
    key: str,
    value,
    options,
    *,
    escape_label: bool = True,
) -> None:
    _settings_field_row(
        label,
        put_select(_settings_pin(key), value=value, options=options),
        error_scope=_field_error_scope(_settings_pin(key)),
        escape_label=escape_label,
    )

def _settings_field_with_browse(label: str, key: str, value, *, escape_label: bool = True) -> None:
    _settings_field_row(
        label,
        put_row(
            [
                put_input(_settings_pin(key), value=value),
                None,
                put_button(
                    t("settings.browse"),
                    onclick=lambda: _open_browse(
                        _settings_pin(key),
                        mode=BROWSE_FIELD_MODES[key],
                        label=t(f"settings.{key}"),
                    ),
                    color="secondary",
                ),
            ],
            size="1fr .5rem auto",
        ),
        error_scope=_field_error_scope(_settings_pin(key)),
        escape_label=escape_label,
    )

def _settings_toggle_scope(key: str) -> str:
    return f"settings_toggle_{key}"

def _settings_toggle(label: str, key: str, *, escape_label: bool = True) -> None:
    _settings_field_row(label, put_scope(_settings_toggle_scope(key)), escape_label=escape_label)
    _render_settings_toggle(key)

def _render_settings_toggle(key: str) -> None:
    with use_scope(_settings_toggle_scope(key), clear=True):
        parent = SETTINGS_TOGGLE_PARENT.get(key)
        if parent is not None and not _setting_enabled(parent):
            put_button(t("common.off"), onclick=lambda: None, color="secondary", disabled=True)
            return
        value = local.settings_toggles.get(key, False)
        if value:
            put_button(t("common.on"), onclick=lambda: _toggle_setting(key), color="success")
        else:
            put_button(t("common.off"), onclick=lambda: _toggle_setting(key), color="secondary")

def _toggle_setting(key: str) -> None:
    _mark_dirty_form()
    local.settings_toggles[key] = not local.settings_toggles.get(key, False)
    _render_settings_toggle(key)
    for dependent in SETTINGS_TOGGLE_DEPENDENTS.get(key, []):
        _render_settings_toggle(dependent)
    for dependent in SETTINGS_TOGGLE_PARENT:
        if SETTINGS_TOGGLE_PARENT[dependent] == key:
            _render_settings_toggle(dependent)
    for field in SETTINGS_FIELD_PARENT:
        _render_dependent_field(field)


def _setting_enabled(key: str) -> bool:
    parent = SETTINGS_TOGGLE_PARENT.get(key)
    if parent is not None and not _setting_enabled(parent):
        return False
    return bool(local.settings_toggles.get(key, False))


def _settings_dependent_field(
    label: str,
    key: str,
    value,
    *,
    escape_label: bool = True,
    **kwargs,
) -> None:
    _settings_field_row(
        label,
        put_input(_settings_pin(key), value=value, **kwargs),
        error_scope=_field_error_scope(_settings_pin(key)),
        escape_label=escape_label,
    )
    _render_dependent_field(key)


def _render_dependent_field(key: str) -> None:
    parent = SETTINGS_FIELD_PARENT[key]
    disabled = not _setting_enabled(parent)
    client_call(
        "dom.setControlDisabled",
        selector=f'input[name="{_settings_pin(key)}"]',
        disabled=disabled,
    )

def _path_validation_error(value: str, mode: str) -> str | None:
    try:
        path = _browse_normalize_path(str(value or ""))
    except (OSError, ValueError):
        return t("validation.path_invalid")
    try:
        if not path.exists():
            return t("validation.path_missing")
        if mode == "dir" and not path.is_dir():
            return t("validation.path_not_dir")
        if mode == "file" and not path.is_file():
            return t("validation.path_not_file")
    except OSError:
        return t("validation.path_unavailable")
    return None

def _validate_settings_form(data: dict, fields: set[str] | None = None) -> bool:
    valid = True
    pin_names = [_settings_pin(key) for key in data if hasattr(pin, _settings_pin(key))]
    pin_names.extend(_settings_pin(key) for key in BROWSE_FIELD_MODES)
    _clear_field_errors(list(dict.fromkeys(pin_names)))
    for key, caster in SETTINGS_NUMERIC_FIELDS.items():
        if fields is not None and key not in fields:
            continue
        value = data.get(key)
        try:
            caster(value)
        except (TypeError, ValueError):
            _show_field_error(_settings_pin(key), t("validation.number_required"))
            valid = False
    for key, mode in BROWSE_FIELD_MODES.items():
        if fields is not None and key not in fields:
            continue
        error = _path_validation_error(data.get(key, ""), mode)
        if error is not None:
            _show_field_error(_settings_pin(key), error)
            valid = False
    dedicated_name = str(data.get("dedicated_server_name", ""))
    if (fields is None or "dedicated_server_name" in fields) and not DEDICATED_SERVER_NAME_RE.fullmatch(dedicated_name):
        _show_field_error(
            _settings_pin("dedicated_server_name"),
            t("validation.dedicated_server_name"),
        )
        valid = False
    worker_threads = str(data.get("launch_worker_threads_server", "") or "").strip()
    if fields is None or "launch_worker_threads_server" in fields:
        if worker_threads:
            try:
                if int(worker_threads) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                _show_field_error(
                    _settings_pin("launch_worker_threads_server"),
                    t("validation.positive_integer"),
                )
                valid = False
    if fields is None or "memory_restart_mb" in fields:
        try:
            if int(data.get("memory_restart_mb")) < 0:
                raise ValueError
        except (TypeError, ValueError):
            _show_field_error(
                _settings_pin("memory_restart_mb"), t("validation.nonnegative_integer")
            )
            valid = False
    if fields is None or "crash_restart_limit_per_hour" in fields:
        try:
            if int(data.get("crash_restart_limit_per_hour")) < 1:
                raise ValueError
        except (TypeError, ValueError):
            _show_field_error(
                _settings_pin("crash_restart_limit_per_hour"),
                t("validation.positive_integer"),
            )
            valid = False
    if fields is None or "auto_update_idle_minutes" in fields:
        try:
            if int(data.get("auto_update_idle_minutes")) < 1:
                raise ValueError
        except (TypeError, ValueError):
            _show_field_error(
                _settings_pin("auto_update_idle_minutes"),
                t("validation.positive_integer"),
            )
            valid = False
    for key in ("self_heal_trigger_frame_minutes", "self_heal_trigger_crash_times"):
        if fields is not None and key not in fields:
            continue
        try:
            if int(data.get(key)) < 1:
                raise ValueError
        except (TypeError, ValueError):
            _show_field_error(
                _settings_pin(key),
                t("validation.positive_integer"),
            )
            valid = False
    mode = str(data.get("planned_restart_mode", "off"))
    if mode == "interval":
        try:
            if float(data.get("planned_restart_interval_hours")) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            _show_field_error(
                _settings_pin("planned_restart_interval_hours"),
                t("validation.positive_number"),
            )
            valid = False
    if mode == "daily" and not re.fullmatch(
        r"(?:[01]\d|2[0-3]):[0-5]\d",
        str(data.get("planned_restart_daily_time", "")),
    ):
        _show_field_error(
            _settings_pin("planned_restart_daily_time"), t("validation.time_hhmm")
        )
        valid = False
    return valid
