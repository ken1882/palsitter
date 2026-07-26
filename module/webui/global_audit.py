from __future__ import annotations

import datetime as dt
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from module.instances import config_dir


GLOBAL_AUDIT_TYPES = ("web_login_success", "web_login_failure")
_locks: dict[Path, threading.RLock] = {}
_locks_guard = threading.Lock()


def _normalize_timestamp(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def global_audit_dir() -> Path:
    return config_dir() / "webui"


@dataclass(frozen=True)
class GlobalAuditEvent:
    timestamp: dt.datetime
    type: str
    username: str = ""
    source_ip: str = ""
    auth_method: str = "basic"
    message: str = ""

    def __post_init__(self) -> None:
        if self.type not in GLOBAL_AUDIT_TYPES:
            raise ValueError(f"unsupported global audit type: {self.type}")
        object.__setattr__(self, "timestamp", _normalize_timestamp(self.timestamp))

    def to_dict(self) -> dict[str, str]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "type": self.type,
            "username": self.username,
            "source_ip": self.source_ip,
            "auth_method": self.auth_method,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GlobalAuditEvent":
        return cls(
            dt.datetime.fromisoformat(str(data["timestamp"])),
            str(data["type"]),
            str(data.get("username") or ""),
            str(data.get("source_ip") or ""),
            str(data.get("auth_method") or "basic"),
            str(data.get("message") or ""),
        )


class GlobalAuditStore:
    @staticmethod
    def _path(timestamp: dt.datetime) -> Path:
        return global_audit_dir() / f"audit-{_normalize_timestamp(timestamp):%Y%m}.jsonl"

    @classmethod
    def _lock_for(cls, path: Path) -> threading.RLock:
        path = path.resolve()
        with _locks_guard:
            return _locks.setdefault(path, threading.RLock())

    def append(self, event: GlobalAuditEvent) -> None:
        path = self._path(event.timestamp)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(event.to_dict(), ensure_ascii=False)
        with self._lock_for(path):
            try:
                if payload in path.read_text(encoding="utf-8").splitlines():
                    return
            except FileNotFoundError:
                pass
            with path.open("a", encoding="utf-8") as handle:
                handle.write(payload + "\n")

    def load(self) -> tuple[GlobalAuditEvent, ...]:
        events: list[GlobalAuditEvent] = []
        for path in sorted(global_audit_dir().glob("audit-*.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    events.append(GlobalAuditEvent.from_dict(json.loads(line)))
                except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                    continue
        events.sort(key=lambda event: event.timestamp, reverse=True)
        return tuple(events)


__all__ = ["GLOBAL_AUDIT_TYPES", "GlobalAuditEvent", "GlobalAuditStore", "global_audit_dir"]
