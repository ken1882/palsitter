from __future__ import annotations

import copy
import threading
import time
from typing import Any, Callable

from pywebio.exceptions import SessionException
from pywebio.output import clear, put_button, put_scope, use_scope
from pywebio.session import register_thread

from module.webui.assets import client_call, put_asset_widget
from module.webui.i18n import t
from module.webui.session import register_stop_event
from module.webui.shutdown import ShutdownResult, active_records, force_shutdown_all, shutdown_all


FORCE_SHUTDOWN_DELAY_SECONDS = 5
CLIENT_NOTICE_DELAY_SECONDS = 3
_STATE_LOCK = threading.RLock()
_WORKFLOW_THREAD: threading.Thread | None = None
_WORKFLOW_STATE: dict[str, Any] | None = None
_FORCE_REQUESTED = threading.Event()
_ON_COMPLETE: Callable[[], None] | None = None
_RENDER_LOCK = threading.RLock()


def configure_completion(callback: Callable[[], None] | None, *, replace: bool = False) -> None:
    global _ON_COMPLETE
    with _STATE_LOCK:
        if replace or _ON_COMPLETE is None:
            _ON_COMPLETE = callback


def load_state() -> dict[str, Any] | None:
    with _STATE_LOCK:
        return copy.deepcopy(_WORKFLOW_STATE)


def _instance_snapshot() -> dict[str, dict[str, str]]:
    return {
        record.name: {"status": "pending", "message": t("utils.shutdown_pending")}
        for record in active_records()
    }


def _request_result(status: str) -> ShutdownResult:
    state = load_state() or {}
    return ShutdownResult(
        status in {"shutdown_started", "shutdown_in_progress", "force_shutdown_started"},
        {
            name: {"status": status, "message": str(item.get("message") or status)}
            for name, item in (state.get("instances") or {}).items()
        },
        None if status not in {"shutdown_unavailable", "force_shutdown_locked"} else status,
    )


def start_workflow() -> ShutdownResult:
    global _WORKFLOW_THREAD, _WORKFLOW_STATE
    with _STATE_LOCK:
        if _WORKFLOW_STATE is not None and _WORKFLOW_STATE.get("phase") in {
            "stopping",
            "force_stopping",
        }:
            return _request_result("shutdown_in_progress")
        _FORCE_REQUESTED.clear()
        _WORKFLOW_STATE = {
            "phase": "stopping",
            "force_available_at": time.time() + FORCE_SHUTDOWN_DELAY_SECONDS,
            "instances": _instance_snapshot(),
        }
        _WORKFLOW_THREAD = threading.Thread(target=_run_graceful, daemon=True)
        _WORKFLOW_THREAD.start()
    return _request_result("shutdown_started")


def request_gui_only_shutdown() -> ShutdownResult:
    return ShutdownResult(True, {})


def stop_gui_only() -> ShutdownResult:
    callback: Callable[[], None] | None
    result = request_gui_only_shutdown()
    with _STATE_LOCK:
        callback = _ON_COMPLETE if result.ok else None
    if callback is not None:
        threading.Thread(target=callback, daemon=True).start()
    return result


def request_force_shutdown() -> ShutdownResult:
    global _WORKFLOW_THREAD
    with _STATE_LOCK:
        state = _WORKFLOW_STATE
        if state is None or state.get("phase") not in {"stopping", "failed"}:
            return _request_result("shutdown_unavailable")
        if time.time() < float(state.get("force_available_at", 0)):
            return _request_result("force_shutdown_locked")
        _FORCE_REQUESTED.set()
        state["phase"] = "force_stopping"
        _WORKFLOW_THREAD = threading.Thread(target=_run_force, daemon=True)
        _WORKFLOW_THREAD.start()
    return _request_result("force_shutdown_started")


def _finish(result, *, force: bool) -> None:
    callback: Callable[[], None] | None = None
    with _STATE_LOCK:
        state = _WORKFLOW_STATE
        if state is None:
            return
        if not force and state.get("phase") != "stopping":
            return
        state["phase"] = "completed" if result.ok else "failed"
        state["instances"] = result.instances
        state["error"] = result.error
        if result.ok:
            callback = _ON_COMPLETE
    if callback is not None:
        threading.Thread(target=_finish_after_client_notice, args=(callback,), daemon=True).start()


def _finish_after_client_notice(callback: Callable[[], None]) -> None:
    time.sleep(CLIENT_NOTICE_DELAY_SECONDS)
    callback()


def _run_graceful() -> None:
    try:
        result = shutdown_all()
    except Exception as exc:
        result = ShutdownResult(False, {}, str(exc))
    if not _FORCE_REQUESTED.is_set():
        _finish(result, force=False)


def _run_force() -> None:
    try:
        result = force_shutdown_all()
    except Exception as exc:
        result = ShutdownResult(False, {}, str(exc))
    _finish(result, force=True)


def _rows(state: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"name": str(name), "message": str(item.get("message") or item.get("status") or "")}
        for name, item in (state.get("instances") or {}).items()
    ]


def render_overlay() -> None:
    with _RENDER_LOCK:
        _render_overlay()


def _render_overlay() -> None:
    state = load_state()
    clear("shutdown_overlay")
    if state is None:
        client_call("shutdown.destroy")
        client_call("dom.setClasses", scope="shutdown_overlay", classes="")
        return
    phase = str(state.get("phase") or "stopping")
    remaining = max(0, int(float(state.get("force_available_at", 0)) - time.time() + 0.999))
    force_allowed = phase in {"stopping", "failed"}
    force_enabled = force_allowed and remaining == 0
    if phase == "completed":
        title = t("utils.shutdown_complete")
        message = t("utils.shutdown_finished")
    elif phase == "failed":
        title = t("utils.shutdown_in_progress")
        message = t("utils.shutdown_failed", error=state.get("error") or "")
    elif phase == "force_stopping":
        title = t("utils.shutdown_in_progress")
        message = t("utils.shutdown_force_stopping")
    else:
        title = t("utils.shutdown_in_progress")
        message = t("utils.shutdown_stopping")
    rows = _rows(state)
    button_text = (
        t("utils.force_shutdown_countdown", seconds=remaining)
        if remaining
        else t("utils.force_shutdown")
    )
    with use_scope("shutdown_overlay"):
        put_asset_widget(
            "shared.shutdown_overlay",
            {
                "title": title,
                "message": message,
                "instances": bool(rows),
                "items": rows,
                "force_button": put_scope("shutdown_force_button"),
            },
        )
        with use_scope("shutdown_force_button"):
            put_button(
                button_text,
                onclick=request_force_shutdown,
                color="danger",
                disabled=False,
            )
    classes = "shutdown-overlay shutdown-overlay-complete" if phase == "completed" else "shutdown-overlay"
    client_call("dom.setClasses", scope="shutdown_overlay", classes=classes)
    client_call(
        "shutdown.mount",
        forceAt=float(state.get("force_available_at", 0)),
        selector="#pywebio-scope-shutdown_force_button button",
        enabled=force_allowed,
    )


def start_overlay_updates() -> None:
    stop_event = threading.Event()
    register_stop_event(stop_event)

    def update() -> None:
        last = None
        try:
            while not stop_event.wait(0.5):
                state = load_state()
                marker = repr(state)
                if marker != last:
                    render_overlay()
                    last = marker
        except SessionException:
            return

    thread = threading.Thread(target=update, daemon=True)
    register_thread(thread)
    thread.start()


def mount_overlay() -> None:
    put_scope("shutdown_overlay")
    render_overlay()
    start_overlay_updates()


__all__ = [
    "FORCE_SHUTDOWN_DELAY_SECONDS",
    "CLIENT_NOTICE_DELAY_SECONDS",
    "configure_completion",
    "load_state",
    "mount_overlay",
    "request_gui_only_shutdown",
    "request_force_shutdown",
    "stop_gui_only",
    "start_workflow",
]
