from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

from module.games import ForceStopHandle
from module.webui import desktop_control, shutdown
from module.games.palworld.server import RestError


class FakeManager:
    def __init__(self, *, remains_active: bool = False):
        self._active = True
        self.remains_active = remains_active
        self.stop_calls = 0
        self.kill_calls = 0
        self.prepare_calls = 0
        self.prepare_force_calls = 0
        self.terminate_supervisor_calls = 0
        self.operation_busy = False
        self.logs = []
        self.ownership = "managed"

    def append_log(self, message):
        self.logs.append(message)

    @property
    def active(self):
        return self._active

    @property
    def alive(self):
        return self._active

    def stop(self, *, shutdown=False):
        assert shutdown is True
        self.stop_calls += 1
        if not self.remains_active:
            self._active = False
        return True

    def kill(self, *, shutdown=False):
        assert shutdown is True
        self.kill_calls += 1
        self._active = False
        return True

    def prepare_shutdown(self):
        self.prepare_calls += 1
        return True

    def prepare_force_shutdown(self):
        self.prepare_force_calls += 1
        return self.ownership

    def terminate_supervisor_immediate(self):
        self.terminate_supervisor_calls += 1
        was_active = self._active
        self._active = False
        return was_active

    def force_shutdown_supervisor_stopped(self):
        return not self._active


def _install_fakes(monkeypatch, managers):
    records = [SimpleNamespace(name=name, game="palworld") for name in managers]
    adapter = SimpleNamespace(
        capabilities=SimpleNamespace(lifecycle=True),
        save_before_shutdown=lambda record: None,
        is_running=lambda record: False,
        detached_agent_is_running=lambda record: False,
        force_stop_managed_immediate=lambda record: ForceStopHandle(
            True, lambda: True
        ),
    )
    monkeypatch.setattr(shutdown, "list_instances", lambda: records)
    monkeypatch.setattr(shutdown, "get_game", lambda game: adapter)
    monkeypatch.setattr(shutdown.ProcessManager, "get", lambda name: managers[name])


def test_active_records_ignores_stale_detached_agent_state(monkeypatch):
    record = SimpleNamespace(name="alpha", game="palworld")
    manager = FakeManager()
    manager._active = False
    adapter = SimpleNamespace(
        capabilities=SimpleNamespace(lifecycle=True),
        is_running=lambda current: False,
        detached_agent_is_running=lambda current: False,
    )

    monkeypatch.setattr(shutdown, "list_instances", lambda: [record])
    monkeypatch.setattr(shutdown, "get_game", lambda game: adapter)
    monkeypatch.setattr(shutdown.ProcessManager, "get", lambda name: manager)
    monkeypatch.setattr(
        shutdown,
        "load_agent_state",
        lambda name: {"agent_pid": 123},
        raising=False,
    )
    monkeypatch.setattr(
        shutdown,
        "os",
        SimpleNamespace(name="nt"),
        raising=False,
    )

    assert shutdown.active_records() == []


def test_shutdown_all_stops_every_active_instance_without_kill(monkeypatch):
    managers = {"alpha": FakeManager(), "beta": FakeManager()}
    _install_fakes(monkeypatch, managers)

    result = shutdown.shutdown_all(timeout=1)

    assert result.ok is True
    assert all(manager.stop_calls == 1 for manager in managers.values())
    assert all(manager.kill_calls == 0 for manager in managers.values())
    assert all(manager.prepare_calls == 1 for manager in managers.values())
    assert all(item["status"] == "stopped" for item in result.instances.values())
    assert shutdown.is_shutting_down() is False


def test_shutdown_timeout_does_not_force_kill(monkeypatch):
    manager = FakeManager(remains_active=True)
    _install_fakes(monkeypatch, {"alpha": manager})

    result = shutdown.shutdown_all(timeout=0.01)

    assert result.ok is False
    assert manager.stop_calls == 1
    assert manager.kill_calls == 0
    assert result.instances["alpha"]["status"] == "shutdown_failed"


def test_shutdown_all_force_stops_when_save_api_is_unavailable(monkeypatch):
    record = SimpleNamespace(name="alpha", game="palworld")
    manager = FakeManager()
    force_stop_calls = []

    def save_before_shutdown(current):
        raise RestError("Palworld server is not running or REST API is unavailable")

    adapter = SimpleNamespace(
        capabilities=SimpleNamespace(lifecycle=True),
        save_before_shutdown=save_before_shutdown,
        is_running=lambda current: False,
        detached_agent_is_running=lambda current: False,
        is_api_unavailable_error=lambda error: isinstance(error, RestError),
        force_stop=lambda current: force_stop_calls.append(current.name),
    )
    monkeypatch.setattr(shutdown, "list_instances", lambda: [record])
    monkeypatch.setattr(shutdown, "get_game", lambda game: adapter)
    monkeypatch.setattr(shutdown.ProcessManager, "get", lambda name: manager)

    result = shutdown.shutdown_all(timeout=1)

    assert result.ok is True
    assert manager.kill_calls == 1
    assert manager.stop_calls == 0
    assert force_stop_calls == []
    assert result.instances["alpha"]["status"] == "force_stopped"


def test_force_shutdown_kills_every_active_instance(monkeypatch):
    managers = {"alpha": FakeManager(), "beta": FakeManager()}
    _install_fakes(monkeypatch, managers)

    result = shutdown.force_shutdown_all()

    assert result.ok is True
    assert all(manager.prepare_force_calls == 1 for manager in managers.values())
    assert all(
        manager.terminate_supervisor_calls == 1 for manager in managers.values()
    )
    assert all(manager.kill_calls == 0 for manager in managers.values())
    assert all(manager.stop_calls == 0 for manager in managers.values())
    assert all(item["status"] == "force_stopped" for item in result.instances.values())


def test_force_shutdown_arms_every_manager_before_parallel_dispatch(monkeypatch):
    managers = {"alpha": FakeManager(), "beta": FakeManager()}
    _install_fakes(monkeypatch, managers)
    barrier = threading.Barrier(2)
    dispatches = []

    def dispatch(record):
        assert all(
            manager.prepare_force_calls == 1 for manager in managers.values()
        )
        dispatches.append(record.name)
        barrier.wait(timeout=1)
        return ForceStopHandle(True, lambda: True)

    adapter = SimpleNamespace(
        force_stop_managed_immediate=dispatch,
    )
    monkeypatch.setattr(shutdown, "get_game", lambda game: adapter)

    result = shutdown.force_shutdown_all(
        records=[
            SimpleNamespace(name=name, game="palworld")
            for name in managers
        ]
    )

    assert result.ok
    assert sorted(dispatches) == ["alpha", "beta"]


def test_force_shutdown_uses_one_shared_verification_deadline(monkeypatch):
    managers = {"alpha": FakeManager(), "beta": FakeManager()}
    _install_fakes(monkeypatch, managers)
    adapter = SimpleNamespace(
        force_stop_managed_immediate=lambda record: ForceStopHandle(
            True, lambda: False
        ),
    )
    monkeypatch.setattr(shutdown, "get_game", lambda game: adapter)
    monkeypatch.setattr(shutdown, "FORCE_VERIFY_SECONDS", 0.05)

    started = time.monotonic()
    result = shutdown.force_shutdown_all(
        records=[
            SimpleNamespace(name=name, game="palworld")
            for name in managers
        ]
    )

    assert time.monotonic() - started < 0.15
    assert not result.ok
    assert {
        item["status"] for item in result.instances.values()
    } == {"force_stop_failed"}


def test_force_shutdown_does_not_wait_for_stalled_dispatch(monkeypatch):
    manager = FakeManager()
    manager._active = False
    record = SimpleNamespace(name="alpha", game="palworld")
    blocked = threading.Event()
    adapter = SimpleNamespace(
        force_stop_managed_immediate=lambda current: (
            blocked.wait(1),
            ForceStopHandle(True, lambda: True),
        )[-1],
    )
    monkeypatch.setattr(shutdown.ProcessManager, "get", lambda name: manager)
    monkeypatch.setattr(shutdown, "get_game", lambda game: adapter)
    monkeypatch.setattr(shutdown, "FORCE_VERIFY_SECONDS", 0.05)

    started = time.monotonic()
    result = shutdown.force_shutdown_all(records=[record])
    elapsed = time.monotonic() - started
    blocked.set()

    assert elapsed < 0.15
    assert result.instances["alpha"]["status"] == "force_stop_failed"
    assert "deadline" in result.instances["alpha"]["message"].lower()


def test_force_shutdown_treats_already_stopped_managed_instance_as_stopped(
    monkeypatch,
):
    manager = FakeManager()
    manager._active = False
    record = SimpleNamespace(name="alpha", game="palworld")
    adapter = SimpleNamespace(
        force_stop_managed_immediate=lambda current: ForceStopHandle(
            False, lambda: True
        ),
    )
    monkeypatch.setattr(shutdown.ProcessManager, "get", lambda name: manager)
    monkeypatch.setattr(shutdown, "get_game", lambda game: adapter)

    result = shutdown.force_shutdown_all(records=[record])

    assert result.ok
    assert result.instances["alpha"]["status"] == "force_stopped"


def test_force_shutdown_skips_inactive_external_server(monkeypatch):
    record = SimpleNamespace(name="alpha", game="palworld")
    manager = FakeManager()
    manager._active = False
    force_stop_calls = []
    adapter = SimpleNamespace(
        force_stop_managed_immediate=lambda current: (
            force_stop_calls.append(current.name)
            or ForceStopHandle(False, lambda: True, external=True)
        ),
    )

    monkeypatch.setattr(shutdown, "_active_records", lambda: [record])
    monkeypatch.setattr(shutdown, "_agent_running", lambda current: True)
    monkeypatch.setattr(shutdown.ProcessManager, "get", lambda name: manager)
    monkeypatch.setattr(shutdown, "get_game", lambda game: adapter)

    result = shutdown.force_shutdown_all()

    assert result.ok is True
    assert force_stop_calls == ["alpha"]
    assert result.instances["alpha"]["status"] == "external_skipped"


def test_stop_detached_agent_dispatches_through_adapter_and_logs(monkeypatch):
    record = SimpleNamespace(name="alpha", game="palworld")
    manager = FakeManager()
    manager._active = False
    stop_calls = []
    adapter = SimpleNamespace(
        stop_detached_agent=lambda current: stop_calls.append(current.name),
    )

    monkeypatch.setattr(shutdown, "_agent_running", lambda current: True)
    monkeypatch.setattr(shutdown.ProcessManager, "get", lambda name: manager)
    monkeypatch.setattr(shutdown, "get_game", lambda game: adapter)

    name, error = shutdown._stop_one(record)

    assert (name, error) == ("alpha", None)
    assert stop_calls == ["alpha"]
    assert manager.logs == [
        "Stopping detached managed agent",
        "Detached managed agent stopped",
    ]


def test_desktop_control_requires_token_and_runs_shutdown():
    completed = threading.Event()
    control = desktop_control.DesktopControlServer(
        0,
        "secret",
        lambda: shutdown.ShutdownResult(True, {"alpha": {"status": "stopped"}}),
        completed.set,
    )
    control.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{control.port}/desktop/shutdown",
            method="POST",
        )
        request.add_header("X-Palsitter-Token", "wrong")
        try:
            urllib.request.urlopen(request, timeout=2)
        except urllib.error.HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("invalid token was accepted")

        request = urllib.request.Request(
            f"http://127.0.0.1:{control.port}/desktop/shutdown",
            data=b"",
            method="POST",
            headers={"X-Palsitter-Token": "secret"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 200
            assert json.load(response)["ok"] is True
        assert completed.wait(2)
    finally:
        control.close()


def test_desktop_control_exposes_authenticated_force_shutdown():
    completed = threading.Event()
    forced = threading.Event()

    def force_shutdown():
        forced.set()
        return shutdown.ShutdownResult(True, {"alpha": {"status": "force_stopped"}})

    control = desktop_control.DesktopControlServer(
        0,
        "secret",
        lambda: shutdown.ShutdownResult(True, {}),
        completed.set,
        force_shutdown,
    )
    control.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{control.port}/desktop/force-shutdown",
            data=b"",
            method="POST",
            headers={"X-Palsitter-Token": "secret"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 200
            assert json.load(response)["instances"]["alpha"]["status"] == "force_stopped"
        assert forced.wait(2)
        assert completed.wait(2)
    finally:
        control.close()


def test_desktop_control_can_start_shared_shutdown_without_stopping_immediately():
    started = threading.Event()
    completed = threading.Event()

    def start_shutdown():
        started.set()
        return shutdown.ShutdownResult(True, {})

    control = desktop_control.DesktopControlServer(
        0,
        "secret",
        lambda: shutdown.ShutdownResult(True, {}),
        completed.set,
        start_shutdown=start_shutdown,
    )
    control.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{control.port}/desktop/shutdown",
            data=b"",
            method="POST",
            headers={"X-Palsitter-Token": "secret"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 200
            assert json.load(response)["ok"] is True
        assert started.wait(2)
        assert not completed.is_set()
    finally:
        control.close()


def test_desktop_control_can_close_gui_without_stopping_instances():
    gui_only_called = threading.Event()
    completed = threading.Event()

    def gui_only_shutdown():
        gui_only_called.set()
        return shutdown.ShutdownResult(True, {})

    control = desktop_control.DesktopControlServer(
        0,
        "secret",
        lambda: (_ for _ in ()).throw(AssertionError("full shutdown was called")),
        completed.set,
        gui_only_shutdown=gui_only_shutdown,
    )
    control.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{control.port}/desktop/gui-only",
            data=b"",
            method="POST",
            headers={"X-Palsitter-Token": "secret"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 200
            assert json.load(response)["ok"] is True
        assert gui_only_called.wait(2)
        assert completed.wait(2)
    finally:
        control.close()
