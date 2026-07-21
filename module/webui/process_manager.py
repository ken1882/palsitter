from __future__ import annotations

import datetime as dt
import multiprocessing as mp
import queue
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass
from dataclasses import replace
from typing import Dict, List, Optional

import psutil

from module.games import AdapterEvent, InstanceStatusSummary, OperationProgress, UpdateInfo, get_game
from module.instances import load_instance, profile_log_path


_PROCESS_CONTEXT = mp.get_context("spawn")
_PROCESS_FACTORY = _PROCESS_CONTEXT.Process


def _shutdown_in_progress() -> bool:
    # Keep the shutdown coordinator independent from ProcessManager's import
    # path while still preventing new lifecycle work during desktop exit.
    from module.webui.shutdown import is_shutting_down

    return is_shutting_down()


@dataclass(frozen=True)
class LifecycleEventRecord:
    timestamp: dt.datetime
    reason: str
    outcome: str
    next_scheduled_restart: dt.datetime | None = None


def _run_profile(
    config_name: str,
    log_queue: mp.Queue,
    stop_requested: mp.synchronize.Event,
    state_queue: mp.Queue | None = None,
    event_queue: mp.Queue | None = None,
    handoff_requested: mp.synchronize.Event | None = None,
    force_stop_requested: mp.synchronize.Event | None = None,
    adopt_managed: bool = False,
) -> None:
    def log(message: str) -> None:
        log_queue.put(f"[{config_name}] {message}")

    def set_state(state: str) -> None:
        if state_queue is not None:
            state_queue.put(state)

    def emit_event(event) -> None:
        if event_queue is not None:
            event_queue.put(event)

    handoff_requested = handoff_requested or _PROCESS_CONTEXT.Event()
    force_stop_requested = force_stop_requested or _PROCESS_CONTEXT.Event()

    try:
        record = load_instance(config_name)
        get_game(record.game).supervise(
            record,
            log,
            stop_requested.is_set,
            set_state,
            event_callback=emit_event,
            handoff_requested=handoff_requested.is_set,
            force_stop_requested=force_stop_requested.is_set,
            adopt_managed=adopt_managed,
        )
    except Exception:
        set_state("warning")
        log("Supervisor crashed:")
        for line in traceback.format_exc().rstrip().splitlines():
            log(line)
        raise


def _kill_process_tree(pid: int, grace: float = 3.0) -> None:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    try:
        children = parent.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        children = []
    processes = [*children, parent]
    for proc in processes:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, alive = psutil.wait_procs(processes, timeout=grace)
    for proc in alive:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


class ProcessManager:
    _managers: Dict[str, "ProcessManager"] = {}
    _managers_lock = threading.Lock()
    _LOG_REPLAY_BYTES = 128 * 1024

    def __init__(self, config_name: str) -> None:
        self.config_name = config_name
        self._queue: mp.Queue = _PROCESS_CONTEXT.Queue()
        self._state_queue: mp.Queue = _PROCESS_CONTEXT.Queue()
        self._event_queue: mp.Queue = _PROCESS_CONTEXT.Queue()
        self._process: Optional[mp.Process] = None
        self._stop_requested: mp.synchronize.Event = _PROCESS_CONTEXT.Event()
        self._handoff_requested: mp.synchronize.Event = _PROCESS_CONTEXT.Event()
        self._force_stop_requested: mp.synchronize.Event = _PROCESS_CONTEXT.Event()
        self._lock = threading.RLock()
        self.max_logs = 300
        self.logs = self._load_persisted_logs()
        self._reader: Optional[threading.Thread] = None
        self._reader_process: Optional[mp.Process] = None
        self.warning = False
        self._stop_reason: Optional[str] = None
        self._state = "inactive"
        self._operation_thread: Optional[threading.Thread] = None
        self._bootstrap_thread: Optional[threading.Thread] = None
        self._restart_thread: Optional[threading.Thread] = None
        self._operation_progress: OperationProgress | None = None
        self._update_info = UpdateInfo()
        self._ownership = "none"
        self._check_display_running = False
        self._display_probe_at = 0.0
        self._display_probe_running = False
        self._intentional_exit = False
        self._last_operation: tuple[str, bool] | None = None
        self.backup_schedule_started_at: float | None = None
        self._recent_events: deque[LifecycleEventRecord] = deque(maxlen=20)
        self._resource_process_cache: dict[int, object] = {}

    def _load_persisted_logs(self) -> List[str]:
        path = profile_log_path(self.config_name)
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                handle.seek(max(0, size - self._LOG_REPLAY_BYTES))
                data = handle.read()
        except OSError:
            return []
        if size > self._LOG_REPLAY_BYTES:
            _, separator, data = data.partition(b"\n")
            if not separator:
                return []
        return data.decode("utf-8", errors="replace").splitlines()[-self.max_logs :]

    @classmethod
    def get(cls, config_name: str) -> "ProcessManager":
        with cls._managers_lock:
            if config_name not in cls._managers:
                cls._managers[config_name] = cls(config_name)
            return cls._managers[config_name]

    @property
    def alive(self) -> bool:
        with self._lock:
            process = self._process
        return process is not None and process.is_alive()

    @property
    def state(self) -> str:
        self._drain_states()
        with self._lock:
            return self._state

    @property
    def display_state(self) -> str:
        state = self.state
        with self._lock:
            operation_active = (
                self._operation_thread is not None and self._operation_thread.is_alive()
            )
            check_display_running = self._check_display_running
        if state == "checking" and check_display_running:
            return "running"
        if state not in ("inactive", "warning"):
            return state
        with self._lock:
            process_active = self._process is not None and self._process.is_alive()
        if process_active or operation_active:
            if operation_active and check_display_running:
                return "running"
            return state
        now = time.monotonic()
        with self._lock:
            if now - self._display_probe_at < 1.0:
                return "running" if self._display_probe_running else state
        try:
            record = load_instance(self.config_name)
            adapter = get_game(record.game)
            running = adapter.capabilities.lifecycle and adapter.is_running(record)
        except (FileNotFoundError, OSError):
            running = False
        with self._lock:
            self._display_probe_at = now
            self._display_probe_running = bool(running)
        return "running" if running else state

    @property
    def active(self) -> bool:
        return self.alive or self.state not in ("inactive", "warning")

    @property
    def ownership(self) -> str:
        with self._lock:
            return self._ownership

    @property
    def operation_progress(self) -> OperationProgress | None:
        with self._lock:
            return self._operation_progress

    @property
    def update_info(self) -> UpdateInfo:
        with self._lock:
            return self._update_info

    @property
    def operation_busy(self) -> bool:
        with self._lock:
            thread = self._operation_thread
        return thread is not None and thread.is_alive()

    @property
    def recent_lifecycle_events(self) -> tuple[LifecycleEventRecord, ...]:
        self._drain_events()
        with self._lock:
            return tuple(self._recent_events)

    @property
    def next_scheduled_restart(self) -> dt.datetime | None:
        self._drain_events()
        with self._lock:
            for event in reversed(self._recent_events):
                if event.next_scheduled_restart is not None:
                    return event.next_scheduled_restart
        return None

    def _record_lifecycle_event(
        self,
        reason: str,
        outcome: str,
        *,
        timestamp: dt.datetime | None = None,
        next_scheduled_restart: dt.datetime | None = None,
    ) -> None:
        with self._lock:
            self._recent_events.append(
                LifecycleEventRecord(
                    timestamp or dt.datetime.now(),
                    reason,
                    outcome,
                    next_scheduled_restart,
                )
            )

    def _record_adapter_event(
        self,
        event_type: str,
        message: str,
        timestamp: dt.datetime | None = None,
    ) -> None:
        try:
            record = load_instance(self.config_name)
            get_game(record.game).record_audit_event(
                record,
                AdapterEvent(
                    timestamp or dt.datetime.now(dt.timezone.utc),
                    event_type,
                    message,
                ),
            )
        except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
            self.append_log(f"Could not persist audit event: {exc}")

    def _drain_events(self) -> None:
        while True:
            try:
                event = self._event_queue.get_nowait()
            except queue.Empty:
                return
            event_type = getattr(event, "type", None)
            if event_type is not None:
                if event_type == "update_check":
                    details = getattr(event, "details", {})
                    if isinstance(details, dict):
                        event_timestamp = getattr(event, "timestamp", None)
                        with self._lock:
                            current_timestamp = self._update_info.checked_at
                            if (
                                current_timestamp is None
                                or event_timestamp is None
                                or event_timestamp >= current_timestamp
                            ):
                                self._update_info = UpdateInfo(
                                    details.get("installed_build_id"),
                                    details.get("available_build_id"),
                                    event_timestamp,
                                    str(details.get("status", "unknown")),
                                )
                self._record_adapter_event(
                    str(event_type),
                    str(getattr(event, "message", "")),
                    getattr(event, "timestamp", None),
                )
                continue
            reason = str(getattr(event, "reason", "lifecycle"))
            outcome = str(getattr(event, "outcome", "unknown"))
            timestamp = getattr(event, "timestamp", None)
            self._record_lifecycle_event(
                reason,
                outcome,
                timestamp=timestamp,
                next_scheduled_restart=getattr(event, "next_scheduled_restart", None),
            )
            if reason == "crash":
                self._record_adapter_event(
                    "server_crash",
                    f"Server crashed: {outcome}",
                    timestamp,
                )

    @property
    def is_installed(self) -> bool:
        record = load_instance(self.config_name)
        return get_game(record.game).is_installed(record)

    def _set_state(self, state: str) -> None:
        with self._lock:
            self._state = state

    def _set_progress(self, progress: OperationProgress) -> None:
        with self._lock:
            self._operation_progress = progress
            if progress.kind == "check_update" and progress.phase not in ("complete", "cached", "failed"):
                self._state = "checking"
            elif progress.kind == "install" and progress.phase not in ("complete", "failed"):
                self._state = "installing"
            elif progress.kind in ("update", "validate") and progress.phase not in ("complete", "failed"):
                self._state = "updating"

    def _drain_states(self) -> None:
        while True:
            try:
                self._set_state(self._state_queue.get_nowait())
            except queue.Empty:
                return

    def _operation_rejected(self, message: str) -> bool:
        self.append_log(message)
        return False

    def start(
        self,
        *,
        update: bool | None = None,
        reason: str = "manual start",
        adopt_managed: bool = False,
        shutdown: bool = False,
    ) -> bool:
        if _shutdown_in_progress() and not shutdown:
            return self._operation_rejected("Palsitter is shutting down")
        record = load_instance(self.config_name)
        adapter = get_game(record.game)
        if not adapter.capabilities.lifecycle:
            with self._lock:
                self.warning = True
                self._state = "warning"
                self._operation_progress = OperationProgress(
                    "start", "failed", error=f"{adapter.display_name} support is not implemented"
                )
            self.append_log(f"{adapter.display_name} support is not implemented")
            return False
        installed = adapter.is_installed(record)
        should_update = (
            True
            if not installed
            else adapter.update_on_start(record) if update is None else bool(update)
        )
        self._drain_states()
        with self._lock:
            process_active = self._process is not None and self._process.is_alive()
            operation_active = (
                self._operation_thread is not None and self._operation_thread.is_alive()
            )
            if (
                process_active
                or operation_active
                or self._state not in ("inactive", "warning")
            ):
                thread = None
            else:
                self.warning = False
                self._stop_reason = None
                self._intentional_exit = False
                self._stop_requested.clear()
                self._handoff_requested.clear()
                self._force_stop_requested.clear()
                self._state = "booting" if installed and not should_update else "installing"
                self._operation_progress = OperationProgress(
                    "start" if installed else "install",
                    "preparing",
                    0.0,
                    "Preparing to start" if installed else "Preparing installation",
                )
                self._last_operation = ("start", False)
                thread = threading.Thread(
                    target=self._bootstrap,
                    args=(should_update, reason, adopt_managed),
                    daemon=True,
                )
                self._operation_thread = thread
                self._bootstrap_thread = thread
        if thread is None:
            return self._operation_rejected("Already running")
        thread.start()
        return True

    def _bootstrap(
        self,
        update: bool = True,
        reason: str = "manual start",
        adopt_managed: bool = False,
    ) -> None:
        record = load_instance(self.config_name)
        adapter = get_game(record.game)
        external_running = False
        try:
            external_running = adapter.is_running(record)
            if external_running:
                with self._lock:
                    self._ownership = "managed" if adopt_managed else "external"
                self._set_progress(
                    OperationProgress(
                        "start", "attaching", None, "Attaching to the reachable server"
                    )
                )
                self.append_log(
                    "Existing server detected; skipping installation and "
                    f"{'adopting managed process' if adopt_managed else 'attaching watcher'}"
                )
            else:
                with self._lock:
                    self._ownership = "managed"
                if update:
                    info = adapter.install_or_update(
                        record,
                        log=self.append_log,
                        progress=self._set_progress,
                    )
                    with self._lock:
                        self._update_info = info
                    self._record_adapter_event(
                        "server_update",
                        "Server updated before start",
                    )
                else:
                    self.append_log("Restarting installed server without SteamCMD update")
                if self._stop_requested.is_set():
                    with self._lock:
                        self._ownership = "none"
                    self._set_state("inactive")
                    return

            self._set_state("booting")
            self.backup_schedule_started_at = time.time()
            process = _PROCESS_FACTORY(
                target=_run_profile,
                args=(
                    self.config_name,
                    self._queue,
                    self._stop_requested,
                    self._state_queue,
                    self._event_queue,
                    self._handoff_requested,
                    self._force_stop_requested,
                    adopt_managed,
                ),
                daemon=True,
            )
            process.start()
            with self._lock:
                self._process = process
                self._operation_progress = OperationProgress(
                    "start", "complete", 100.0, "Supervisor started"
                )
            self.append_log("Started supervisor process")
            self._record_lifecycle_event(
                reason,
                "adopted" if external_running and adopt_managed else
                "attached" if external_running else "supervisor started",
            )
            self._record_adapter_event(
                "server_start",
                "Server attached" if external_running else "Server started",
            )
            self._ensure_reader()
        except Exception as exc:
            with self._lock:
                self.warning = True
                self._state = "warning"
                if not external_running:
                    self._ownership = "none"
                previous = self._operation_progress
                kind = previous.kind if previous is not None else "start"
                self._operation_progress = OperationProgress(
                    kind, "failed", None, "Start failed", str(exc)
                )
            self.append_log(f"Start failed: {exc}")
            self._record_lifecycle_event(reason, f"failed: {exc}")

    def _begin_adapter_operation(self, kind: str, *, validate: bool = False, force: bool = False) -> bool:
        if _shutdown_in_progress():
            return self._operation_rejected("Palsitter is shutting down")
        record = load_instance(self.config_name)
        adapter = get_game(record.game)
        if not adapter.capabilities.updates:
            return self._operation_rejected(f"{adapter.display_name} does not support updates")
        self._drain_states()
        with self._lock:
            process_active = self._process is not None and self._process.is_alive()
            operation_active = (
                self._operation_thread is not None and self._operation_thread.is_alive()
            )
            if (
                operation_active
                or (
                    kind != "check_update"
                    and (process_active or self._state not in ("inactive", "warning"))
                )
                or (
                    kind == "check_update"
                    and not process_active
                    and self._state not in ("inactive", "warning")
                )
            ):
                thread = None
            else:
                check_display_running = process_active
                if kind == "check_update" and not check_display_running:
                    try:
                        check_display_running = adapter.is_running(record)
                    except (OSError, RuntimeError):
                        check_display_running = False
                self._check_display_running = bool(check_display_running) if kind == "check_update" else False
                self.warning = False
                self._state = "checking" if kind == "check_update" else "updating"
                self._operation_progress = OperationProgress(kind, "queued", 0.0, "Operation queued")
                self._last_operation = (kind, validate if kind != "check_update" else force)
                thread = threading.Thread(
                    target=self._run_adapter_operation,
                    args=(kind, validate, force, kind == "check_update" and process_active),
                    daemon=True,
                )
                self._operation_thread = thread
        if thread is None:
            return self._operation_rejected(
                "Server must be inactive before running this operation"
                if kind != "check_update"
                else "Server is not ready to check for updates"
            )
        thread.start()
        return True

    def check_update(self, *, force: bool = True) -> bool:
        return self._begin_adapter_operation("check_update", force=force)

    def update(self, *, validate: bool = False) -> bool:
        return self._begin_adapter_operation("validate" if validate else "update", validate=validate)

    def validate(self) -> bool:
        return self.update(validate=True)

    def _run_adapter_operation(
        self,
        kind: str,
        validate: bool,
        force: bool,
        restore_running: bool = False,
    ) -> None:
        record = load_instance(self.config_name)
        adapter = get_game(record.game)
        try:
            external_running = kind != "check_update" and adapter.is_running(record)
            if external_running or (kind == "check_update" and self.ownership == "external"):
                with self._lock:
                    self._ownership = "external"
                raise RuntimeError("A reachable external server is using this instance; stop or detach it first")
            with self._lock:
                if self._ownership == "external":
                    self._ownership = "none"
            if kind == "check_update":
                self.append_log("Checking for Palworld updates")
                info = adapter.check_update(
                    record,
                    self._set_progress,
                    force=force,
                    log=self.append_log,
                )
                if info.status == "update_available":
                    self.append_log(
                        "Palworld update available: "
                        f"{info.installed_build_id} -> {info.available_build_id}"
                    )
                elif info.status == "up_to_date":
                    self.append_log(
                        f"Palworld is up to date at build {info.installed_build_id}"
                    )
                elif info.status == "not_installed":
                    self.append_log("Palworld update check skipped because the server is not installed")
                else:
                    self.append_log("Palworld update status is unknown; retry the check later")
            else:
                info = adapter.install_or_update(
                    record,
                    log=self.append_log,
                    progress=self._set_progress,
                    validate=validate,
                )
            with self._lock:
                self._update_info = info
                process_still_active = (
                    self._process is not None and self._process.is_alive()
                )
                self._state = (
                    "running"
                    if kind == "check_update" and (process_still_active or restore_running)
                    else "inactive"
                )
                progress = self._operation_progress
                if progress is None or progress.error is None:
                    self._operation_progress = OperationProgress(
                        kind, "complete", 100.0, "Operation completed"
                    )
            if kind != "check_update":
                self._record_adapter_event("server_update", "Server updated")
        except Exception as exc:
            with self._lock:
                process_still_active = (
                    self._process is not None and self._process.is_alive()
                )
                self._state = (
                    "running"
                    if kind == "check_update" and (process_still_active or restore_running)
                    else "inactive"
                )
                self._operation_progress = OperationProgress(
                    kind, "failed", None, "Operation failed", str(exc)
                )
            self.append_log(f"{kind.replace('_', ' ').title()} failed: {exc}")

    def retry_operation(self) -> bool:
        with self._lock:
            previous = self._last_operation
        if previous is None:
            return self._operation_rejected("No operation is available to retry")
        kind, option = previous
        if kind == "start":
            return self.start()
        if kind == "check_update":
            return self.check_update(force=option)
        return self.update(validate=option)

    def stop(self, *, shutdown: bool = False) -> bool:
        if _shutdown_in_progress() and not shutdown:
            return self._operation_rejected("Palsitter is shutting down")
        if not self.active:
            return self._operation_rejected("Not running")
        with self._lock:
            self._stop_reason = "manual stop"
            self._state = "stopping"
        self.append_log("Stop requested: manual stop")
        self._record_lifecycle_event("manual stop", "requested")
        self._record_adapter_event("server_stop", "Server stop requested")
        self._stop_requested.set()
        return True

    def kill(self) -> bool:
        if _shutdown_in_progress():
            return self._operation_rejected("Palsitter is shutting down")
        if self.ownership == "external":
            return self._operation_rejected("Cannot KILL an externally managed server")
        if not self.active and not self.alive:
            return self._operation_rejected("Not running")
        with self._lock:
            self._stop_reason = "manual kill"
            self._intentional_exit = True
            self._state = "killing"
        self.append_log("Stop requested: manual kill")
        self._stop_requested.set()
        self._force_stop_requested.set()
        try:
            record = load_instance(self.config_name)
            get_game(record.game).force_stop(record)
        except (FileNotFoundError, OSError, RuntimeError) as exc:
            self.append_log(f"Could not force-stop server process: {exc}")
        with self._lock:
            process = self._process
        if process is not None and process.is_alive():
            _kill_process_tree(process.pid, grace=0)
            process.join(timeout=2)
        self.append_log("Killed supervisor process")
        self._record_lifecycle_event("manual kill", "stopped")
        self._record_adapter_event("server_stop", "Server force-stopped")
        with self._lock:
            self.warning = False
            self._ownership = "none"
            self._state = "inactive"
            self.backup_schedule_started_at = None
        return True

    def handoff(self) -> bool:
        """Detach a managed supervisor while leaving its server process alive."""
        if self.ownership != "managed":
            return self._operation_rejected("No managed server is available for handoff")
        with self._lock:
            process = self._process
            self._intentional_exit = True
            self._stop_reason = "GUI handoff"
            self._state = "detaching"
        self.append_log("Handoff requested; leaving managed server running")
        self._handoff_requested.set()
        if process is not None and process.is_alive():
            process.join(timeout=5)
        if process is not None and process.is_alive():
            terminate = getattr(process, "terminate", None)
            if terminate is not None:
                terminate()
            process.join(timeout=2)
        if process is not None and process.is_alive():
            self.append_log("Supervisor did not exit during handoff")
            with self._lock:
                self._state = "warning"
            return False
        try:
            record = load_instance(self.config_name)
            if not get_game(record.game).is_running(record):
                self.append_log("Managed server exited during handoff")
                with self._lock:
                    self._state = "warning"
                return False
        except (FileNotFoundError, OSError, RuntimeError) as exc:
            self.append_log(f"Could not verify managed server handoff: {exc}")
            with self._lock:
                self._state = "warning"
            return False
        with self._lock:
            self._state = "inactive"
            self._ownership = "none"
            self.backup_schedule_started_at = None
        return True

    def detach(self) -> bool:
        if self.ownership != "external":
            return self._operation_rejected("No externally managed server is attached")
        with self._lock:
            process = self._process
            self._intentional_exit = True
            self._stop_reason = "manual detach"
        if process is not None and process.is_alive():
            terminate = getattr(process, "terminate", None)
            if terminate is not None:
                terminate()
            process.join(timeout=2)
        with self._lock:
            self.warning = False
            self._ownership = "none"
            self._state = "inactive"
            self.backup_schedule_started_at = None
        self.append_log("Detached watcher; external server was left running")
        self._record_lifecycle_event("manual detach", "external server left running")
        return True

    def restart(self) -> bool:
        if _shutdown_in_progress():
            return self._operation_rejected("Palsitter is shutting down")
        if self.ownership == "external":
            return self._operation_rejected("Cannot restart an externally managed server")
        if not self.active:
            return self.start()
        with self._lock:
            if self._restart_thread is not None and self._restart_thread.is_alive():
                return self._operation_rejected("Restart is already in progress")
        self._record_lifecycle_event("manual restart", "requested")
        if not self.stop():
            return False

        def finish_restart() -> None:
            while self.alive or self.state not in ("inactive", "warning"):
                time.sleep(0.1)
            self.start(update=False)

        thread = threading.Thread(target=finish_restart, daemon=True)
        with self._lock:
            self._restart_thread = thread
        thread.start()
        return True

    def resource_usage(self) -> Optional[dict]:
        record = load_instance(self.config_name)
        adapter = get_game(record.game)
        target = adapter.process_name(record)
        if target is None:
            return None

        children = []
        if self.alive:
            with self._lock:
                process = self._process
            assert process is not None
            try:
                children = psutil.Process(process.pid).children(recursive=True)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        def safe_name(proc: psutil.Process) -> str:
            try:
                return proc.name().lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return ""

        roots = [child for child in children if safe_name(child) == target]
        if not roots and len(children) == 1:
            roots = children
        if not roots:
            roots = list(adapter.resource_processes(record))
        if not roots:
            with self._lock:
                self._resource_process_cache = {}
            return None

        tree: list[psutil.Process] = []
        seen: set[int] = set()
        for root in roots:
            descendants = []
            child_getter = getattr(root, "children", None)
            if child_getter is not None:
                try:
                    descendants = child_getter(recursive=True)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            for item in [root, *descendants]:
                identity = getattr(item, "pid", id(item))
                if identity not in seen:
                    seen.add(identity)
                    tree.append(item)

        cpu_percent = 0.0
        memory_percent = 0.0
        memory_bytes = 0
        has_memory_bytes = False
        sampled: dict[int, object] = {}
        with self._lock:
            cached_processes = dict(self._resource_process_cache)
        try:
            for item in tree:
                identity = getattr(item, "pid", id(item))
                process_item = cached_processes.get(identity, item)
                sampled[identity] = process_item
                cpu_percent += float(process_item.cpu_percent())
                memory_percent += float(process_item.memory_percent())
                memory_info = getattr(process_item, "memory_info", None)
                if memory_info is not None:
                    memory_bytes += int(memory_info().rss)
                    has_memory_bytes = True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
        with self._lock:
            self._resource_process_cache = sampled
        result = {
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
        }
        if has_memory_bytes:
            result["memory_bytes"] = memory_bytes
        return result

    def status_summary(self, *, rest_timeout: float | None = None) -> InstanceStatusSummary:
        self._drain_events()
        record = load_instance(self.config_name)
        adapter = get_game(record.game)
        summary = adapter.status_summary(
            record,
            self.resource_usage(),
            rest_timeout=rest_timeout,
        )
        update = self.update_info
        next_backup_at = None
        if self.backup_schedule_started_at is not None and adapter.capabilities.backups:
            profile = adapter.load_typed_profile(record.name, record.game_config)
            interval = max(0.0, float(profile.backup_interval_minutes) * 60)
            if interval:
                next_backup_at = dt.datetime.fromtimestamp(self.backup_schedule_started_at + interval)
        return replace(
            summary,
            state=self.display_state,
            installed_build_id=update.installed_build_id,
            available_build_id=update.available_build_id,
            next_backup_at=next_backup_at,
        )

    def append_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = f"{stamp} {message}"
        with self._lock:
            self.logs.append(line)
            if len(self.logs) > self.max_logs:
                self.logs = self.logs[-self.max_logs:]
        try:
            path = profile_log_path(self.config_name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")
        except OSError:
            pass

    def _ensure_reader(self) -> None:
        with self._lock:
            process = self._process
            if process is None:
                return
            if (
                self._reader is not None
                and self._reader.is_alive()
                and self._reader_process is process
            ):
                return
            self._reader_process = process
            self._reader = threading.Thread(
                target=self._read_logs, args=(process,), daemon=True
            )
            reader = self._reader
        reader.start()

    def _read_logs(self, watched_process: mp.Process | None = None) -> None:
        if watched_process is None:
            with self._lock:
                watched_process = self._process
        if watched_process is None:
            return
        while watched_process.is_alive():
            self._drain_states()
            self._drain_events()
            try:
                self.append_log(self._queue.get(timeout=1))
                while True:
                    try:
                        self.append_log(self._queue.get_nowait())
                    except queue.Empty:
                        break
            except queue.Empty:
                continue
        process = watched_process
        with self._lock:
            is_current_process = self._process is process
            intentional = self._intentional_exit
        if not is_current_process:
            return
        if process is not None and process.exitcode not in (None, 0):
            if self._stop_requested.is_set() or intentional:
                with self._lock:
                    self.warning = False
                    self._state = "inactive"
                    self._ownership = "none"
                    self.backup_schedule_started_at = None
                    reason = self._stop_reason or "manual stop"
                self.append_log(f"Supervisor exited after {reason} with code {process.exitcode}")
            else:
                with self._lock:
                    self.warning = True
                    self._state = "warning"
                    self._ownership = "none"
                    self.backup_schedule_started_at = None
                self.append_log(f"Supervisor exited with code {process.exitcode}")
        else:
            self._drain_states()
            with self._lock:
                if not self.warning:
                    self._state = "inactive"
                self._ownership = "none"
                self.backup_schedule_started_at = None
