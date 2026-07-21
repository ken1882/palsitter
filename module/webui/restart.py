from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pywebio.exceptions import SessionException
from pywebio.output import clear, put_button, put_scope, use_scope
from pywebio.session import register_thread

from module.games import get_game
from module.instances import list_instances, load_instance
from module.webui.i18n import t
from module.webui.process_manager import ProcessManager
from module.webui.session import register_stop_event
from module.webui.assets import client_call, put_asset_widget


RESTART_PHASES = {"saving", "stopping", "detaching", "restarting_gui", "restoring", "completed", "failed"}
TERMINAL_PHASES = {"completed", "failed"}
RESTART_EXIT_CODE = 75
SHUTDOWN_TIMEOUT_SECONDS = 60

_STATE_LOCK = threading.RLock()
_WORKFLOW_LOCK = threading.Lock()
_WORKFLOW_THREAD: threading.Thread | None = None
_RESTORE_STARTED = False


def state_path() -> Path:
    from module.config import config_dir

    return config_dir() / "webui" / "restart-state.json"


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def load_state() -> dict[str, Any] | None:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) and data.get("phase") in RESTART_PHASES else None


def _write_state(state: dict[str, Any]) -> None:
    _atomic_write(state_path(), state)


def _update_state(**changes: Any) -> dict[str, Any] | None:
    with _STATE_LOCK:
        state = load_state()
        if state is None:
            return None
        state.update(changes)
        state["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        _write_state(state)
        return state


def _update_instance(name: str, **changes: Any) -> None:
    with _STATE_LOCK:
        state = load_state()
        if state is None:
            return
        instances = state.setdefault("instances", {})
        item = instances.setdefault(name, {})
        item.update(changes)
        state["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        _write_state(state)


def _snapshot() -> dict[str, dict[str, str]]:
    snapshot: dict[str, dict[str, str]] = {}
    for record in list_instances():
        adapter = get_game(record.game)
        if not adapter.capabilities.lifecycle:
            continue
        manager = ProcessManager.get(record.name)
        if not manager.active:
            continue
        snapshot[record.name] = {
            "game": record.game,
            "ownership": "external" if manager.ownership == "external" else "managed",
            "status": "pending",
        }
    return snapshot


def begin_operation() -> dict[str, Any] | None:
    with _STATE_LOCK:
        current = load_state()
        if current is not None:
            return current
        instances = _snapshot()
        operation = {
            "operation_id": uuid.uuid4().hex,
            "phase": "saving",
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "instances": instances,
            "summary": {},
        }
        _write_state(operation)
        return operation


def dismiss_terminal() -> None:
    with _STATE_LOCK:
        state = load_state()
        if state is not None and state.get("phase") in TERMINAL_PHASES:
            try:
                state_path().unlink()
            except FileNotFoundError:
                pass


def _managed_names(state: dict[str, Any]) -> list[str]:
    return [
        name
        for name, item in state.get("instances", {}).items()
        if item.get("ownership") == "managed"
    ]


def _external_names(state: dict[str, Any]) -> list[str]:
    return [
        name
        for name, item in state.get("instances", {}).items()
        if item.get("ownership") == "external"
    ]


def _handoff_managed(state: dict[str, Any]) -> list[tuple[str, str]]:
    failures: list[tuple[str, str]] = []
    managers = {name: ProcessManager.get(name) for name in _managed_names(state)}

    def request(pair: tuple[str, Any]) -> tuple[str, bool, str | None]:
        name, manager = pair
        try:
            return name, bool(manager.handoff()), None
        except Exception as exc:
            return name, False, str(exc)

    with ThreadPoolExecutor(max_workers=max(1, len(managers))) as executor:
        for name, handed_off, error in executor.map(request, managers.items()):
            if handed_off:
                _update_instance(name, status="handed_off", message=t("utils.restart_handed_off"))
            else:
                message = error or t("utils.restart_handoff_failed")
                _update_instance(name, status="handoff_failed", message=message)
                failures.append((name, message))
    return failures


def _verify_managed(state: dict[str, Any]) -> list[tuple[str, str]]:
    failures: list[tuple[str, str]] = []
    names = _managed_names(state)

    def verify(name: str) -> tuple[str, str | None]:
        try:
            record = load_instance(name)
            adapter = get_game(record.game)
            verifier = getattr(adapter, "verify_managed", adapter.is_running)
            if not verifier(record):
                return name, "Managed agent or PalServer is not alive"
            _update_instance(name, status="verified", message=t("utils.restart_verified"))
            return name, None
        except Exception as exc:
            return name, str(exc)

    with ThreadPoolExecutor(max_workers=max(1, len(names))) as executor:
        for name, error in executor.map(verify, names):
            if error is not None:
                _update_instance(name, status="verification_failed", message=error)
                failures.append((name, error))
    return failures


def _rollback_handoff(state: dict[str, Any]) -> list[tuple[str, str]]:
    failures: list[tuple[str, str]] = []
    state = load_state() or state
    names = [
        name
        for name, item in state.get("instances", {}).items()
        if item.get("ownership") == "managed"
        and item.get("status") in {"handed_off", "verified", "verification_failed"}
    ]

    def restore(name: str) -> tuple[str, str | None]:
        try:
            manager = ProcessManager.get(name)
            if not manager.start(
                update=False,
                reason="GUI restart rollback",
                adopt_managed=True,
            ):
                return name, "Could not reconnect the handed-off supervisor"
            _update_instance(name, status="rollback_restored", message=t("utils.restart_restored"))
            return name, None
        except Exception as exc:
            return name, str(exc)

    with ThreadPoolExecutor(max_workers=max(1, len(names))) as executor:
        for name, error in executor.map(restore, names):
            if error is not None:
                _update_instance(name, status="rollback_failed", message=error)
                failures.append((name, error))
    return failures


def _save_one(name: str) -> tuple[str, str | None]:
    try:
        record = load_instance(name)
        get_game(record.game).save_before_shutdown(record)
        _update_instance(name, status="saved", message=t("utils.restart_saved"))
        return name, None
    except Exception as exc:
        message = str(exc)
        _update_instance(name, status="save_failed", message=message)
        return name, message


def _preflight_saves(state: dict[str, Any]) -> list[tuple[str, str]]:
    failures: list[tuple[str, str]] = []
    names = _managed_names(state)
    with ThreadPoolExecutor(max_workers=max(1, len(names))) as executor:
        for name, error in executor.map(_save_one, names):
            if error is not None:
                failures.append((name, error))
    return failures


def _request_stop(pair: tuple[str, Any]) -> tuple[str, bool, str | None]:
    name, manager = pair
    try:
        return name, bool(manager.stop()), None
    except Exception as exc:
        return name, False, str(exc)


def _stop_and_wait(state: dict[str, Any]) -> list[str]:
    names = _managed_names(state)
    managers = {name: ProcessManager.get(name) for name in names}
    with ThreadPoolExecutor(max_workers=max(1, len(managers))) as executor:
        requests = executor.map(_request_stop, managers.items())
        for name, requested, error in requests:
            if requested:
                _update_instance(name, status="shutdown_requested", message=t("utils.restart_shutdown_requested"))
            elif error:
                _update_instance(name, status="shutdown_failed", message=error)
            else:
                _update_instance(name, status="already_stopped")

    deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS
    _update_state(
        shutdown_deadline_at=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=SHUTDOWN_TIMEOUT_SECONDS)).isoformat()
    )
    while time.monotonic() < deadline:
        active = [name for name, manager in managers.items() if manager.active]
        if not active:
            break
        time.sleep(0.25)

    killed: list[str] = []
    for name, manager in managers.items():
        if manager.active:
            try:
                manager.kill()
            except Exception as exc:
                _update_instance(name, status="kill_failed", message=str(exc))
                continue
            killed.append(name)
            _update_instance(
                name,
                status="force_killed",
                message=t("utils.restart_force_killed"),
            )
        else:
            _update_instance(name, status="stopped", message=t("utils.restart_stopped"))
    return killed


def _detach_external(state: dict[str, Any]) -> None:
    for name in _external_names(state):
        manager = ProcessManager.get(name)
        if manager.detach():
            _update_instance(name, status="detached", message=t("utils.restart_external_detached"))
        else:
            _update_instance(name, status="detach_failed", message=t("utils.restart_external_detach_failed"))


def run_workflow() -> None:
    global _WORKFLOW_THREAD
    state = load_state()
    if state is None or state.get("phase") != "saving":
        return
    failures = _preflight_saves(state)
    if failures:
        _update_state(
            phase="failed",
            summary={"reason": "save_failed", "failures": dict(failures)},
        )
        return

    _update_state(phase="detaching")
    handoff_failures = _handoff_managed(state)
    if handoff_failures:
        rollback_failures = _rollback_handoff(state)
        _update_state(
            phase="failed",
            summary={
                "reason": "handoff_failed",
                "failures": dict(handoff_failures),
                "rollback_failures": dict(rollback_failures),
            },
        )
        return
    verification_failures = _verify_managed(state)
    if verification_failures:
        rollback_failures = _rollback_handoff(state)
        _update_state(
            phase="failed",
            summary={
                "reason": "verification_failed",
                "failures": dict(verification_failures),
                "rollback_failures": dict(rollback_failures),
            },
        )
        return
    _detach_external(state)
    _update_state(phase="restarting_gui", summary={"handed_off": _managed_names(state)})
    os._exit(RESTART_EXIT_CODE)


def start_workflow() -> dict[str, Any] | None:
    global _WORKFLOW_THREAD
    with _WORKFLOW_LOCK:
        state = begin_operation()
        if state is None or state.get("phase") != "saving":
            return state
        if _WORKFLOW_THREAD is None or not _WORKFLOW_THREAD.is_alive():
            _WORKFLOW_THREAD = threading.Thread(target=run_workflow, daemon=True)
            _WORKFLOW_THREAD.start()
        return state


def _restore_one(name: str, item: dict[str, Any]) -> tuple[str, str | None]:
    try:
        manager = ProcessManager.get(name)
        if not manager.start(
            update=False,
            reason="GUI restart restore",
            adopt_managed=item.get("ownership") == "managed",
        ):
            raise RuntimeError("Could not start the instance supervisor")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if manager.state == "warning":
                raise RuntimeError("Instance supervisor entered warning state")
            if not manager.operation_busy and (
                manager.active
                or (
                    item.get("ownership") == "managed"
                    and manager.state == "inactive"
                )
            ):
                _update_instance(name, status="restored", message=t("utils.restart_restored"))
                return name, None
            time.sleep(0.25)
        raise TimeoutError("Timed out while restoring the instance")
    except Exception as exc:
        message = str(exc)
        _update_instance(name, status="restore_failed", message=message)
        return name, message


def _restore_state(state: dict[str, Any]) -> None:
    failures: dict[str, str] = {}
    items = state.get("instances", {})
    with ThreadPoolExecutor(max_workers=max(1, len(items))) as executor:
        for name, error in executor.map(lambda pair: _restore_one(*pair), items.items()):
            if error is not None:
                failures[name] = error
    final_state = load_state() or state
    for name, item in final_state.get("instances", {}).items():
        if str(item.get("status", "")).endswith("_failed"):
            failures.setdefault(name, str(item.get("message") or item.get("status")))
    _update_state(
        phase="failed" if failures else "completed",
        summary={"restore_failures": failures},
    )


def start_restore_if_needed() -> None:
    global _RESTORE_STARTED
    if _RESTORE_STARTED:
        return
    with _STATE_LOCK:
        state = load_state()
        if state is None or state.get("phase") not in {"restarting_gui", "restoring"}:
            return
        _RESTORE_STARTED = True
        _update_state(phase="restoring")
    thread = threading.Thread(target=lambda: _restore_state(load_state() or state), daemon=True)
    thread.start()


def _instance_rows(state: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name, item in state.get("instances", {}).items():
        rows.append({
            "name": str(name),
            "message": str(item.get("message") or item.get("status") or ""),
        })
    return rows


def render_overlay() -> None:
    state = load_state()
    clear("restart_overlay")
    if state is None:
        client_call("dom.setClasses", scope="restart_overlay", classes="")
        return
    phase = state.get("phase")
    terminal = phase in TERMINAL_PHASES
    title = t("utils.restart_complete") if terminal else t("utils.restart_in_progress")
    message_key = "utils.restart_result_failed" if phase == "failed" else "utils.restart_phase"
    message = t(message_key, phase=t(f"utils.restart_phase_{phase}"))
    if not terminal and phase == "restarting_gui":
        message += " " + t("utils.restart_disconnect_notice")
    deadline = state.get("shutdown_deadline_at")
    if phase == "stopping" and deadline:
        message += " " + t("utils.restart_deadline", deadline=deadline)
    classes = "restart-overlay restart-overlay-terminal" if terminal else "restart-overlay"
    with use_scope("restart_overlay"):
        rows = _instance_rows(state)
        put_asset_widget(
            "shared.restart_overlay",
            {
                "title": title,
                "message": message,
                "instances": bool(rows),
                "items": rows,
            },
        )
        if terminal:
            put_button(t("utils.restart_dismiss"), onclick=dismiss_overlay, color="secondary")
    client_call("dom.setClasses", scope="restart_overlay", classes=classes)


def dismiss_overlay() -> None:
    dismiss_terminal()
    render_overlay()


def start_overlay_updates() -> None:
    stop_event = threading.Event()
    register_stop_event(stop_event)

    def update() -> None:
        last = None
        try:
            while not stop_event.wait(0.5):
                state = load_state()
                marker = json.dumps(state, sort_keys=True) if state else None
                if marker != last:
                    render_overlay()
                    last = marker
        except SessionException:
            return

    thread = threading.Thread(target=update, daemon=True)
    register_thread(thread)
    thread.start()


def mount_overlay() -> None:
    put_scope("restart_overlay")
    render_overlay()
    start_overlay_updates()
    start_restore_if_needed()


__all__ = [
    "RESTART_EXIT_CODE",
    "TERMINAL_PHASES",
    "dismiss_overlay",
    "load_state",
    "mount_overlay",
    "start_workflow",
]
