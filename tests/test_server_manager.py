import datetime as dt
import io
import os
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from module.config import Profile, game_user_settings_path
from module.worldsettings.service import resolve_ini_path
from module.games.registry import UpdateInfo
from module.server.manager import PalServerManager
from module.steamcmd import steamcmd_platform_args


@pytest.fixture(autouse=True)
def _mock_external_server_probe(monkeypatch):
    monkeypatch.setattr(
        "module.games.palworld.server.manager.instance_is_running",
        lambda profile: False,
    )


class FakeProc:
    pid = 123

    def __init__(self, stdout=None, returncode=None):
        self.returncode = returncode
        self.terminated = False
        self.stdout = stdout

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class FakeRest:
    def __init__(self):
        self.announcements = []
        self.shutdowns = []
        self.stops = 0
        self.saves = 0

    def announce(self, message):
        self.announcements.append(message)

    def shutdown(self):
        self.shutdowns.append(True)

    def stop(self):
        self.stops += 1

    def save(self):
        self.saves += 1

    def metrics(self):
        return {"currentplayernum": 1, "maxplayernum": 4, "serverfps": 60, "uptime": 10}


class FakeBackupService:
    def __init__(self, backup_before_result=None, create_error=None, create_result=None):
        self.create_backup_calls = 0
        self.backup_before_calls = []
        self.restore_calls = []
        self._backup_before_result = backup_before_result
        self._create_error = create_error
        self._create_result = create_result

    def create_backup(self):
        self.create_backup_calls += 1
        if self._create_error is not None:
            raise self._create_error
        return self._create_result

    def backup_before(self, cutoff):
        self.backup_before_calls.append(cutoff)
        return self._backup_before_result

    def restore(self, path):
        self.restore_calls.append(path)


def test_update_failure_prevents_launch(monkeypatch):
    launched = False

    def popen(*args, **kwargs):
        nonlocal launched
        launched = True
        return FakeProc()

    manager = PalServerManager(
        Profile(name="test"),
        popen_factory=popen,
        pty_process_factory=lambda *a, **k: FakeProc(stdout=iter(["boom\n"]), returncode=1),
    )

    with pytest.raises(RuntimeError):
        manager.start(update=True)
    assert launched is False


def test_memory_restart_announcement_uses_countdown_value(monkeypatch):
    proc = FakeProc()
    rest = FakeRest()

    rss = 64 * 1024 * 1024
    monkeypatch.setattr(
        "module.server.manager.psutil.Process",
        lambda pid: SimpleNamespace(
            status=lambda: "running",
            memory_info=lambda: SimpleNamespace(rss=rss),
            children=lambda recursive: [],
        ),
    )

    manager = PalServerManager(
        Profile(name="test", memory_restart_mb=50, memory_restart_countdown_minutes=3),
        rest_client=rest,
        popen_factory=lambda *a, **k: proc,
        pty_process_factory=lambda *a, **k: FakeProc(stdout=iter([]), returncode=0),
    )
    manager.start()
    manager.monitor_once()
    manager.monitor_once()
    manager.monitor_once()

    assert rest.announcements == [
        "Server will restart in 3 minutes due to excessive PalServer memory use"
    ]
    assert manager.countdown == 2


def test_start_passes_port_flags(monkeypatch):
    captured_cmd = {}

    def popen(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(
        "module.server.manager.psutil.Process", lambda pid: SimpleNamespace(status=lambda: "running")
    )

    manager = PalServerManager(
        Profile(
            name="test",
            game_port=8222,
            query_port=27099,
            launch_enable_gamedata_api=False,
        ),
        popen_factory=popen,
        pty_process_factory=lambda *a, **k: FakeProc(stdout=iter([]), returncode=0),
    )
    manager.start()

    assert captured_cmd["cmd"] == ["PalServer.exe", "-port=8222", "-queryport=27099"]


@pytest.mark.skipif(os.name != "nt", reason="Windows detached-process behavior is Windows-only")
def test_windows_start_uses_console_binary_for_detached_file_output(tmp_path, monkeypatch):
    captured = {}
    install_dir = tmp_path / "PalServer"
    wrapper = install_dir / "PalServer.exe"
    console = install_dir / "Pal" / "Binaries" / "Win64" / "PalServer-Win64-Shipping-Cmd.exe"
    console.parent.mkdir(parents=True)
    wrapper.write_text("wrapper", encoding="utf-8")
    console.write_text("console", encoding="utf-8")

    def popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr("module.games.palworld.server.manager.WINDOWS", True)
    monkeypatch.setattr(
        "module.server.manager.psutil.Process", lambda pid: SimpleNamespace(status=lambda: "running")
    )
    manager = PalServerManager(
        Profile(
            name="test",
            executable=str(wrapper),
            workdir=str(install_dir),
            launch_enable_gamedata_api=False,
        ),
        popen_factory=popen,
    )

    manager.start()

    assert captured["cmd"] == [
        str(console),
        "-stdout",
        "-FullStdOutLogOutput",
        "-FORCELOGFLUSH",
        "-port=8211",
        "-queryport=27015",
    ]
    assert captured["kwargs"]["cwd"] == str(install_dir)
    assert captured["kwargs"]["stderr"] == subprocess.STDOUT
    assert captured["kwargs"]["creationflags"] == subprocess.DETACHED_PROCESS
    assert captured["kwargs"]["stdout"] == subprocess.PIPE


def test_start_reports_updating_booting_and_running_states(monkeypatch):
    states = []
    monkeypatch.setattr(
        "module.server.manager.psutil.Process", lambda pid: SimpleNamespace(status=lambda: "running")
    )
    manager = PalServerManager(
        Profile(name="test"),
        state_callback=states.append,
        popen_factory=lambda *args, **kwargs: FakeProc(),
        pty_process_factory=lambda *args, **kwargs: FakeProc(stdout=iter([]), returncode=0),
    )

    manager.start(update=True)

    assert states == ["updating", "booting", "running"]


def test_start_attaches_to_reachable_external_server_without_update_or_launch():
    states = []
    logs = []
    update_calls = []
    launch_calls = []
    reachable = [True]
    manager = PalServerManager(
        Profile(name="test"),
        logger=logs.append,
        state_callback=states.append,
        running_probe=lambda profile: reachable[0],
        pty_process_factory=lambda *args, **kwargs: update_calls.append(args),
        popen_factory=lambda *args, **kwargs: launch_calls.append(args),
    )

    manager.start(update=True)

    assert manager.external_attached is True
    assert states == ["running"]
    assert update_calls == []
    assert launch_calls == []
    assert "attached watcher" in logs[0]

    manager.monitor_once()
    assert states == ["running"]
    reachable[0] = False
    manager.monitor_once()
    assert states == ["running", "warning"]


def test_supervisor_stop_shuts_down_attached_external_server():
    rest = FakeRest()
    states = []
    manager = PalServerManager(
        Profile(name="test", shutdown_wait_seconds=0),
        rest_client=rest,
        state_callback=states.append,
        running_probe=lambda profile: True,
        stop_requested=lambda: True,
    )

    manager.supervise_loop()

    assert rest.shutdowns == [True]
    assert rest.stops == 1
    assert manager.external_attached is False
    assert states == ["running", "stopping", "inactive", "inactive"]


def test_supervisor_polls_owned_process_crashes_without_waiting_for_periodic_tick(
    monkeypatch,
):
    proc = FakeProc()
    stop = False
    monitor_calls = []
    observed_alive = []
    sleeps = []

    manager = PalServerManager(
        Profile(name="test"),
        stop_requested=lambda: stop,
        sleep=lambda seconds: (sleeps.append(seconds), setattr(proc, "returncode", 1)),
        popen_factory=lambda *args, **kwargs: proc,
    )

    def fake_start(*args, **kwargs):
        manager.process = proc

    def fake_monitor_once():
        nonlocal stop
        monitor_calls.append(True)
        observed_alive.append(manager.alive)
        if len(monitor_calls) == 2:
            proc.returncode = 0
            stop = True

    monkeypatch.setattr(manager, "start", fake_start)
    monkeypatch.setattr(manager, "monitor_once", fake_monitor_once)

    manager.supervise_loop(interval_seconds=60)

    assert len(monitor_calls) == 2
    assert observed_alive == [True, False]
    assert sleeps == [1]


def test_supervisor_keeps_periodic_work_at_configured_interval(monkeypatch):
    proc = FakeProc()
    stop = False
    monitor_calls = []
    sleeps = []

    manager = PalServerManager(
        Profile(name="test"),
        stop_requested=lambda: stop,
        sleep=lambda seconds: sleeps.append(seconds),
        popen_factory=lambda *args, **kwargs: proc,
    )

    def fake_start(*args, **kwargs):
        manager.process = proc

    def fake_monitor_once():
        nonlocal stop
        monitor_calls.append(True)
        if len(monitor_calls) == 2:
            proc.returncode = 0
            stop = True

    monkeypatch.setattr(manager, "start", fake_start)
    monkeypatch.setattr(manager, "monitor_once", fake_monitor_once)

    manager.supervise_loop(interval_seconds=60)

    assert len(monitor_calls) == 2
    assert sleeps == [1] * 60


def test_steam_update_uses_fixed_palworld_server_app_id():
    manager = PalServerManager(Profile(name="test"))

    assert manager.steam_update_args() == [
        "steamcmd",
        *steamcmd_platform_args(),
        "+force_install_dir",
        str(Path.cwd()),
        "+login",
        "anonymous",
        "+app_update",
        "2394010",
        "+quit",
    ]


def test_linux_steam_update_selects_linux_platform(monkeypatch):
    monkeypatch.setattr("module.steamcmd.WINDOWS", False)
    manager = PalServerManager(Profile(name="test"))

    assert manager.steam_update_args()[1:5] == [
        "+@sSteamCmdForcePlatformType",
        "linux",
        "+force_install_dir",
        str(Path.cwd()),
    ]


def test_start_uses_executable_parent_as_working_directory(tmp_path, monkeypatch):
    captured = {}
    exe = tmp_path / "server" / "PalServer.exe"
    exe.parent.mkdir()
    exe.write_text("stub", encoding="utf-8")

    def pty_process(cmd, **kwargs):
        captured["update_cwd"] = kwargs["cwd"]
        return FakeProc(stdout=iter([]), returncode=0)

    def popen(cmd, **kwargs):
        captured["start_cwd"] = kwargs["cwd"]
        return FakeProc()

    monkeypatch.setattr(
        "module.server.manager.psutil.Process", lambda pid: SimpleNamespace(status=lambda: "running")
    )

    manager = PalServerManager(
        Profile(name="test", workdir=str(tmp_path / "old"), executable=str(exe)),
        popen_factory=popen,
        pty_process_factory=pty_process,
    )
    manager.start(update=True)

    assert captured["update_cwd"] == str(exe.parent)
    assert captured["start_cwd"] == str(exe.parent)


def test_start_resolves_relative_canonical_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    install_dir = Path("profile/default/steamcmd/steamapps/common/PalServer")
    install_dir.mkdir(parents=True)
    (install_dir / "PalServer.exe").write_text("server executable", encoding="utf-8")
    captured = {}

    def popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs["cwd"]
        captured["exists_during_launch"] = Path(cmd[0]).is_file()
        return FakeProc(stdout=iter([]))

    monkeypatch.setattr(
        "module.server.manager.psutil.Process", lambda pid: SimpleNamespace(status=lambda: "running")
    )
    manager = PalServerManager(
        Profile(
            name="default",
            workdir=str(install_dir),
            executable=str(install_dir / "PalServer.exe"),
            launch_enable_gamedata_api=False,
        ),
        popen_factory=popen,
    )

    manager.start(update=False)

    expected_dir = (tmp_path / install_dir).resolve()
    assert captured == {
        "cmd": [
            str(expected_dir / "PalServer.exe"),
            "-port=8211",
            "-queryport=27015",
        ],
        "cwd": str(expected_dir),
        "exists_during_launch": True,
    }


def test_linux_fixed_server_launches_from_server_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("module.games.palworld.config.WINDOWS", False)
    monkeypatch.setattr("module.games.palworld.server.manager.WINDOWS", False)
    profile = Profile(name="linux", launch_enable_gamedata_api=False)
    profile.apply_fixed_paths()
    executable = Path(profile.executable)
    executable.parent.mkdir(parents=True)
    executable.write_text("server executable", encoding="utf-8")
    captured = {}

    def popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs["cwd"]
        captured["exists_during_launch"] = Path(cmd[0]).is_file()
        return FakeProc(stdout=iter([]))

    monkeypatch.setattr(
        "module.server.manager.psutil.Process", lambda pid: SimpleNamespace(status=lambda: "running")
    )
    PalServerManager(profile, popen_factory=popen).start(update=False)

    expected_dir = (tmp_path / profile.workdir).resolve()
    expected_executable = expected_dir / "Pal" / "Binaries" / "Linux" / "PalServer-Linux-Shipping"
    assert captured == {
        "cmd": [str(expected_executable), "-port=8211", "-queryport=27015"],
        "cwd": str(expected_dir),
        "exists_during_launch": True,
    }


def test_linux_fixed_server_uses_palserver_launcher_script(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("module.games.palworld.config.WINDOWS", False)
    monkeypatch.setattr("module.games.palworld.server.manager.WINDOWS", False)
    profile = Profile(name="linux", launch_enable_gamedata_api=False)
    profile.apply_fixed_paths()
    executable = Path(profile.executable)
    executable.parent.mkdir(parents=True)
    executable.write_text("server executable", encoding="utf-8")
    launcher = Path(profile.workdir) / "PalServer.sh"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    source = Path(profile.workdir) / "linux64" / "steamclient.so"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"steam client")
    captured = {}

    def popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs["cwd"]
        return FakeProc(stdout=iter([]))

    monkeypatch.setattr(
        "module.server.manager.psutil.Process", lambda pid: SimpleNamespace(status=lambda: "running")
    )
    PalServerManager(profile, popen_factory=popen).start(update=False)

    expected_dir = (tmp_path / profile.workdir).resolve()
    assert captured == {
        "cmd": [
            str(expected_dir / "Pal" / "Binaries" / "Linux" / "PalServer-Linux-Shipping"),
            "Pal",
            "-port=8211",
            "-queryport=27015",
        ],
        "cwd": str(expected_dir),
    }
    assert (expected_dir / "Pal" / "Binaries" / "Linux" / "steamclient.so").read_bytes() == b"steam client"


def test_adoption_repairs_linux_launcher_runtime_metadata(tmp_path, monkeypatch):
    executable = tmp_path / "PalServer-Linux-Shipping"
    executable.write_text("stub", encoding="utf-8")
    output = tmp_path / f"palserver-{dt.datetime.now():%Y%m%d}.log"
    output.write_text("", encoding="utf-8")
    saved = []

    class AdoptedPsProcess:
        def __init__(self, pid, path, created, children=()):
            self.pid = pid
            self._path = path
            self._created = created
            self._children = list(children)

        def is_running(self):
            return True

        def exe(self):
            return str(self._path)

        def create_time(self):
            return self._created

        def status(self):
            return "running"

        def children(self, recursive=True):
            return self._children

        def terminate(self):
            return None

        def kill(self):
            return None

        def wait(self, timeout=None):
            return None

    child = AdoptedPsProcess(4322, executable, 456.0)
    wrapper = AdoptedPsProcess(4321, tmp_path / "PalServer.sh", 123.0, [child])
    monkeypatch.setattr(
        "module.games.palworld.server.manager.psutil.Process",
        lambda pid: wrapper,
    )
    runtime = {
        "ownership": "managed",
        "pid": 4321,
        "executable": str(executable),
        "create_time": 123.0,
        "output_path": str(output),
        "output_offset": 0,
        "output_file_id": [output.stat().st_dev, output.stat().st_ino],
    }
    monkeypatch.setattr(
        "module.games.palworld.server.manager.load_runtime",
        lambda name: runtime,
    )
    monkeypatch.setattr(
        "module.games.palworld.server.manager.save_runtime",
        lambda name, data: saved.append(data),
    )
    manager = PalServerManager(
        Profile(name="test", executable=str(executable)),
        running_probe=lambda profile: True,
        adopt_managed=True,
    )

    manager.start()

    assert manager.process.pid == 4322
    assert saved[-1]["pid"] == 4322
    assert saved[-1]["create_time"] == 456.0


@pytest.mark.parametrize(
    ("windows", "directory"),
    ((True, "WindowsServer"), (False, "LinuxServer")),
)
def test_start_creates_both_platform_settings_files_before_launch(
    tmp_path, monkeypatch, windows, directory
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("module.games.palworld.config.WINDOWS", windows)
    monkeypatch.setattr("module.games.palworld.server.manager.WINDOWS", windows)
    profile = Profile(name="settings")
    profile.apply_fixed_paths()
    executable = Path(profile.executable)
    executable.parent.mkdir(parents=True)
    executable.write_text("server executable", encoding="utf-8")
    captured = {}

    def popen(cmd, **kwargs):
        captured["game_user_settings"] = game_user_settings_path(profile.name)
        captured["world_settings"] = resolve_ini_path(profile)
        return FakeProc(stdout=iter([]))

    monkeypatch.setattr(
        "module.server.manager.psutil.Process", lambda pid: SimpleNamespace(status=lambda: "running")
    )
    PalServerManager(profile, popen_factory=popen).start(update=False)

    assert captured["game_user_settings"].parts[-2:] == (
        directory,
        "GameUserSettings.ini",
    )
    assert captured["world_settings"].parts[-2:] == (
        directory,
        "PalWorldSettings.ini",
    )
    assert captured["game_user_settings"].is_file()
    assert captured["world_settings"].is_file()


def test_start_does_not_promote_incomplete_steam_staging(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    install_dir = Path("profile/default/steamcmd/steamapps/common/PalServer")
    staged_dir = Path("profile/default/steamcmd/steamapps/downloading/2394010")
    staged_dir.mkdir(parents=True)
    (staged_dir / "PalServer.exe").write_text("incomplete staging", encoding="utf-8")
    profile = Profile(
        name="default",
        workdir=str(install_dir),
        executable=str(install_dir / "PalServer.exe"),
    )
    manager = PalServerManager(profile)

    with pytest.raises(FileNotFoundError, match="PalServer executable not found after update"):
        manager.start(update=False)

    assert not (install_dir / "PalServer.exe").exists()


def test_start_streams_palserver_stdout_and_stderr_to_log(tmp_path, monkeypatch):
    exe = tmp_path / "server" / "PalServer.exe"
    exe.parent.mkdir()
    exe.write_text("stub", encoding="utf-8")
    logs = []
    output_finished = threading.Event()
    captured = {}

    def logger(message):
        logs.append(message)
        if message == "PalServer: stderr line":
            output_finished.set()

    def popen(cmd, **kwargs):
        captured.update(kwargs)
        return FakeProc(stdout=io.BytesIO(b"stdout line\n\nstderr line\r\n"))

    monkeypatch.setattr(
        "module.server.manager.psutil.Process", lambda pid: SimpleNamespace(status=lambda: "running")
    )
    manager = PalServerManager(
        Profile(name="test", workdir=str(exe.parent), executable=str(exe)),
        logger=logger,
        popen_factory=popen,
        running_probe=lambda profile: False,
    )

    manager.start(update=False)

    assert output_finished.wait(timeout=1)
    assert "PalServer: stdout line" in logs
    assert "PalServer: stderr line" in logs
    assert "PalServer: " not in logs
    assert captured["stdout"] == subprocess.PIPE
    assert captured["stderr"] == subprocess.STDOUT


def test_adopt_managed_server_validates_runtime_and_replays_output(tmp_path, monkeypatch):
    exe = tmp_path / "PalServer.exe"
    exe.write_text("stub", encoding="utf-8")
    output = tmp_path / f"palserver-{dt.datetime.now():%Y%m%d}.log"
    output.write_text("missed line\n", encoding="utf-8")
    logs = []

    class AdoptedPsProcess:
        pid = 4321

        def is_running(self):
            return True

        def exe(self):
            return str(exe)

        def create_time(self):
            return 123.0

        def status(self):
            return "running"

        def children(self, recursive=True):
            return []

        def terminate(self):
            return None

        def kill(self):
            return None

        def wait(self, timeout=None):
            return None

    monkeypatch.setattr(
        "module.games.palworld.server.manager.psutil.Process",
        lambda pid: AdoptedPsProcess(),
    )
    monkeypatch.setattr(
        "module.games.palworld.server.manager.load_runtime",
        lambda name: {
            "ownership": "managed",
            "pid": 4321,
            "executable": str(exe),
            "create_time": 123.0,
            "output_path": str(output),
            "output_offset": 0,
            "output_file_id": [output.stat().st_dev, output.stat().st_ino],
        },
    )
    manager = PalServerManager(
        Profile(name="test", executable=str(exe)),
        logger=logs.append,
        running_probe=lambda profile: True,
        adopt_managed=True,
    )

    manager.start()
    _wait_for_log(logs, "PalServer: missed line")

    assert manager.external_attached is False
    assert manager.process.pid == 4321
    manager.stop(graceful=False)


def test_adoption_rejects_missing_runtime_without_launching(tmp_path, monkeypatch):
    exe = tmp_path / "PalServer.exe"
    exe.write_text("stub", encoding="utf-8")
    monkeypatch.setattr("module.games.palworld.server.manager.load_runtime", lambda name: None)
    manager = PalServerManager(
        Profile(name="test", executable=str(exe)),
        running_probe=lambda profile: True,
        adopt_managed=True,
        popen_factory=lambda *args, **kwargs: pytest.fail("adoption must not launch a server"),
    )

    with pytest.raises(RuntimeError, match="runtime metadata"):
        manager.start()


def test_server_output_repairs_packed_utf8_and_splits_embedded_newline():
    original = "Game version is v1.0.1.100619\n"
    encoded = original.encode("utf-8")
    assert len(encoded) % 2 == 0
    packed = "".join(
        chr(int.from_bytes(encoded[index : index + 2], "little"))
        for index in range(0, len(encoded), 2)
    )

    lines = PalServerManager._server_output_lines(
        packed + '[2026.07.17-02.03.01:309][  0]r.NGX.DLSS.AutoExposure = "0"\r\n'
    )

    assert lines == [
        "Game version is v1.0.1.100619",
        '[2026.07.17-02.03.01:309][  0]r.NGX.DLSS.AutoExposure = "0"',
    ]


def test_server_output_preserves_normal_non_ascii_text():
    assert PalServerManager._server_output_lines("伺服器已啟動\r\n") == ["伺服器已啟動"]


def test_server_output_sanitizes_adminpassword_in_log_and_audit():
    logs = []
    events = []
    manager = PalServerManager(
        Profile(name="test", rest_password="secret"),
        logger=logs.append,
        event_callback=events.append,
    )

    manager._server_output_line(
        "[2026-07-17 21:10:47] [LOG] Alice executed the command. adminpassword secret"
    )

    assert logs == [
        "PalServer: [2026-07-17 21:10:47] [LOG] "
        "Alice executed: adminpassword (result: success)"
    ]
    assert events[0].message == "Alice executed: adminpassword (result: success)"
    assert "secret" not in logs[0]


def test_server_output_reads_non_iterable_pty_and_removes_terminal_controls():
    class PtyOutput:
        def __init__(self):
            self.characters = iter("\x1b[1tREST API started on port 8212\r\n")

        def read(self, size=1):
            return next(self.characters, "")

    logs = []
    manager = PalServerManager(Profile(name="test"), logger=logs.append)

    manager._stream_server_output(FakeProc(stdout=PtyOutput()))

    assert logs == ["PalServer: REST API started on port 8212"]


def _wait_for_log(logs, expected, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if expected in logs:
            return
        time.sleep(0.01)
    raise AssertionError(f"Timed out waiting for {expected!r}; logs={logs!r}")


def test_ue4ss_stream_skips_managed_stale_bytes_and_reads_new_lines(tmp_path):
    path = tmp_path / "UE4SS.log"
    path.write_bytes(b"stale line\n")
    stat = path.stat()
    logs = []
    active = threading.Event()
    active.set()
    stop_event = threading.Event()
    thread = threading.Thread(
        target=PalServerManager(Profile(name="test"), logger=logs.append)._stream_ue4ss_output,
        args=(path, stop_event),
        kwargs={
            "initial_offset": stat.st_size,
            "initial_file_id": (stat.st_dev, stat.st_ino),
            "active": active.is_set,
        },
        daemon=True,
    )
    thread.start()
    with path.open("ab") as handle:
        handle.write(b"fresh line\r\n")
    _wait_for_log(logs, "UE4SS: fresh line")
    active.clear()
    stop_event.set()
    thread.join(timeout=1)
    assert "UE4SS: stale line" not in logs


def test_ue4ss_stream_replays_external_log_handles_partial_utf8_and_truncation(tmp_path):
    path = tmp_path / "UE4SS.log"
    path.write_bytes(b"existing line\npartial")
    logs = []
    active = threading.Event()
    active.set()
    stop_event = threading.Event()
    manager = PalServerManager(Profile(name="test"), logger=logs.append)
    thread = threading.Thread(
        target=manager._stream_ue4ss_output,
        args=(path, stop_event),
        kwargs={
            "initial_offset": 0,
            "initial_file_id": (path.stat().st_dev, path.stat().st_ino),
            "active": active.is_set,
        },
        daemon=True,
    )
    thread.start()
    _wait_for_log(logs, "UE4SS: existing line")
    with path.open("ab") as handle:
        handle.write(b"\xff\n")
    _wait_for_log(logs, "UE4SS: partial�")
    path.write_bytes(b"replacement line\n")
    _wait_for_log(logs, "UE4SS: replacement line")
    active.clear()
    stop_event.set()
    thread.join(timeout=1)
    assert logs.count("UE4SS: existing line") == 1


def test_ue4ss_stream_retries_transient_read_errors_once(tmp_path, monkeypatch):
    path = tmp_path / "UE4SS.log"
    path.write_text("recovered line\n", encoding="utf-8")
    logs = []
    active = threading.Event()
    active.set()
    stop_event = threading.Event()
    manager = PalServerManager(Profile(name="test"), logger=logs.append)
    original_open = Path.open
    failures = {"remaining": 2}

    def flaky_open(candidate, *args, **kwargs):
        if candidate == path and failures["remaining"]:
            failures["remaining"] -= 1
            raise OSError("temporary sharing violation")
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky_open)
    thread = threading.Thread(
        target=manager._stream_ue4ss_output,
        args=(path, stop_event),
        kwargs={
            "initial_offset": 0,
            "initial_file_id": (path.stat().st_dev, path.stat().st_ino),
            "active": active.is_set,
        },
        daemon=True,
    )
    thread.start()
    _wait_for_log(logs, "UE4SS: recovered line")
    active.clear()
    stop_event.set()
    thread.join(timeout=1)
    assert sum("log streaming unavailable" in line for line in logs) == 1


def test_server_stream_continues_after_callback_error(tmp_path):
    path = tmp_path / "palserver.log"
    path.write_text("first line\n", encoding="utf-8")
    logs = []
    emitted = []
    active = threading.Event()
    active.set()
    stop_event = threading.Event()
    manager = PalServerManager(Profile(name="test"), logger=logs.append)

    def emit(line):
        if line == "first line":
            raise RuntimeError("transient callback failure")
        emitted.append(line)

    stat = path.stat()
    thread = threading.Thread(
        target=manager._stream_file_output,
        args=(path, stop_event),
        kwargs={
            "initial_offset": 0,
            "initial_file_id": (stat.st_dev, stat.st_ino),
            "active": active.is_set,
            "emit": emit,
        },
        daemon=True,
    )
    thread.start()
    _wait_for_log(logs, "PalServer: log streaming unavailable: transient callback failure")

    with path.open("a", encoding="utf-8") as handle:
        handle.write("second line\n")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and "second line" not in emitted:
        time.sleep(0.01)

    active.clear()
    stop_event.set()
    thread.join(timeout=1)
    assert "second line" in emitted


def test_external_attach_starts_ue4ss_stream_when_installed(tmp_path, monkeypatch):
    path = tmp_path / "UE4SS.log"
    path.write_text("attached line\n", encoding="utf-8")
    logs = []
    manager = PalServerManager(
        Profile(name="test"),
        logger=logs.append,
        running_probe=lambda profile: True,
    )
    monkeypatch.setattr(manager, "_ue4ss_log_path", lambda: path)

    manager.start(update=False)
    _wait_for_log(logs, "UE4SS: attached line")
    manager.external_attached = False
    manager._stop_ue4ss_output()


def test_managed_start_streams_only_new_ue4ss_lines(tmp_path, monkeypatch):
    path = tmp_path / "UE4SS.log"
    path.write_text("stale line\n", encoding="utf-8")
    logs = []
    process = FakeProc(stdout=iter([]))
    monkeypatch.setattr(
        "module.games.palworld.server.manager.psutil.Process",
        lambda pid: SimpleNamespace(status=lambda: "running"),
    )
    manager = PalServerManager(
        Profile(name="test", workdir=str(tmp_path), executable=str(tmp_path / "PalServer.exe")),
        logger=logs.append,
        popen_factory=lambda *args, **kwargs: process,
        running_probe=lambda profile: False,
    )
    monkeypatch.setattr(manager, "_ue4ss_log_path", lambda: path)

    manager.start(update=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("fresh line\n")
    _wait_for_log(logs, "UE4SS: fresh line")
    process.returncode = 0
    manager._stop_ue4ss_output()
    assert "UE4SS: stale line" not in logs


def test_ue4ss_stream_is_not_started_without_installation(monkeypatch):
    manager = PalServerManager(Profile(name="test"))
    monkeypatch.setattr(manager, "_ue4ss_log_path", lambda: None)

    manager._start_ue4ss_output(replay_existing=True, active=lambda: True)

    assert manager._ue4ss_output_thread is None


def test_run_update_creates_missing_working_directory(tmp_path):
    workdir = tmp_path / "missing" / "PalServer"
    captured = {}

    def pty_process(cmd, **kwargs):
        captured["cwd"] = kwargs["cwd"]
        captured["exists_during_update"] = workdir.exists()
        return FakeProc(stdout=iter([]), returncode=0)

    manager = PalServerManager(
        Profile(name="test", workdir=str(workdir), executable="PalServer.exe"),
        pty_process_factory=pty_process,
    )

    manager.run_update()

    assert captured == {"cwd": str(workdir.resolve()), "exists_during_update": True}
    assert workdir.is_dir()


def test_run_update_uses_absolute_paths_for_fresh_relative_profile(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    steamcmd = Path("profile/default/steamcmd/steamcmd.exe")
    install_dir = Path("profile/default/steamcmd/steamapps/common/PalServer")
    steamcmd.parent.mkdir(parents=True)
    steamcmd.write_text("stub", encoding="utf-8")
    captured = {}

    def pty_process(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs["cwd"]
        return FakeProc(stdout=iter([]), returncode=0)

    profile = Profile(
        name="default",
        workdir=str(install_dir),
        executable=str(install_dir / "PalServer.exe"),
        steamcmd=str(steamcmd),
        steam_validate=True,
    )
    manager = PalServerManager(
        profile,
        pty_process_factory=pty_process,
    )

    manager.run_update()

    expected_steamcmd = (tmp_path / steamcmd).resolve()
    expected_install = (tmp_path / install_dir).resolve()
    assert captured == {
        "cmd": [
            str(expected_steamcmd),
            *steamcmd_platform_args(),
            "+force_install_dir",
            str(expected_install),
            "+login",
            "anonymous",
            "+app_update",
            "2394010",
            "validate",
            "+quit",
        ],
        "cwd": str(expected_steamcmd.parent),
    }


def test_run_update_calls_pty_process_factory_with_steam_update_args_and_workdir(tmp_path):
    workdir = tmp_path / "server"
    captured = {}
    profile = Profile(name="test", workdir=str(workdir), executable="PalServer.exe")

    def pty_process(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs["cwd"]
        return FakeProc(stdout=iter([]), returncode=0)

    manager = PalServerManager(profile, pty_process_factory=pty_process)

    manager.run_update()

    assert captured == {"cmd": manager.steam_update_args(), "cwd": str(workdir.resolve())}


def test_run_update_never_calls_popen_factory():
    def popen(*args, **kwargs):
        raise AssertionError("run_update must not use popen_factory")

    manager = PalServerManager(
        Profile(name="test"),
        popen_factory=popen,
        pty_process_factory=lambda *a, **k: FakeProc(stdout=iter([]), returncode=0),
    )

    manager.run_update()


def test_run_update_logs_steamcmd_output():
    logs = []

    def pty_process(cmd, **kwargs):
        return FakeProc(stdout=iter(["line one\n", "\n", "line two\n"]), returncode=0)

    manager = PalServerManager(Profile(name="test"), logger=logs.append, pty_process_factory=pty_process)

    manager.run_update()

    assert "SteamCMD: line one" in logs
    assert "SteamCMD: line two" in logs
    assert logs[-1] == "Update completed"


def test_run_update_strips_steamcmd_ansi_color_codes():
    logs = []

    def pty_process(cmd, **kwargs):
        output = "\x1b[0m\x1b[0m Update state (0x61) downloading, progress: 14.79\n"
        return FakeProc(stdout=iter([output]), returncode=0)

    manager = PalServerManager(Profile(name="test"), logger=logs.append, pty_process_factory=pty_process)

    manager.run_update()

    assert "SteamCMD: Update state (0x61) downloading, progress: 14.79" in logs
    assert not any("\x1b[" in log for log in logs)


def test_run_update_splits_steamcmd_carriage_return_progress():
    logs = []

    def pty_process(cmd, **kwargs):
        output = "progress 1\rprogress 2\rcomplete\n"
        return FakeProc(stdout=iter([output]), returncode=0)

    manager = PalServerManager(Profile(name="test"), logger=logs.append, pty_process_factory=pty_process)

    manager.run_update()

    assert "SteamCMD: progress 1" in logs
    assert "SteamCMD: progress 2" in logs
    assert "SteamCMD: complete" in logs


def test_run_update_throttles_repeated_steamcmd_progress_lines(monkeypatch):
    logs = []
    times = iter([0.0, 0.1, 0.2, 0.3, 0.4, 3.0])
    monkeypatch.setattr("module.server.manager.time.monotonic", lambda: next(times, 3.0))

    def pty_process(cmd, **kwargs):
        output = (
            "Update state (0x61) downloading, progress: 1.00 (1 / 100)\n"
            "Update state (0x61) downloading, progress: 1.02 (1 / 100)\n"
            "Update state (0x61) downloading, progress: 1.05 (1 / 100)\n"
            "Update state (0x61) downloading, progress: 2.00 (2 / 100)\n"
            "Update state (0x61) downloading, progress: 2.01 (2 / 100)\n"
        )
        return FakeProc(stdout=iter([output]), returncode=0)

    manager = PalServerManager(Profile(name="test"), logger=logs.append, pty_process_factory=pty_process)

    manager.run_update()

    assert "SteamCMD: Update state (0x61) downloading, progress: 1.00 (1 / 100)" in logs
    assert "SteamCMD: Update state (0x61) downloading, progress: 1.02 (1 / 100)" not in logs
    assert "SteamCMD: Update state (0x61) downloading, progress: 1.05 (1 / 100)" not in logs
    assert "SteamCMD: Update state (0x61) downloading, progress: 2.00 (2 / 100)" in logs
    assert "SteamCMD: Update state (0x61) downloading, progress: 2.01 (2 / 100)" in logs


def test_run_update_failure_includes_last_steamcmd_output():
    def pty_process(cmd, **kwargs):
        return FakeProc(stdout=iter(["first line\n", "last useful line\n"]), returncode=7)

    manager = PalServerManager(Profile(name="test"), pty_process_factory=pty_process)

    with pytest.raises(RuntimeError, match=r"SteamCMD update failed \(7\): last useful line"):
        manager.run_update()


def test_run_update_retries_after_steamcmd_self_update():
    logs = []
    calls = []

    def pty_process(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            return FakeProc(
                stdout=iter(
                    [
                        "[----] Update complete, launching Steamcmd...\n",
                        "ERROR! Failed to install app '2394010' (Missing configuration)\n",
                        "CWorkThreadPool::~CWorkThreadPool: work processing queue not empty: 2 items discarded.\n",
                    ]
                ),
                returncode=7,
            )
        return FakeProc(stdout=iter(["Success! App '2394010' fully installed.\n"]), returncode=0)

    manager = PalServerManager(Profile(name="test"), logger=logs.append, pty_process_factory=pty_process)

    manager.run_update()

    assert len(calls) == 2
    assert "SteamCMD updated itself; retrying server update" in logs
    assert logs[-1] == "Update completed"


def test_run_update_logs_steamcmd_silence_notice(monkeypatch):
    logs = []
    times = iter([0.0, 31.0])

    class QuietProc:
        pid = 123
        stdout = iter([])

        def __init__(self):
            self.polls = 0

        def poll(self):
            self.polls += 1
            if self.polls == 1:
                return None
            return 0

    monkeypatch.setattr("module.server.manager.time.monotonic", lambda: next(times, 32.0))

    manager = PalServerManager(
        Profile(name="test"),
        logger=logs.append,
        pty_process_factory=lambda *a, **k: QuietProc(),
    )

    manager.run_update()

    assert any(log.startswith("SteamCMD still running; no output for ") for log in logs)
    assert logs[-1] == "Update completed"


def test_unexpected_exit_sets_warning():
    proc = FakeProc()
    events = []
    manager = PalServerManager(
        Profile(name="test", restart_on_crash=False),
        run_command=lambda *a, **k: SimpleNamespace(returncode=0, stderr="", stdout=""),
        popen_factory=lambda *a, **k: proc,
        event_callback=events.append,
    )
    manager.process = proc
    proc.returncode = 1

    manager.monitor_once()

    assert manager.state == "warning"
    event = manager.recent_events[-1]
    assert event.outcome == "restart_disabled"
    assert event.termination.raw_exit_code == 1
    assert [event.type for event in events if hasattr(event, "type")] == ["server_exit"]
    assert "exit code: 1" in events[0].message


def test_agent_and_server_exit_are_audited_on_agent_stop():
    class Agent:
        def __init__(self):
            self.running = True

        def status(self):
            return {
                "server_state": "running" if self.running else "stopped",
                "exit_code": 0,
                "exit_reason": "stopped",
            }

        def stop(self):
            self.running = False
            return self.status()

        def kill(self):
            self.running = False
            return self.status()

    class Process:
        pid = 123

        def __init__(self, agent):
            self.agent = agent

        def poll(self):
            return None if self.agent.running else 0

        def wait(self, timeout=None):
            return 0

    events = []
    agent = Agent()
    manager = PalServerManager(
        Profile(name="test"),
        rest_client=FakeRest(),
        event_callback=events.append,
    )
    manager.agent_client = agent
    manager.process = Process(agent)

    manager.stop()

    assert [event.type for event in events] == ["server_exit", "agent_exit"]
    assert events[0].message == "PalServer exited: stopped (exit code: 0)"
    assert events[1].message == "PalServer agent exited: stopped"


def test_unexpected_agent_exit_is_audited():
    class DisconnectedAgent:
        def status(self):
            raise RuntimeError("agent disconnected")

    events = []
    manager = PalServerManager(
        Profile(name="test", restart_on_crash=False),
        event_callback=events.append,
    )
    manager.agent_client = DisconnectedAgent()
    manager.process = FakeProc(returncode=1)

    manager.monitor_once()

    assert [event.type for event in events if hasattr(event, "type")] == [
        "agent_exit",
        "server_exit",
    ]


def test_crash_history_retains_final_output_and_failed_relaunch_permission(monkeypatch):
    monkeypatch.setattr(
        "module.server.manager.psutil.Process", lambda pid: SimpleNamespace(status=lambda: "running")
    )
    dead = FakeProc(returncode=-1073741819)

    def denied(*args, **kwargs):
        raise PermissionError(13, "denied", "PalServer.exe")

    manager = PalServerManager(
        Profile(name="test"),
        popen_factory=denied,
        sleep=lambda *_: None,
    )
    manager.process = dead
    manager._server_output_tail.extend(["native failure", "final context"])

    with pytest.raises(PermissionError):
        manager.monitor_once()

    event = manager.recent_events[-1]
    assert event.outcome == "restart_failed"
    assert event.termination.diagnostic_output == ("native failure", "final context")
    assert event.detail["restart_error"]["summary_code"] == "permission_denied"
    assert event.detail["restart_error"]["os_error"]["errno"] == 13


def test_unexpected_exit_restarts_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "module.server.manager.psutil.Process", lambda pid: SimpleNamespace(status=lambda: "running")
    )
    dead = FakeProc()
    dead.returncode = 1
    alive = FakeProc()
    rest = FakeRest()

    manager = PalServerManager(
        Profile(name="test"),
        rest_client=rest,
        run_command=lambda *a, **k: SimpleNamespace(returncode=0, stderr="", stdout=""),
        popen_factory=lambda *a, **k: alive,
        sleep=lambda *_: None,
    )
    manager.process = dead

    manager.monitor_once()

    assert manager.process is alive
    assert manager.state == "running"
    assert rest.shutdowns == []


def test_stop_requested_suppresses_restart():
    dead = FakeProc()
    dead.returncode = 1
    called = False

    def popen(*a, **k):
        nonlocal called
        called = True
        return FakeProc()

    manager = PalServerManager(
        Profile(name="test"),
        run_command=lambda *a, **k: SimpleNamespace(returncode=0, stderr="", stdout=""),
        popen_factory=popen,
        stop_requested=lambda: True,
    )
    manager.process = dead

    manager.monitor_once()

    assert called is False
    assert manager.state == "inactive"


def test_graceful_stop_waits_without_force_killing_when_server_stays_up():
    states = []
    rest = FakeRest()

    class SlowProcess(FakeProc):
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("PalServer.exe", timeout)

    process = SlowProcess()
    manager = PalServerManager(
        Profile(name="test", shutdown_wait_seconds=1),
        rest_client=rest,
        state_callback=states.append,
    )
    manager.process = process

    manager.stop(graceful=True)

    assert rest.shutdowns == [True]
    assert process.terminated is False
    assert process.returncode is None
    assert states == ["stopping"]


def test_second_crash_within_default_trigger_frame_restores_backup(monkeypatch):
    monkeypatch.setattr(
        "module.server.manager.psutil.Process", lambda pid: SimpleNamespace(status=lambda: "running")
    )
    times = iter(
        [dt.datetime(2026, 1, 1, 0, 0, 0), dt.datetime(2026, 1, 1, 0, 10, 0)]
    )
    backup_service = FakeBackupService(backup_before_result=Path("backup.zip"))

    manager = PalServerManager(
        Profile(name="test"),
        run_command=lambda *a, **k: SimpleNamespace(returncode=0, stderr="", stdout=""),
        popen_factory=lambda *a, **k: FakeProc(),
        sleep=lambda *_: None,
        backup_service=backup_service,
        now=lambda: next(times),
    )

    dead = FakeProc()
    dead.returncode = 1
    manager.process = dead
    manager.monitor_once()  # first crash: restarts, no rollback yet
    assert backup_service.create_backup_calls == 0
    assert manager.state == "running"

    manager.process.returncode = 1  # crashes again 10 minutes later
    manager.monitor_once()

    assert backup_service.create_backup_calls == 1
    assert backup_service.backup_before_calls == [dt.datetime(2025, 12, 31, 23, 40, 0)]
    assert backup_service.restore_calls == [Path("backup.zip")]
    assert list(manager.self_heal_crash_times) == []
    assert manager.state == "running"


def test_second_crash_within_trigger_frame_does_not_roll_back_when_self_heal_disabled(monkeypatch):
    monkeypatch.setattr(
        "module.server.manager.psutil.Process", lambda pid: SimpleNamespace(status=lambda: "running")
    )
    times = iter(
        [dt.datetime(2026, 1, 1, 0, 0, 0), dt.datetime(2026, 1, 1, 0, 10, 0)]
    )
    backup_service = FakeBackupService(backup_before_result=Path("backup.zip"))

    manager = PalServerManager(
        Profile(name="test", self_heal_enabled=False),
        run_command=lambda *a, **k: SimpleNamespace(returncode=0, stderr="", stdout=""),
        popen_factory=lambda *a, **k: FakeProc(),
        sleep=lambda *_: None,
        backup_service=backup_service,
        now=lambda: next(times),
    )

    dead = FakeProc()
    dead.returncode = 1
    manager.process = dead
    manager.monitor_once()  # first crash: restarts, self-heal disabled so no rollback tracking used

    manager.process.returncode = 1  # crashes again 10 minutes later
    manager.monitor_once()

    assert backup_service.create_backup_calls == 0
    assert backup_service.restore_calls == []
    assert list(manager.self_heal_crash_times) == []
    assert manager.state == "running"


def test_crash_after_window_does_not_roll_back(monkeypatch):
    monkeypatch.setattr(
        "module.server.manager.psutil.Process", lambda pid: SimpleNamespace(status=lambda: "running")
    )
    times = iter(
        [dt.datetime(2026, 1, 1, 0, 0, 0), dt.datetime(2026, 1, 1, 0, 31, 0)]
    )
    backup_service = FakeBackupService(backup_before_result=Path("backup.zip"))

    manager = PalServerManager(
        Profile(name="test"),
        run_command=lambda *a, **k: SimpleNamespace(returncode=0, stderr="", stdout=""),
        popen_factory=lambda *a, **k: FakeProc(),
        sleep=lambda *_: None,
        backup_service=backup_service,
        now=lambda: next(times),
    )

    dead = FakeProc()
    dead.returncode = 1
    manager.process = dead
    manager.monitor_once()

    manager.process.returncode = 1
    manager.monitor_once()

    assert backup_service.create_backup_calls == 0
    assert backup_service.restore_calls == []
    assert list(manager.self_heal_crash_times) == [dt.datetime(2026, 1, 1, 0, 31, 0)]


def test_custom_self_heal_trigger_count_and_frame_are_respected(monkeypatch):
    monkeypatch.setattr(
        "module.server.manager.psutil.Process", lambda pid: SimpleNamespace(status=lambda: "running")
    )
    times = iter(
        [
            dt.datetime(2026, 1, 1, 0, 0, 0),
            dt.datetime(2026, 1, 1, 0, 5, 0),
            dt.datetime(2026, 1, 1, 0, 10, 0),
        ]
    )
    backup_service = FakeBackupService(backup_before_result=Path("backup.zip"))
    manager = PalServerManager(
        Profile(
            name="test",
            self_heal_trigger_frame_minutes=15,
            self_heal_trigger_crash_times=3,
        ),
        popen_factory=lambda *a, **k: FakeProc(),
        sleep=lambda *_: None,
        backup_service=backup_service,
        now=lambda: next(times),
    )

    manager.process = FakeProc(returncode=1)
    manager.monitor_once()
    manager.process.returncode = 1
    manager.monitor_once()
    assert backup_service.create_backup_calls == 0

    manager.process.returncode = 1
    manager.monitor_once()

    assert backup_service.create_backup_calls == 1
    assert backup_service.backup_before_calls == [dt.datetime(2025, 12, 31, 23, 55, 0)]
    assert backup_service.restore_calls == [Path("backup.zip")]


def test_rollback_with_no_backup_service_is_a_noop(monkeypatch):
    monkeypatch.setattr(
        "module.server.manager.psutil.Process", lambda pid: SimpleNamespace(status=lambda: "running")
    )
    fixed_time = dt.datetime(2026, 1, 1, 0, 0, 0)

    manager = PalServerManager(
        Profile(name="test", self_heal_trigger_crash_times=1),
        run_command=lambda *a, **k: SimpleNamespace(returncode=0, stderr="", stdout=""),
        popen_factory=lambda *a, **k: FakeProc(),
        sleep=lambda *_: None,
        now=lambda: fixed_time,
    )
    dead = FakeProc()
    dead.returncode = 1
    manager.process = dead

    manager.monitor_once()

    assert manager.state == "running"


def test_structured_launch_options_are_used_for_server_command(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "module.server.manager.psutil.Process",
        lambda pid: SimpleNamespace(status=lambda: "running"),
    )
    profile = Profile(
        name="test",
        launch_useperfthreads=True,
        launch_worker_threads_server=4,
        launch_public_lobby=True,
        launch_enable_gamedata_api=False,
        extra_args=["-custom=value"],
    )
    manager = PalServerManager(
        profile,
        popen_factory=lambda cmd, **kwargs: (captured.setdefault("cmd", cmd), FakeProc())[1],
    )

    manager.start()

    assert captured["cmd"] == [
        "PalServer.exe",
        "-useperfthreads",
        "-NumberOfWorkerThreadsServer=4",
        "-publiclobby",
        "-custom=value",
        "-port=8211",
        "-queryport=27015",
    ]


def test_restart_never_runs_update(monkeypatch):
    manager = PalServerManager(Profile(name="test"))
    calls = []
    monkeypatch.setattr(manager, "stop", lambda graceful: calls.append(("stop", graceful)))
    monkeypatch.setattr(
        manager,
        "start",
        lambda update=False, manual=True: calls.append(("start", update, manual)),
    )

    manager.restart(reason="planned restart")

    assert calls == [("stop", True), ("start", False, False)]


def test_memory_policy_sums_process_tree_and_requires_three_samples(monkeypatch):
    rest = FakeRest()
    child = SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=24 * 1024 * 1024))
    root = SimpleNamespace(
        status=lambda: "running",
        memory_info=lambda: SimpleNamespace(rss=32 * 1024 * 1024),
        children=lambda recursive: [child],
    )
    monkeypatch.setattr("module.server.manager.psutil.Process", lambda pid: root)
    manager = PalServerManager(
        Profile(name="test", memory_restart_mb=50, memory_restart_countdown_minutes=2),
        rest_client=rest,
        popen_factory=lambda *args, **kwargs: FakeProc(),
    )
    manager.start()

    manager.monitor_once()
    manager.monitor_once()
    assert rest.announcements == []

    manager.monitor_once()

    assert rest.announcements == [
        "Server will restart in 2 minutes due to excessive PalServer memory use"
    ]
    assert manager.countdown == 1


def test_failed_safety_backup_prevents_self_heal_restore(monkeypatch):
    monkeypatch.setattr(
        "module.server.manager.psutil.Process",
        lambda pid: SimpleNamespace(status=lambda: "running"),
    )
    times = iter(
        [dt.datetime(2026, 1, 1, 0, 0), dt.datetime(2026, 1, 1, 0, 5)]
    )
    backups = FakeBackupService(
        backup_before_result=Path("backup.zip"), create_error=OSError("disk full")
    )
    manager = PalServerManager(
        Profile(name="test"),
        popen_factory=lambda *args, **kwargs: FakeProc(),
        sleep=lambda *_: None,
        backup_service=backups,
        now=lambda: next(times),
    )

    manager.process = FakeProc(returncode=1)
    manager.monitor_once()
    manager.process.returncode = 1
    manager.monitor_once()

    assert backups.create_backup_calls == 1
    assert backups.backup_before_calls == []
    assert backups.restore_calls == []
    assert manager.state == "running"


def test_skipped_safety_backup_prevents_self_heal_restore(monkeypatch):
    monkeypatch.setattr(
        "module.server.manager.psutil.Process",
        lambda pid: SimpleNamespace(status=lambda: "running"),
    )
    times = iter(
        [dt.datetime(2026, 1, 1, 0, 0), dt.datetime(2026, 1, 1, 0, 5)]
    )
    backups = FakeBackupService(
        backup_before_result=Path("backup.zip"),
        create_result=SimpleNamespace(skipped=True, path=None),
    )
    manager = PalServerManager(
        Profile(name="test"),
        popen_factory=lambda *args, **kwargs: FakeProc(),
        sleep=lambda *_: None,
        backup_service=backups,
        now=lambda: next(times),
    )

    manager.process = FakeProc(returncode=1)
    manager.monitor_once()
    manager.process.returncode = 1
    manager.monitor_once()

    assert backups.create_backup_calls == 1
    assert backups.backup_before_calls == []
    assert backups.restore_calls == []


def test_crash_restarts_are_bounded_and_manual_start_clears_cap(monkeypatch):
    root = SimpleNamespace(status=lambda: "running")
    monkeypatch.setattr("module.server.manager.psutil.Process", lambda pid: root)
    times = iter(dt.datetime(2026, 1, 1, 0, minute) for minute in range(6))
    launches = []
    states = []
    manager = PalServerManager(
        Profile(name="test", crash_restart_limit_per_hour=5),
        popen_factory=lambda *args, **kwargs: (launches.append(1), FakeProc())[1],
        sleep=lambda *_: None,
        now=lambda: next(times),
        state_callback=states.append,
    )
    manager.process = FakeProc(returncode=1)

    for _ in range(6):
        manager.monitor_once()
        if manager.process is not None:
            manager.process.returncode = 1

    assert len(launches) == 5
    assert manager.state == "warning"
    assert states[-1] == "warning"
    assert len(manager.crash_times) == 5

    manager.start()

    assert len(launches) == 6
    assert list(manager.crash_times) == []
    assert list(manager.self_heal_crash_times) == []
    assert manager.state == "running"


def test_due_interval_restart_saves_then_restarts_without_update(monkeypatch):
    root = SimpleNamespace(status=lambda: "running")
    monkeypatch.setattr("module.server.manager.psutil.Process", lambda pid: root)
    rest = FakeRest()
    times = iter(
        [
            dt.datetime(2026, 1, 1, 0, 0),
            dt.datetime(2026, 1, 1, 1, 0),
            dt.datetime(2026, 1, 1, 1, 0),
        ]
    )
    manager = PalServerManager(
        Profile(
            name="test",
            planned_restart_mode="interval",
            planned_restart_interval_hours=1,
            planned_restart_countdown_minutes=0,
        ),
        rest_client=rest,
        popen_factory=lambda *args, **kwargs: FakeProc(),
        now=lambda: next(times),
    )
    reasons = []
    monkeypatch.setattr(manager, "restart", lambda reason: reasons.append(reason))
    manager.start()

    manager.monitor_once()
    assert manager.next_planned_restart == dt.datetime(2026, 1, 1, 1, 0)

    manager.monitor_once()

    assert rest.saves == 1
    assert reasons == ["planned restart"]
    assert manager.next_planned_restart == dt.datetime(2026, 1, 1, 2, 0)
    assert manager.recent_events[-1].reason == "planned_restart"


def test_auto_update_checks_after_thirty_minutes_and_restarts_on_verified_idle(
    monkeypatch,
):
    rest = FakeRest()
    rest.metrics = lambda: {"currentplayernum": 0}
    checks = []
    events = []

    class FakeUpdateService:
        def __init__(self, *args, **kwargs):
            pass

        def check_update(self, *, force):
            checks.append(force)
            return UpdateInfo("100", "200", status="update_available")

    monkeypatch.setattr(
        "module.games.palworld.server.manager.PalworldUpdateService",
        FakeUpdateService,
    )
    monotonic = iter([0.0, 1800.0, 1801.0])
    now = iter(
        [
            dt.datetime(2026, 1, 1, 0, 0),
            dt.datetime(2026, 1, 1, 0, 30),
        ]
    )
    manager = PalServerManager(
        Profile(
            name="test",
            auto_update_idle_minutes=30,
            planned_restart_countdown_minutes=0,
        ),
        rest_client=rest,
        monotonic=lambda: next(monotonic),
        now=lambda: next(now),
        event_callback=events.append,
    )
    manager.process = FakeProc()
    manager.ps_process = SimpleNamespace(status=lambda: "running")
    restarts = []
    monkeypatch.setattr(
        manager,
        "restart",
        lambda reason, update=False: restarts.append((reason, update)),
    )

    manager.monitor_once()
    assert checks == []
    manager.monitor_once()
    assert checks == [True]
    manager.monitor_once()

    assert restarts == [("automatic update", True)]
    assert rest.saves == 1
    assert manager.recent_events[-1].reason == "auto_update"
    assert manager.recent_events[-1].detail["available_build_id"] == "200"
    update_events = [event for event in events if getattr(event, "type", None) == "update_check"]
    assert update_events
    assert update_events[-1].details["available_build_id"] == "200"


def test_auto_update_idle_timer_resets_for_players_and_rest_failures(monkeypatch):
    rest = FakeRest()
    player_counts = iter([0, 1, 0, 0])
    rest.metrics = lambda: {"currentplayernum": next(player_counts)}
    times = iter(
        [
            dt.datetime(2026, 1, 1, 0, 0),
            dt.datetime(2026, 1, 1, 0, 29),
            dt.datetime(2026, 1, 1, 0, 30),
            dt.datetime(2026, 1, 1, 0, 31),
        ]
    )
    manager = PalServerManager(
        Profile(name="test", auto_update_idle_minutes=30),
        rest_client=rest,
        now=lambda: next(times),
    )
    manager.auto_update_info = UpdateInfo("100", "200", status="update_available")

    manager._check_auto_update_idle()
    assert manager.auto_update_idle_since is not None
    manager._check_auto_update_idle()
    assert manager.auto_update_idle_since is None
    manager._check_auto_update_idle()
    assert manager.countdown < 0

    rest.metrics = lambda: (_ for _ in ()).throw(RuntimeError("REST unavailable"))
    manager._check_auto_update_idle()
    assert manager.auto_update_idle_since is None


def test_auto_update_does_not_replace_an_existing_restart_countdown():
    rest = FakeRest()
    manager = PalServerManager(
        Profile(name="test", auto_update_idle_minutes=1),
        rest_client=rest,
    )
    manager.auto_update_info = UpdateInfo("100", "200", status="update_available")
    manager.countdown = 4
    manager.countdown_reason = "planned_restart"

    manager._check_auto_update_idle()

    assert manager.countdown == 4
    assert manager.countdown_reason == "planned_restart"
    assert manager.auto_update_idle_since is None


def test_auto_update_is_disabled_when_update_on_start_is_off(monkeypatch):
    checks = []

    class FakeUpdateService:
        def __init__(self, *args, **kwargs):
            pass

        def check_update(self, **kwargs):
            checks.append(True)
            return UpdateInfo("100", "200", status="update_available")

    monkeypatch.setattr(
        "module.games.palworld.server.manager.PalworldUpdateService",
        FakeUpdateService,
    )
    manager = PalServerManager(Profile(name="test", update_on_start=False))
    manager.process = FakeProc()
    manager.ps_process = SimpleNamespace(status=lambda: "running")

    manager.monitor_once()

    assert checks == []
    assert manager.auto_update_info is None


def test_external_server_skips_due_planned_restart():
    times = iter(
        [dt.datetime(2026, 1, 1, 0, 0), dt.datetime(2026, 1, 1, 1, 0)]
    )
    logs = []
    manager = PalServerManager(
        Profile(
            name="test",
            planned_restart_mode="interval",
            planned_restart_interval_hours=1,
        ),
        logger=logs.append,
        now=lambda: next(times),
        running_probe=lambda profile: True,
    )
    manager.external_attached = True

    manager.monitor_once()
    manager.monitor_once()

    assert "Planned restart skipped because the server is externally managed" in logs
    assert manager.recent_events[-1].outcome == "external_skipped"


def test_daily_restart_uses_host_local_time():
    profile = Profile(
        name="test", planned_restart_mode="daily", planned_restart_daily_time="04:30"
    )
    manager = PalServerManager(profile)

    manager._ensure_next_planned_restart(dt.datetime(2026, 1, 1, 3, 0))
    assert manager.next_planned_restart == dt.datetime(2026, 1, 1, 4, 30)

    manager.next_planned_restart = None
    manager._ensure_next_planned_restart(dt.datetime(2026, 1, 1, 5, 0))
    assert manager.next_planned_restart == dt.datetime(2026, 1, 2, 4, 30)
