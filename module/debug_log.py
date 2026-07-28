from __future__ import annotations

import datetime as dt
import json
import re
import shlex
import threading
from pathlib import Path
from typing import Iterable, TextIO

from module.instances import LOG_RETENTION_DAYS, config_dir


_LOCK = threading.Lock()
_MAX_COMMAND_OUTPUT = 16_384
_SAFE_COMPONENT = re.compile(r"[^a-z0-9_-]+")
_DATED_LOG = re.compile(r"^[a-z0-9_-]+-(\d{8})\.log$")


def debug_enabled() -> bool:
    try:
        data = json.loads(
            (config_dir() / "webui" / "settings.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, ValueError):
        return False
    return isinstance(data, dict) and bool(data.get("debug_mode", False))


def _debug_directory() -> Path:
    path = config_dir() / "webui" / "debug"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _component_name(component: str) -> str:
    value = _SAFE_COMPONENT.sub("-", str(component).casefold()).strip("-")
    return value or "palsitter"


def _prune(directory: Path, today: dt.date) -> None:
    cutoff = today - dt.timedelta(days=LOG_RETENTION_DAYS - 1)
    try:
        paths = tuple(directory.iterdir())
    except OSError:
        return
    for path in paths:
        match = _DATED_LOG.fullmatch(path.name)
        if match is None:
            continue
        try:
            date = dt.datetime.strptime(match.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if date < cutoff:
            try:
                path.unlink()
            except OSError:
                pass


def debug_log_path(component: str, when: dt.date | None = None) -> Path:
    date = when or dt.date.today()
    directory = _debug_directory()
    _prune(directory, date)
    return directory / f"{_component_name(component)}-{date:%Y%m%d}.log"


def open_debug_log(component: str) -> TextIO | None:
    if not debug_enabled():
        return None
    try:
        return debug_log_path(component).open("a", encoding="utf-8")
    except OSError:
        return None


def append_debug_log(component: str, message: str) -> None:
    if not debug_enabled():
        return
    stamp = dt.datetime.now().strftime("%H:%M:%S")
    lines = str(message).splitlines() or [""]
    try:
        with _LOCK, debug_log_path(component).open("a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(f"{stamp} {line}\n")
    except OSError:
        pass


def log_command_result(
    component: str,
    command: Iterable[str],
    *,
    returncode: int,
    stdout: str | bytes | None = None,
    stderr: str | bytes | None = None,
) -> None:
    if not debug_enabled():
        return
    append_debug_log(component, f"command: {shlex.join([str(value) for value in command])}")
    for stream, output in (("stdout", stdout), ("stderr", stderr)):
        text = output.decode(errors="replace") if isinstance(output, bytes) else str(output or "")
        if len(text) > _MAX_COMMAND_OUTPUT:
            text = text[:_MAX_COMMAND_OUTPUT] + "\n[output truncated]"
        for line in text.splitlines():
            append_debug_log(component, f"{stream}: {line}")
    append_debug_log(component, f"exit code: {returncode}")


__all__ = [
    "append_debug_log",
    "debug_enabled",
    "debug_log_path",
    "log_command_result",
    "open_debug_log",
]
