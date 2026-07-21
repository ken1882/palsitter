import builtins
import subprocess
from types import SimpleNamespace

import pytest

import module.pty_process as pty_process
from module.pty_process import PosixPtyProcess, WindowsPtyProcess


class FakePopen:
    pid = 321

    def __init__(self, returncode=0):
        self.returncode = returncode
        self.wait_timeouts = []
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class FakeWinptyProcess:
    pid = 654

    def __init__(self, chunks=None, alive=None, exitstatus=0):
        self.chunks = list(chunks or [])
        self.alive = list(alive if alive is not None else [False])
        self.exitstatus = exitstatus
        self.terminate_forces = []

    def read(self, size):
        if not self.chunks:
            raise EOFError("closed")
        chunk = self.chunks.pop(0)
        if isinstance(chunk, BaseException):
            raise chunk
        return chunk

    def isalive(self):
        if self.alive:
            return self.alive.pop(0)
        return False

    def terminate(self, force=False):
        self.terminate_forces.append(force)


def test_posix_pty_process_spawns_on_slave_fd_and_closes_fds():
    closed = []
    captured = {}
    fake = FakePopen(returncode=5)
    chunks = iter([b"o", b"k", b""])

    def popen_factory(cmd, **kwargs):
        captured["cmd"] = cmd
        captured.update(kwargs)
        return fake

    proc = PosixPtyProcess(
        ["steamcmd"],
        "workdir",
        openpty=lambda: (10, 11),
        popen_factory=popen_factory,
        os_read=lambda fd, size: next(chunks),
        os_close=closed.append,
    )

    assert captured == {
        "cmd": ["steamcmd"],
        "cwd": "workdir",
        "stdin": 11,
        "stdout": 11,
        "stderr": 11,
        "close_fds": True,
        "start_new_session": True,
    }
    assert closed == [11]
    assert proc.pid == fake.pid
    assert proc.stdout.read(1) == "o"
    assert proc.stdout.read(1) == "k"
    assert proc.stdout.read(1) == ""
    assert closed == [11, 10]
    assert proc.stdout.read(1) == ""
    assert closed == [11, 10]


def test_posix_pty_process_decodes_split_utf8():
    closed = []
    euro = b"\xe2\x82\xac"
    chunks = iter([euro[0:1], euro[1:2], euro[2:3], b""])

    proc = PosixPtyProcess(
        ["steamcmd"],
        "workdir",
        openpty=lambda: (10, 11),
        popen_factory=lambda *a, **k: FakePopen(),
        os_read=lambda fd, size: next(chunks),
        os_close=closed.append,
    )

    assert proc.stdout.read(1) == "\u20ac"
    assert proc.stdout.read(1) == ""
    assert closed == [11, 10]


def test_posix_pty_process_treats_oserror_as_eof():
    closed = []

    def os_read(fd, size):
        raise OSError("pty closed")

    proc = PosixPtyProcess(
        ["steamcmd"],
        "workdir",
        openpty=lambda: (10, 11),
        popen_factory=lambda *a, **k: FakePopen(),
        os_read=os_read,
        os_close=closed.append,
    )

    assert proc.stdout.read(1) == ""
    assert closed == [11, 10]


def test_posix_pty_process_delegates_process_methods():
    fake = FakePopen(returncode=7)

    proc = PosixPtyProcess(
        ["steamcmd"],
        "workdir",
        openpty=lambda: (10, 11),
        popen_factory=lambda *a, **k: fake,
        os_read=lambda fd, size: b"",
        os_close=lambda fd: None,
    )

    assert proc.poll() == 7
    assert proc.wait(timeout=3) == 7
    assert fake.wait_timeouts == [3]
    proc.terminate()
    proc.kill()
    assert fake.terminated is True
    assert fake.killed is True


def test_windows_pty_process_reads_str_bytes_and_eof():
    spawned = {}
    fake = FakeWinptyProcess(
        chunks=["a", b"\xe2", b"\x82", b"\xac", EOFError("done")],
        exitstatus=4,
    )

    class FakePtyProcess:
        @staticmethod
        def spawn(cmd, cwd=None):
            spawned["cmd"] = cmd
            spawned["cwd"] = cwd
            return fake

    proc = WindowsPtyProcess(
        ["steamcmd", "+quit"],
        "workdir",
        pty_module=SimpleNamespace(PtyProcess=FakePtyProcess),
    )

    assert spawned == {"cmd": ["steamcmd", "+quit"], "cwd": "workdir"}
    assert proc.pid == fake.pid
    assert proc.stdout.read(1) == "a"
    assert proc.stdout.read(1) == "\u20ac"
    assert proc.stdout.read(1) == ""


def test_windows_pty_process_poll_wait_and_termination():
    fake = FakeWinptyProcess(alive=[True, False], exitstatus=9)

    class FakePtyProcess:
        @staticmethod
        def spawn(cmd, cwd=None):
            return fake

    proc = WindowsPtyProcess(
        ["steamcmd"],
        "workdir",
        pty_module=SimpleNamespace(PtyProcess=FakePtyProcess),
        sleep=lambda seconds: None,
    )

    assert proc.poll() is None
    assert proc.wait(timeout=0) == 9
    proc.terminate()
    proc.kill()
    assert fake.terminate_forces == [False, True]


def test_windows_pty_process_wait_timeout():
    fake = FakeWinptyProcess(alive=[True, True], exitstatus=0)
    times = iter([0.0, 0.1])

    class FakePtyProcess:
        @staticmethod
        def spawn(cmd, cwd=None):
            return fake

    proc = WindowsPtyProcess(
        ["steamcmd"],
        "workdir",
        pty_module=SimpleNamespace(PtyProcess=FakePtyProcess),
        sleep=lambda seconds: None,
        monotonic=lambda: next(times),
    )

    with pytest.raises(subprocess.TimeoutExpired):
        proc.wait(timeout=0.05)


def test_windows_pty_process_missing_pywinpty_error(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "winpty":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="pywinpty"):
        WindowsPtyProcess(["steamcmd"], "workdir")


def test_spawn_pty_process_dispatches_by_os_name(monkeypatch):
    class FakeWindowsProcess:
        def __init__(self, cmd, cwd):
            self.cmd = cmd
            self.cwd = cwd

    class FakePosixProcess:
        def __init__(self, cmd, cwd):
            self.cmd = cmd
            self.cwd = cwd

    monkeypatch.setattr(pty_process, "WindowsPtyProcess", FakeWindowsProcess)
    monkeypatch.setattr(pty_process, "PosixPtyProcess", FakePosixProcess)

    monkeypatch.setattr(pty_process.os, "name", "nt")
    win_proc = pty_process.spawn_pty_process(["cmd"], "cwd")
    assert isinstance(win_proc, FakeWindowsProcess)
    assert win_proc.cmd == ["cmd"]
    assert win_proc.cwd == "cwd"

    monkeypatch.setattr(pty_process.os, "name", "posix")
    posix_proc = pty_process.spawn_pty_process(["cmd"], "cwd")
    assert isinstance(posix_proc, FakePosixProcess)
    assert posix_proc.cmd == ["cmd"]
    assert posix_proc.cwd == "cwd"
