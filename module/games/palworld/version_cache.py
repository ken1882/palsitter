from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from module.instances import profile_dir


VERSION_CACHE_FILENAME = "version-cache.json"
_CACHE_LOCK = threading.RLock()
_UNSET = object()


def version_cache_path(name: str) -> Path:
    return profile_dir(name) / VERSION_CACHE_FILENAME


def read_version_cache(name: str) -> dict[str, Any]:
    with _CACHE_LOCK:
        try:
            data = json.loads(version_cache_path(name).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        return dict(data) if isinstance(data, dict) else {}


def update_version_cache(
    name: str,
    *,
    game_version: str | None | object = _UNSET,
    installed_build_id: str | None | object = _UNSET,
    available_build_id: str | None | object = _UNSET,
    checked_at: str | None | object = _UNSET,
    status: str | None | object = _UNSET,
) -> None:
    values = {
        "game_version": game_version,
        "installed_build_id": installed_build_id,
        "available_build_id": available_build_id,
        "checked_at": checked_at,
        "status": status,
    }
    with _CACHE_LOCK:
        data = read_version_cache(name)
        for key, value in values.items():
            if value is _UNSET:
                continue
            if value is None:
                data.pop(key, None)
            else:
                data[key] = str(value)
        path = version_cache_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        except Exception:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise
