from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pywebio import session as pywebio_session
from pywebio.session import local

from module.config import config_dir


DEFAULT_LANGUAGE = "en-US"
LANGUAGES = {
    "en-US": "English",
    "zh-TW": "繁體中文",
    "ja-JP": "日本語",
}


def normalize_language(language: str | None) -> str:
    value = (language or "").replace("_", "-").lower()
    for supported in LANGUAGES:
        if value == supported.lower():
            return supported
    if value.startswith("zh-tw") or value.startswith("zh-hk") or value.startswith("zh-hant"):
        return "zh-TW"
    if value.startswith("ja"):
        return "ja-JP"
    return DEFAULT_LANGUAGE


def settings_path() -> Path:
    return config_dir() / "webui" / "settings.json"


def load_preferred_language(browser_language: str | None = None) -> str:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        return normalize_language(data.get("language"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, AttributeError):
        return normalize_language(browser_language)


def save_preferred_language(language: str) -> str:
    selected = normalize_language(language)
    path = settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    data["language"] = selected
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return selected


def init_language(browser_language: str | None = None) -> str:
    local.language = load_preferred_language(browser_language)
    return local.language


def set_language(language: str) -> str:
    local.language = save_preferred_language(language)
    return local.language


def get_language() -> str:
    # PyWebIO starts its blocking script-mode server when no session
    # implementation has been registered yet. Worker threads must fall back
    # before touching `local` in that state.
    if not pywebio_session._active_session_cls:
        return DEFAULT_LANGUAGE
    try:
        language = getattr(local, "language", DEFAULT_LANGUAGE)
    except Exception:
        # Worker threads used by restart/backup operations do not have a
        # PyWebIO session; use the stable default catalog there.
        language = DEFAULT_LANGUAGE
    return normalize_language(language)


def language_options() -> list[dict[str, str]]:
    return [{"label": label, "value": code} for code, label in LANGUAGES.items()]


@lru_cache(maxsize=None)
def _catalog(language: str) -> dict[str, str]:
    path = Path(__file__).with_name("locales") / f"{language}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def t(key: str, *, language: str | None = None, **values: Any) -> str:
    selected = normalize_language(language) if language else get_language()
    text = _catalog(selected).get(key)
    if text is None:
        text = _catalog(DEFAULT_LANGUAGE).get(key, key)
    return text.format(**values)
