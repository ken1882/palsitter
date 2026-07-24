from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace

from module.webui import desktop_control, shutdown


class FakeManager:
    def __init__(self, *, remains_active: bool = False):
        self._active = True
        self.remains_active = remains_active
        self.stop_calls = 0
        self.kill_calls = 0
        self.prepare_calls = 0
        self.operation_busy = False

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


def _install_fakes(monkeypatch, managers):
    records = [SimpleNamespace(name=name, game="palworld") for name in managers]
    adapter = SimpleNamespace(
        capabilities=SimpleNamespace(lifecycle=True),
        save_before_shutdown=lambda record: None,
        is_running=lambda record: False,
    )
    monkeypatch.setattr(shutdown, "list_instances", lambda: records)
    monkeypatch.setattr(shutdown, "get_game", lambda game: adapter)
    monkeypatch.setattr(shutdown.ProcessManager, "get", lambda name: managers[name])


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


def test_force_shutdown_kills_every_active_instance(monkeypatch):
    managers = {"alpha": FakeManager(), "beta": FakeManager()}
    _install_fakes(monkeypatch, managers)

    result = shutdown.force_shutdown_all()

    assert result.ok is True
    assert all(manager.kill_calls == 1 for manager in managers.values())
    assert all(manager.stop_calls == 0 for manager in managers.values())
    assert all(item["status"] == "force_stopped" for item in result.instances.values())


def test_force_shutdown_uses_adapter_fallback_when_manager_is_inactive(monkeypatch):
    record = SimpleNamespace(name="alpha", game="palworld")
    manager = FakeManager()
    manager._active = False
    force_stop_calls = []
    adapter = SimpleNamespace(
        force_stop=lambda current: force_stop_calls.append(current.name),
    )

    monkeypatch.setattr(shutdown, "_active_records", lambda: [record])
    monkeypatch.setattr(shutdown, "_agent_running", lambda current: True)
    monkeypatch.setattr(shutdown.ProcessManager, "get", lambda name: manager)
    monkeypatch.setattr(shutdown, "get_game", lambda game: adapter)

    result = shutdown.force_shutdown_all()

    assert result.ok is True
    assert force_stop_calls == ["alpha"]
    assert result.instances["alpha"]["status"] == "force_stopped"


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
