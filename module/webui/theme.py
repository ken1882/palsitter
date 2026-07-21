from __future__ import annotations

import json

from module.webui.i18n import settings_path


DEFAULT_THEME = "dark"
THEMES = {"light", "dark"}


def normalize_theme(theme: str | None) -> str:
    return theme if theme in THEMES else DEFAULT_THEME


def load_preferred_theme() -> str:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        return normalize_theme(data.get("theme"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, AttributeError):
        return DEFAULT_THEME


def save_preferred_theme(theme: str) -> str:
    selected = normalize_theme(theme)
    path = settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    data["theme"] = selected
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return selected
