from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from module.games import get_game
from module.instances import list_instances, load_agent_state
from module.webui.process_manager import ProcessManager


SHUTDOWN_TIMEOUT_SECONDS = 60

_STATE_LOCK = threading.RLock()
_SHUTDOWN_LOCK = threading.Lock()
_SHUTTING_DOWN = False


@dataclass(frozen=True)
class ShutdownResult:
    ok: bool
    instances: dict[str, dict[str, str]]
    error: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "instances": self.instances,
            "error": self.error,
        }


def is_shutting_down() -> bool:
    with _STATE_LOCK:
        return _SHUTTING_DOWN


def _set_shutting_down(value: bool) -> None:
    global _SHUTTING_DOWN
    with _STATE_LOCK:
        _SHUTTING_DOWN = value


def _active_records() -> list[Any]:
    records: list[Any] = []
    for record in list_instances():
        adapter = get_game(record.game)
        if not adapter.capabilities.lifecycle:
            continue
        manager = ProcessManager.get(record.name)
        server_running = False
        try:
            server_running = bool(adapter.is_running(record))
        except (OSError, RuntimeError):
            pass
        agent_running = os.name == "nt" and load_agent_state(record.name) is not None
        if manager.active or manager.operation_busy or server_running or agent_running:
            records.append(record)
    return records


def _agent_running(record: Any) -> bool:
    return os.name == "nt" and load_agent_state(record.name) is not None


def _attach_unmanaged_target(record: Any) -> tuple[str, str | None]:
    manager = ProcessManager.get(record.name)
    if manager.active or manager.operation_busy:
        return record.name, None
    try:
        adapter = get_game(record.game)
        if adapter.is_running(record):
            if not manager.start(
                update=False,
                reason="desktop shutdown attach",
                adopt_managed=_agent_running(record),
                shutdown=True,
            ):
                return record.name, "Could not attach to the active server"
    except Exception as exc:
        return record.name, str(exc)
    return record.name, None


def _wait_for_operations(records: list[Any], deadline: float) -> dict[str, dict[str, str]]:
    failures: dict[str, dict[str, str]] = {}
    while time.monotonic() < deadline:
        busy = [
            record.name
            for record in records
            if ProcessManager.get(record.name).operation_busy
        ]
        if not busy:
            return failures
        time.sleep(0.1)
    for record in records:
        manager = ProcessManager.get(record.name)
        if manager.operation_busy:
            failures[record.name] = {
                "status": "operation_timeout",
                "message": "An operation did not finish",
            }
    return failures


def _save_one(record: Any) -> tuple[str, str | None]:
    try:
        manager = ProcessManager.get(record.name)
        adapter = get_game(record.game)
        if manager.active or adapter.is_running(record):
            adapter.save_before_shutdown(record)
    except Exception as exc:
        return record.name, str(exc)
    return record.name, None


def _stop_one(record: Any) -> tuple[str, str | None]:
    manager = ProcessManager.get(record.name)
    try:
        if manager.active:
            if not manager.stop(shutdown=True):
                return record.name, "Could not request graceful shutdown"
        elif _agent_running(record):
            from module.games.palworld.server.agent import AgentClient

            AgentClient.connect_existing(record.name).stop()
    except Exception as exc:
        return record.name, str(exc)
    return record.name, None


def _verify_stopped(record: Any) -> tuple[str, str | None]:
    manager = ProcessManager.get(record.name)
    if manager.active or manager.alive:
        return record.name, "Supervisor did not stop"
    if _agent_running(record):
        return record.name, "PalServer agent is still running"
    try:
        if get_game(record.game).is_running(record):
            return record.name, "Game server is still running"
    except Exception as exc:
        return record.name, str(exc)
    return record.name, None


def shutdown_all(timeout: float = SHUTDOWN_TIMEOUT_SECONDS) -> ShutdownResult:
    """Gracefully stop every active lifecycle instance and its supervisor."""
    if not _SHUTDOWN_LOCK.acquire(blocking=False):
        return ShutdownResult(False, {}, "Shutdown is already in progress")
    _set_shutting_down(True)
    try:
        records = _active_records()
        statuses = {
            record.name: {"status": "pending", "message": "Shutdown pending"}
            for record in records
        }
        deadline = time.monotonic() + timeout

        with ThreadPoolExecutor(max_workers=max(1, len(records))) as executor:
            attach_results = executor.map(_attach_unmanaged_target, records)
            attach_failures = {name: error for name, error in attach_results if error}
        for name, error in attach_failures.items():
            statuses[name] = {"status": "attach_failed", "message": error}
        if attach_failures:
            return ShutdownResult(False, statuses, "Could not attach to every active server")

        operation_failures = _wait_for_operations(records, deadline)
        for name, item in operation_failures.items():
            statuses[name] = item
        if operation_failures:
            return ShutdownResult(False, statuses, "An operation did not finish")

        with ThreadPoolExecutor(max_workers=max(1, len(records))) as executor:
            save_results = executor.map(_save_one, records)
            save_failures = {name: error for name, error in save_results if error}
        for record in records:
            if record.name in save_failures:
                statuses[record.name] = {
                    "status": "save_failed",
                    "message": save_failures[record.name],
                }
            else:
                statuses[record.name] = {"status": "saved", "message": "State saved"}
        if save_failures:
            return ShutdownResult(False, statuses, "Could not save every active server")

        with ThreadPoolExecutor(max_workers=max(1, len(records))) as executor:
            stop_results = executor.map(_stop_one, records)
            stop_failures = {name: error for name, error in stop_results if error}
        for record in records:
            if record.name in stop_failures:
                statuses[record.name] = {
                    "status": "stop_failed",
                    "message": stop_failures[record.name],
                }
            else:
                statuses[record.name] = {"status": "stop_requested", "message": "Graceful stop requested"}
        if stop_failures:
            return ShutdownResult(False, statuses, "Could not request every server shutdown")

        while time.monotonic() < deadline:
            pending = [record for record in records if ProcessManager.get(record.name).active]
            if not pending:
                break
            time.sleep(0.25)

        with ThreadPoolExecutor(max_workers=max(1, len(records))) as executor:
            verify_results = executor.map(_verify_stopped, records)
            verify_failures = {name: error for name, error in verify_results if error}
        for record in records:
            if record.name in verify_failures:
                statuses[record.name] = {
                    "status": "shutdown_failed",
                    "message": verify_failures[record.name],
                }
            else:
                statuses[record.name] = {"status": "stopped", "message": "Gracefully stopped"}
        if verify_failures:
            return ShutdownResult(False, statuses, "Some agents or servers did not stop gracefully")
        return ShutdownResult(True, statuses)
    finally:
        _set_shutting_down(False)
        _SHUTDOWN_LOCK.release()


__all__ = ["SHUTDOWN_TIMEOUT_SECONDS", "ShutdownResult", "is_shutting_down", "shutdown_all"]
