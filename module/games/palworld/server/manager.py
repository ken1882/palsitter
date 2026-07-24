from __future__ import annotations

import datetime as dt
import base64
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

import psutil

from module.games.palworld.backup import BackupService
from module.games.palworld.config import (
    PALWORLD_SERVER_APP_ID,
    PalworldProfile,
    executable_workdir,
    fixed_executable_path,
    fixed_server_launcher_path,
    legacy_memory_restart_mb,
    sync_game_user_settings,
    windows_console_executable_path,
)
from module.pty_process import PtyProcessLike, spawn_pty_process
from module.games.palworld.server.rest import PalRestClient
from module.games.palworld.server.agent import AgentClient
from module.games.palworld.server.status import instance_is_running, matching_instance_processes
from module.games.palworld.server.history import (
    LifecycleEvent,
    TerminationInfo,
    classify_launch_error,
    classify_process_exit,
)
from module.games.palworld.worldsettings.service import ensure_world_settings
from module.games.palworld.audit import (
    format_palserver_log_line,
    parse_palserver_audit_line,
)
from module.games.palworld.update import PalworldUpdateService
from module.steamcmd import steamcmd_platform_args
from module.thread_watchdog import ThreadWatchdog
from module.games.registry import AdapterEvent, UpdateInfo
from module.instances import (
    DailyLogWriter,
    clear_runtime,
    load_runtime,
    profile_server_output_path,
    save_runtime,
    update_runtime,
)


CRASH_RESTART_LIMIT_WINDOW_SECONDS = 3600
STEAMCMD_SELF_UPDATE_MARKER = "Update complete, launching"
STEAMCMD_SILENCE_NOTICE_SECONDS = 30
STEAMCMD_PROGRESS_LOG_INTERVAL_SECONDS = 2.0
STEAMCMD_PROGRESS_RE = re.compile(r"Update state \(0x[0-9a-fA-F]+\).*progress: (\d+\.\d+)")
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PACKED_OUTPUT_RE = re.compile(r"[\u0080-\uffff]+")
MEMORY_RESTART_SUSTAINED_SAMPLES = 3
MEBIBYTE = 1024 * 1024
CRASH_POLL_INTERVAL_SECONDS = 1
AUTO_UPDATE_CHECK_INTERVAL_SECONDS = 30 * 60
WINDOWS = os.name == "nt"
WINDOWS_STDOUT_ARGS = ("-stdout", "-FullStdOutLogOutput", "-FORCELOGFLUSH")
SERVER_OUTPUT_POLL_SECONDS = 0.5
SERVER_OUTPUT_REPLAY_LINES = 300
SERVER_OUTPUT_RESTART_DELAY_SECONDS = 1.0


class AttachedServerProcess:
    """Small subprocess-compatible view over an adopted psutil process."""

    stdout = None

    def __init__(self, process: psutil.Process) -> None:
        self._process = process
        self.pid = process.pid

    def poll(self) -> int | None:
        try:
            return None if self._process.is_running() else 0
        except psutil.Error:
            return 0

    def wait(self, timeout: float | None = None) -> int:
        self._process.wait(timeout=timeout)
        return 0

    def terminate(self) -> None:
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()


class AgentServerProcess:
    """Subprocess-compatible view over a server owned by a detached agent."""

    stdout = None

    def __init__(self, client: AgentClient, status: dict[str, object]) -> None:
        self.client = client
        self.pid = int(status.get("server_pid") or 0)
        self.create_time = status.get("server_create_time")
        self.returncode: int | None = None

    def poll(self) -> int | None:
        try:
            status = self.client.status()
        except (OSError, RuntimeError, TimeoutError):
            return 0
        if str(status.get("server_state")) in {"running", "starting"}:
            return None
        value = status.get("exit_code")
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            code = self.poll()
            if code is not None:
                self.returncode = code
                return code
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.pid, timeout)
            time.sleep(0.1)

    def terminate(self) -> None:
        self.client.stop()

    def kill(self) -> None:
        self.client.kill()


def force_stop_process(profile: PalworldProfile) -> None:
    """Force-stop the exact PalServer process for shared ProcessManager KILL."""
    if WINDOWS:
        try:
            AgentClient.connect_existing(profile.name).kill()
            clear_runtime(profile.name)
            return
        except (OSError, RuntimeError, TimeoutError):
            pass
    try:
        roots = list(matching_instance_processes(profile))
    except (OSError, psutil.Error):
        roots = []
    processes: list[psutil.Process] = []
    seen: set[int] = set()
    for root in roots:
        try:
            candidates = [root, *root.children(recursive=True)]
        except (OSError, psutil.Error):
            candidates = [root]
        for process in candidates:
            if process.pid not in seen:
                seen.add(process.pid)
                processes.append(process)
    for process in processes:
        try:
            process.terminate()
        except (OSError, psutil.Error):
            pass
    if processes:
        _, alive = psutil.wait_procs(processes, timeout=0.5)
        for process in alive:
            try:
                process.kill()
            except (OSError, psutil.Error):
                pass
    clear_runtime(profile.name)


class PalServerManager:
    def __init__(
        self,
        profile: PalworldProfile,
        logger: Callable[[str], None] = print,
        rest_client: Optional[PalRestClient] = None,
        run_command: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        pty_process_factory: Callable[..., PtyProcessLike] = spawn_pty_process,
        sleep: Callable[[float], None] = time.sleep,
        virtual_memory: Callable[[], object] = psutil.virtual_memory,
        stop_requested: Callable[[], bool] = lambda: False,
        handoff_requested: Callable[[], bool] = lambda: False,
        force_stop_requested: Callable[[], bool] = lambda: False,
        adopt_managed: bool = False,
        backup_service: Optional[BackupService] = None,
        now: Callable[[], dt.datetime] = dt.datetime.now,
        monotonic: Callable[[], float] = time.monotonic,
        state_callback: Callable[[str], None] = lambda _: None,
        event_callback: Callable[[LifecycleEvent], None] = lambda _: None,
        running_probe: Callable[[PalworldProfile], bool] | None = None,
        agent_client_factory: Callable[..., AgentClient] | None = None,
    ) -> None:
        self.profile = profile
        self.log = logger
        # This manager only issues REST requests after it has established that
        # the exact configured process is managed or attached. Avoid repeating
        # the comparatively expensive process scan in every fresh supervisor.
        self.rest = rest_client or PalRestClient(profile, availability_probe=lambda _: True)
        self.run_command = run_command
        self.popen_factory = popen_factory
        self.pty_process_factory = pty_process_factory
        self.sleep = sleep
        self.virtual_memory = virtual_memory
        self.stop_requested = stop_requested
        self.handoff_requested = handoff_requested
        self.force_stop_requested = force_stop_requested
        self.adopt_managed = adopt_managed
        self.backup_service = backup_service
        self.now = now
        self.monotonic = monotonic
        self.state_callback = state_callback
        self.event_callback = event_callback
        self.running_probe = running_probe or instance_is_running
        self.agent_client_factory = agent_client_factory
        self.agent_client: AgentClient | None = None
        self._crash_recovery = False
        self.process: Optional[subprocess.Popen | AttachedServerProcess] = None
        self.ps_process: Optional[psutil.Process] = None
        self.last_status: Optional[str] = None
        self.countdown = -1
        self.countdown_reason: str | None = None
        self.countdown_detail: dict[str, object] = {}
        self.countdown_triggered_at: dt.datetime | None = None
        self.memory_threshold_samples = 0
        self.warning = False
        self.external_attached = False
        self.crash_times: deque[dt.datetime] = deque()
        self.self_heal_crash_times: deque[dt.datetime] = deque()
        self.recent_events: deque[LifecycleEvent] = deque(maxlen=20)
        self.next_planned_restart: dt.datetime | None = None
        self.next_auto_update_check_at: float | None = None
        self.auto_update_info: UpdateInfo | None = None
        self.auto_update_idle_since: dt.datetime | None = None
        self._server_output_tail: deque[str] = deque(maxlen=5)
        self._output_thread: threading.Thread | None = None
        self._capture_output_thread: threading.Thread | None = None
        self._capture_output_watchdog: ThreadWatchdog | None = None
        self._server_output_writer: DailyLogWriter | None = None
        self._server_output_stop_event: threading.Event | None = None
        self._server_output_watchdog: ThreadWatchdog | None = None
        self._server_output_path: Path | None = None
        self._ue4ss_output_thread: threading.Thread | None = None
        self._ue4ss_stop_event: threading.Event | None = None
        self._ue4ss_output_watchdog: ThreadWatchdog | None = None

    def _record_event(
        self,
        reason: str,
        outcome: str,
        *,
        when: dt.datetime | None = None,
        detail: dict[str, object] | None = None,
        termination: TerminationInfo | None = None,
    ) -> None:
        event = LifecycleEvent(
            when or self.now(),
            reason,
            outcome,
            self.next_planned_restart,
            detail or {},
            termination,
        )
        self.recent_events.append(event)
        self.event_callback(event)

    def _record_adapter_event(
        self,
        event_type: str,
        message: str,
        *,
        when: dt.datetime | None = None,
    ) -> None:
        self.event_callback(
            AdapterEvent(when or self.now(), event_type, message)
        )

    def _run_steamcmd_update(self) -> tuple[int, str, str]:
        process = self.pty_process_factory(
            self.steam_update_args(),
            cwd=self._steamcmd_workdir(),
        )
        assert process.stdout is not None
        output: queue.Queue[str] = queue.Queue()

        def read_output() -> None:
            buffer = ""
            read = getattr(process.stdout, "read", None)

            def push_chunk(chunk: str) -> None:
                nonlocal buffer
                for char in chunk:
                    if char in ("\r", "\n"):
                        output.put(buffer)
                        buffer = ""
                    else:
                        buffer += char

            if read is None:
                for chunk in process.stdout:
                    push_chunk(chunk)
            else:
                while True:
                    chunk = read(1)
                    if not chunk:
                        break
                    push_chunk(chunk)
            if buffer:
                output.put(buffer)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        all_lines: list[str] = []
        last_line = ""
        last_output_at = time.monotonic()
        silence_logged_at = last_output_at
        last_progress_logged_at: float | None = None
        last_progress_percent: float | None = None

        def handle_line(line: str) -> None:
            nonlocal last_line, last_output_at, silence_logged_at
            nonlocal last_progress_logged_at, last_progress_percent
            text = ANSI_ESCAPE_RE.sub("", line.rstrip()).lstrip()
            if not text:
                return
            all_lines.append(text)
            last_line = text
            last_output_at = time.monotonic()
            silence_logged_at = last_output_at

            match = STEAMCMD_PROGRESS_RE.search(text)
            if match:
                percent = float(match.group(1))
                if (
                    last_progress_logged_at is not None
                    and last_output_at - last_progress_logged_at < STEAMCMD_PROGRESS_LOG_INTERVAL_SECONDS
                    and int(percent) == int(last_progress_percent)
                ):
                    return
                last_progress_logged_at = last_output_at
                last_progress_percent = percent
            self.log(f"SteamCMD: {text}")

        while True:
            returncode = process.poll()
            if returncode is not None:
                reader.join(timeout=1)
                while True:
                    try:
                        handle_line(output.get_nowait())
                    except queue.Empty:
                        break
                return returncode, last_line, "\n".join(all_lines)

            try:
                handle_line(output.get(timeout=1))
                continue
            except queue.Empty:
                pass

            now = time.monotonic()
            if (
                now - last_output_at >= STEAMCMD_SILENCE_NOTICE_SECONDS
                and now - silence_logged_at >= STEAMCMD_SILENCE_NOTICE_SECONDS
            ):
                silence = int(now - last_output_at)
                self.log(f"SteamCMD still running; no output for {silence}s")
                silence_logged_at = now

    def _workdir(self) -> str:
        configured = Path(self.profile.executable)
        fixed = fixed_executable_path(self.profile.name)
        if configured.resolve() == fixed.resolve():
            return self.profile.workdir
        return executable_workdir(self.profile.executable) or self.profile.workdir

    def _resolved_workdir(self) -> Path:
        return Path(self._workdir()).resolve()

    def _resolved_steamcmd(self) -> str:
        steamcmd = Path(self.profile.steamcmd)
        if steamcmd.is_absolute() or steamcmd.parent != Path("."):
            return str(steamcmd.resolve())
        return str(steamcmd)

    def _steamcmd_workdir(self) -> str:
        steamcmd = Path(self.profile.steamcmd)
        if steamcmd.is_absolute() or steamcmd.parent != Path("."):
            return str(steamcmd.resolve().parent)
        return str(self._resolved_workdir())

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def state(self) -> str:
        if self.alive:
            return "running"
        if self.warning:
            return "warning"
        return "inactive"

    def steam_update_args(self) -> list[str]:
        args = [
            self._resolved_steamcmd(),
            *steamcmd_platform_args(),
            "+force_install_dir",
            str(self._resolved_workdir()),
            "+login",
            "anonymous",
            "+app_update",
            PALWORLD_SERVER_APP_ID,
        ]
        if self.profile.steam_validate:
            args.append("validate")
        args.append("+quit")
        return args

    def _stream_server_output(self, process: subprocess.Popen | PtyProcessLike) -> None:
        if process.stdout is None:
            return

        def emit(raw_line: str) -> None:
            for line in self._server_output_lines(raw_line):
                admin_password = self.profile.rest_password
                display_line = format_palserver_log_line(line, admin_password)
                self._server_output_tail.append(display_line[:500])
                event = parse_palserver_audit_line(line, admin_password)
                if event is not None:
                    self.event_callback(event)
                self.log(f"PalServer: {display_line}")

        try:
            output = iter(process.stdout)
        except TypeError:
            buffer = ""
            while True:
                chunk = process.stdout.read(1)
                if not chunk:
                    break
                for character in chunk:
                    if character in "\r\n":
                        if buffer:
                            emit(buffer)
                            buffer = ""
                    else:
                        buffer += character
            if buffer:
                emit(buffer)
            return

        for raw_line in output:
            emit(raw_line)

    def _capture_server_output(self, process: subprocess.Popen) -> None:
        output = process.stdout
        if output is None:
            return
        log_directory = self._server_output_path.parent if self._server_output_path else None
        writer = DailyLogWriter(
            lambda: (
                log_directory / f"palserver-{dt.datetime.now():%Y%m%d}.log"
                if log_directory is not None
                else profile_server_output_path(self.profile.name)
            )
        )
        self._server_output_writer = writer
        try:
            for chunk in output:
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                writer.write(bytes(chunk))
                writer.flush()
        finally:
            writer.close()
            if self._server_output_writer is writer:
                self._server_output_writer = None

    def _stop_capture_output(self) -> None:
        watchdog = self._capture_output_watchdog
        thread = self._capture_output_thread
        if watchdog is not None:
            watchdog.stop(timeout=1)
        elif thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1)
        self._capture_output_watchdog = None
        self._capture_output_thread = None

    @staticmethod
    def _file_cursor(path: Path) -> tuple[int, tuple[int, int] | None, bytes]:
        try:
            stat = path.stat()
        except (FileNotFoundError, OSError):
            return 0, None, b""
        try:
            with path.open("rb") as handle:
                prefix = handle.read(min(stat.st_size, 4096))
        except OSError:
            prefix = b""
        return stat.st_size, (stat.st_dev, stat.st_ino), prefix

    @staticmethod
    def _bounded_replay_offset(path: Path, offset: int, max_lines: int) -> int:
        if max_lines <= 0:
            return offset
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                data = handle.read()
        except OSError:
            return offset
        parts = data.splitlines(keepends=True)
        if len(parts) <= max_lines:
            return offset
        return offset + sum(len(part) for part in parts[-max_lines:])

    def _stream_file_output(
        self,
        path: Path,
        stop_event: threading.Event,
        *,
        initial_offset: int,
        initial_file_id: tuple[int, int] | None,
        initial_prefix: bytes = b"",
        active: Callable[[], bool],
        emit: Callable[[str], None],
        replay_limit: int = 0,
        cursor_callback: Callable[[Path, int, tuple[int, int] | None, bytes], None] | None = None,
        path_provider: Callable[[], Path] | None = None,
    ) -> None:
        active_path = path
        offset = self._bounded_replay_offset(path, initial_offset, replay_limit)
        file_id = initial_file_id
        prefix = initial_prefix
        pending = b""
        error_reported = False

        def report_stream_error(exc: Exception) -> None:
            nonlocal error_reported
            if error_reported:
                return
            try:
                self.log(f"PalServer: log streaming unavailable: {exc}")
            except Exception:
                pass
            error_reported = True

        def emit_complete_lines(*, flush: bool = False) -> None:
            nonlocal pending
            parts = pending.split(b"\n")
            pending = b"" if flush else parts.pop()
            for raw_line in parts:
                line = raw_line.rstrip(b"\r").decode("utf-8", errors="replace")
                if line:
                    try:
                        emit(line)
                    except Exception as exc:
                        report_stream_error(exc)
            if flush and pending:
                line = pending.rstrip(b"\r").decode("utf-8", errors="replace")
                pending = b""
                if line:
                    try:
                        emit(line)
                    except Exception as exc:
                        report_stream_error(exc)

        while not stop_event.is_set():
            try:
                next_path = path_provider() if path_provider is not None else active_path
                if next_path != active_path:
                    active_path = next_path
                    offset = 0
                    file_id = None
                    prefix = b""
                    pending = b""
                stat = active_path.stat()
                current_id = (stat.st_dev, stat.st_ino)
                if file_id is not None and current_id != file_id:
                    offset = self._bounded_replay_offset(active_path, 0, replay_limit)
                    pending = b""
                    prefix = b""
                elif stat.st_size < offset:
                    offset = self._bounded_replay_offset(active_path, 0, replay_limit)
                    pending = b""
                    prefix = b""
                file_id = current_id

                if prefix and offset >= len(prefix) and stat.st_size >= len(prefix):
                    with active_path.open("rb") as handle:
                        current_prefix = handle.read(len(prefix))
                    if current_prefix != prefix:
                        offset = self._bounded_replay_offset(active_path, 0, replay_limit)
                        pending = b""
                        prefix = b""

                with active_path.open("rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                if chunk:
                    offset += len(chunk)
                    pending += chunk
                    emit_complete_lines()
                    if not prefix:
                        with active_path.open("rb") as handle:
                            prefix = handle.read(min(offset, 4096))
                    if cursor_callback is not None:
                        cursor_callback(active_path, offset, file_id, prefix)
                error_reported = False
            except FileNotFoundError:
                file_id = None
                offset = 0
                pending = b""
                prefix = b""
            except OSError as exc:
                report_stream_error(exc)
            except Exception as exc:
                report_stream_error(exc)

            try:
                is_active = active()
            except Exception as exc:
                report_stream_error(exc)
                is_active = True
            if not is_active:
                break
            stop_event.wait(SERVER_OUTPUT_POLL_SECONDS)

        emit_complete_lines(flush=True)

    def _server_output_line(self, line: str) -> None:
        for parsed in self._server_output_lines(line):
            admin_password = self.profile.rest_password
            display_line = format_palserver_log_line(parsed, admin_password)
            self._server_output_tail.append(display_line[:500])
            event = parse_palserver_audit_line(parsed, admin_password)
            if event is not None:
                self.event_callback(event)
            self.log(f"PalServer: {display_line}")

    def _start_server_output(self, *, replay_existing: bool) -> None:
        self._stop_server_output()
        path = self._server_output_path or profile_server_output_path(self.profile.name)
        log_directory = path.parent
        path_provider = lambda: log_directory / f"palserver-{dt.datetime.now():%Y%m%d}.log"
        runtime = load_runtime(self.profile.name) or {}
        if replay_existing:
            offset = int(runtime.get("output_offset", 0) or 0)
            raw_file_id = runtime.get("output_file_id")
            file_id = tuple(raw_file_id) if isinstance(raw_file_id, list) and len(raw_file_id) == 2 else None
            _, current_id, prefix = self._file_cursor(path)
            if file_id is None:
                file_id = current_id
            encoded_prefix = runtime.get("output_prefix")
            if isinstance(encoded_prefix, str):
                try:
                    prefix = base64.b64decode(encoded_prefix.encode("ascii"), validate=True)
                except (ValueError, UnicodeEncodeError):
                    prefix = b""
        else:
            current_size, file_id, prefix = self._file_cursor(path)
            offset = min(int(runtime.get("output_offset", current_size) or 0), current_size)

        stop_event = threading.Event()

        cursor_state = {
            "offset": offset,
            "file_id": file_id,
            "prefix": prefix,
        }

        def persist_cursor(
            current_path: Path,
            new_offset: int,
            new_file_id: tuple[int, int] | None,
            new_prefix: bytes,
        ) -> None:
            cursor_state.update(
                offset=new_offset,
                file_id=new_file_id,
                prefix=new_prefix,
            )
            try:
                update_runtime(
                    self.profile.name,
                    output_path=str(current_path),
                    output_offset=new_offset,
                    output_file_id=list(new_file_id) if new_file_id is not None else None,
                    output_prefix=base64.b64encode(new_prefix).decode("ascii"),
                )
            except OSError:
                pass

        def stream_output() -> None:
            self._stream_file_output(
                path,
                stop_event,
                initial_offset=cursor_state["offset"],
                initial_file_id=cursor_state["file_id"],
                initial_prefix=cursor_state["prefix"],
                active=lambda: self.alive,
                emit=self._server_output_line,
                replay_limit=SERVER_OUTPUT_REPLAY_LINES if replay_existing else 0,
                cursor_callback=persist_cursor,
                path_provider=path_provider,
            )

        watchdog = ThreadWatchdog(
            stream_output,
            name=f"{self.profile.name} PalServer log tailer",
            should_run=lambda: self.alive,
            stop_event=stop_event,
            restart_delay=SERVER_OUTPUT_RESTART_DELAY_SECONDS,
            logger=lambda message: self.log(f"PalServer: {message}"),
        )
        self._server_output_stop_event = stop_event
        self._server_output_watchdog = watchdog
        watchdog.start()
        self._output_thread = watchdog.thread

    def _stop_server_output(self) -> None:
        stop_event = self._server_output_stop_event
        watchdog = self._server_output_watchdog
        thread = self._output_thread
        if stop_event is not None:
            stop_event.set()
        if watchdog is not None:
            watchdog.stop(timeout=1)
        elif thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1)
        self._server_output_stop_event = None
        self._server_output_watchdog = None
        self._output_thread = None

    def _ue4ss_log_path(self) -> Path | None:
        from module.games.palworld.mods import UE4SSService

        return UE4SSService(self.profile).log_path()

    def _ue4ss_log_cursor(self) -> tuple[Path, int, tuple[int, int] | None, bytes] | None:
        path = self._ue4ss_log_path()
        if path is None:
            return None
        try:
            stat = path.stat()
        except (FileNotFoundError, OSError):
            return path, 0, None, b""
        try:
            with path.open("rb") as handle:
                prefix = handle.read(min(stat.st_size, 4096))
        except OSError:
            prefix = b""
        return path, stat.st_size, (stat.st_dev, stat.st_ino), prefix

    def _stream_ue4ss_output(
        self,
        path: Path,
        stop_event: threading.Event,
        *,
        initial_offset: int,
        initial_file_id: tuple[int, int] | None,
        initial_prefix: bytes = b"",
        active: Callable[[], bool],
        cursor_callback: Callable[[int, tuple[int, int] | None, bytes], None] | None = None,
    ) -> None:
        offset = initial_offset
        file_id = initial_file_id
        prefix = initial_prefix
        pending = b""
        error_reported = False

        def emit_complete_lines(*, flush: bool = False) -> None:
            nonlocal pending
            parts = pending.split(b"\n")
            pending = b"" if flush else parts.pop()
            for raw_line in parts:
                line = raw_line.rstrip(b"\r").decode("utf-8", errors="replace")
                if line:
                    self.log(f"UE4SS: {line}")
            if flush and pending:
                line = pending.rstrip(b"\r").decode("utf-8", errors="replace")
                pending = b""
                if line:
                    self.log(f"UE4SS: {line}")

        while not stop_event.is_set():
            try:
                stat = path.stat()
                current_id = (stat.st_dev, stat.st_ino)
                if file_id is not None and current_id != file_id:
                    offset = 0
                    pending = b""
                    prefix = b""
                elif stat.st_size < offset:
                    offset = 0
                    pending = b""
                    prefix = b""
                file_id = current_id

                if prefix and offset >= len(prefix) and stat.st_size >= len(prefix):
                    with path.open("rb") as handle:
                        current_prefix = handle.read(len(prefix))
                    if current_prefix != prefix:
                        offset = 0
                        pending = b""
                        prefix = b""

                with path.open("rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                if chunk:
                    offset += len(chunk)
                    pending += chunk
                    emit_complete_lines()
                    if not prefix:
                        with path.open("rb") as handle:
                            prefix = handle.read(min(offset, 4096))
                    if cursor_callback is not None:
                        cursor_callback(offset, file_id, prefix)
                error_reported = False
            except FileNotFoundError:
                file_id = None
                offset = 0
                pending = b""
                prefix = b""
            except OSError as exc:
                if not error_reported:
                    self.log(f"UE4SS: log streaming unavailable: {exc}")
                    error_reported = True
            except Exception as exc:
                if not error_reported:
                    self.log(f"UE4SS: log streaming unavailable: {exc}")
                    error_reported = True

            if not active():
                break
            stop_event.wait(0.1)

        emit_complete_lines(flush=True)

    def _stop_ue4ss_output(self) -> None:
        stop_event = self._ue4ss_stop_event
        watchdog = self._ue4ss_output_watchdog
        thread = self._ue4ss_output_thread
        if stop_event is not None:
            stop_event.set()
        if watchdog is not None:
            watchdog.stop(timeout=1)
        elif thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1)
        self._ue4ss_stop_event = None
        self._ue4ss_output_watchdog = None
        self._ue4ss_output_thread = None

    def _start_ue4ss_output(
        self,
        *,
        replay_existing: bool,
        active: Callable[[], bool],
        cursor: tuple[Path, int, tuple[int, int] | None, bytes] | None = None,
    ) -> None:
        self._stop_ue4ss_output()
        cursor = cursor if cursor is not None else self._ue4ss_log_cursor()
        if cursor is None:
            return
        path, offset, file_id, prefix = cursor
        if replay_existing:
            offset = 0
        stop_event = threading.Event()
        cursor_state = {
            "offset": offset,
            "file_id": file_id,
            "prefix": prefix,
        }

        def persist_cursor(
            new_offset: int,
            new_file_id: tuple[int, int] | None,
            new_prefix: bytes,
        ) -> None:
            cursor_state.update(
                offset=new_offset,
                file_id=new_file_id,
                prefix=new_prefix,
            )

        def stream_output() -> None:
            self._stream_ue4ss_output(
                path,
                stop_event,
                initial_offset=cursor_state["offset"],
                initial_file_id=cursor_state["file_id"],
                initial_prefix=cursor_state["prefix"],
                active=active,
                cursor_callback=persist_cursor,
            )

        watchdog = ThreadWatchdog(
            stream_output,
            name=f"{self.profile.name} UE4SS log tailer",
            should_run=active,
            stop_event=stop_event,
            restart_delay=SERVER_OUTPUT_RESTART_DELAY_SECONDS,
            logger=lambda message: self.log(f"UE4SS: {message}"),
        )
        self._ue4ss_stop_event = stop_event
        self._ue4ss_output_watchdog = watchdog
        watchdog.start()
        self._ue4ss_output_thread = watchdog.thread

    @staticmethod
    def _server_output_lines(raw_line: str) -> list[str]:
        def unpack(match: re.Match[str]) -> str:
            try:
                packed = b"".join(
                    ord(character).to_bytes(2, "little") for character in match.group()
                )
                decoded = packed.decode("utf-8")
            except (OverflowError, UnicodeDecodeError):
                return match.group()
            if not decoded or not all(
                character.isprintable() or character in "\r\n\t" for character in decoded
            ):
                return match.group()
            return decoded

        repaired = PACKED_OUTPUT_RE.sub(unpack, ANSI_ESCAPE_RE.sub("", raw_line))
        return [line for line in repaired.splitlines() if line]

    def run_update(self) -> None:
        self.state_callback("updating")
        self.log("Check updates")
        self._resolved_workdir().mkdir(parents=True, exist_ok=True)
        fake_log = os.getenv("PALSITTER_FAKE_STEAMCMD_CALLS")
        if fake_log:
            with open(fake_log, "a", encoding="utf-8") as handle:
                handle.write(" ".join(self.steam_update_args()[1:]) + "\n")
            self.log("Update completed")
            return
        returncode, last_line, output = self._run_steamcmd_update()
        if returncode != 0 and STEAMCMD_SELF_UPDATE_MARKER in output:
            self.log("SteamCMD updated itself; retrying server update")
            returncode, last_line, _ = self._run_steamcmd_update()
        if returncode != 0:
            detail = f": {last_line}" if last_line else ""
            raise RuntimeError(f"SteamCMD update failed ({returncode}){detail}")
        self.log("Update completed")

    def _adopt_existing_process(self) -> None:
        runtime = load_runtime(self.profile.name)
        if runtime is None or runtime.get("ownership") != "managed":
            raise RuntimeError("Managed server runtime metadata is missing")
        try:
            pid = int(runtime["pid"])
            expected_executable = os.path.normcase(
                os.path.abspath(str(runtime["executable"]))
            )
            process = psutil.Process(pid)
            expected_created = runtime.get("create_time")
            if expected_created is not None and abs(process.create_time() - float(expected_created)) > 0.01:
                raise RuntimeError("Managed server PID was reused")

            attached_executable = process.exe()
            actual_executable = os.path.normcase(os.path.abspath(attached_executable))
            if actual_executable != expected_executable:
                # Older Linux launches tracked PalServer.sh, while runtime
                # metadata stored the child PalServer executable. Adopt that
                # child and repair the metadata so future restarts are exact.
                matching_child = None
                for child in process.children(recursive=True):
                    try:
                        child_executable = os.path.normcase(
                            os.path.abspath(child.exe())
                        )
                    except (OSError, psutil.Error):
                        continue
                    if child_executable == expected_executable:
                        matching_child = child
                        attached_executable = child.exe()
                        break
                if matching_child is None:
                    raise RuntimeError(
                        "Managed server executable does not match runtime metadata"
                    )
                process = matching_child
                save_runtime(
                    self.profile.name,
                    {
                        **runtime,
                        "pid": process.pid,
                        "executable": str(Path(attached_executable).resolve()),
                        "create_time": process.create_time(),
                    },
                )
        except (KeyError, ValueError, OSError, psutil.Error) as exc:
            raise RuntimeError(f"Could not adopt managed server: {exc}") from exc

        self.process = AttachedServerProcess(process)
        self.ps_process = process
        self.external_attached = False
        self._server_output_path = Path(
            str(runtime.get("output_path") or profile_server_output_path(self.profile.name))
        )
        self._server_output_tail.clear()
        self._start_server_output(replay_existing=True)
        self._start_ue4ss_output(
            replay_existing=True,
            active=lambda: self.alive,
        )
        self.warning = False
        self.countdown = -1
        self.countdown_reason = None
        self.countdown_detail = {}
        self.countdown_triggered_at = None
        self.memory_threshold_samples = 0
        self._reset_auto_update_schedule()
        self.state_callback("running")

    def _agent_executables(self) -> set[str]:
        executable = Path(self.profile.executable)
        if executable.is_absolute() or executable.parent != Path("."):
            executable = executable.resolve()
        else:
            executable = (Path(self._workdir()) / executable).resolve()
        expected = {os.path.normcase(os.path.abspath(str(executable)))}
        console = windows_console_executable_path(executable, self._resolved_workdir())
        if console is not None and console.is_file():
            expected.add(os.path.normcase(os.path.abspath(str(console))))
        return expected

    def _attach_agent(self, client: AgentClient, status: dict[str, object], *, replay_existing: bool) -> bool:
        server_pid = status.get("server_pid")
        server_state = str(status.get("server_state") or "")
        if server_pid is None or server_state not in {"running", "starting"}:
            self.agent_client = client
            self.process = None
            self.ps_process = None
            self.state_callback("inactive")
            return False
        try:
            actual_executable = os.path.normcase(
                os.path.abspath(str(status.get("server_executable") or ""))
            )
            if actual_executable not in self._agent_executables():
                raise RuntimeError("Agent server executable does not match the configured instance")
            process = psutil.Process(int(server_pid))
            expected_created = status.get("server_create_time")
            if expected_created is not None and abs(process.create_time() - float(expected_created)) > 0.01:
                raise RuntimeError("Agent server PID was reused")
        except (OSError, psutil.Error, TypeError, ValueError) as exc:
            raise RuntimeError(f"Could not validate PalServer agent identity: {exc}") from exc

        runtime = load_runtime(self.profile.name) or {}
        session_id = status.get("session_id")
        previous_session_id = runtime.get("session_id")
        if replay_existing and previous_session_id and previous_session_id != session_id:
            raise RuntimeError("PalServer agent session does not match runtime metadata")
        self.agent_client = client
        self.process = AgentServerProcess(client, status)
        self.ps_process = process
        self.external_attached = False
        self._server_output_path = Path(
            str(status.get("output_path") or runtime.get("output_path") or profile_server_output_path(self.profile.name))
        )
        self._server_output_tail.clear()
        save_runtime(
            self.profile.name,
            {
                **runtime,
                "ownership": "managed",
                "agent_pid": status.get("agent_pid"),
                "server_pid": int(server_pid),
                "pid": int(server_pid),
                "executable": str(status.get("server_executable") or ""),
                "create_time": status.get("server_create_time"),
                "session_id": session_id,
                "output_path": str(self._server_output_path),
                "output_offset": (
                    runtime.get("output_offset", 0)
                    if replay_existing
                    else int(status.get("output_offset", 0) or 0)
                ),
                "output_prefix": runtime.get(
                    "output_prefix",
                    base64.b64encode(self._file_cursor(self._server_output_path)[2]).decode("ascii"),
                ),
            },
        )
        self._start_server_output(replay_existing=replay_existing)
        self._start_ue4ss_output(replay_existing=replay_existing, active=lambda: self.alive)
        self.warning = False
        self.countdown = -1
        self.countdown_reason = None
        self.countdown_detail = {}
        self.countdown_triggered_at = None
        self.memory_threshold_samples = 0
        self._reset_auto_update_schedule()
        self.state_callback("running")
        return True

    def _adopt_agent_server(self) -> bool:
        assert self.agent_client_factory is not None
        client = self.agent_client_factory.connect_existing(self.profile.name)
        status = client.status()
        self.log("Existing PalServer agent detected; restoring by status only")
        if str(status.get("server_state") or "") == "crashed":
            self.agent_client = client
            self.process = AgentServerProcess(client, status)
            self.ps_process = None
            self.external_attached = False
            self._server_output_path = Path(
                str(status.get("output_path") or profile_server_output_path(self.profile.name))
            )
            self._server_output_tail.clear()
            self._start_server_output(replay_existing=True)
            self._start_ue4ss_output(replay_existing=True, active=lambda: True)
            self.warning = False
            self.state_callback("running")
            return True
        return self._attach_agent(client, status, replay_existing=True)

    def _start_agent_server(self, update: bool) -> bool:
        assert self.agent_client_factory is not None
        self._prepare_server_settings()
        if update:
            self.run_update()
        if self.stop_requested():
            self.state_callback("inactive")
            return False
        self.state_callback("booting")
        self.log("Starting PalServer agent")
        client = self.agent_client_factory.launch_idle(self.profile.name)
        status = client.start()
        return self._attach_agent(client, status, replay_existing=False)

    def start(self, update: bool = False, *, manual: bool = True) -> None:
        if self.alive:
            self.log("Server is already running")
            return
        if manual:
            self.crash_times.clear()
            self.self_heal_crash_times.clear()
            self.next_planned_restart = None
        if self.agent_client_factory is not None and WINDOWS:
            if self.adopt_managed and not self._crash_recovery:
                self._adopt_agent_server()
                return
            if self.running_probe(self.profile):
                self.external_attached = True
                self.warning = False
                self.log("Existing server detected; attached watcher without updating or launching")
                self._start_ue4ss_output(replay_existing=True, active=lambda: self.external_attached)
                self.state_callback("running")
                return
            self._start_agent_server(update)
            return
        if self.adopt_managed:
            if not self.running_probe(self.profile):
                raise RuntimeError("Managed server is no longer running; adoption aborted")
            self.log("Existing managed server detected; adopting without launching")
            self._adopt_existing_process()
            return
        if self.running_probe(self.profile):
            self.external_attached = True
            self.warning = False
            self.log("Existing server detected; attached watcher without updating or launching")
            self._start_ue4ss_output(
                replay_existing=True,
                active=lambda: self.external_attached,
            )
            self.state_callback("running")
            return
        self._stop_capture_output()
        self._prepare_server_settings()
        if update:
            self.run_update()
        if self.stop_requested():
            self.state_callback("inactive")
            return
        self.state_callback("booting")
        ue4ss_cursor = self._ue4ss_log_cursor()
        exe = Path(self.profile.executable)
        workdir = self._resolved_workdir()
        if workdir.name.casefold() == "palserver" and workdir.parent.name.casefold() == "common":
            exe = exe.resolve()
            if not exe.is_file():
                raise FileNotFoundError(f"PalServer executable not found after update: {exe}")
        launch_args = self.profile.build_executable_args()
        console_exe = windows_console_executable_path(exe, workdir) if WINDOWS else None
        if console_exe is not None and console_exe.is_file():
            exe = console_exe
            existing_args = {argument.casefold() for argument in launch_args}
            launch_args = [
                *(
                    argument
                    for argument in WINDOWS_STDOUT_ARGS
                    if argument.casefold() not in existing_args
                ),
                *launch_args,
            ]
        if not WINDOWS:
            launch_args = [
                *self._prepare_linux_server_runtime(exe, workdir),
                *launch_args,
            ]
        cmd = [
            str(exe),
            *launch_args,
            f"-port={self.profile.game_port}",
            f"-queryport={self.profile.query_port}",
        ]
        self.log(f"Starting server: {' '.join(cmd)}")
        self._server_output_tail.clear()
        self._server_output_path = profile_server_output_path(self.profile.name)
        self._server_output_path.parent.mkdir(parents=True, exist_ok=True)
        launch_kwargs = {
            "cwd": str(workdir),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
        }
        if WINDOWS:
            launch_kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        else:
            launch_kwargs["start_new_session"] = True
        self.process = self.popen_factory(cmd, **launch_kwargs)
        process = self.process
        self._start_ue4ss_output(
            replay_existing=False,
            active=lambda: process.poll() is None,
            cursor=ue4ss_cursor,
        )
        self.ps_process = psutil.Process(self.process.pid)
        try:
            create_time = float(self.ps_process.create_time())
        except (AttributeError, OSError, psutil.Error, TypeError, ValueError):
            create_time = None
        output_offset, file_id, output_prefix = self._file_cursor(self._server_output_path)
        save_runtime(
            self.profile.name,
            {
                "ownership": "managed",
                "pid": self.process.pid,
                "executable": str(Path(exe).resolve()),
                "create_time": create_time,
                "output_path": str(self._server_output_path),
                "output_offset": output_offset,
                "output_file_id": list(file_id) if file_id is not None else None,
                "output_prefix": base64.b64encode(output_prefix).decode("ascii"),
            },
        )
        self._start_server_output(replay_existing=False)
        if getattr(self.process, "stdout", None) is not None:
            process = self.process
            watchdog = ThreadWatchdog(
                lambda: self._capture_server_output(process),
                name=f"{self.profile.name} PalServer stdout capture",
                should_run=lambda: process.poll() is None,
                restart_delay=SERVER_OUTPUT_RESTART_DELAY_SECONDS,
                restart_on_return=False,
                logger=lambda message: self.log(f"PalServer: {message}"),
            )
            self._capture_output_watchdog = watchdog
            watchdog.start()
            self._capture_output_thread = watchdog.thread
        self.warning = False
        self.countdown = -1
        self.countdown_reason = None
        self.countdown_detail = {}
        self.countdown_triggered_at = None
        self.memory_threshold_samples = 0
        self._reset_auto_update_schedule()
        self.state_callback("running")

    def _prepare_linux_server_runtime(self, executable: Path, workdir: Path) -> list[str]:
        if executable.resolve() != fixed_executable_path(self.profile.name).resolve():
            return []
        launcher = fixed_server_launcher_path(self.profile.name).resolve()
        if not launcher.is_file():
            return []

        source = workdir / "linux64" / "steamclient.so"
        destination = executable.parent / "steamclient.so"
        if not destination.is_file():
            if not source.is_file():
                raise FileNotFoundError(
                    f"PalServer Steam client library not found: {source}"
                )
            shutil.copy2(source, destination)
        try:
            executable.chmod(executable.stat().st_mode | 0o111)
        except OSError:
            pass
        return ["Pal"]

    def _prepare_server_settings(self) -> None:
        sync_game_user_settings(self.profile)
        ensure_world_settings(self.profile)

    def stop(self, graceful: bool = True) -> None:
        self.state_callback("stopping")
        external_attached = self.external_attached
        if graceful:
            try:
                self.rest.shutdown()
            except Exception as exc:
                self.log(f"Graceful shutdown request failed: {exc}")
        if external_attached and graceful:
            wait_seconds = max(0, int(self.profile.shutdown_wait_seconds))
            for _ in range(wait_seconds):
                if not self.running_probe(self.profile):
                    break
                self.sleep(1)
            if wait_seconds == 0 or self.running_probe(self.profile):
                try:
                    self.rest.stop()
                except Exception as exc:
                    self.log(f"Force stop request failed: {exc}")
        agent_managed = self.agent_client is not None
        agent_status: dict[str, object] | None = None
        if self.process is not None and self.alive and graceful and not agent_managed:
            try:
                self.process.wait(timeout=max(3, self.profile.shutdown_wait_seconds + 5))
            except subprocess.TimeoutExpired:
                self.log("PalServer is still running; use KILL to force stop")
                return
        elif self.process is not None and self.alive and not agent_managed:
            self.process.terminate()
            self.process.wait(timeout=5)
        if agent_managed:
            try:
                agent_status = (
                    self.agent_client.stop if graceful else self.agent_client.kill
                )()
            except (OSError, RuntimeError, TimeoutError):
                pass
            if self.process is not None:
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.log("PalServer agent did not report shutdown")
        if self.process is not None and not self.alive:
            exit_code = (
                agent_status.get("exit_code")
                if agent_status is not None
                else self.process.poll()
            )
            exit_reason = (
                agent_status.get("exit_reason")
                if agent_status is not None
                else "stopped"
            )
            self._record_adapter_event(
                "server_exit",
                f"PalServer exited: {exit_reason} (exit code: {exit_code})",
            )
        if agent_status is not None:
            self._record_adapter_event(
                "agent_exit",
                f"PalServer agent exited: {'force stopped' if not graceful else 'stopped'}",
            )
        self.warning = False
        self.external_attached = False
        self._stop_server_output()
        self._stop_ue4ss_output()
        self._stop_capture_output()
        if self.process is not None:
            clear_runtime(self.profile.name, pid=self.process.pid)
        self.agent_client = None
        self._server_output_path = None
        self.state_callback("inactive")

    def restart(self, reason: str = "manual", *, update: bool = False) -> None:
        self.log(f"Restarting server ({reason})")
        self.stop(graceful=True)
        self.sleep(1)
        self.start(update=update, manual=False)

    def _crash_termination(self) -> TerminationInfo:
        process = self.process
        returncode = process.poll() if process is not None else 0
        output_thread = self._output_thread
        if output_thread is not None and output_thread.is_alive():
            output_thread.join(timeout=1)
        return classify_process_exit(
            int(returncode if returncode is not None else 0),
            output=tuple(self._server_output_tail),
        )

    def _handle_crash(self, *, now: dt.datetime | None = None) -> None:
        now = now or self.now()
        termination = self._crash_termination()
        cutoff = now - dt.timedelta(seconds=CRASH_RESTART_LIMIT_WINDOW_SECONDS)
        while self.crash_times and self.crash_times[0] < cutoff:
            self.crash_times.popleft()
        limit = int(self.profile.crash_restart_limit_per_hour)
        if len(self.crash_times) >= limit:
            self.warning = True
            clear_runtime(self.profile.name, pid=self.process.pid if self.process is not None else None)
            self.process = None
            self.ps_process = None
            self.log(f"Crash restart limit reached ({limit} per hour); manual Start is required")
            self.state_callback("warning")
            self._record_event(
                "crash",
                "restart_limit_reached",
                when=now,
                detail={"limit_per_hour": limit},
                termination=termination,
            )
            return
        self.crash_times.append(now)
        detail: dict[str, object] = {}
        self_heal_triggered = False
        if self.profile.self_heal_enabled:
            frame_minutes = int(self.profile.self_heal_trigger_frame_minutes)
            trigger_crashes = int(self.profile.self_heal_trigger_crash_times)
            frame_start = now - dt.timedelta(minutes=frame_minutes)
            while (
                self.self_heal_crash_times
                and self.self_heal_crash_times[0] < frame_start
            ):
                self.self_heal_crash_times.popleft()
            self.self_heal_crash_times.append(now)
            self_heal_triggered = len(self.self_heal_crash_times) >= trigger_crashes
        else:
            self.self_heal_crash_times.clear()
        if self_heal_triggered:
            self.log(
                f"Self-heal triggered after {trigger_crashes} crashes within "
                f"{frame_minutes} minutes; restoring the latest backup from before "
                "the trigger frame"
            )
            restored, rollback_detail = self._rollback(frame_start)
            detail["rollback"] = rollback_detail
            detail["self_heal_trigger"] = {
                "crashes": trigger_crashes,
                "frame_minutes": frame_minutes,
                "frame_start": frame_start.isoformat(),
            }
            outcome = "restarted_after_restore" if restored else "restarted_without_restore"
            self.self_heal_crash_times.clear()
        else:
            self.log("Process exited unexpectedly; restarting")
            outcome = "restarted"
        self.sleep(1)
        self._crash_recovery = True
        try:
            self.start(update=False, manual=False)
        except Exception as exc:
            if isinstance(exc, OSError):
                launch = classify_launch_error(exc, self.profile.executable)
                detail["restart_error"] = asdict(launch)
            else:
                detail["restart_error"] = {"type": type(exc).__name__, "message": str(exc)}
            self.warning = True
            self.state_callback("warning")
            self._record_event(
                "crash",
                "restart_failed",
                when=now,
                detail=detail,
                termination=termination,
            )
            raise
        finally:
            self._crash_recovery = False
        self._record_event(
            "crash",
            outcome,
            when=now,
            detail=detail,
            termination=termination,
        )

    def _rollback(self, cutoff: dt.datetime) -> tuple[bool, str]:
        if self.backup_service is None:
            self.log("No backup service is available; skipping rollback")
            return False, "backup_service_unavailable"
        try:
            safety_backup = self.backup_service.create_backup()
        except Exception as exc:
            self.log(f"Pre-rollback safety backup failed: {exc}")
            self.log("Rollback skipped because the safety backup did not complete")
            return False, f"safety_backup_failed: {exc}"
        if getattr(safety_backup, "skipped", False) or (
            hasattr(safety_backup, "path") and safety_backup.path is None
        ):
            self.log("Rollback skipped because the safety backup did not contain save files")
            return False, "safety_backup_empty"
        try:
            target = self.backup_service.backup_before(cutoff)
        except Exception as exc:
            self.log(f"Rollback failed: {exc}")
            return False, f"backup_lookup_failed: {exc}"
        if target is None:
            self.log("No backup found before the self-heal trigger frame; skipping rollback")
            return False, "no_eligible_backup"
        try:
            self.backup_service.restore(target)
            self.log(f"Restored backup: {target}")
            return True, str(target)
        except Exception as exc:
            self.log(f"Rollback failed: {exc}")
            return False, f"restore_failed: {exc}"

    def _process_tree_rss_mb(self) -> float:
        root = self.ps_process
        if root is None and self.process is not None:
            root = psutil.Process(self.process.pid)
            self.ps_process = root
        if root is None:
            return 0.0
        processes = [root]
        try:
            processes.extend(root.children(recursive=True))
        except (psutil.Error, AttributeError):
            pass
        rss = 0
        for process in processes:
            try:
                rss += int(process.memory_info().rss)
            except (psutil.Error, AttributeError):
                continue
        return rss / MEBIBYTE

    def _memory_restart_threshold_mb(self) -> int:
        configured = int(self.profile.memory_restart_mb or 0)
        if configured > 0:
            return configured
        legacy_percent = float(self.profile.memory_restart_percent or 0)
        if legacy_percent <= 0:
            return 0
        memory = self.virtual_memory()
        total = getattr(memory, "total", None)
        if total is None and hasattr(memory, "_asdict"):
            total = memory._asdict().get("total")
        if not total:
            return 0
        return legacy_memory_restart_mb(legacy_percent, int(total))

    def _auto_update_enabled(self) -> bool:
        return bool(self.profile.update_on_start and self.profile.auto_update)

    def _reset_auto_update_schedule(self) -> None:
        self.auto_update_info = None
        self.auto_update_idle_since = None
        self.next_auto_update_check_at = None

    def _clear_auto_update_pending(self) -> None:
        self.auto_update_info = None
        self.auto_update_idle_since = None

    def _record_update_check(self, info: UpdateInfo) -> None:
        self.event_callback(
            AdapterEvent(
                info.checked_at or dt.datetime.now(dt.timezone.utc),
                "update_check",
                f"Palworld update check: {info.status}",
                {
                    "installed_build_id": info.installed_build_id,
                    "available_build_id": info.available_build_id,
                    "status": info.status,
                },
            )
        )

    def _check_auto_update(self) -> None:
        if not self._auto_update_enabled():
            self._clear_auto_update_pending()
            self.next_auto_update_check_at = None
            return
        current = self.monotonic()
        if self.next_auto_update_check_at is None:
            self.next_auto_update_check_at = current + AUTO_UPDATE_CHECK_INTERVAL_SECONDS
            return
        if current < self.next_auto_update_check_at:
            return
        self.next_auto_update_check_at = current + AUTO_UPDATE_CHECK_INTERVAL_SECONDS
        self.log("Checking for Palworld updates")
        info = PalworldUpdateService(
            self.profile,
            logger=self.log,
            pty_process_factory=self.pty_process_factory,
        ).check_update(force=True)
        if info.status == "update_available":
            self.auto_update_info = info
            self.log(
                "Palworld update available: "
                f"{info.installed_build_id} -> {info.available_build_id}"
            )
            self._record_update_check(info)
            return
        self._clear_auto_update_pending()
        if info.status == "up_to_date":
            self.log(f"Palworld is up to date at build {info.installed_build_id}")
        elif info.status == "unknown":
            self.log("Palworld update status is unknown; retrying at the next check")
        self._record_update_check(info)

    def _online_player_count(self) -> int | None:
        try:
            metrics = self.rest.metrics()
            value = metrics.get("currentplayernum")
            if value is None:
                raise ValueError("currentplayernum is missing")
            players = int(value)
            if players < 0:
                raise ValueError("currentplayernum is negative")
            return players
        except Exception as exc:
            self.log(f"Could not read online player count for update: {exc}")
            return None

    def _check_auto_update_idle(self) -> None:
        info = self.auto_update_info
        if info is None or self.countdown >= 0:
            return
        now = self.now()
        players = self._online_player_count()
        if players is None or players != 0:
            self.auto_update_idle_since = None
            return
        if self.auto_update_idle_since is None:
            self.auto_update_idle_since = now
            return
        idle_minutes = float((now - self.auto_update_idle_since).total_seconds()) / 60
        if idle_minutes < int(self.profile.auto_update_idle_minutes):
            return
        detail = {
            "installed_build_id": info.installed_build_id,
            "available_build_id": info.available_build_id,
            "idle_minutes": int(self.profile.auto_update_idle_minutes),
        }
        self.countdown = int(self.profile.planned_restart_countdown_minutes)
        self.countdown_reason = "auto_update"
        self.countdown_detail = detail
        self.countdown_triggered_at = now
        self.log(
            "Palworld update restart countdown started "
            f"after {self.profile.auto_update_idle_minutes} idle minutes"
        )

    def _ensure_next_planned_restart(self, now: dt.datetime) -> None:
        if self.next_planned_restart is not None:
            return
        mode = self.profile.planned_restart_mode
        if mode == "interval":
            self.next_planned_restart = now + dt.timedelta(
                hours=float(self.profile.planned_restart_interval_hours)
            )
        elif mode == "daily":
            hour, minute = (int(part) for part in self.profile.planned_restart_daily_time.split(":"))
            scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if scheduled <= now:
                scheduled += dt.timedelta(days=1)
            self.next_planned_restart = scheduled
        if self.next_planned_restart is not None:
            self._record_event("planned_restart", "scheduled", when=now)

    def _advance_planned_restart(self, now: dt.datetime) -> None:
        mode = self.profile.planned_restart_mode
        if mode == "interval":
            interval = dt.timedelta(hours=float(self.profile.planned_restart_interval_hours))
            scheduled = self.next_planned_restart or now
            while scheduled <= now:
                scheduled += interval
            self.next_planned_restart = scheduled
        elif mode == "daily":
            self.next_planned_restart = None
            tomorrow = now + dt.timedelta(days=1)
            hour, minute = (int(part) for part in self.profile.planned_restart_daily_time.split(":"))
            self.next_planned_restart = tomorrow.replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
        else:
            self.next_planned_restart = None

    def _check_planned_restart(self, *, external: bool = False) -> None:
        if self.profile.planned_restart_mode == "off":
            self.next_planned_restart = None
            return
        now = self.now()
        self._ensure_next_planned_restart(now)
        if self.next_planned_restart is None or now < self.next_planned_restart:
            return
        due_at = self.next_planned_restart
        self._advance_planned_restart(now)
        detail = {
            "mode": self.profile.planned_restart_mode,
            "due_at": due_at.isoformat(),
        }
        if external:
            self.log("Planned restart skipped because the server is externally managed")
            self._record_event(
                "planned_restart",
                "external_skipped",
                when=now,
                detail=detail,
            )
            return
        if self.countdown < 0:
            self.countdown = int(self.profile.planned_restart_countdown_minutes)
            self.countdown_reason = "planned_restart"
            self.countdown_detail = detail
            self.countdown_triggered_at = now
            self.log(f"Planned restart countdown started ({self.countdown} minutes)")

    def _check_memory_restart(self) -> None:
        threshold_mb = self._memory_restart_threshold_mb()
        if threshold_mb <= 0:
            self.memory_threshold_samples = 0
            return
        rss_mb = self._process_tree_rss_mb()
        if rss_mb > threshold_mb:
            self.memory_threshold_samples += 1
        else:
            self.memory_threshold_samples = 0
        if (
            self.countdown < 0
            and self.memory_threshold_samples >= MEMORY_RESTART_SUSTAINED_SAMPLES
        ):
            self.countdown = int(self.profile.memory_restart_countdown_minutes)
            self.countdown_reason = "memory_threshold"
            self.countdown_detail = {
                "observed_rss_mb": round(rss_mb, 1),
                "threshold_mb": threshold_mb,
                "sustained_samples": MEMORY_RESTART_SUSTAINED_SAMPLES,
            }
            self.countdown_triggered_at = self.now()
            self.memory_threshold_samples = 0
            self.log(
                f"PalServer process-tree memory threshold exceeded: "
                f"{rss_mb:.1f} MiB > {threshold_mb} MiB"
            )

    def _process_restart_countdown(self) -> None:
        if self.countdown < 0:
            return
        reason = self.countdown_reason or "automatic_restart"
        if self.countdown == 0:
            self.countdown = -1
            self.countdown_reason = None
            detail = self.countdown_detail
            triggered_at = self.countdown_triggered_at
            self.countdown_detail = {}
            self.countdown_triggered_at = None
            save_outcome = "world saved"
            try:
                self.rest.save()
            except Exception as exc:
                save_outcome = f"save failed: {exc}"
                self.log(f"Automatic restart save failed: {exc}")
            try:
                if reason == "auto_update":
                    self.restart(reason="automatic update", update=True)
                else:
                    self.restart(reason=reason.replace("_", " "))
            except Exception as exc:
                self.warning = True
                self.state_callback("warning")
                detail["save_outcome"] = save_outcome
                if isinstance(exc, OSError):
                    detail["restart_error"] = asdict(
                        classify_launch_error(exc, self.profile.executable)
                    )
                else:
                    detail["restart_error"] = {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                self._record_event(
                    reason,
                    "restart_failed",
                    when=triggered_at,
                    detail=detail,
                )
                raise
            detail["save_outcome"] = save_outcome
            self._record_event(
                reason,
                "restarted",
                when=triggered_at,
                detail=detail,
            )
            return
        minute = self.countdown
        message = (
            f"Server will restart in {minute} minutes due to excessive PalServer memory use"
            if reason == "memory_threshold"
            else f"Server will restart in {minute} minutes to apply a Palworld update"
            if reason == "auto_update"
            else f"Planned server restart in {minute} minutes"
        )
        try:
            self.rest.announce(message)
        except Exception as exc:
            self.log(f"{reason.title()} announce failed: {exc}")
        self.countdown -= 1

    def monitor_once(self) -> None:
        if self.external_attached:
            if self.running_probe(self.profile):
                self._check_planned_restart(external=True)
                return
            self.external_attached = False
            self._stop_ue4ss_output()
            self._record_adapter_event(
                "server_exit",
                "PalServer exited: external server is no longer reachable",
            )
            if not self.stop_requested():
                self.warning = True
                self.log("Attached server is no longer reachable")
                self.state_callback("warning")
                self._record_event("external server", "no longer reachable")
            return
        if not self.alive:
            if self.process is None:
                return
            if self.stop_requested():
                self.log("Process stopped intentionally")
                self.warning = False
                return
            termination = self._crash_termination()
            exit_time = self.now()
            if self.agent_client is not None:
                try:
                    self.agent_client.status()
                except (OSError, RuntimeError, TimeoutError):
                    self._record_adapter_event(
                        "agent_exit",
                        "PalServer agent exited unexpectedly",
                        when=exit_time,
                    )
            self._record_adapter_event(
                "server_exit",
                f"PalServer exited: {termination.summary_code} "
                f"(exit code: {termination.raw_exit_code})",
                when=exit_time,
            )
            if not self.profile.restart_on_crash:
                self.log("Process not running")
                self.warning = True
                self.state_callback("warning")
                clear_runtime(self.profile.name, pid=self.process.pid if self.process is not None else None)
                self.process = None
                self.ps_process = None
                self._record_event(
                    "crash",
                    "restart_disabled",
                    termination=termination,
                )
                return
            self._handle_crash(now=exit_time)
            return

        try:
            status = self.ps_process.status() if self.ps_process else "unknown"
            if status != self.last_status:
                self.log(f"Server status changed, now is {status}")
                self.last_status = status
        except Exception as exc:
            self.log(f"Could not read process status: {exc}")
            self.warning = True

        self._check_memory_restart()
        self._check_planned_restart()
        self._check_auto_update()
        self._check_auto_update_idle()
        self._process_restart_countdown()

    def supervise_loop(self, interval_seconds: int = 60) -> None:
        self.start(update=False)
        if self.adopt_managed and self.process is None and not self.external_attached:
            self.state_callback("inactive")
            return
        periodic_seconds_remaining = 0
        while not self.stop_requested() and not self.handoff_requested():
            # Crash detection is frequent; the remaining checks keep their configured cadence.
            crash_pending = (
                not self.external_attached
                and self.process is not None
                and not self.alive
            )
            if crash_pending or periodic_seconds_remaining <= 0:
                self.monitor_once()
                periodic_seconds_remaining = max(0, int(interval_seconds))
            if self.stop_requested():
                break
            if self.handoff_requested():
                break
            self.sleep(CRASH_POLL_INTERVAL_SECONDS)
            periodic_seconds_remaining -= CRASH_POLL_INTERVAL_SECONDS
        if self.force_stop_requested():
            self.stop(graceful=False)
            self.state_callback("inactive")
            return
        if self.handoff_requested():
            self._stop_server_output()
            self._stop_ue4ss_output()
            self.state_callback("inactive")
            return
        if self.alive or self.external_attached:
            self.stop(graceful=True)
            while self.alive:
                self.sleep(1)
        self.state_callback("inactive")

    def dashboard(self) -> dict:
        data = {"state": self.state, "players": "-", "fps": "-", "uptime": "-", "memory": "-"}
        try:
            metrics = self.rest.metrics()
            data["players"] = f"{metrics.get('currentplayernum', '-')}/{metrics.get('maxplayernum', '-')}"
            data["fps"] = metrics.get("serverfps", "-")
            data["uptime"] = metrics.get("uptime", "-")
        except Exception as exc:
            data["metrics_error"] = str(exc)
        try:
            data["memory"] = f"{self._process_tree_rss_mb():.1f} MiB"
        except Exception:
            pass
        return data
