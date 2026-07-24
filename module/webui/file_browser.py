from __future__ import annotations

import os
import queue
import re
import stat
import threading
import time
from pathlib import Path
from typing import Callable

from pywebio.exceptions import SessionException
from pywebio.output import (
    clear,
    close_popup,
    datatable_update,
    popup,
    put_button,
    put_datatable,
    put_row,
    put_scope,
    put_text,
    put_warning,
    toast,
    use_scope,
)
from pywebio.pin import pin, pin_on_change, pin_update, put_checkbox, put_input, put_select
from pywebio.session import local, register_thread

from module.webui.assets import client_call, client_query, put_asset_icon, put_asset_widget
from module.webui.i18n import t
from module.webui.session import page_context, register_page_cleanup, run_if_current

ENTRY_LIMIT = 500
BROWSE_TIMEOUT_SECONDS = 3
BROWSE_TABLE_ID = "server_file_picker"
BROWSE_ADDRESS_PIN = "browse_address_value"
BROWSE_ADDRESS_AUTOCOMPLETE_ID = "browse-address-autocomplete"
AUTOCOMPLETE_MAX_OPTIONS = 100
_WSL_MOUNT_PATH_RE = re.compile(r"^/mnt/([A-Za-z])(?:/(.*))?$")


def _safe_dom_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)


def _autocomplete_input(
    name: str,
    *,
    label: str,
    value: str,
    input_id: str,
) -> object:
    datalist_id = f"{input_id}-list"
    return put_scope(
        f"{_safe_dom_id(name)}_autocomplete",
        [
            put_input(name, label=label, value=value),
            put_asset_widget("shared.datalist", {"id": datalist_id}),
        ],
    )


def _autocomplete_register(
    name: str,
    *,
    input_id: str,
    suggestions: list[str],
    platform: str,
    current_dir: str,
    version: int,
    max_options: int = AUTOCOMPLETE_MAX_OPTIONS,
) -> None:
    datalist_id = f"{input_id}-list"
    home = Path.home()
    home_dir = (
        str(home)
        if getattr(local, "browse", {}).get("base_dir") is None
        or _browse_is_within(home, local.browse["base_dir"])
        else ""
    )
    client_call(
        "fileBrowser.register",
        name=name,
        inputId=input_id,
        datalistId=datalist_id,
        suggestions=suggestions,
        platform=platform,
        currentDir=current_dir,
        homeDir=home_dir,
        version=version,
        maxOptions=max_options,
    )


def _autocomplete_cleanup(input_id: str) -> None:
    client_call("fileBrowser.cleanup", inputId=input_id)

def _browse_normalize_path(value: str, *, require_absolute: bool = False) -> Path:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    if not text:
        raise ValueError("empty")
    if os.name == "nt":
        wsl_match = _WSL_MOUNT_PATH_RE.fullmatch(text)
        if wsl_match is not None:
            drive, tail = wsl_match.groups()
            text = f"{drive.upper()}:/{tail or ''}"
    candidate = Path(text).expanduser()
    if require_absolute and not candidate.is_absolute():
        raise ValueError("absolute")
    return Path(os.path.abspath(os.path.normpath(candidate)))


def _browse_resolve_start(value: str, base_dir: Path | None = None) -> Path:
    try:
        candidate = _browse_normalize_path(value, require_absolute=False)
        if candidate.is_file():
            candidate = candidate.parent
        if candidate.is_dir():
            if base_dir is None or _browse_is_within(candidate, base_dir):
                return candidate
    except (OSError, ValueError):
        pass
    return base_dir or Path.cwd()


def _browse_is_within(path: Path, base_dir: Path) -> bool:
    try:
        Path(os.path.realpath(path)).relative_to(Path(os.path.realpath(base_dir)))
        return True
    except (OSError, ValueError):
        return False


def _browse_is_hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith("."):
        return True
    if os.name != "nt":
        return False
    try:
        attributes = entry.stat(follow_symlinks=False).st_file_attributes
        return bool(attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))
    except (AttributeError, OSError):
        return False


def scan_directory(request: dict) -> dict:
    request_id = request["request_id"]
    target = request["target_path"]
    base_dir = request["base_dir"]
    try:
        path = _browse_normalize_path(target)
    except (OSError, ValueError) as exc:
        return {"request_id": request_id, "status": "invalid", "error": str(exc)}

    if base_dir is not None and not _browse_is_within(path, base_dir):
        return {"request_id": request_id, "status": "outside_base"}
    try:
        if not path.exists():
            return {"request_id": request_id, "status": "missing"}
        if not path.is_dir():
            return {"request_id": request_id, "status": "not_directory"}
        entries = []
        unreadable = 0
        with os.scandir(path) as children:
            for child in children:
                if not request["show_hidden"] and _browse_is_hidden(child):
                    continue
                if request["filter_text"] and request["filter_text"] not in child.name.casefold():
                    continue
                try:
                    is_symlink = child.is_symlink()
                    is_dir = child.is_dir(follow_symlinks=True)
                    is_file = child.is_file(follow_symlinks=True)
                except OSError:
                    unreadable += 1
                    continue
                if not is_dir and not is_file:
                    if is_symlink:
                        entries.append({"name": child.name, "kind": "broken"})
                    continue
                if is_file and request["allowed_extensions"]:
                    if Path(child.name).suffix.casefold() not in request["allowed_extensions"]:
                        continue
                if is_file and request.get("allowed_names"):
                    if child.name.casefold() not in request["allowed_names"]:
                        continue
                kind = "symlink_dir" if is_symlink and is_dir else "symlink_file" if is_symlink else "dir" if is_dir else "file"
                entries.append({"name": child.name, "kind": kind})
        entries.sort(key=lambda item: (0 if item["kind"] in {"dir", "symlink_dir"} else 1, item["name"].casefold()))
        total = len(entries)
        return {
            "request_id": request_id,
            "status": "success",
            "target_path": str(path),
            "entries": entries[:ENTRY_LIMIT],
            "total": total,
            "unreadable": unreadable,
        }
    except PermissionError:
        return {"request_id": request_id, "status": "permission"}
    except OSError:
        return {"request_id": request_id, "status": "unavailable"}


def _browse_locations(base_dir: Path | None = None) -> list[Path]:
    if base_dir is not None:
        return [base_dir]
    candidates: list[Path]
    if os.name == "nt":
        try:
            import ctypes

            mask = ctypes.windll.kernel32.GetLogicalDrives()
            candidates = [Path(f"{letter}:\\") for index, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ") if mask & (1 << index)]
        except (AttributeError, OSError):
            candidates = [Path(Path.cwd().anchor)]
    else:
        home = Path.home()
        candidates = [
            Path("/"),
            home,
            Path.cwd(),
            Path("/mnt"),
            Path("/media"),
            Path("/run/media") / home.name,
        ]
        candidates = [path for path in candidates if path.is_dir()]
    locations = []
    seen = set()
    for path in candidates:
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized not in seen:
            seen.add(normalized)
            locations.append(Path(os.path.abspath(path)))
    return locations


def _browse_platform() -> str:
    return "windows" if os.name == "nt" else "linux"


def _browse_suggestion_allowed(path: Path, base_dir: Path | None) -> bool:
    try:
        absolute = Path(os.path.abspath(path))
    except OSError:
        return False
    return base_dir is None or _browse_is_within(absolute, base_dir)


def _browse_suggestion_text(path: Path, *, is_dir: bool) -> str:
    text = str(Path(os.path.abspath(path)))
    if is_dir and not text.endswith(("/", "\\")):
        text += os.sep
    return text


def autocomplete_suggestions(state: dict) -> list[str]:
    base_dir = state["base_dir"]
    current_dir = state["current_dir"]
    suggestions: list[str] = []
    seen: set[str] = set()

    def add(path: Path, *, is_dir: bool) -> None:
        if not _browse_suggestion_allowed(path, base_dir):
            return
        text = _browse_suggestion_text(path, is_dir=is_dir)
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            return
        seen.add(key)
        suggestions.append(text)

    add(current_dir, is_dir=True)
    for location in _browse_locations(base_dir):
        add(location, is_dir=True)
    add(Path.home(), is_dir=True)
    add(Path.cwd(), is_dir=True)

    for entry in state.get("visible_entries", []):
        kind = entry["kind"]
        path = current_dir / entry["name"]
        if kind in {"dir", "symlink_dir"}:
            add(path, is_dir=True)
        elif state["mode"] == "file" and kind in {"file", "symlink_file"}:
            add(path, is_dir=False)
    return suggestions


def open_browser(
    pin_name: str,
    *,
    mode: str,
    label: str,
    base_dir: str | Path | None = None,
    allowed_extensions: tuple[str, ...] | None = None,
    allowed_names: tuple[str, ...] | None = None,
    on_close: Callable[[], None] | None = None,
) -> None:
    normalized_base = _browse_normalize_path(str(base_dir)) if base_dir is not None else None
    start = _browse_resolve_start(getattr(pin, pin_name), normalized_base)
    local.browse = {
        "picker_id": time.monotonic_ns(),
        "pin_name": pin_name,
        "mode": mode,
        "original_value": str(getattr(pin, pin_name) or ""),
        "current_dir": start,
        "previous_valid_dir": start,
        "address_value": str(start),
        "selected_entry": None,
        "selected_type": None,
        "entries": {},
        "show_hidden": False,
        "filter_text": "",
        "allowed_extensions": {
            extension.casefold() if extension.startswith(".") else f".{extension.casefold()}"
            for extension in (allowed_extensions or ())
        },
        "allowed_names": {name.casefold() for name in (allowed_names or ())},
        "on_close": on_close,
        "base_dir": normalized_base,
        "visible_entries": [],
        "latest_request_id": 0,
        "loading": False,
        "active": True,
        "worker_slots": threading.BoundedSemaphore(2),
        "lock": threading.Lock(),
        "page_context": page_context(),
    }
    state = local.browse
    register_page_cleanup(lambda: _browse_deactivate(state))
    with popup(t("settings.browse_title", field=label), size="large", closable=True) as scope:
        put_scope("browse_address", scope=scope)
        put_scope("browse_options", scope=scope)
        put_scope("browse_error", scope=scope)
        put_scope("browse_status", scope=scope)
        put_scope("browse_list", scope=scope)
        put_scope("browse_selected", scope=scope)
        put_scope("browse_actions", scope=scope)
        put_scope("browse_double_click", scope=scope)
        put_scope("browse_address_enter", scope=scope)
        put_scope("browse_address_check", scope=scope)
    with use_scope("browse_options"):
        put_row(
            [
                put_scope("browse_hidden"),
                None,
                put_input("browse_filter_value", label=t("settings.browse_filter"), value=""),
                None,
                put_button(t("settings.browse_apply_filter"), onclick=_browse_apply_filter, color="secondary"),
            ],
            size="auto .5rem 1fr .5rem auto",
        )
    with use_scope("browse_list"):
        put_datatable(
            [],
            onselect=_browse_on_select,
            id_field="row_id",
            height=360,
            theme="balham-dark",
            cell_content_bar=False,
            instance_id=BROWSE_TABLE_ID,
            column_order={"type": t("settings.browse_type"), "name": t("settings.browse_name")},
            column_args={"type": {"width": 150}, "name": {"flex": 1}},
            grid_args={"rowSelection": "single"},
        )
    with use_scope("browse_double_click"):
        put_button("Open", onclick=_browse_double_click)
    with use_scope("browse_address_enter"):
        put_button("Enter", onclick=_browse_confirm_address)
    with use_scope("browse_address_check"):
        put_button("Check", onclick=_browse_check_address)
    _browse_render_toolbar()
    _browse_render_hidden()
    _browse_render_selected()
    _browse_render_actions()
    client_call("fileBrowser.mountTable")
    _browse_request(start)


def _browse_type_label(kind: str) -> str:
    return t(f"settings.browse_kind_{kind}")


def _browse_table_rows(entries: list[dict], *, include_current: bool = False) -> tuple[list[dict], dict[str, dict]]:
    if include_current:
        entries = [{"name": ".", "kind": "current"}, *entries]
    rows = []
    mapping = {}
    for index, entry in enumerate(entries):
        row_id = str(index)
        mapping[row_id] = entry
        rows.append({"row_id": row_id, "type": _browse_type_label(entry["kind"]), "name": entry["name"]})
    return rows, mapping


def _browse_icon_button(icon: str, label: str, onclick, *, color: str, disabled: bool = False):
    button = put_asset_widget(
        "shared.icon_button",
        {
            "color": color,
            "classes": "picker-icon-button",
            "label": label,
            "disabled": disabled,
            "icon": [put_asset_icon(icon)],
        },
    )
    if not disabled:
        button.onclick(onclick)
    return button


def _browse_render_toolbar() -> None:
    state = local.browse
    path = state["current_dir"]
    address_value = state.get("address_value", str(path))
    at_boundary = path.parent == path or (
        state["base_dir"] is not None
        and os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(state["base_dir"]))
    )
    locations = _browse_locations(state["base_dir"])
    current_location = next(
        (
            location
            for location in sorted(locations, key=lambda item: len(str(item)), reverse=True)
            if _browse_is_within(path, location)
        ),
        locations[0],
    )
    location_map = {f"location_{index}": str(location) for index, location in enumerate(locations)}
    state["location_map"] = location_map
    current_location_token = next(
        (token for token, location in location_map.items() if location == str(current_location)),
        next(iter(location_map)),
    )
    location_options = [{"label": location, "value": token} for token, location in location_map.items()]
    suggestions = autocomplete_suggestions(state)
    with use_scope("browse_address", clear=True):
        put_row(
            [
                put_select(
                    "browse_location_value",
                    label=t("settings.browse_location"),
                    options=location_options,
                    value=current_location_token,
                ),
                None,
                _browse_icon_button(
                    "arrow-up",
                    t("settings.browse_up"),
                    _browse_up,
                    color="secondary",
                    disabled=at_boundary or state["loading"],
                ),
                None,
                _autocomplete_input(
                    BROWSE_ADDRESS_PIN,
                    label=t("settings.browse_address"),
                    value=address_value,
                    input_id=BROWSE_ADDRESS_AUTOCOMPLETE_ID,
                ),
                None,
                _browse_icon_button(
                    "arrow-right",
                    t("settings.browse_go"),
                    _browse_go,
                    color="primary",
                ),
                None,
                put_button(
                    t("settings.browse_refresh"),
                    onclick=_browse_refresh,
                    color="secondary",
                    disabled=state["loading"],
                ),
            ],
            size="7.5rem .5rem auto .5rem 1fr .5rem auto .5rem auto",
        )
    _autocomplete_register(
        BROWSE_ADDRESS_PIN,
        input_id=BROWSE_ADDRESS_AUTOCOMPLETE_ID,
        suggestions=suggestions,
        platform=_browse_platform(),
        current_dir=str(path),
        version=state["latest_request_id"],
    )
    pin_on_change("browse_location_value", onchange=_browse_location_changed, clear=True)


def _browse_render_hidden() -> None:
    state = local.browse
    label = t("settings.browse_hidden", state=t("common.on") if state["show_hidden"] else t("common.off"))
    with use_scope("browse_hidden", clear=True):
        put_button(label, onclick=_browse_toggle_hidden, color="success" if state["show_hidden"] else "secondary")


def _browse_render_selected() -> None:
    state = local.browse
    if state["selected_entry"] is not None:
        value = state["selected_entry"]
    elif state["mode"] == "dir":
        value = t("settings.browse_current_folder")
    else:
        value = t("settings.browse_no_file")
    with use_scope("browse_selected", clear=True):
        put_text(f"{t('settings.browse_selected')}: {value}")


def _browse_render_actions() -> None:
    state = local.browse
    primary = t("settings.browse_select") if state["mode"] == "dir" else t("settings.browse_open")
    with use_scope("browse_actions", clear=True):
        put_row(
            [
                put_button(primary, onclick=_browse_confirm, color="success", disabled=state["loading"]),
                None,
                put_button(t("common.cancel"), onclick=_browse_cancel, color="secondary"),
            ],
            size="auto .5rem auto",
        )


def _browse_show_error(status: str) -> None:
    key = {
        "invalid": "settings.browse_error_invalid",
        "missing": "settings.browse_error_missing",
        "not_directory": "settings.browse_error_not_directory",
        "permission": "settings.browse_error_permission",
        "unavailable": "settings.browse_error_unavailable",
        "outside_base": "settings.browse_error_outside_base",
        "timeout": "settings.browse_error_timeout",
        "busy": "settings.browse_error_busy",
        "selection_missing": "settings.browse_error_selection_missing",
        "select_file": "settings.browse_error_select_file",
        "select_folder": "settings.browse_error_select_folder",
    }[status]
    with use_scope("browse_error", clear=True):
        put_warning(t(key))


def _browse_request(
    path: str | Path,
    *,
    preserve_selection: bool = False,
    select_name: str | None = None,
) -> None:
    state = local.browse
    state["address_value"] = str(path)
    if not state["worker_slots"].acquire(blocking=False):
        _browse_show_error("busy")
        return
    with state["lock"]:
        state["latest_request_id"] += 1
        request_id = state["latest_request_id"]
        state["loading"] = True
    request = {
        "request_id": request_id,
        "target_path": str(path),
        "base_dir": state["base_dir"],
        "show_hidden": state["show_hidden"],
        "filter_text": state["filter_text"].casefold(),
        "allowed_extensions": state["allowed_extensions"],
        "allowed_names": state["allowed_names"],
    }
    result_queue = queue.Queue(maxsize=1)
    clear("browse_error")
    with use_scope("browse_status", clear=True):
        put_text(t("settings.browse_loading"))
    _browse_render_toolbar()
    _browse_render_actions()

    def scan() -> None:
        try:
            result_queue.put(scan_directory(request))
        finally:
            state["worker_slots"].release()

    def receive() -> None:
        try:
            result = result_queue.get(timeout=BROWSE_TIMEOUT_SECONDS)
        except queue.Empty:
            result = {"request_id": request_id, "status": "timeout"}
        try:
            _browse_apply_result(state, result, preserve_selection, select_name)
        except SessionException:
            return

    threading.Thread(target=scan, daemon=True).start()
    receiver = threading.Thread(target=receive, daemon=True)
    register_thread(receiver)
    receiver.start()


def _browse_deactivate(state: dict) -> None:
    state["active"] = False
    with state["lock"]:
        state["latest_request_id"] += 1
    _autocomplete_cleanup(BROWSE_ADDRESS_AUTOCOMPLETE_ID)
    if getattr(local, "browse", None) is state:
        close_popup()


def _browse_apply_result(
    state: dict,
    result: dict,
    preserve_selection: bool,
    select_name: str | None = None,
) -> None:
    return run_if_current(
        state.get("page_context"),
        lambda: _browse_apply_result_current(state, result, preserve_selection, select_name),
    )


def _browse_apply_result_current(
    state: dict,
    result: dict,
    preserve_selection: bool,
    select_name: str | None = None,
) -> None:
    if not state["active"] or getattr(local, "browse", None) is not state:
        return
    with state["lock"]:
        if result["request_id"] != state["latest_request_id"]:
            return
        state["loading"] = False
    if result["status"] != "success":
        clear("browse_status")
        _browse_show_error(result["status"])
        _browse_render_toolbar()
        _browse_render_actions()
        return

    rows, mapping = _browse_table_rows(result["entries"], include_current=state["mode"] == "dir")
    old_name = state["selected_entry"] if preserve_selection else None
    old_type = state["selected_type"] if preserve_selection else None
    state["previous_valid_dir"] = state["current_dir"]
    state["current_dir"] = Path(result["target_path"])
    state["address_value"] = str(state["current_dir"])
    state["visible_entries"] = result["entries"]
    state["entries"] = mapping
    state["selected_entry"] = None
    state["selected_type"] = None
    if old_name is not None:
        for entry in mapping.values():
            if entry["name"] == old_name and entry["kind"] == old_type:
                state["selected_entry"] = old_name
                state["selected_type"] = old_type
                break
    elif select_name is not None:
        target_key = select_name.casefold()
        for entry in mapping.values():
            if entry["kind"] in {"file", "symlink_file"} and entry["name"].casefold() == target_key:
                state["selected_entry"] = entry["name"]
                state["selected_type"] = entry["kind"]
                break
    pin_update("browse_address_value", value=str(state["current_dir"]))
    datatable_update(BROWSE_TABLE_ID, rows)
    clear("browse_error")
    with use_scope("browse_status", clear=True):
        if result["total"] > ENTRY_LIMIT:
            put_text(t("settings.browse_limited", shown=ENTRY_LIMIT, total=result["total"]))
        elif result["unreadable"]:
            put_text(t("settings.browse_unreadable", count=result["unreadable"]))
        elif not result["entries"]:
            put_text(t("settings.browse_empty"))
        else:
            put_text(t("settings.browse_count", count=result["total"]))
    _browse_render_toolbar()
    _browse_render_selected()
    _browse_render_actions()


def _browse_go() -> None:
    _browse_request(str(pin.browse_address_value or ""))


def _browse_location_changed(value: str) -> None:
    target = local.browse.get("location_map", {}).get(value, value)
    if target:
        _browse_request(target)


def _browse_up() -> None:
    state = local.browse
    _browse_request(state["current_dir"].parent)


def _browse_refresh() -> None:
    _browse_request(local.browse["current_dir"], preserve_selection=True)


def _browse_apply_filter() -> None:
    state = local.browse
    state["filter_text"] = str(pin.browse_filter_value or "").strip()
    _browse_request(state["current_dir"])


def _browse_toggle_hidden() -> None:
    state = local.browse
    state["show_hidden"] = not state["show_hidden"]
    _browse_render_hidden()
    _browse_request(state["current_dir"])


def _browse_on_select(row_id) -> None:
    state = local.browse
    entry = state["entries"].get(str(row_id))
    if entry is None:
        return
    state["selected_entry"] = entry["name"]
    state["selected_type"] = entry["kind"]
    clear("browse_error")
    _browse_render_selected()


def _browse_entry_path(state: dict, row_id: str | None = None) -> tuple[Path | None, str | None]:
    if row_id is not None:
        entry = state["entries"].get(str(row_id))
    else:
        entry = next(
            (
                item
                for item in state["entries"].values()
                if item["name"] == state["selected_entry"] and item["kind"] == state["selected_type"]
            ),
            None,
        )
    if entry is None:
        return None, None
    if entry["kind"] == "current":
        return state["current_dir"], entry["kind"]
    return state["current_dir"] / entry["name"], entry["kind"]


def _browse_double_click() -> None:
    state = local.browse
    if state["loading"]:
        return
    row_id = client_query("fileBrowser.doubleClickRow")
    path, kind = _browse_entry_path(state, str(row_id))
    if path is None:
        return
    if kind in {"dir", "symlink_dir"}:
        _browse_request(path)
    elif kind in {"file", "symlink_file"} and state["mode"] == "file":
        _browse_confirm_path(path)


def validate_selection(path: Path, mode: str, base_dir: Path | None) -> str | None:
    if base_dir is not None and not _browse_is_within(path, base_dir):
        return "outside_base"
    try:
        if not path.exists():
            return "selection_missing"
        if mode == "dir" and not path.is_dir():
            return "select_folder"
        if mode == "file" and not path.is_file():
            return "select_file"
        if not os.access(path, os.R_OK | (os.X_OK if mode == "dir" else 0)):
            return "permission"
    except OSError:
        return "unavailable"
    return None


def _browse_confirm() -> None:
    state = local.browse
    if state["loading"]:
        return
    if state["mode"] == "dir" and state["selected_entry"] is None:
        path = state["current_dir"]
    else:
        path, kind = _browse_entry_path(state)
        if path is None:
            _browse_show_error("select_file" if state["mode"] == "file" else "select_folder")
            return
        if state["mode"] == "dir" and kind not in {"current", "dir", "symlink_dir"}:
            _browse_show_error("select_folder")
            return
        if state["mode"] == "file" and kind not in {"file", "symlink_file"}:
            _browse_show_error("select_file")
            return
    _browse_confirm_path(path)


def _browse_file_matches_filter(name: str, state: dict) -> bool:
    if state["allowed_extensions"] and Path(name).suffix.casefold() not in state["allowed_extensions"]:
        return False
    if state["allowed_names"] and name.casefold() not in state["allowed_names"]:
        return False
    return True


def _browse_confirm_address() -> None:
    state = local.browse
    if state["loading"]:
        return
    try:
        path = _browse_normalize_path(str(pin.browse_address_value or ""))
    except (OSError, ValueError):
        _browse_restore_original_field(state)
        _browse_show_error("invalid")
        return

    if state["base_dir"] is not None and not _browse_is_within(path, state["base_dir"]):
        _browse_show_error("outside_base")
        return

    try:
        exists = path.exists()
        is_dir = exists and path.is_dir()
        is_file = exists and path.is_file()
    except OSError:
        _browse_show_error("unavailable")
        return

    if is_dir:
        _browse_request(path)
        return
    if is_file:
        if state["mode"] == "file" and _browse_file_matches_filter(path.name, state):
            _browse_request(path.parent, select_name=path.name)
        else:
            _browse_request(path.parent)
        return

    _browse_show_error("missing")


def _browse_check_address() -> None:
    state = local.browse
    if state["loading"] or not state["active"]:
        return
    try:
        path = _browse_normalize_path(str(pin.browse_address_value or ""))
    except (OSError, ValueError):
        _browse_show_error("invalid")
        return

    error = _browse_validate_address_path(path, state["base_dir"])
    if error is not None:
        _browse_show_error(error)
        return
    clear("browse_error")


def _browse_validate_address_path(path: Path, base_dir: Path | None) -> str | None:
    if base_dir is not None and not _browse_is_within(path, base_dir):
        return "outside_base"
    try:
        if not path.exists():
            return "missing"
        if not os.access(path, os.R_OK):
            return "permission"
    except OSError:
        return "unavailable"
    return None


def _browse_restore_original_field(state: dict) -> None:
    pin[state["pin_name"]] = state.get("original_value", "")


def _browse_confirm_path(path: Path) -> None:
    state = local.browse
    error = validate_selection(path, state["mode"], state["base_dir"])
    if error is not None:
        _browse_restore_original_field(state)
        _browse_show_error(error)
        return
    pin[state["pin_name"]] = str(Path(os.path.abspath(path)))
    state["selected_path"] = str(Path(os.path.abspath(path)))
    state["active"] = False
    _autocomplete_cleanup(BROWSE_ADDRESS_AUTOCOMPLETE_ID)
    close_popup()
    if state.get("on_close") is not None:
        state["on_close"]()


def _browse_cancel() -> None:
    state = local.browse
    state["active"] = False
    _autocomplete_cleanup(BROWSE_ADDRESS_AUTOCOMPLETE_ID)
    close_popup()
    if state.get("on_close") is not None:
        state["on_close"]()


__all__ = [
    "ENTRY_LIMIT",
    "autocomplete_suggestions",
    "open_browser",
    "scan_directory",
    "validate_selection",
]
