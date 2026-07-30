from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from module.webui import shutdown_workflow
from module.webui.shutdown import (
    ShutdownResult,
    is_shutting_down,
    release_shutdown_latch,
)


@pytest.fixture(autouse=True)
def _reset_shutdown_workflow(monkeypatch):
    release_shutdown_latch()
    monkeypatch.setattr(shutdown_workflow, "_WORKFLOW_RECORDS", ())
    yield
    release_shutdown_latch()


def test_shared_force_request_kills_and_wins_over_graceful_worker(monkeypatch):
    graceful_started = threading.Event()
    release_graceful = threading.Event()
    force_called = threading.Event()
    completed = threading.Event()
    manifests = []

    monkeypatch.setattr(
        shutdown_workflow,
        "active_records",
        lambda: [SimpleNamespace(name="alpha")],
    )
    monkeypatch.setattr(shutdown_workflow, "_WORKFLOW_STATE", None)
    monkeypatch.setattr(shutdown_workflow, "_ON_COMPLETE", completed.set)

    def graceful(*, records):
        manifests.append(tuple(record.name for record in records))
        graceful_started.set()
        release_graceful.wait(2)
        return ShutdownResult(True, {"alpha": {"status": "stopped"}})

    def force(*, records):
        manifests.append(tuple(record.name for record in records))
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
    assert manifests == [("alpha",), ("alpha",)]
    assert completed.wait(0.2)
    assert is_shutting_down()


def test_workflow_latches_before_capturing_manifest(monkeypatch):
    captured = []
    monkeypatch.setattr(
        shutdown_workflow,
        "active_records",
        lambda: captured.append(is_shutting_down()) or [],
    )
    monkeypatch.setattr(shutdown_workflow, "_WORKFLOW_STATE", None)
    monkeypatch.setattr(
        shutdown_workflow,
        "shutdown_all",
        lambda *, records: ShutdownResult(False, {}),
    )

    shutdown_workflow.start_workflow()

    assert captured == [True]
    assert is_shutting_down()


def test_force_failure_still_completes_backend_without_client_notice(monkeypatch):
    completed = threading.Event()
    monkeypatch.setattr(
        shutdown_workflow,
        "_WORKFLOW_STATE",
        {"phase": "force_stopping", "instances": {}},
    )
    monkeypatch.setattr(shutdown_workflow, "_ON_COMPLETE", completed.set)
    monkeypatch.setattr(shutdown_workflow, "CLIENT_NOTICE_DELAY_SECONDS", 60)

    shutdown_workflow._finish(
        ShutdownResult(False, {}, "survivor"),
        force=True,
    )

    assert completed.wait(0.2)
    assert shutdown_workflow.load_state()["phase"] == "failed"


def test_graceful_completion_retains_client_notice_delay(monkeypatch):
    completed = threading.Event()
    monkeypatch.setattr(
        shutdown_workflow,
        "_WORKFLOW_STATE",
        {"phase": "stopping", "instances": {}},
    )
    monkeypatch.setattr(shutdown_workflow, "_ON_COMPLETE", completed.set)
    monkeypatch.setattr(shutdown_workflow, "CLIENT_NOTICE_DELAY_SECONDS", 0.05)

    shutdown_workflow._finish(ShutdownResult(True, {}), force=False)

    assert not completed.wait(0.01)
    assert completed.wait(0.2)


def test_gui_only_shutdown_closes_gui_without_running_instance_shutdown(monkeypatch):
    completed = threading.Event()
    shutdown_called = threading.Event()

    monkeypatch.setattr(shutdown_workflow, "_ON_COMPLETE", completed.set)
    monkeypatch.setattr(
        shutdown_workflow,
        "shutdown_all",
        lambda: shutdown_called.set(),
    )

    result = shutdown_workflow.stop_gui_only()

    assert result.ok is True
    assert completed.wait(2)
    assert not shutdown_called.is_set()
