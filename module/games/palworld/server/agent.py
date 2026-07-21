"""Detached Windows PalServer owner and its local control protocol.

The agent deliberately owns only the low-level PalServer process and its
ConPTY output.  Palsitter remains responsible for lifecycle policy and reads
the persistent output file independently of this process.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psutil

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover
    msvcrt = None

from module.games.palworld.config import (
    PalworldProfile,
    executable_workdir,
    load_profile,
    windows_console_executable_path,
)
from module.instances import (
    clear_agent_state,
    load_agent_state,
    load_runtime,
    profile_agent_state_path,
    profile_server_output_path,
    safe_profile_name,
    save_agent_state,
    update_agent_state,
)
from module.pty_process import PtyProcessLike, spawn_pty_process


PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 0
AGENT_IMPLEMENTATION = 2
AGENT_CONNECT_TIMEOUT = 10.0
AGENT_MODULE = "module.games.palworld.server.agent"
_PIPE_PREFIX = r"\\.\pipe\palsitter-agent-v1-"

if os.name != "nt":  # pragma: no cover - the agent is Windows-first.
    raise_import_error = None
else:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    _kernel32.IsProcessInJob.restype = wintypes.BOOL
    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [wintypes.LPVOID]
    _kernel32.LocalFree.restype = wintypes.LPVOID

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]

    class _TokenUser(ctypes.Structure):
        _fields_ = [("User", _SidAndAttributes)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [("values", wintypes.ULARGE_INTEGER * 6)]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _TOKEN_QUERY = 0x0008
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001
    _TOKEN_USER_CLASS = 1
    _TOKEN_SESSION_ID_CLASS = 12
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _PIPE_ACCESS_DUPLEX = 0x00000003
    _PIPE_TYPE_MESSAGE = 0x00000004
    _PIPE_READMODE_MESSAGE = 0x00000002
    _PIPE_WAIT = 0x00000000
    _PIPE_UNLIMITED_INSTANCES = 255
    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _OPEN_EXISTING = 3
    _ERROR_PIPE_CONNECTED = 535
    _ERROR_PIPE_BUSY = 231
    _ERROR_BROKEN_PIPE = 109
    _ERROR_FILE_NOT_FOUND = 2
    _ERROR_MORE_DATA = 234
    _WAIT_TIMEOUT = 258
    _ERROR_ALREADY_EXISTS = 183
    _WAIT_OBJECT_0 = 0
    _WAIT_ABANDONED = 0x80
    _INFINITE = 0xFFFFFFFF
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    _kernel32.ReleaseMutex.restype = wintypes.BOOL


def _require_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("The PalServer agent is only implemented on Windows")


def pipe_name(instance_name: str) -> str:
    canonical = safe_profile_name(instance_name).casefold().encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()[:32]
    return f"{_PIPE_PREFIX}{digest}"


def _win_error(prefix: str) -> OSError:
    error = ctypes.get_last_error()
    return OSError(error, f"{prefix} (WinError {error})")


def _current_session_id() -> int:
    session = wintypes.DWORD()
    if not _kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise _win_error("Could not read Windows session ID")
    return int(session.value)


def _token_identity(token: wintypes.HANDLE) -> tuple[str, int]:
    size = wintypes.DWORD()
    _advapi32.GetTokenInformation(token, _TOKEN_USER_CLASS, None, 0, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    if not _advapi32.GetTokenInformation(
        token,
        _TOKEN_USER_CLASS,
        buffer,
        size.value,
        ctypes.byref(size),
    ):
        raise _win_error("Could not read token user")
    token_user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
    sid_text = wintypes.LPWSTR()
    if not _advapi32.ConvertSidToStringSidW(token_user.User.Sid, ctypes.byref(sid_text)):
        raise _win_error("Could not format token SID")
    try:
        sid = str(sid_text.value)
    finally:
        _kernel32.LocalFree(sid_text)
    session = wintypes.DWORD()
    session_size = wintypes.DWORD(ctypes.sizeof(session))
    if not _advapi32.GetTokenInformation(
        token,
        _TOKEN_SESSION_ID_CLASS,
        ctypes.byref(session),
        ctypes.sizeof(session),
        ctypes.byref(session_size),
    ):
        raise _win_error("Could not read token session")
    return sid, int(session.value)


def _current_identity() -> tuple[str, int]:
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(
        _kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
    ):
        raise _win_error("Could not open current process token")
    try:
        return _token_identity(token)
    finally:
        _kernel32.CloseHandle(token)


def _process_identity(pid: int) -> tuple[str, int]:
    process = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        raise _win_error("Could not open named-pipe client process")
    token = wintypes.HANDLE()
    try:
        if not _advapi32.OpenProcessToken(process, _TOKEN_QUERY, ctypes.byref(token)):
            raise _win_error("Could not open named-pipe client token")
        return _token_identity(token)
    finally:
        if token:
            _kernel32.CloseHandle(token)
        _kernel32.CloseHandle(process)


def _security_attributes(owner_sid: str) -> tuple[_SecurityAttributes, wintypes.LPVOID]:
    descriptor = wintypes.LPVOID()
    descriptor_size = wintypes.DWORD()
    sddl = f"D:P(A;;GA;;;{owner_sid})"
    if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        1,
        ctypes.byref(descriptor),
        ctypes.byref(descriptor_size),
    ):
        raise _win_error("Could not create named-pipe security descriptor")
    attributes = _SecurityAttributes(
        ctypes.sizeof(_SecurityAttributes), descriptor, False
    )
    return attributes, descriptor


class _PipeConnection:
    def __init__(self, handle: wintypes.HANDLE) -> None:
        self.handle = handle

    def close(self) -> None:
        if self.handle:
            _kernel32.CloseHandle(self.handle)
            self.handle = wintypes.HANDLE()

    def write_line(self, value: dict[str, Any]) -> None:
        payload = (json.dumps(value, separators=(",", ":")) + "\n").encode("utf-8")
        written = wintypes.DWORD()
        if not _kernel32.WriteFile(
            self.handle,
            payload,
            len(payload),
            ctypes.byref(written),
            None,
        ):
            raise _win_error("Could not write named-pipe response")

    def read_line(self) -> bytes | None:
        output = bytearray()
        while True:
            byte = ctypes.create_string_buffer(1)
            read = wintypes.DWORD()
            if not _kernel32.ReadFile(self.handle, byte, 1, ctypes.byref(read), None):
                error = ctypes.get_last_error()
                if error in (_ERROR_BROKEN_PIPE, _ERROR_FILE_NOT_FOUND):
                    return None
                if error != _ERROR_MORE_DATA:
                    raise OSError(error, f"Could not read named-pipe request (WinError {error})")
            if read.value == 0:
                return None
            if byte.raw == b"\n":
                return bytes(output)
            output.extend(byte.raw)


class _NamedPipeServer:
    def __init__(self, name: str, owner_sid: str, session_id: int) -> None:
        self.name = name
        self.owner_sid = owner_sid
        self.session_id = session_id

    def accept(self) -> _PipeConnection | None:
        attributes, descriptor = _security_attributes(self.owner_sid)
        try:
            handle = _kernel32.CreateNamedPipeW(
                self.name,
                _PIPE_ACCESS_DUPLEX,
                _PIPE_TYPE_MESSAGE | _PIPE_READMODE_MESSAGE | _PIPE_WAIT,
                _PIPE_UNLIMITED_INSTANCES,
                65536,
                65536,
                0,
                ctypes.byref(attributes),
            )
            if handle == wintypes.HANDLE(-1).value:
                raise _win_error("Could not create named pipe")
            connected = _kernel32.ConnectNamedPipe(handle, None)
            if not connected and ctypes.get_last_error() != _ERROR_PIPE_CONNECTED:
                _kernel32.CloseHandle(handle)
                return None
            client_pid = wintypes.ULONG()
            if not _kernel32.GetNamedPipeClientProcessId(handle, ctypes.byref(client_pid)):
                _kernel32.CloseHandle(handle)
                return None
            if _process_identity(int(client_pid.value)) != (self.owner_sid, self.session_id):
                _kernel32.DisconnectNamedPipe(handle)
                _kernel32.CloseHandle(handle)
                return None
            return _PipeConnection(handle)
        finally:
            _kernel32.LocalFree(descriptor)


def _connect_pipe(name: str, timeout: float = AGENT_CONNECT_TIMEOUT) -> _PipeConnection:
    _require_windows()
    deadline = time.monotonic() + timeout
    delay = 0.05
    while time.monotonic() < deadline:
        if _kernel32.WaitNamedPipeW(name, 500):
            handle = _kernel32.CreateFileW(
                name,
                _GENERIC_READ | _GENERIC_WRITE,
                0,
                None,
                _OPEN_EXISTING,
                0,
                None,
            )
            if handle != wintypes.HANDLE(-1).value:
                return _PipeConnection(handle)
        error = ctypes.get_last_error()
        if error not in (_ERROR_PIPE_BUSY, _ERROR_FILE_NOT_FOUND, _WAIT_TIMEOUT):
            raise OSError(error, f"Could not connect to agent pipe (WinError {error})")
        time.sleep(delay)
        delay = min(0.5, delay * 1.5)
    raise TimeoutError(f"Timed out connecting to PalServer agent: {name}")


def _mutex_name(prefix: str, profile_name: str) -> str:
    digest = hashlib.sha256(
        safe_profile_name(profile_name).casefold().encode("utf-8")
    ).hexdigest()[:32]
    return f"Local\\palsitter-{prefix}-{digest}"


@contextmanager
def _named_mutex(name: str, timeout: float) -> None:
    _require_windows()
    handle = _kernel32.CreateMutexW(None, False, name)
    if not handle:
        raise _win_error(f"Could not create mutex {name}")
    wait_ms = max(1, int(timeout * 1000))
    acquired = _kernel32.WaitForSingleObject(handle, wait_ms)
    if acquired not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
        _kernel32.CloseHandle(handle)
        raise TimeoutError(f"Timed out waiting for mutex {name}")
    try:
        yield
    finally:
        _kernel32.ReleaseMutex(handle)
        _kernel32.CloseHandle(handle)


@contextmanager
def _agent_launch_lock(profile_name: str):
    """Serialize GUI Start requests so two callers cannot spawn duplicate agents."""
    with _named_mutex(_mutex_name("agent-launch", profile_name), AGENT_CONNECT_TIMEOUT):
        yield


@contextmanager
def _agent_runtime_lock(profile_name: str):
    """Keep one agent process and one named-pipe server per instance."""
    with _named_mutex(_mutex_name("agent-runtime", profile_name), 0.01):
        yield


class JobObject:
    def __init__(self) -> None:
        _require_windows()
        self.handle = _kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise _win_error("Could not create PalServer Job Object")
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            self.handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            self.close()
            raise _win_error("Could not configure PalServer Job Object")

    def assign(self, pid: int) -> None:
        process = _kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid,
        )
        if not process:
            raise _win_error("Could not open PalServer for Job Object assignment")
        try:
            if not _kernel32.AssignProcessToJobObject(self.handle, process):
                raise _win_error("Could not assign PalServer to Job Object")
        finally:
            _kernel32.CloseHandle(process)

    def contains(self, pid: int) -> bool:
        """Return whether an exact process identity is in this Job Object."""
        process = _kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid,
        )
        if not process:
            raise _win_error("Could not open process for Job Object inspection")
        try:
            in_job = wintypes.BOOL()
            if not _kernel32.IsProcessInJob(process, self.handle, ctypes.byref(in_job)):
                raise _win_error("Could not inspect Job Object membership")
            return bool(in_job.value)
        finally:
            _kernel32.CloseHandle(process)

    def descendant_membership(self, root_pid: int) -> dict[int, bool]:
        """Inspect the root and all currently visible descendants for smoke tests."""
        try:
            root = psutil.Process(root_pid)
            processes = [root, *root.children(recursive=True)]
        except (OSError, psutil.Error) as exc:
            raise RuntimeError(f"Could not enumerate PalServer descendants: {exc}") from exc
        return {process.pid: self.contains(process.pid) for process in processes}

    def close(self) -> None:
        if getattr(self, "handle", None):
            _kernel32.CloseHandle(self.handle)
            self.handle = wintypes.HANDLE()


class AgentClient:
    def __init__(
        self,
        profile_name: str,
        pipe: str | None = None,
        *,
        expected_agent_pid: int | None = None,
        expected_agent_create_time: float | None = None,
    ) -> None:
        self.profile_name = profile_name
        self.pipe = pipe or pipe_name(profile_name)
        self.expected_agent_pid = expected_agent_pid
        self.expected_agent_create_time = expected_agent_create_time

    @classmethod
    def connect_existing(cls, profile_name: str) -> "AgentClient":
        state = load_agent_state(profile_name)
        if state is None or not state.get("pipe_name"):
            raise RuntimeError("PalServer agent state is missing")
        client = cls(
            profile_name,
            str(state["pipe_name"]),
            expected_agent_pid=int(state.get("agent_pid") or 0),
            expected_agent_create_time=float(state.get("agent_create_time") or 0),
        )
        status = client.request("ping")
        if not _agent_identity_matches(status, state):
            raise RuntimeError("PalServer agent identity does not match state")
        return client

    @classmethod
    def launch_idle(cls, profile_name: str) -> "AgentClient":
        _require_windows()
        client = cls(profile_name)
        with _agent_launch_lock(profile_name):
            state = load_agent_state(profile_name)
            if state and _agent_identity_alive(state):
                if int(state.get("agent_implementation", 0) or 0) == AGENT_IMPLEMENTATION:
                    return cls.connect_existing(profile_name)
                if _server_identity_alive(state):
                    raise RuntimeError(
                        "An older PalServer agent owns an active server; stop it before updating the agent"
                    )
                # An idle agent can be replaced safely. Active agents must remain
                # untouched because closing their Job Object kills PalServer.
                cls.connect_existing(profile_name).stop()
                deadline = time.monotonic() + AGENT_CONNECT_TIMEOUT
                while load_agent_state(profile_name) is not None and time.monotonic() < deadline:
                    time.sleep(0.05)
                remaining_state = load_agent_state(profile_name)
                if remaining_state is not None and _agent_identity_alive(remaining_state):
                    raise RuntimeError("Older PalServer agent did not exit; refusing to launch a duplicate")
                state = remaining_state
            if state and _server_identity_alive(state):
                raise RuntimeError("A PalServer remains alive but its agent is unavailable")
            if state:
                clear_agent_state(profile_name)
            command = [
                sys.executable,
                "-m",
                AGENT_MODULE,
                "--profile",
                profile_name,
            ]
            flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            subprocess.Popen(
                command,
                cwd=str(Path(__file__).resolve().parents[4]),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=flags,
            )
            deadline = time.monotonic() + AGENT_CONNECT_TIMEOUT
            while load_agent_state(profile_name) is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if load_agent_state(profile_name) is None:
                raise RuntimeError("PalServer agent exited before publishing agent-state.json")
            return cls.connect_existing(profile_name)

    def request(self, command: str, **arguments: Any) -> dict[str, Any]:
        if self.expected_agent_pid is None:
            connection = _connect_pipe(self.pipe)
            try:
                connection.write_line(
                    {
                        "protocol": {"major": PROTOCOL_MAJOR, "minor": PROTOCOL_MINOR},
                        "command": command,
                        "arguments": arguments,
                    }
                )
                raw = connection.read_line()
                if raw is None:
                    raise RuntimeError("PalServer agent disconnected")
                response = json.loads(raw.decode("utf-8"))
                if not response.get("ok"):
                    raise RuntimeError(str(response.get("error") or "PalServer agent request failed"))
                return dict(response.get("result") or {})
            finally:
                connection.close()
        deadline = time.monotonic() + AGENT_CONNECT_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for the expected PalServer agent")
            connection = _connect_pipe(self.pipe, timeout=remaining)
            try:
                connection.write_line(
                    {
                        "protocol": {"major": PROTOCOL_MAJOR, "minor": PROTOCOL_MINOR},
                        "command": command,
                        "arguments": arguments,
                    }
                )
                raw = connection.read_line()
                if raw is None:
                    raise RuntimeError("PalServer agent disconnected")
                response = json.loads(raw.decode("utf-8"))
                if not response.get("ok"):
                    raise RuntimeError(str(response.get("error") or "PalServer agent request failed"))
                result = dict(response.get("result") or {})
                if self.expected_agent_pid is not None and not _agent_identity_matches(
                    result,
                    {
                        "agent_pid": self.expected_agent_pid,
                        "agent_create_time": self.expected_agent_create_time,
                    },
                ):
                    continue
                return result
            finally:
                connection.close()

    def start(self) -> dict[str, Any]:
        return self.request("start")

    def status(self) -> dict[str, Any]:
        return self.request("status")

    def verify(self, profile: PalworldProfile) -> dict[str, Any]:
        state = load_agent_state(self.profile_name)
        if state is None:
            raise RuntimeError("PalServer agent state is missing")
        status = self.status()
        if not _agent_identity_matches(status, state):
            raise RuntimeError("PalServer agent identity does not match state")
        if str(status.get("server_state")) not in {"running", "starting"}:
            raise RuntimeError("PalServer is not running under the agent")
        server_pid = int(status.get("server_pid") or 0)
        if not _server_identity_matches(status, server_pid):
            raise RuntimeError("PalServer identity does not match agent state")
        expected = _expected_executables(profile)
        actual = os.path.normcase(os.path.abspath(str(status.get("server_executable") or "")))
        if actual not in expected:
            raise RuntimeError("PalServer executable does not match the configured instance")
        runtime = load_runtime(self.profile_name) or {}
        if runtime.get("session_id") and runtime["session_id"] != status.get("session_id"):
            raise RuntimeError("PalServer agent session does not match runtime metadata")
        return status

    def stop(self) -> dict[str, Any]:
        return self.request("stop")

    def kill(self) -> dict[str, Any]:
        return self.request("kill")

    def restart(self) -> dict[str, Any]:
        return self.request("restart")

    def diagnose_job(self) -> dict[str, Any]:
        return self.request("job_status")


def _agent_identity_alive(state: dict[str, Any]) -> bool:
    try:
        process = psutil.Process(int(state["agent_pid"]))
        expected = float(state["agent_create_time"])
        return process.is_running() and abs(process.create_time() - expected) <= 0.01
    except (KeyError, OSError, psutil.Error, TypeError, ValueError):
        return False


def _agent_identity_matches(status: dict[str, Any], state: dict[str, Any]) -> bool:
    try:
        return (
            int(status["agent_pid"]) == int(state["agent_pid"])
            and abs(float(status["agent_create_time"]) - float(state["agent_create_time"])) <= 0.01
        )
    except (KeyError, TypeError, ValueError):
        return False


def _server_identity_matches(status: dict[str, Any], pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return (
            process.is_running()
            and abs(process.create_time() - float(status["server_create_time"])) <= 0.01
        )
    except (KeyError, OSError, psutil.Error, TypeError, ValueError):
        return False


def _expected_executables(profile: PalworldProfile) -> set[str]:
    executable = Path(profile.executable)
    workdir = Path(executable_workdir(profile.executable) or profile.workdir).resolve()
    if executable.is_absolute() or executable.parent != Path("."):
        executable = executable.resolve()
    else:
        executable = (workdir / executable).resolve()
    expected = {os.path.normcase(os.path.abspath(str(executable)))}
    console = windows_console_executable_path(executable, workdir)
    if console is not None and console.is_file():
        expected.add(os.path.normcase(os.path.abspath(str(console))))
    return expected


def _server_identity_alive(state: dict[str, Any]) -> bool:
    try:
        process = psutil.Process(int(state["server_pid"]))
        expected = float(state["server_create_time"])
        return process.is_running() and abs(process.create_time() - expected) <= 0.01
    except (KeyError, OSError, psutil.Error, TypeError, ValueError):
        return False


class ServerAgent:
    def __init__(self, profile: PalworldProfile) -> None:
        _require_windows()
        self.profile = profile
        self.name = profile.name
        self.pipe = pipe_name(profile.name)
        self.owner_sid, self.windows_session_id = _current_identity()
        self.session_id = uuid.uuid4().hex
        self.process: PtyProcessLike | None = None
        self.job: JobObject | None = None
        self.output_thread: threading.Thread | None = None
        self.output_handle = None
        self.stop_reader = threading.Event()
        self.lock = threading.RLock()
        self.state = "idle"
        self.server_state = "not_started"
        self.exit_code: int | None = None
        self.exit_reason: str | None = None
        self.server_pid: int | None = None
        self.server_create_time: float | None = None
        self.server_executable: str | None = None
        self.output_path = str(profile_server_output_path(profile.name))

    def _write_state(self, **changes: Any) -> None:
        with self.lock:
            data = {
                "protocol_major": PROTOCOL_MAJOR,
                "protocol_minor": PROTOCOL_MINOR,
                "agent_implementation": AGENT_IMPLEMENTATION,
                "agent_pid": os.getpid(),
                "agent_create_time": _create_time(os.getpid()),
                "pipe_name": self.pipe,
                "owner_sid": self.owner_sid,
                "windows_session_id": self.windows_session_id,
                "session_id": self.session_id,
                "output_path": self.output_path,
                "agent_state": self.state,
                "server_state": self.server_state,
                "server_pid": self.server_pid,
                "server_create_time": self.server_create_time,
                "server_executable": self.server_executable,
                "exit_code": self.exit_code,
                "exit_reason": self.exit_reason,
                "heartbeat": time.time(),
            }
            data.update(changes)
            save_agent_state(self.name, data)

    def _command(self) -> list[str]:
        exe = Path(self.profile.executable)
        workdir = Path(executable_workdir(self.profile.executable) or self.profile.workdir).resolve()
        if exe.is_absolute() or exe.parent != Path("."):
            exe = exe.resolve()
        else:
            exe = (workdir / exe).resolve()
        console = windows_console_executable_path(exe, workdir)
        if console is not None and console.is_file():
            exe = console
            flags = {argument.casefold() for argument in self.profile.build_executable_args()}
            args = [
                argument
                for argument in ("-stdout", "-FullStdOutLogOutput", "-FORCELOGFLUSH")
                if argument.casefold() not in flags
            ]
        else:
            args = []
        args.extend(self.profile.build_executable_args())
        return [
            str(exe),
            *args,
            f"-port={self.profile.game_port}",
            f"-queryport={self.profile.query_port}",
        ]

    def _alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _start_server(self) -> dict[str, Any]:
        with self.lock:
            if self._alive():
                return self.status()
            self.stop_reader.clear()
            self.exit_code = None
            self.exit_reason = None
            self.server_state = "starting"
            self.state = "running"
            self.session_id = uuid.uuid4().hex
            self.output_path = str(profile_server_output_path(self.name))
            Path(self.output_path).parent.mkdir(parents=True, exist_ok=True)
            self.output_handle = open(self.output_path, "wb")
            workdir = Path(
                executable_workdir(self.profile.executable) or self.profile.workdir
            ).resolve()
            workdir.mkdir(parents=True, exist_ok=True)
            self.process = spawn_pty_process(self._command(), cwd=str(workdir))
            self.job = JobObject()
            try:
                self.job.assign(self.process.pid)
            except Exception:
                self.process.kill()
                self.output_handle.close()
                self.output_handle = None
                self.process = None
                self.job.close()
                self.job = None
                self.state = "idle"
                self.server_state = "failed"
                self._write_state()
                raise
            self.server_pid = int(self.process.pid)
            self.server_create_time = _create_time(self.server_pid)
            self.server_executable = str(Path(self._command()[0]).resolve())
            self.server_state = "running"
            self._write_state()
            self.output_thread = threading.Thread(target=self._read_output, daemon=True)
            self.output_thread.start()
            return self.status()

    def _read_output(self) -> None:
        process = self.process
        handle = self.output_handle
        if process is None or handle is None:
            return
        last_flush = time.monotonic()
        bytes_since_flush = 0
        try:
            while not self.stop_reader.is_set():
                # pywinpty's Windows console stream can hold a long-lived
                # server's small writes behind a large read request. SteamCMD
                # uses the same one-character strategy in Palsitter and gets
                # prompt output from this backend.
                chunk = process.stdout.read(1)
                if not chunk:
                    break
                raw = chunk.encode("utf-8", errors="replace") if isinstance(chunk, str) else bytes(chunk)
                handle.write(raw)
                handle.flush()
                bytes_since_flush += len(raw)
                now = time.monotonic()
                if bytes_since_flush >= 4096 or now - last_flush >= 0.25:
                    _flush_file_buffers(handle)
                    bytes_since_flush = 0
                    last_flush = now
        except Exception as exc:
            self.exit_reason = f"output capture failed: {exc}"
        finally:
            try:
                handle.flush()
                _flush_file_buffers(handle)
            except Exception as exc:
                self.exit_reason = self.exit_reason or f"output flush failed: {exc}"
            with self.lock:
                process = self.process
                code = process.poll() if process is not None else None
                self.exit_code = code
                if self.stop_reader.is_set():
                    self.server_state = "stopped"
                    self.exit_reason = self.exit_reason or "stopped"
                elif code is None:
                    self.server_state = "crashed"
                    self.exit_reason = self.exit_reason or "output ended while process was alive"
                else:
                    self.server_state = "crashed" if code else "stopped"
                    self.exit_reason = self.exit_reason or ("process exited" if code else "process exited normally")
                self.state = "crashed" if self.server_state == "crashed" else "idle"
                self._write_state()
            try:
                handle.close()
            except Exception:
                pass
            self.output_handle = None
            if self.job is not None:
                self.job.close()
                self.job = None
            self.process = None
            self._write_state()

    def _stop_server(self, *, force: bool) -> dict[str, Any]:
        process = self.process
        if process is not None and process.poll() is None:
            self.stop_reader.set()
            if force:
                process.kill()
            else:
                process.terminate()
            try:
                process.wait(timeout=10)
            except Exception:
                process.kill()
        if self.output_thread is not None and self.output_thread.is_alive():
            self.output_thread.join(timeout=2)
        self.server_state = "stopped"
        self.state = "idle"
        self.exit_reason = "force stopped" if force else "stopped"
        self._write_state()
        return self.status()

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "protocol_major": PROTOCOL_MAJOR,
                "protocol_minor": PROTOCOL_MINOR,
                "agent_implementation": AGENT_IMPLEMENTATION,
                "agent_pid": os.getpid(),
                "agent_create_time": _create_time(os.getpid()),
                "server_pid": self.server_pid,
                "server_create_time": self.server_create_time,
                "server_executable": self.server_executable,
                "agent_state": self.state,
                "server_state": self.server_state,
                "session_id": self.session_id,
                "output_path": self.output_path,
                "exit_code": self.exit_code,
                "exit_reason": self.exit_reason,
            }

    def handle(self, request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        protocol = request.get("protocol") or {}
        if int(protocol.get("major", -1)) != PROTOCOL_MAJOR:
            return {"ok": False, "error": "Unsupported agent protocol"}, False
        command = str(request.get("command") or "")
        if command == "ping" or command == "status":
            return {"ok": True, "result": self.status()}, False
        if command == "start":
            return {"ok": True, "result": self._start_server()}, False
        if command == "stop":
            return {"ok": True, "result": self._stop_server(force=False)}, True
        if command == "kill":
            return {"ok": True, "result": self._stop_server(force=True)}, True
        if command == "restart":
            self._stop_server(force=False)
            return {"ok": True, "result": self._start_server()}, False
        if command == "job_status":
            if self.job is None or self.server_pid is None or not self._alive():
                return {
                    "ok": True,
                    "result": {"server_pid": self.server_pid, "members": {}},
                }, False
            members = self.job.descendant_membership(self.server_pid)
            return {
                "ok": True,
                "result": {
                    "server_pid": self.server_pid,
                    "members": members,
                    "outside_job": [pid for pid, in_job in members.items() if not in_job],
                },
            }, False
        return {"ok": False, "error": f"Unknown agent command: {command}"}, False

    def run(self) -> int:
        with _agent_runtime_lock(self.name):
            self._write_state()
            server = _NamedPipeServer(self.pipe, self.owner_sid, self.windows_session_id)
            try:
                while True:
                    connection = server.accept()
                    if connection is None:
                        continue
                    close_after_response = False
                    try:
                        while True:
                            raw = connection.read_line()
                            if raw is None:
                                break
                            try:
                                request = json.loads(raw.decode("utf-8"))
                                response, close_after_response = self.handle(request)
                            except Exception as exc:
                                response = {"ok": False, "error": str(exc)}
                            connection.write_line(response)
                            if close_after_response:
                                break
                    finally:
                        connection.close()
                    if close_after_response:
                        break
                return 0
            finally:
                if self.process is not None and self.process.poll() is None:
                    try:
                        self._stop_server(force=True)
                    except Exception:
                        pass
                if self.job is not None:
                    self.job.close()
                clear_agent_state(self.name)


def _create_time(pid: int) -> float | None:
    try:
        return float(psutil.Process(pid).create_time())
    except (OSError, psutil.Error, TypeError, ValueError):
        return None


def _flush_file_buffers(handle: Any) -> None:
    if os.name != "nt":
        return
    if not _kernel32.FlushFileBuffers(ctypes.c_void_p(msvcrt.get_osfhandle(handle.fileno()))):
        raise _win_error("Could not flush PalServer output file")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Palsitter PalServer agent")
    parser.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    _require_windows()
    profile = load_profile(args.profile)
    return ServerAgent(profile).run()


if __name__ == "__main__":
    raise SystemExit(main())
