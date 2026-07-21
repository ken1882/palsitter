import socket
from pathlib import Path
from types import SimpleNamespace

import psutil

from module.config import Profile
from module.server.status import endpoint_status, instance_is_running, rest_is_available


class FakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_endpoint_status_reports_open_udp_rest_and_rcon():
    opened = []

    def create_connection(address, timeout):
        opened.append((address, timeout))
        return FakeConnection()

    result = endpoint_status(
        Profile(name="test", game_port=8211, rest_host="localhost", rest_port=8212),
        udp_connections=lambda **kwargs: [SimpleNamespace(laddr=SimpleNamespace(port=8211))],
        create_connection=create_connection,
        settings_loader=lambda profile: SimpleNamespace(
            values={"RCONEnabled": True, "RCONPort": 25575}
        ),
    )

    assert result == {"udp": "open", "rest": "open", "rcon": "open"}
    assert opened == [(('localhost', 8212), 1), (('127.0.0.1', 25575), 1)]


def test_endpoint_status_reports_closed_and_disabled():
    def create_connection(address, timeout):
        raise OSError("closed")

    result = endpoint_status(
        Profile(name="test"),
        udp_connections=lambda **kwargs: [],
        create_connection=create_connection,
        settings_loader=lambda profile: SimpleNamespace(
            values={"rcon_enabled": False, "rcon_port": 25575}
        ),
    )

    assert result == {"udp": "closed", "rest": "closed", "rcon": "disabled"}


def test_endpoint_status_handles_probe_errors():
    def udp_connections(**kwargs):
        raise psutil.AccessDenied()

    def create_connection(address, timeout):
        raise socket.timeout()

    def load_settings(profile):
        raise OSError("missing")

    assert endpoint_status(
        Profile(name="test"),
        udp_connections=udp_connections,
        create_connection=create_connection,
        settings_loader=load_settings,
    ) == {"udp": "closed", "rest": "closed", "rcon": "disabled"}


def test_instance_running_requires_matching_name_and_exact_executable_path(tmp_path):
    expected = tmp_path / "instance" / "PalServer.exe"
    profile = Profile(
        name="test",
        executable=str(expected),
        workdir=str(expected.parent),
    )

    class FakeProcess:
        def __init__(self, name, executable):
            self.info = {"name": name, "exe": str(executable)}

    wrong_path = FakeProcess("PalServer.exe", tmp_path / "other" / "PalServer.exe")
    wrong_name = FakeProcess("Other.exe", expected)
    matching = FakeProcess("palserver.EXE", expected)

    assert instance_is_running(
        profile,
        process_iter=lambda attrs: [wrong_path, wrong_name],
    ) is False
    assert instance_is_running(
        profile,
        process_iter=lambda attrs: [wrong_path, matching],
    ) is True


def test_windows_console_process_matches_standard_instance_path(tmp_path, monkeypatch):
    install_dir = tmp_path / "PalServer"
    wrapper = install_dir / "PalServer.exe"
    console = install_dir / "Pal" / "Binaries" / "Win64" / "PalServer-Win64-Shipping-Cmd.exe"
    console.parent.mkdir(parents=True)
    wrapper.write_text("wrapper", encoding="utf-8")
    console.write_text("console", encoding="utf-8")
    profile = Profile(name="test", executable=str(wrapper), workdir=str(install_dir))

    class FakeProcess:
        info = {"name": console.name, "exe": str(console)}

    monkeypatch.setattr("module.games.palworld.server.status.WINDOWS", True)

    assert instance_is_running(
        profile,
        process_iter=lambda attrs: [FakeProcess()],
    ) is True


def test_linux_server_process_matches_linux_shipping_binary(tmp_path, monkeypatch):
    install_dir = tmp_path / "PalServer"
    executable = install_dir / "Pal" / "Binaries" / "Linux" / "PalServer-Linux-Shipping"
    executable.parent.mkdir(parents=True)
    executable.write_text("server", encoding="utf-8")
    profile = Profile(name="test", executable=str(executable), workdir=str(install_dir))

    class FakeProcess:
        info = {"name": executable.name, "exe": str(executable)}

    monkeypatch.setattr("module.games.palworld.server.status.WINDOWS", False)

    assert instance_is_running(
        profile,
        process_iter=lambda attrs: [FakeProcess()],
    ) is True


def test_instance_running_does_not_prepend_workdir_to_relative_executable_path():
    executable = Path("profile") / "test" / "PalServer.exe"
    profile = Profile(
        name="test",
        executable=str(executable),
        workdir=str(executable.parent),
    )

    class FakeProcess:
        info = {
            "name": "PalServer.exe",
            "exe": str(executable.resolve()),
        }

    assert instance_is_running(
        profile,
        process_iter=lambda attrs: [FakeProcess()],
    ) is True


def test_no_endpoint_or_rest_connection_is_attempted_without_matching_process():
    attempts = []

    def connect(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("network must not be probed")

    profile = Profile(name="test")
    assert endpoint_status(
        profile,
        process_running=False,
        udp_connections=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("UDP must not be probed")
        ),
        create_connection=connect,
        settings_loader=lambda profile: SimpleNamespace(values={"RCONEnabled": True}),
    ) == {"udp": "closed", "rest": "closed", "rcon": "closed"}
    assert rest_is_available(
        profile,
        process_iter=lambda attrs: [],
        create_connection=connect,
    ) is False
    assert attempts == []
