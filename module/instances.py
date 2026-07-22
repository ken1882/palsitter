from __future__ import annotations

import json
import os
import re
import shutil
import threading
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


DEFAULT_CONFIG_DIR = Path(os.getenv("PALSITTER_CONFIG_DIR", "config"))
PROFILE_CONFIG_NAME = "profile.json"
_INITIALIZE_LOCK = threading.RLock()
_RUNTIME_LOCK = threading.RLock()
_AGENT_LOCK = threading.RLock()
LOG_RETENTION_DAYS = 30
_DATED_LOG_RE = re.compile(r"^(?:overview|palserver)-(?P<date>\d{8})\.log$")


@dataclass(frozen=True)
class InstanceRecord:
    name: str
    game: str
    game_config: dict[str, Any]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InstanceRecord":
        return cls(
            name=str(data["name"]),
            game=str(data["game"]),
            game_config=dict(data.get("game_config") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "game": self.game,
            "game_config": dict(self.game_config),
        }


def config_dir() -> Path:
    path = Path(os.getenv("PALSITTER_CONFIG_DIR", str(DEFAULT_CONFIG_DIR)))
    path.mkdir(parents=True, exist_ok=True)
    return path


def profile_root() -> Path:
    cfg = config_dir()
    default = cfg.parent / "profile" if cfg.name == "config" else cfg / "profile"
    path = Path(os.getenv("PALSITTER_PROFILE_DIR", str(default)))
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_profile_name(name: str) -> str:
    cleaned = "".join(ch for ch in name.strip() if ch.isalnum() or ch in ("-", "_"))
    if not cleaned:
        raise ValueError("Profile name must contain letters, numbers, '-' or '_'.")
    if cleaned.casefold().startswith("template"):
        raise ValueError("Profile name cannot start with template.")
    return cleaned


def profile_dir(name: str) -> Path:
    return profile_root() / safe_profile_name(name)


def profile_path(name: str) -> Path:
    return profile_dir(name) / PROFILE_CONFIG_NAME


def _log_date(value: dt.date | dt.datetime | None = None) -> dt.date:
    if value is None:
        return dt.datetime.now().date()
    return value.date() if isinstance(value, dt.datetime) else value


def profile_log_path(name: str, when: dt.date | dt.datetime | None = None) -> Path:
    return profile_dir(name) / "logs" / f"overview-{_log_date(when):%Y%m%d}.log"


def profile_runtime_path(name: str) -> Path:
    return profile_dir(name) / "runtime.json"


def profile_agent_state_path(name: str) -> Path:
    return profile_dir(name) / "agent-state.json"


def profile_server_output_path(name: str, when: dt.date | dt.datetime | None = None) -> Path:
    return profile_dir(name) / "logs" / f"palserver-{_log_date(when):%Y%m%d}.log"


def prune_dated_log_files(
    directory: Path, when: dt.date | dt.datetime | None = None
) -> None:
    if LOG_RETENTION_DAYS <= 0:
        return
    cutoff = _log_date(when) - dt.timedelta(days=LOG_RETENTION_DAYS - 1)
    try:
        paths = directory.iterdir()
    except OSError:
        return
    for path in paths:
        match = _DATED_LOG_RE.fullmatch(path.name)
        if match is None:
            continue
        try:
            log_date = dt.datetime.strptime(match.group("date"), "%Y%m%d").date()
        except ValueError:
            continue
        if log_date >= cutoff:
            continue
        try:
            path.unlink()
        except OSError:
            pass


class DailyLogWriter:
    """Append bytes to the current day's log, reopening it at midnight."""

    def __init__(self, path_factory) -> None:
        self._path_factory = path_factory
        self._handle = None
        self.path: Path | None = None

    def _current_handle(self):
        path = Path(self._path_factory())
        if self._handle is None or self.path != path:
            self.close()
            path.parent.mkdir(parents=True, exist_ok=True)
            prune_dated_log_files(path.parent)
            self._handle = path.open("ab")
            self.path = path
        return self._handle

    def write(self, data: bytes) -> int:
        return self._current_handle().write(data)

    def flush(self) -> None:
        if self._handle is not None:
            self._handle.flush()

    def fileno(self) -> int:
        return self._current_handle().fileno()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def _atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        temporary.write_text(json.dumps(dict(data), indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def load_runtime(name: str) -> dict[str, Any] | None:
    try:
        data = json.loads(profile_runtime_path(name).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return dict(data) if isinstance(data, dict) else None


def save_runtime(name: str, data: Mapping[str, Any]) -> None:
    with _RUNTIME_LOCK:
        _atomic_write_json(profile_runtime_path(name), data)


def update_runtime(name: str, **changes: Any) -> dict[str, Any]:
    with _RUNTIME_LOCK:
        current = load_runtime(name) or {}
        current.update(changes)
        _atomic_write_json(profile_runtime_path(name), current)
        return current


def load_agent_state(name: str) -> dict[str, Any] | None:
    try:
        data = json.loads(profile_agent_state_path(name).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return dict(data) if isinstance(data, dict) else None


def save_agent_state(name: str, data: Mapping[str, Any]) -> None:
    with _AGENT_LOCK:
        _atomic_write_json(profile_agent_state_path(name), data)


def update_agent_state(name: str, **changes: Any) -> dict[str, Any]:
    with _AGENT_LOCK:
        current = load_agent_state(name) or {}
        current.update(changes)
        _atomic_write_json(profile_agent_state_path(name), current)
        return current


def clear_agent_state(name: str) -> None:
    with _AGENT_LOCK:
        try:
            profile_agent_state_path(name).unlink()
        except FileNotFoundError:
            pass


def clear_runtime(name: str, *, pid: int | None = None) -> None:
    with _RUNTIME_LOCK:
        current = load_runtime(name)
        if current is None or (pid is not None and int(current.get("pid", -1)) != pid):
            return
        try:
            profile_runtime_path(name).unlink()
        except FileNotFoundError:
            pass


def _profile_files() -> list[Path]:
    return sorted(profile_root().glob(f"*/{PROFILE_CONFIG_NAME}"))


def _assert_no_case_conflicts(paths: list[Path]) -> None:
    names: dict[str, str] = {}
    for path in paths:
        name = path.parent.name
        folded = name.casefold()
        previous = names.get(folded)
        if previous is not None and previous != name:
            raise ValueError(
                f"Profile names differ only by case: {previous!r} and {name!r}"
            )
        names[folded] = name


def initialize_instances() -> None:
    with _INITIALIZE_LOCK:
        existing = _profile_files()
        _assert_no_case_conflicts(existing)
        by_fold = {path.parent.name.casefold(): path for path in existing}

        for legacy in sorted(config_dir().glob("*.json")):
            if legacy.stem.casefold().startswith("template"):
                continue
            safe = safe_profile_name(legacy.stem)
            folded = safe.casefold()
            current = by_fold.get(folded)
            if current is not None:
                if current.parent.name != safe:
                    raise ValueError(
                        f"Profile names differ only by case: {current.parent.name!r} and {safe!r}"
                    )
                continue
            target = profile_path(safe)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy), str(target))
            by_fold[folded] = target

        files = _profile_files()
        _assert_no_case_conflicts(files)
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            if "game" in data and "game_config" in data:
                continue
            name = str(data.get("name") or path.parent.name)
            migrated = InstanceRecord(
                name=name,
                game="palworld",
                game_config={key: value for key, value in data.items() if key != "name"},
            )
            _atomic_write_json(path, migrated.to_dict())


def _instance_index() -> dict[str, tuple[str, Path]]:
    initialize_instances()
    result: dict[str, tuple[str, Path]] = {}
    paths = _profile_files()
    _assert_no_case_conflicts(paths)
    for path in paths:
        result[path.parent.name.casefold()] = (path.parent.name, path)
    return result


def list_instances() -> list[InstanceRecord]:
    return [
        InstanceRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
        for _, path in _instance_index().values()
    ]


def load_instance(name: str) -> InstanceRecord:
    safe = safe_profile_name(name)
    found = _instance_index().get(safe.casefold())
    if found is None:
        raise FileNotFoundError(f"Profile not found: {name}")
    _, path = found
    return InstanceRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_instance(record: InstanceRecord) -> None:
    from module.games.registry import get_game

    safe = safe_profile_name(record.name)
    get_game(record.game)
    index = _instance_index()
    existing = index.get(safe.casefold())
    if existing is not None and existing[0] != safe:
        raise FileExistsError(f"Profile already exists: {existing[0]}")
    _atomic_write_json(
        profile_path(safe),
        InstanceRecord(safe, record.game, dict(record.game_config)).to_dict(),
    )


def create_instance(
    name: str,
    game: str,
    source_name: str | None = None,
) -> InstanceRecord:
    from module.games.registry import get_game

    safe = safe_profile_name(name)
    if safe.casefold() in _instance_index():
        raise FileExistsError(f"Profile already exists: {safe}")
    adapter = get_game(game)
    if source_name in (None, "template"):
        game_config = adapter.default_config(safe)
    else:
        source = load_instance(source_name)
        if source.game != game:
            raise ValueError("Source profile belongs to a different game.")
        game_config = adapter.clone_config(source.name, source.game_config, safe)
    record = InstanceRecord(safe, game, game_config)
    save_instance(record)
    adapter.after_create(record)
    return record


def delete_instance(name: str) -> None:
    safe = safe_profile_name(name)
    found = _instance_index().get(safe.casefold())
    if found is None:
        raise FileNotFoundError(f"Profile not found: {name}")
    found[1].unlink()


def next_instance_name(game: str) -> str:
    from module.games.registry import get_game

    get_game(game)
    existing = set(_instance_index())
    if game.casefold() not in existing:
        return game
    index = 2
    while f"{game}{index}".casefold() in existing:
        index += 1
    return f"{game}{index}"
