from __future__ import annotations

import datetime as dt
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from module.instances import profile_dir


AUDIT_TYPES = (
    "palsitter_command",
    "game_command",
    "player_login",
    "player_logout",
    "server_start",
    "server_update",
    "server_crash",
    "server_stop",
    "server_exit",
    "agent_exit",
)

_MONTH_FILE = re.compile(r"^audit-(\d{6})\.jsonl$")
_PALSERVER_TIMESTAMP = re.compile(
    r"^\[(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[LOG\] (?P<body>.*)$"
)
_JOINED = re.compile(
    r"^(?P<name>.+) joined the server\. \(User id: (?P<userid>[^,)]+)(?:, Player id: (?P<playerid>[^)]+))?\)$"
)
_LEFT = re.compile(r"^(?P<name>.+) left the server\. \(User id: (?P<userid>[^)]+)\)$")
_COMMAND = re.compile(r"^(?P<name>.+) executed the command\. (?P<command>.+)$")


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def normalize_timestamp(value: dt.datetime) -> dt.datetime:
    if not isinstance(value, dt.datetime):
        raise TypeError("audit timestamp must be a datetime")
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    return value.astimezone(dt.timezone.utc)


def _format_command_message(
    player: str,
    command: str,
    admin_password: str | None,
) -> str:
    args = command.strip().split()
    if args and args[0].casefold().lstrip("/") == "adminpassword":
        result = (
            admin_password is not None
            and len(args) > 1
            and args[1] == admin_password
        )
        return f"{player} executed: adminpassword (result: {'success' if result else 'fail'})"
    return f"{player} executed: {command.strip()}"


@dataclass(frozen=True)
class AuditEvent:
    timestamp: dt.datetime
    type: str
    message: str

    def __post_init__(self) -> None:
        if self.type not in AUDIT_TYPES:
            raise ValueError(f"unsupported audit type: {self.type}")
        object.__setattr__(self, "timestamp", normalize_timestamp(self.timestamp))
        object.__setattr__(self, "message", str(self.message))

    def to_dict(self) -> dict[str, str]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "type": self.type,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AuditEvent":
        return cls(
            dt.datetime.fromisoformat(str(data["timestamp"])),
            str(data["type"]),
            str(data["message"]),
        )


def audit_log_dir(name: str) -> Path:
    return profile_dir(name) / "logs"


def audit_path(name: str, timestamp: dt.datetime) -> Path:
    timestamp = normalize_timestamp(timestamp)
    return audit_log_dir(name) / f"audit-{timestamp:%Y%m}.jsonl"


def _month_start(value: dt.datetime) -> dt.datetime:
    value = normalize_timestamp(value)
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month(value: dt.datetime) -> dt.datetime:
    value = _month_start(value)
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1)
    return value.replace(month=value.month + 1)


class AuditStore:
    _locks: dict[Path, threading.RLock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, name: str) -> None:
        self.name = name

    @classmethod
    def _lock_for(cls, path: Path) -> threading.RLock:
        path = path.resolve()
        with cls._locks_guard:
            return cls._locks.setdefault(path, threading.RLock())

    def append(self, event: AuditEvent) -> None:
        path = audit_path(self.name, event.timestamp)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = self._lock_for(path)
        payload = json.dumps(event.to_dict(), ensure_ascii=False)
        with lock:
            try:
                if payload in path.read_text(encoding="utf-8").splitlines():
                    return
            except FileNotFoundError:
                pass
            with path.open("a", encoding="utf-8") as handle:
                handle.write(payload + "\n")

    def _paths(
        self,
        start: dt.datetime | None = None,
        end: dt.datetime | None = None,
    ) -> list[Path]:
        if start is None or end is None:
            return sorted(
                path
                for path in audit_log_dir(self.name).glob("audit-*.jsonl")
                if _MONTH_FILE.match(path.name)
            )
        start = normalize_timestamp(start)
        end = normalize_timestamp(end)
        if end < start:
            return []
        result = []
        month = _month_start(start)
        last = _month_start(end)
        while month <= last:
            result.append(audit_log_dir(self.name) / f"audit-{month:%Y%m}.jsonl")
            month = _next_month(month)
        return [path for path in result if path.is_file()]

    def load(
        self,
        start: dt.datetime | None = None,
        end: dt.datetime | None = None,
    ) -> tuple[AuditEvent, ...]:
        events: list[AuditEvent] = []
        for path in self._paths(start, end):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    event = AuditEvent.from_dict(json.loads(line))
                except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                    continue
                if start is not None and event.timestamp < normalize_timestamp(start):
                    continue
                if end is not None and event.timestamp > normalize_timestamp(end):
                    continue
                events.append(event)
        events.sort(key=lambda event: event.timestamp, reverse=True)
        return tuple(events)


def parse_palserver_audit_line(
    line: str,
    admin_password: str | None = None,
) -> AuditEvent | None:
    match = _PALSERVER_TIMESTAMP.match(line.strip())
    if match is None:
        return None
    timestamp = dt.datetime.strptime(match.group("timestamp"), "%Y-%m-%d %H:%M:%S")
    body = match.group("body")
    joined = _JOINED.match(body)
    if joined:
        player = joined.group("name")
        userid = joined.group("userid")
        return AuditEvent(timestamp, "player_login", f"{player} ({userid}) joined the server")
    left = _LEFT.match(body)
    if left:
        player = left.group("name")
        userid = left.group("userid")
        return AuditEvent(timestamp, "player_logout", f"{player} ({userid}) left the server")
    command = _COMMAND.match(body)
    if command:
        player = command.group("name")
        value = _format_command_message(player, command.group("command"), admin_password)
        return AuditEvent(timestamp, "game_command", value)
    return None


def format_palserver_log_line(line: str, admin_password: str | None = None) -> str:
    """Return a server log line with sensitive admin-password commands sanitized."""
    normalized = line.strip()
    match = _PALSERVER_TIMESTAMP.match(normalized)
    if match is None:
        return normalized
    command = _COMMAND.match(match.group("body"))
    if command is None:
        return normalized
    args = command.group("command").strip().split()
    if not args or args[0].casefold().lstrip("/") != "adminpassword":
        return normalized
    message = _format_command_message(
        command.group("name"), command.group("command"), admin_password
    )
    return normalized[: match.start("body")] + message


__all__ = [
    "AUDIT_TYPES",
    "AuditEvent",
    "AuditStore",
    "audit_log_dir",
    "audit_path",
    "format_palserver_log_line",
    "normalize_timestamp",
    "parse_palserver_audit_line",
    "utc_now",
]
