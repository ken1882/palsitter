from __future__ import annotations

import datetime as dt
import json
import os
import signal
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from module.instances import profile_dir


HISTORY_VERSION = 1
MAX_HISTORY_EVENTS = 20
MAX_DIAGNOSTIC_LINES = 5
MAX_DIAGNOSTIC_LINE_LENGTH = 500


WINDOWS_NTSTATUS: dict[int, tuple[str, str]] = {
    0xC0000005: ("STATUS_ACCESS_VIOLATION", "access_violation"),
    0xC0000006: ("STATUS_IN_PAGE_ERROR", "in_page_error"),
    0xC0000017: ("STATUS_NO_MEMORY", "insufficient_memory"),
    0xC000001D: ("STATUS_ILLEGAL_INSTRUCTION", "illegal_instruction"),
    0xC0000022: ("STATUS_ACCESS_DENIED", "access_denied"),
    0xC000007B: ("STATUS_INVALID_IMAGE_FORMAT", "invalid_image"),
    0xC0000094: ("STATUS_INTEGER_DIVIDE_BY_ZERO", "integer_divide_by_zero"),
    0xC00000FD: ("STATUS_STACK_OVERFLOW", "stack_overflow"),
    0xC0000135: ("STATUS_DLL_NOT_FOUND", "dll_not_found"),
    0xC0000142: ("STATUS_DLL_INIT_FAILED", "dll_init_failed"),
    0xC0000374: ("STATUS_HEAP_CORRUPTION", "heap_corruption"),
    0xC0000409: ("STATUS_STACK_BUFFER_OVERRUN", "stack_buffer_overrun"),
}

POSIX_SIGNAL_SUMMARIES = {
    "SIGSEGV": "segmentation_fault",
    "SIGABRT": "abort",
    "SIGBUS": "bus_error",
    "SIGILL": "illegal_instruction",
    "SIGFPE": "arithmetic_exception",
    "SIGKILL": "forcibly_killed",
    "SIGTERM": "termination_signal",
    "SIGINT": "interrupt_signal",
}


@dataclass(frozen=True)
class TerminationInfo:
    kind: str
    raw_exit_code: int | None = None
    normalized_code: str | None = None
    symbol: str | None = None
    summary_code: str = "unknown"
    os_error: dict[str, Any] | None = None
    diagnostic_output: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TerminationInfo":
        return cls(
            kind=str(data.get("kind", "unknown")),
            raw_exit_code=_optional_int(data.get("raw_exit_code")),
            normalized_code=_optional_str(data.get("normalized_code")),
            symbol=_optional_str(data.get("symbol")),
            summary_code=str(data.get("summary_code", "unknown")),
            os_error=dict(data["os_error"]) if isinstance(data.get("os_error"), Mapping) else None,
            diagnostic_output=tuple(str(line) for line in data.get("diagnostic_output") or ()),
        )


@dataclass(frozen=True)
class LifecycleEvent:
    timestamp: dt.datetime
    reason: str
    outcome: str
    next_scheduled_restart: dt.datetime | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    termination: TerminationInfo | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "reason": self.reason,
            "outcome": self.outcome,
            "next_scheduled_restart": (
                self.next_scheduled_restart.isoformat()
                if self.next_scheduled_restart is not None
                else None
            ),
            "detail": dict(self.detail),
            "termination": asdict(self.termination) if self.termination is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LifecycleEvent":
        termination = data.get("termination")
        return cls(
            timestamp=dt.datetime.fromisoformat(str(data["timestamp"])),
            reason=str(data["reason"]),
            outcome=str(data["outcome"]),
            next_scheduled_restart=(
                dt.datetime.fromisoformat(str(data["next_scheduled_restart"]))
                if data.get("next_scheduled_restart")
                else None
            ),
            detail=dict(data.get("detail") or {}),
            termination=(
                TerminationInfo.from_dict(termination)
                if isinstance(termination, Mapping)
                else None
            ),
        )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def diagnostic_tail(lines) -> tuple[str, ...]:
    cleaned = [str(line).strip()[:MAX_DIAGNOSTIC_LINE_LENGTH] for line in lines if str(line).strip()]
    return tuple(cleaned[-MAX_DIAGNOSTIC_LINES:])


def classify_process_exit(
    returncode: int,
    *,
    platform: str | None = None,
    output: tuple[str, ...] | list[str] = (),
) -> TerminationInfo:
    platform = platform or os.name
    tail = diagnostic_tail(output)
    if platform == "nt":
        normalized = int(returncode) & 0xFFFFFFFF
        known = WINDOWS_NTSTATUS.get(normalized)
        if known is not None:
            symbol, summary = known
            return TerminationInfo(
                "windows_exception",
                int(returncode),
                f"0x{normalized:08X}",
                symbol,
                summary,
                diagnostic_output=tail,
            )
        return TerminationInfo(
            "exit_code",
            int(returncode),
            f"0x{normalized:08X}",
            summary_code="unrecognized_exit_code",
            diagnostic_output=tail,
        )
    if int(returncode) < 0:
        number = -int(returncode)
        try:
            symbol = signal.Signals(number).name
        except ValueError:
            symbol = f"SIGNAL_{number}"
        return TerminationInfo(
            "posix_signal",
            int(returncode),
            str(number),
            symbol,
            POSIX_SIGNAL_SUMMARIES.get(symbol, "unknown_signal"),
            diagnostic_output=tail,
        )
    return TerminationInfo(
        "exit_code",
        int(returncode),
        str(int(returncode)),
        summary_code="unrecognized_exit_code",
        diagnostic_output=tail,
    )


def classify_launch_error(exc: OSError, executable: str | Path) -> TerminationInfo:
    if isinstance(exc, PermissionError):
        summary = "permission_denied"
    elif isinstance(exc, FileNotFoundError):
        summary = "file_not_found"
    else:
        summary = "os_error"
    error = {
        "type": type(exc).__name__,
        "message": str(exc),
        "errno": getattr(exc, "errno", None),
        "winerror": getattr(exc, "winerror", None),
        "filename": str(getattr(exc, "filename", None) or executable),
    }
    return TerminationInfo("launch_error", summary_code=summary, os_error=error)


def restart_history_path(name: str) -> Path:
    return profile_dir(name) / "logs" / "restart-history.json"


class RestartHistoryStore:
    _lock = threading.RLock()

    def __init__(self, name: str) -> None:
        self.path = restart_history_path(name)

    def load(self) -> tuple[LifecycleEvent, ...]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ()
        if not isinstance(payload, Mapping) or payload.get("version") != HISTORY_VERSION:
            raise ValueError("Unsupported restart history format")
        events = payload.get("events")
        if not isinstance(events, list):
            raise ValueError("Restart history events must be a list")
        return tuple(LifecycleEvent.from_dict(item) for item in events[-MAX_HISTORY_EVENTS:])

    def append(self, event: LifecycleEvent) -> None:
        if event.reason not in {"crash", "memory_threshold", "planned_restart"}:
            return
        if event.outcome == "scheduled":
            return
        with self._lock:
            try:
                events = list(self.load())
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                events = []
            events.append(event)
            self._write(events[-MAX_HISTORY_EVENTS:])

    def _write(self, events: list[LifecycleEvent]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        payload = {
            "version": HISTORY_VERSION,
            "events": [event.to_dict() for event in events],
        }
        try:
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
        except Exception:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise


__all__ = [
    "LifecycleEvent",
    "RestartHistoryStore",
    "TerminationInfo",
    "classify_launch_error",
    "classify_process_exit",
    "diagnostic_tail",
    "restart_history_path",
]
