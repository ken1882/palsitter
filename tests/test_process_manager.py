import datetime as dt
import queue
import threading
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from module.config import Profile, fixed_executable_path, profile_log_path, save_profile
from module.games import OperationProgress, UpdateInfo
from module.instances import DailyLogWriter, profile_server_output_path, prune_dated_log_files
from module.webui.process_manager import ProcessManager, _PROCESS_CONTEXT, _run_profile


@pytest.fixture(autouse=True)
def _mock_external_server_probe(monkeypatch):
    monkeypatch.setattr("module.games.registry.GameAdapter.is_running", lambda adapter, record: False)


class FakeMpProcess:
    def __init__(self, pid=999, alive=True):
        self.pid = pid
        self._alive = alive

    def is_alive(self):
        return self._alive


class FakePsProcess:
    def __init__(self, name, memory_percent=0.0, cpu_percent=0.0):
        self._name = name
        self._memory_percent = memory_percent
        self._cpu_percent = cpu_percent

    def name(self):
        return self._name

    def memory_percent(self):
        return self._memory_percent

    def cpu_percent(self):
        return self._cpu_percent


def test_supervisors_use_spawn_context():
    assert _PROCESS_CONTEXT.get_start_method() == "spawn"


def _manager_with_profile(tmp_path, monkeypatch, executable="PalServer.exe"):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test", executable=executable))
    manager = ProcessManager("test")
    manager._process = FakeMpProcess()
    return manager


def test_process_manager_replays_persisted_overview_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    log_path = profile_log_path("test")
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "\n".join(f"12:00:{index:02d} previous log {index}" for index in range(305)),
        encoding="utf-8",
    )

    manager = ProcessManager("test")

    assert len(manager.logs) == 300
    assert manager.logs[0] == "12:00:05 previous log 5"
    assert manager.logs[-1] == "12:00:304 previous log 304"


def test_resource_usage_returns_none_when_not_alive(tmp_path, monkeypatch):
    manager = _manager_with_profile(tmp_path, monkeypatch)
    manager._process = FakeMpProcess(alive=False)

    assert manager.resource_usage() is None


def test_resource_usage_finds_exact_external_instance_and_reuses_cpu_sample(
    tmp_path, monkeypatch
):
    manager = _manager_with_profile(tmp_path, monkeypatch)
    manager._process = FakeMpProcess(alive=False)

    class ExternalProcess(FakePsProcess):
        pid = 42

        def __init__(self):
            super().__init__("PalServer.exe", memory_percent=3.0)
            self.samples = 0

        def cpu_percent(self):
            self.samples += 1
            return 0.0 if self.samples == 1 else 12.5

        def children(self, recursive=True):
            return []

        def memory_info(self):
            return SimpleNamespace(rss=64 * 1024 * 1024)

    monkeypatch.setattr(
        "module.games.registry.GameAdapter.resource_processes",
        lambda adapter, record: (ExternalProcess(),),
    )

    assert manager.resource_usage() == {
        "cpu_percent": 0.0,
        "memory_percent": 3.0,
        "memory_bytes": 64 * 1024 * 1024,
    }
    assert manager.resource_usage() == {
        "cpu_percent": 12.5,
        "memory_percent": 3.0,
        "memory_bytes": 64 * 1024 * 1024,
    }


def test_resource_usage_picks_matching_child_by_executable_name(tmp_path, monkeypatch):
    manager = _manager_with_profile(tmp_path, monkeypatch, executable="PalServer.exe")
    other = FakePsProcess("steamcmd.exe", memory_percent=1.0)
    target = FakePsProcess(Path(fixed_executable_path("test")).name, memory_percent=42.5)
    monkeypatch.setattr(
        "module.webui.process_manager.psutil.Process",
        lambda pid: SimpleNamespace(children=lambda recursive=True: [other, target]),
    )

    assert manager.resource_usage() == {"cpu_percent": 0.0, "memory_percent": 42.5}


def test_resource_usage_falls_back_to_sole_child(tmp_path, monkeypatch):
    manager = _manager_with_profile(tmp_path, monkeypatch, executable="PalServer.exe")
    only_child = FakePsProcess("PalServer-Win64-Shipping.exe", memory_percent=7.0)
    monkeypatch.setattr(
        "module.webui.process_manager.psutil.Process",
        lambda pid: SimpleNamespace(children=lambda recursive=True: [only_child]),
    )

    assert manager.resource_usage() == {"cpu_percent": 0.0, "memory_percent": 7.0}


def test_resource_usage_sums_palserver_process_tree_rss(tmp_path, monkeypatch):
    manager = _manager_with_profile(tmp_path, monkeypatch, executable="PalServer.exe")

    class TreeProcess(FakePsProcess):
        def __init__(self, name, pid, rss, children=()):
            super().__init__(name, memory_percent=1.0, cpu_percent=2.0)
            self.pid = pid
            self._rss = rss
            self._children = list(children)

        def children(self, recursive=True):
            return self._children

        def memory_info(self):
            return SimpleNamespace(rss=self._rss)

    worker = TreeProcess("PalServer-Win64-Shipping.exe", 3, 30)
    target = TreeProcess(Path(fixed_executable_path("test")).name, 2, 20, [worker])
    other = TreeProcess("steamcmd.exe", 4, 99)
    monkeypatch.setattr(
        "module.webui.process_manager.psutil.Process",
        lambda pid: SimpleNamespace(children=lambda recursive=True: [target, worker, other]),
    )

    assert manager.resource_usage() == {
        "cpu_percent": 4.0,
        "memory_percent": 2.0,
        "memory_bytes": 50,
    }


def test_resource_usage_returns_none_when_no_children_resolve(tmp_path, monkeypatch):
    manager = _manager_with_profile(tmp_path, monkeypatch, executable="PalServer.exe")
    a = FakePsProcess("steamcmd.exe")
    b = FakePsProcess("other.exe")
    monkeypatch.setattr(
        "module.games.registry.GameAdapter.resource_processes",
        lambda adapter, record: (),
    )
    monkeypatch.setattr(
        "module.webui.process_manager.psutil.Process",
        lambda pid: SimpleNamespace(children=lambda recursive=True: [a, b]),
    )

    assert manager.resource_usage() is None


def test_resource_usage_returns_none_on_access_denied(tmp_path, monkeypatch):
    manager = _manager_with_profile(tmp_path, monkeypatch)

    def raise_access_denied(pid):
        raise psutil.AccessDenied(pid)

    monkeypatch.setattr("module.webui.process_manager.psutil.Process", raise_access_denied)

    assert manager.resource_usage() is None


def test_start_installs_steamcmd_before_spawning_supervisor(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))
    calls = []

    class FakeProcess:
        pid = 123

        def __init__(self, *args, **kwargs):
            calls.append("process-created")

        def start(self):
            calls.append("process-started")

        def is_alive(self):
            return True

    def fake_install(
        adapter, record, log=print, progress=None, *, validate=False, stop_requested=None
    ):
        calls.append(f"install-{record.name}")
        log("installed")
        if progress:
            progress(OperationProgress("install", "complete", 100.0, "installed"))
        return UpdateInfo(status="unknown")

    monkeypatch.setattr("module.games.registry.GameAdapter.install_or_update", fake_install)
    monkeypatch.setattr("module.webui.process_manager._PROCESS_FACTORY", FakeProcess)

    manager = ProcessManager("test")
    manager.start()
    manager._bootstrap_thread.join(timeout=1)

    assert calls == ["install-test", "process-created", "process-started"]
    assert any("installed" in line for line in manager.logs)
    assert manager.state == "booting"


def test_start_exposes_installing_state_while_bootstrap_is_running(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))
    release = threading.Event()

    def fake_bootstrap(adapter, record, log):
        release.wait(timeout=1)

    monkeypatch.setattr("module.games.registry.GameAdapter.bootstrap", fake_bootstrap)
    manager = ProcessManager("test")

    manager.start()

    assert manager.state == "installing"
    release.set()
    manager._bootstrap_thread.join(timeout=1)


def test_stop_cancels_inflight_bootstrap_without_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))
    started = threading.Event()
    release = threading.Event()

    def fake_install(
        adapter, record, log=print, progress=None, *, validate=False, stop_requested=None
    ):
        started.set()
        assert stop_requested is not None
        release.wait(timeout=1)
        assert stop_requested()
        raise RuntimeError("SteamCMD stopped")

    monkeypatch.setattr("module.games.registry.GameAdapter.install_or_update", fake_install)
    manager = ProcessManager("test")

    manager.start()
    assert started.wait(timeout=1)
    assert manager.state == "installing"
    assert manager.stop() is True
    release.set()
    manager._bootstrap_thread.join(timeout=1)

    assert manager.state == "inactive"
    assert manager.warning is False
    assert any("Start cancelled" in line for line in manager.logs)


def test_start_skips_steamcmd_bootstrap_when_server_is_already_reachable(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))
    calls = []

    class FakeProcess:
        pid = 123

        def __init__(self, *args, **kwargs):
            calls.append("process-created")

        def start(self):
            calls.append("process-started")

        def is_alive(self):
            return True

    monkeypatch.setattr("module.games.registry.GameAdapter.is_running", lambda adapter, record: True)
    monkeypatch.setattr(
        "module.games.registry.GameAdapter.bootstrap",
        lambda *args, **kwargs: calls.append("steamcmd"),
    )
    monkeypatch.setattr("module.webui.process_manager._PROCESS_FACTORY", FakeProcess)

    manager = ProcessManager("test")
    manager.start()
    manager._bootstrap_thread.join(timeout=1)

    assert calls == ["process-created", "process-started"]
    assert any("skipping installation" in line for line in manager.logs)


def test_stop_during_external_attach_still_spawns_responsible_supervisor(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))
    probe_started = threading.Event()
    release_probe = threading.Event()
    calls = []

    def external_probe(adapter, record):
        probe_started.set()
        release_probe.wait(timeout=1)
        return True

    class FakeProcess:
        pid = 123

        def __init__(self, *args, **kwargs):
            calls.append("process-created")

        def start(self):
            calls.append("process-started")

        def is_alive(self):
            return True

    monkeypatch.setattr("module.games.registry.GameAdapter.is_running", external_probe)
    monkeypatch.setattr("module.webui.process_manager._PROCESS_FACTORY", FakeProcess)
    manager = ProcessManager("test")

    manager.start()
    assert probe_started.wait(timeout=1)
    manager.stop()
    release_probe.set()
    manager._bootstrap_thread.join(timeout=1)

    assert manager._stop_requested.is_set()
    assert calls == ["process-created", "process-started"]


def test_start_does_not_spawn_when_steamcmd_install_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))
    calls = []

    def fake_install(
        adapter, record, log=print, progress=None, *, validate=False, stop_requested=None
    ):
        calls.append(f"install-{record.name}")
        raise RuntimeError("download failed")

    monkeypatch.setattr("module.games.registry.GameAdapter.install_or_update", fake_install)
    monkeypatch.setattr("module.webui.process_manager._PROCESS_FACTORY", lambda *a, **k: calls.append("spawned"))

    manager = ProcessManager("test")
    manager.start()
    manager._bootstrap_thread.join(timeout=1)

    assert calls == ["install-test"]
    assert manager.warning is True
    assert any("Start failed: download failed" in line for line in manager.logs)


def test_start_of_installed_server_updates_before_spawning_supervisor(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))
    executable = fixed_executable_path("test")
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("stub", encoding="utf-8")
    calls = []
    expected = UpdateInfo("200", "200", status="up_to_date")

    class FakeProcess:
        pid = 123

        def __init__(self, *args, **kwargs):
            calls.append("process-created")

        def start(self):
            calls.append("process-started")

        def is_alive(self):
            return True

    monkeypatch.setattr(
        "module.games.registry.GameAdapter.install_or_update",
        lambda *args, **kwargs: (calls.append("update"), expected)[1],
    )
    monkeypatch.setattr("module.webui.process_manager._PROCESS_FACTORY", FakeProcess)

    manager = ProcessManager("test")
    assert manager.start() is True
    manager._bootstrap_thread.join(timeout=1)

    assert calls == ["update", "process-created", "process-started"]
    assert manager.ownership == "managed"
    assert manager.update_info == expected


def test_restart_start_skips_update_before_spawning_supervisor(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))
    executable = fixed_executable_path("test")
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("stub", encoding="utf-8")
    calls = []

    class FakeProcess:
        pid = 123

        def __init__(self, *args, **kwargs):
            calls.append("process-created")

        def start(self):
            calls.append("process-started")

        def is_alive(self):
            return True

    monkeypatch.setattr(
        "module.games.registry.GameAdapter.install_or_update",
        lambda *args, **kwargs: calls.append("update"),
    )
    monkeypatch.setattr("module.webui.process_manager._PROCESS_FACTORY", FakeProcess)

    manager = ProcessManager("test")
    assert manager.start(update=False) is True
    manager._bootstrap_thread.join(timeout=1)

    assert calls == ["process-created", "process-started"]
    assert any("without SteamCMD update" in line for line in manager.logs)


def test_update_on_start_off_skips_update_for_installed_server(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test", update_on_start=False))
    executable = fixed_executable_path("test")
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("stub", encoding="utf-8")
    calls = []

    class FakeProcess:
        pid = 123

        def __init__(self, *args, **kwargs):
            calls.append("process-created")

        def start(self):
            calls.append("process-started")

        def is_alive(self):
            return True

    monkeypatch.setattr(
        "module.games.registry.GameAdapter.install_or_update",
        lambda *args, **kwargs: calls.append("update"),
    )
    monkeypatch.setattr("module.webui.process_manager._PROCESS_FACTORY", FakeProcess)

    manager = ProcessManager("test")
    assert manager.start() is True
    manager._bootstrap_thread.join(timeout=1)

    assert calls == ["process-created", "process-started"]
    assert any("without SteamCMD update" in line for line in manager.logs)


def test_update_on_start_off_still_installs_missing_server(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test", update_on_start=False))
    calls = []

    monkeypatch.setattr(
        "module.games.registry.GameAdapter.install_or_update",
        lambda *args, **kwargs: calls.append("update"),
    )
    class FakeProcess:
        pid = 123

        def __init__(self, *args, **kwargs):
            calls.append("process-created")

        def start(self):
            pass

        def is_alive(self):
            return True

    monkeypatch.setattr("module.webui.process_manager._PROCESS_FACTORY", FakeProcess)

    manager = ProcessManager("test")
    manager.start()
    manager._bootstrap_thread.join(timeout=1)

    assert calls == ["update", "process-created"]


def test_explicit_check_update_runs_async_and_stores_typed_result(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))
    expected = UpdateInfo("100", "200", status="update_available")
    calls = []

    def fake_check(adapter, record, progress=None, *, force=False, log=print):
        calls.append((record.name, force))
        progress(OperationProgress("check_update", "checking", None, "checking"))
        return expected

    monkeypatch.setattr("module.games.registry.GameAdapter.check_update", fake_check)
    manager = ProcessManager("test")

    assert manager.check_update(force=True) is True
    manager._operation_thread.join(timeout=1)

    assert calls == [("test", True)]
    assert manager.update_info == expected
    assert manager.state == "inactive"
    assert manager.operation_progress.phase == "complete"


def test_check_update_runs_while_managed_server_is_active(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))
    expected = UpdateInfo("100", "200", status="update_available")

    def fake_check(adapter, record, progress=None, *, force=False, log=print):
        log("SteamCMD: public build output")
        return expected

    monkeypatch.setattr("module.games.registry.GameAdapter.check_update", fake_check)
    manager = ProcessManager("test")
    manager._process = FakeMpProcess()
    manager._set_state("running")

    assert manager.check_update(force=True) is True
    manager._operation_thread.join(timeout=1)

    assert manager.state == "running"
    assert manager.update_info == expected
    assert any("Palworld update available: 100 -> 200" in line for line in manager.logs)
    assert any("SteamCMD: public build output" in line for line in manager.logs)


def test_check_update_restores_running_state_when_supervisor_probe_changes_during_check(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))
    expected = UpdateInfo("100", "100", status="up_to_date")

    def fake_check(adapter, record, progress=None, *, force=False, log=print):
        return expected

    class ProcessProbeChanges(FakeMpProcess):
        def __init__(self):
            super().__init__()
            self.probes = 0

        def is_alive(self):
            self.probes += 1
            return self.probes == 1

    monkeypatch.setattr("module.games.registry.GameAdapter.check_update", fake_check)
    manager = ProcessManager("test")
    manager._process = ProcessProbeChanges()
    manager._set_state("running")

    assert manager.check_update(force=True) is True
    manager._operation_thread.join(timeout=1)

    assert manager.state == "running"


def test_check_update_keeps_banner_running_for_reachable_unmanaged_server(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))
    started = threading.Event()
    release = threading.Event()

    monkeypatch.setattr("module.games.registry.GameAdapter.is_running", lambda *args: True)

    def fake_check(adapter, record, progress=None, *, force=False, log=print):
        started.set()
        release.wait(timeout=1)
        return UpdateInfo("100", "100", status="up_to_date")

    monkeypatch.setattr("module.games.registry.GameAdapter.check_update", fake_check)
    manager = ProcessManager("test")

    assert manager.check_update(force=True) is True
    assert started.wait(timeout=1)
    assert manager.display_state == "running"

    release.set()
    manager._operation_thread.join(timeout=1)
    assert manager.state == "inactive"


@pytest.mark.parametrize(("validate", "kind"), [(False, "update"), (True, "validate")])
def test_explicit_update_is_async_and_leaves_server_inactive(
    tmp_path, monkeypatch, validate, kind
):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))
    calls = []

    def fake_update(
        adapter, record, log=print, progress=None, *, validate=False, stop_requested=None
    ):
        calls.append(validate)
        progress(OperationProgress(kind, "updating", 25.0, "updating"))
        return UpdateInfo("200", "200", status="up_to_date")

    monkeypatch.setattr("module.games.registry.GameAdapter.install_or_update", fake_update)
    manager = ProcessManager("test")

    assert manager.update(validate=validate) is True
    manager._operation_thread.join(timeout=1)

    assert calls == [validate]
    assert manager.state == "inactive"
    assert manager.update_info.installed_build_id == "200"
    assert manager.operation_progress == OperationProgress(
        kind, "complete", 100.0, "Operation completed"
    )


def test_update_is_rejected_while_server_is_active(tmp_path, monkeypatch):
    manager = _manager_with_profile(tmp_path, monkeypatch)
    manager._set_state("running")

    assert manager.update() is False
    assert any("must be inactive" in line for line in manager.logs)


def test_update_refuses_reachable_external_server(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))
    monkeypatch.setattr("module.games.registry.GameAdapter.is_running", lambda *args: True)
    manager = ProcessManager("test")

    assert manager.update() is True
    manager._operation_thread.join(timeout=1)

    assert manager.state == "inactive"
    assert manager.display_state == "running"
    assert manager.ownership == "external"
    assert "external server" in manager.operation_progress.error


def test_display_state_recovers_external_process_after_gui_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))
    running = True
    monkeypatch.setattr(
        "module.games.registry.GameAdapter.is_running",
        lambda adapter, record: running,
    )
    manager = ProcessManager("test")

    assert manager.state == "inactive"
    assert manager.display_state == "running"
    assert manager.ownership == "none"

    running = False
    manager._display_probe_at = 0
    assert manager.display_state == "inactive"
    assert manager.ownership == "none"


def test_external_attachment_disallows_kill_and_restart(tmp_path, monkeypatch):
    manager = _manager_with_profile(tmp_path, monkeypatch)
    manager._ownership = "external"
    manager._set_state("running")

    assert manager.kill() is False
    assert manager.restart() is False
    assert manager.alive is True


def test_stop_requests_graceful_shutdown_without_killing_process_tree(monkeypatch):
    manager = ProcessManager("test")
    manager._process = FakeMpProcess()
    manager._set_state("running")
    monkeypatch.setattr(
        "module.webui.process_manager._kill_process_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not force kill")),
    )

    manager.stop()

    assert manager.state == "stopping"
    assert manager._stop_requested.is_set()
    assert manager.alive is True


def test_kill_force_stops_process_tree_and_returns_inactive(monkeypatch):
    calls = []

    class KillableProcess(FakeMpProcess):
        def join(self, timeout=None):
            calls.append(("join", timeout))
            self._alive = False

    manager = ProcessManager("test")
    manager._process = KillableProcess()
    manager._set_state("stopping")
    monkeypatch.setattr(
        "module.webui.process_manager._kill_process_tree",
        lambda pid, grace: calls.append(("kill", pid, grace)),
    )

    manager.kill()

    assert calls == [("kill", 999, 0), ("join", 2)]
    assert manager.state == "inactive"


def test_handoff_joins_only_supervisor_and_leaves_managed_server_running(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))
    monkeypatch.setattr(
        "module.games.registry.GameAdapter.is_running",
        lambda adapter, record: True,
    )

    class HandoffProcess(FakeMpProcess):
        def join(self, timeout=None):
            self._alive = False

        def terminate(self):
            self._alive = False

    manager = ProcessManager("test")
    manager._process = HandoffProcess()
    manager._ownership = "managed"
    manager._state = "running"
    monkeypatch.setattr(
        "module.webui.process_manager._kill_process_tree",
        lambda *args, **kwargs: pytest.fail("handoff must not kill a process tree"),
    )

    assert manager.handoff() is True
    assert manager._handoff_requested.is_set()
    assert manager.state == "inactive"


def test_append_log_writes_overview_log_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))

    manager = ProcessManager("test")
    manager.append_log("hello file log")

    log_path = profile_log_path("test")
    assert log_path.exists()
    assert log_path.name == f"overview-{dt.datetime.now():%Y%m%d}.log"
    assert "hello file log" in log_path.read_text(encoding="utf-8")


def test_daily_log_writer_switches_files_when_the_date_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    current = [dt.datetime(2026, 7, 22, 23, 59, 59)]
    writer = DailyLogWriter(
        lambda: profile_server_output_path("test", current[0])
    )

    writer.write(b"before midnight\n")
    writer.flush()
    current[0] = dt.datetime(2026, 7, 23, 0, 0, 1)
    writer.write(b"after midnight\n")
    writer.close()

    assert profile_server_output_path("test", current[0]).name == "palserver-20260723.log"
    assert (
        profile_server_output_path("test", dt.date(2026, 7, 22)).read_bytes()
        == b"before midnight\n"
    )
    assert (
        profile_server_output_path("test", dt.date(2026, 7, 23)).read_bytes()
        == b"after midnight\n"
    )


def test_dated_log_retention_removes_files_older_than_thirty_days(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    today = dt.date(2026, 7, 22)
    logs_dir = profile_log_path("test", today).parent
    logs_dir.mkdir(parents=True)

    old_date = today - dt.timedelta(days=30)
    retained_date = today - dt.timedelta(days=29)
    for path in (
        profile_log_path("test", old_date),
        profile_server_output_path("test", old_date),
        profile_log_path("test", retained_date),
        profile_server_output_path("test", retained_date),
    ):
        path.write_text("log", encoding="utf-8")
    (logs_dir / "overview.log").write_text("legacy", encoding="utf-8")
    (logs_dir / "palserver-output.log").write_text("legacy", encoding="utf-8")

    prune_dated_log_files(logs_dir, today)

    assert not profile_log_path("test", old_date).exists()
    assert not profile_server_output_path("test", old_date).exists()
    assert profile_log_path("test", retained_date).exists()
    assert profile_server_output_path("test", retained_date).exists()
    assert (logs_dir / "overview.log").exists()
    assert (logs_dir / "palserver-output.log").exists()


def test_read_logs_drains_queued_messages(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))

    class OneLoopProcess:
        exitcode = 0

        def __init__(self):
            self.calls = 0

        def is_alive(self):
            self.calls += 1
            return self.calls == 1

    class FakeQueue:
        def __init__(self):
            self.extra = ["second", "third"]

        def get(self, timeout=1):
            return "first"

        def get_nowait(self):
            if self.extra:
                return self.extra.pop(0)
            raise queue.Empty

    manager = ProcessManager("test")
    manager._process = OneLoopProcess()
    manager._queue = FakeQueue()

    manager._read_logs()

    assert [line.rsplit(" ", 1)[-1] for line in manager.logs] == ["first", "second", "third"]


def test_read_logs_manual_stop_exit_code_stays_inactive(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))

    class StoppedProcess:
        exitcode = 15

        def is_alive(self):
            return False

    manager = ProcessManager("test")
    manager._process = StoppedProcess()
    manager._stop_requested.set()
    manager._stop_reason = "manual stop"

    manager._read_logs()

    assert manager.state == "inactive"
    assert manager.warning is False
    assert any("Supervisor exited after manual stop with code 15" in line for line in manager.logs)


def test_read_logs_unexpected_exit_code_sets_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))

    class CrashedProcess:
        exitcode = 1

        def is_alive(self):
            return False

    manager = ProcessManager("test")
    manager._process = CrashedProcess()

    manager._read_logs()

    assert manager.state == "warning"
    assert any("Supervisor exited with code 1" in line for line in manager.logs)


def test_run_profile_logs_traceback_before_reraising(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    save_profile(Profile(name="test"))
    messages = []

    class FakeQueue:
        def put(self, message):
            messages.append(message)

    class FakeEvent:
        def is_set(self):
            return False

    monkeypatch.setattr(
        "module.games.registry.GameAdapter.supervise",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    try:
        _run_profile("test", FakeQueue(), FakeEvent())
    except RuntimeError:
        pass
    else:
        raise AssertionError("_run_profile should reraise supervisor exceptions")

    joined = "\n".join(messages)
    assert "[test] Supervisor crashed:" in joined
    assert "RuntimeError: boom" in joined
    assert "Traceback (most recent call last):" in joined


def test_process_manager_drains_bounded_structured_lifecycle_events():
    manager = ProcessManager("test")
    manager._event_queue = queue.Queue()
    next_restart = dt.datetime(2026, 7, 17, 4, 30)
    for index in range(22):
        manager._event_queue.put(
            SimpleNamespace(
                timestamp=dt.datetime(2026, 7, 16, 1, index),
                reason=f"reason-{index}",
                outcome=f"outcome-{index}",
                next_scheduled_restart=next_restart if index == 21 else None,
            )
        )

    events = manager.recent_lifecycle_events

    assert len(events) == 20
    assert events[0].reason == "reason-2"
    assert events[-1].outcome == "outcome-21"
    assert manager.next_scheduled_restart == next_restart


def test_process_manager_does_not_audit_update_checks(tmp_path, monkeypatch):
    manager = _manager_with_profile(tmp_path, monkeypatch)
    manager._event_queue = queue.Queue()
    calls = []
    monkeypatch.setattr(
        manager,
        "_record_adapter_event",
        lambda *args: calls.append(args),
    )
    checked_at = dt.datetime(2026, 7, 24, 10, 0, tzinfo=dt.timezone.utc)
    manager._event_queue.put(
        SimpleNamespace(
            type="update_check",
            timestamp=checked_at,
            message="Palworld update check: up_to_date",
            details={
                "installed_build_id": "100",
                "available_build_id": "100",
                "status": "up_to_date",
            },
        )
    )

    manager._drain_events()

    assert manager.update_info == UpdateInfo(
        "100", "100", checked_at, "up_to_date"
    )
    assert calls == []
