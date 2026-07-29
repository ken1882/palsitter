from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from module.webui import restart


class FakeManager:
    def __init__(self, *, ownership="managed", remains_active=False):
        self.ownership = ownership
        self._active = True
        self.remains_active = remains_active
        self.stop_calls = 0
        self.kill_calls = 0
        self.handoff_calls = 0
        self.start_calls = 0
        self.detach_calls = 0
        self.stop_started = threading.Event()
        self.logs = []

    def append_log(self, message):
        self.logs.append(message)

    @property
    def active(self):
        return self._active

    def stop(self):
        self.stop_calls += 1
        self.stop_started.set()
        if not self.remains_active:
            self._active = False
        return True

    def kill(self):
        self.kill_calls += 1
        self._active = False
        return True

    def handoff(self):
        self.handoff_calls += 1
        self._active = False
        return True

    def start(self, **kwargs):
        self.start_calls += 1
        self._active = True
        return True

    def detach(self):
        self.detach_calls += 1
        self._active = False
        return True


def _state(names):
    return {
        "operation_id": "operation",
        "phase": "saving",
        "instances": {
            name: {"game": "palworld", "ownership": "managed", "status": "pending"}
            for name in names
        },
        "summary": {},
    }


@pytest.fixture(autouse=True)
def _isolated_restart_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(restart, "runtime_dir", lambda: tmp_path / "runtime")


def test_save_failure_never_stops_or_kills_managed_servers(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    records = {
        name: SimpleNamespace(name=name, game="palworld")
        for name in ("alpha", "beta")
    }
    managers = {name: FakeManager() for name in records}
    saved = []

    class Adapter:
        capabilities = SimpleNamespace(lifecycle=True)

        def save_before_shutdown(self, record):
            saved.append(record.name)
            if record.name == "beta":
                raise RuntimeError("save rejected")

    monkeypatch.setattr(restart, "list_instances", lambda: list(records.values()))
    monkeypatch.setattr(restart, "load_instance", records.__getitem__)
    monkeypatch.setattr(restart, "get_game", lambda game: Adapter())
    monkeypatch.setattr(restart.ProcessManager, "get", lambda name: managers[name])

    operation = restart.begin_operation()
    restart.run_workflow()

    result = restart.load_state()
    assert sorted(saved) == ["alpha", "beta"]
    assert result["phase"] == "failed"
    assert result["summary"]["reason"] == "save_failed"
    assert all(manager.stop_calls == 0 for manager in managers.values())
    assert all(manager.kill_calls == 0 for manager in managers.values())
    assert operation["operation_id"] == result["operation_id"]


def test_graceful_shutdown_requests_are_parallel_and_timeout_kills_active_servers(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    names = ("alpha", "beta")
    managers = {name: FakeManager(remains_active=True) for name in names}
    entered = threading.Barrier(len(names))

    def stop(self):
        self.stop_calls += 1
        self.stop_started.set()
        entered.wait(timeout=2)
        return True

    for manager in managers.values():
        manager.stop = stop.__get__(manager, FakeManager)

    monkeypatch.setattr(restart.ProcessManager, "get", lambda name: managers[name])
    monkeypatch.setattr(restart, "SHUTDOWN_TIMEOUT_SECONDS", 0.01)
    state = _state(names)

    started = time.monotonic()
    killed = restart._stop_and_wait(state)
    elapsed = time.monotonic() - started

    assert killed == list(names)
    assert elapsed < 0.8
    assert all(manager.stop_calls == 1 for manager in managers.values())
    assert all(manager.kill_calls == 1 for manager in managers.values())
    result = restart.load_state()
    assert result is None


def test_managed_handoff_does_not_request_server_stop_or_kill(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    managers = {name: FakeManager() for name in ("alpha", "beta")}
    monkeypatch.setattr(restart.ProcessManager, "get", lambda name: managers[name])

    failures = restart._handoff_managed(_state(tuple(managers)))

    assert failures == []
    assert all(manager.handoff_calls == 1 for manager in managers.values())
    assert all(manager.stop_calls == 0 for manager in managers.values())
    assert all(manager.kill_calls == 0 for manager in managers.values())


def test_verification_failure_keeps_current_gui_and_rolls_back_handoff(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    records = {
        name: SimpleNamespace(name=name, game="palworld")
        for name in ("alpha", "beta")
    }
    managers = {name: FakeManager() for name in records}

    class Adapter:
        capabilities = SimpleNamespace(lifecycle=True)

        def save_before_shutdown(self, record):
            pass

        def verify_managed(self, record):
            if record.name == "beta":
                raise RuntimeError("agent disappeared")
            return True

    monkeypatch.setattr(restart, "list_instances", lambda: list(records.values()))
    monkeypatch.setattr(restart, "load_instance", records.__getitem__)
    monkeypatch.setattr(restart, "get_game", lambda game: Adapter())
    monkeypatch.setattr(restart.ProcessManager, "get", lambda name: managers[name])
    monkeypatch.setattr(
        restart.os,
        "_exit",
        lambda code: (_ for _ in ()).throw(AssertionError("GUI must not exit")),
    )

    restart.begin_operation()
    restart.run_workflow()

    result = restart.load_state()
    assert result["phase"] == "failed"
    assert result["summary"]["reason"] == "verification_failed"
    assert all(manager.detach_calls == 0 for manager in managers.values())
    assert all(manager.start_calls == 1 for manager in managers.values())


def test_managed_idle_restore_is_successful_without_active_supervisor(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))

    class IdleManager:
        active = False
        operation_busy = False
        state = "inactive"

        def start(self, **kwargs):
            assert kwargs["adopt_managed"] is True
            assert kwargs["update"] is False
            return True

    monkeypatch.setattr(restart.ProcessManager, "get", lambda name: IdleManager())

    name, error = restart._restore_one(
        "alpha",
        {"ownership": "managed"},
    )

    assert name == "alpha"
    assert error is None


def test_successful_workflow_schedules_reload_before_exit(monkeypatch):
    calls = []

    monkeypatch.setattr(restart, "list_instances", lambda: [])
    monkeypatch.setattr(
        restart,
        "client_call",
        lambda api_name, **payload: calls.append((api_name, payload)),
    )
    monkeypatch.setattr(restart.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        restart.os,
        "_exit",
        lambda code: (_ for _ in ()).throw(SystemExit(code)),
    )

    restart.begin_operation()
    with pytest.raises(SystemExit) as exited:
        restart.run_workflow()

    assert exited.value.code == restart.RESTART_EXIT_CODE
    assert calls == [("page.reload", {"delayMs": 1000})]
    assert restart.load_state()["phase"] == "restarting_gui"
