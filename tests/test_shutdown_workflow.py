from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from module.webui import shutdown_workflow
from module.webui.shutdown import ShutdownResult


def test_shared_force_request_kills_and_wins_over_graceful_worker(monkeypatch):
    graceful_started = threading.Event()
    release_graceful = threading.Event()
    force_called = threading.Event()

    monkeypatch.setattr(
        shutdown_workflow,
        "active_records",
        lambda: [SimpleNamespace(name="alpha")],
    )
    monkeypatch.setattr(shutdown_workflow, "_WORKFLOW_STATE", None)
    monkeypatch.setattr(shutdown_workflow, "_ON_COMPLETE", None)

    def graceful():
        graceful_started.set()
        release_graceful.wait(2)
        return ShutdownResult(True, {"alpha": {"status": "stopped"}})

    def force():
        force_called.set()
        return ShutdownResult(True, {"alpha": {"status": "force_stopped"}})

    monkeypatch.setattr(shutdown_workflow, "shutdown_all", graceful)
    monkeypatch.setattr(shutdown_workflow, "force_shutdown_all", force)

    started = shutdown_workflow.start_workflow()
    assert started.ok is True
    assert graceful_started.wait(2)

    with shutdown_workflow._STATE_LOCK:
        shutdown_workflow._WORKFLOW_STATE["force_available_at"] = time.time() - 1
    forced = shutdown_workflow.request_force_shutdown()

    assert forced.ok is True
    assert force_called.wait(2)
    deadline = time.time() + 2
    while (shutdown_workflow.load_state() or {}).get("phase") != "completed":
        if time.time() >= deadline:
            raise AssertionError("force shutdown workflow did not complete")
        time.sleep(0.01)

    release_graceful.set()
    time.sleep(0.05)
    state = shutdown_workflow.load_state()
    assert state["phase"] == "completed"
    assert state["instances"]["alpha"]["status"] == "force_stopped"
