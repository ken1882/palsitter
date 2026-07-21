from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from module.config import Profile
from module.games.palworld.config import PalworldProfile
from module.games.palworld.server.manager import AgentServerProcess, PalServerManager
from module.games.palworld.server import agent
from module.instances import load_agent_state, profile_agent_state_path


def test_pipe_name_is_stable_and_instance_scoped():
    assert agent.pipe_name("Default") == agent.pipe_name("default")
    assert agent.pipe_name("Default") != agent.pipe_name("other")
    assert agent.pipe_name("Default").startswith(r"\\.\pipe\palsitter-agent-v1-")


def test_agent_client_sends_versioned_json_request(monkeypatch):
    writes = []

    class Connection:
        def write_line(self, value):
            writes.append(value)

        def read_line(self):
            return b'{"ok":true,"result":{"server_state":"idle"}}'

        def close(self):
            pass

    monkeypatch.setattr(agent, "_connect_pipe", lambda name: Connection())

    result = agent.AgentClient("test").request("status")

    assert result == {"server_state": "idle"}
    assert writes[0]["protocol"] == {"major": 1, "minor": 0}
    assert writes[0]["command"] == "status"


def test_agent_captures_pty_chunks_and_flushes_raw_output(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("PALSITTER_PROFILE_DIR", str(tmp_path / "profile"))

    class Output:
        def __init__(self):
            self.chunks = iter(["first\n", "partial", " line\n"])
            self.sizes = []

        def read(self, size=1):
            self.sizes.append(size)
            try:
                return next(self.chunks)
            except StopIteration:
                return ""

    class Process:
        pid = os.getpid()
        stdout = Output()

        def __init__(self):
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = -9

    class Job:
        def assign(self, pid):
            assert pid == os.getpid()

        def close(self):
            pass

    process = Process()
    monkeypatch.setattr(agent, "spawn_pty_process", lambda *args, **kwargs: process)
    monkeypatch.setattr(agent, "JobObject", Job)
    monkeypatch.setattr(agent, "_flush_file_buffers", lambda handle: None)

    manager = agent.ServerAgent(
        PalworldProfile(name="test", workdir=str(tmp_path), executable="PalServer.exe")
    )
    result = manager._start_server()
    assert result["server_state"] == "running"
    assert manager.output_thread is not None
    manager.output_thread.join(timeout=2)

    output = Path(manager.output_path).read_text(encoding="utf-8")
    assert output == "first\npartial line\n"
    assert output and all(size == 1 for size in process.stdout.sizes)


def test_agent_resolves_profile_relative_executable_from_repository_root():
    profile = PalworldProfile(
        name="test",
        executable="profile\\test\\steamapps\\common\\PalServer\\PalServer.exe",
        workdir="profile\\test\\steamapps\\common\\PalServer",
    )
    manager = object.__new__(agent.ServerAgent)
    manager.profile = profile

    command = manager._command()

    expected = str(
        (Path.cwd() / "profile" / "test" / "steamapps" / "common" / "PalServer" / "PalServer.exe")
        .resolve()
    )
    assert command[0] == expected


def test_agent_start_command_is_explicit(monkeypatch):
    manager = object.__new__(agent.ServerAgent)
    manager._start_server = lambda: {"server_state": "running"}
    manager._stop_server = lambda force: {"server_state": "stopped"}
    manager.status = lambda: {"server_state": "idle"}

    response, close = manager.handle(
        {"protocol": {"major": 1, "minor": 0}, "command": "status"}
    )
    assert response["result"]["server_state"] == "idle"
    assert close is False

    response, close = manager.handle(
        {"protocol": {"major": 1, "minor": 0}, "command": "start"}
    )
    assert response["result"]["server_state"] == "running"
    assert close is False


def test_managed_restore_connects_to_idle_agent_without_starting_server(monkeypatch):
    calls = []

    class Client:
        @classmethod
        def connect_existing(cls, name):
            calls.append(("connect", name))
            return cls()

        def status(self):
            calls.append(("status",))
            return {"server_state": "not_started", "server_pid": None}

        def start(self):
            calls.append(("start",))
            raise AssertionError("restore must not launch PalServer")

    monkeypatch.setattr("module.games.palworld.server.manager.WINDOWS", True)
    manager = PalServerManager(
        Profile(name="test"),
        agent_client_factory=Client,
        running_probe=lambda profile: False,
        adopt_managed=True,
    )

    manager.start(update=False, manual=False)

    assert calls == [("connect", "test"), ("status",)]
    assert manager.process is None


def test_agent_managed_stop_sends_stop_before_waiting_for_server_exit():
    class Client:
        def __init__(self):
            self.server_state = "running"
            self.stop_calls = 0

        def status(self):
            return {
                "server_state": self.server_state,
                "server_pid": 123,
                "exit_code": 0,
            }

        def stop(self):
            self.stop_calls += 1
            self.server_state = "stopped"
            return self.status()

    client = Client()
    class Rest:
        def shutdown(self):
            pass

    manager = PalServerManager(Profile(name="test"), rest_client=Rest())
    manager.agent_client = client
    manager.process = AgentServerProcess(
        client,
        {"server_pid": 123, "server_create_time": 1.0},
    )

    manager.stop(graceful=True)

    assert client.stop_calls == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows named pipes are Windows-only")
def test_named_pipe_agent_accepts_status_and_stops_without_starting_server(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("PALSITTER_PROFILE_DIR", str(tmp_path / "profile"))
    profile = PalworldProfile(name="test", workdir=str(tmp_path), executable="PalServer.exe")
    server = agent.ServerAgent(profile)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if profile_agent_state_path("test").exists():
            break
        time.sleep(0.01)

    client = agent.AgentClient.connect_existing("test")
    status = client.status()
    assert status["server_state"] == "not_started"
    client.stop()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert load_agent_state("test") is None


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are Windows-only")
def test_job_object_can_be_created_without_breakaway_flags():
    job = agent.JobObject()
    try:
        assert job.handle
    finally:
        job.close()
