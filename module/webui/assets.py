from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from pywebio.output import put_widget
from pywebio.session import eval_js, run_js

from module.webui.theme import load_preferred_theme


ASSETS_DIR = Path(__file__).parents[2] / "assets"
GUI_DIR = ASSETS_DIR / "gui"
MANIFEST_PATH = GUI_DIR / "manifest.json"
_CLIENT_API = re.compile(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$")


@lru_cache(maxsize=1)
def asset_manifest() -> dict[str, Any]:
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("GUI asset manifest must be an object")
    return data


def _asset_path(relative_path: str) -> Path:
    path = (ASSETS_DIR / relative_path).resolve()
    if ASSETS_DIR.resolve() not in path.parents:
        raise ValueError(f"GUI asset escapes the assets directory: {relative_path}")
    return path


def asset_urls(kind: str) -> list[str]:
    values = asset_manifest().get(kind, [])
    if not isinstance(values, list):
        raise ValueError(f"GUI manifest {kind!r} entry must be a list")
    urls = []
    for value in values:
        relative = str(value).replace("\\", "/")
        # Static files are loaded by the browser independently of the Python
        # process. Include the source timestamp so a long-lived browser cannot
        # retain a pre-refactor stylesheet or script after deployment.
        version = _asset_path(relative).stat().st_mtime_ns
        urls.append(f"/static/{relative}?v={version}")
    return urls


def asset_css(name: str) -> str:
    return (GUI_DIR / "css" / f"{name}.css").read_text(encoding="utf-8")


def asset_icon(name: str) -> str:
    return (GUI_DIR / "icon" / f"{name}.svg").read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def asset_template(name: str) -> str:
    templates = asset_manifest().get("templates", {})
    if not isinstance(templates, dict) or name not in templates:
        raise KeyError(f"Unknown GUI template: {name}")
    path = _asset_path(str(templates[name]))
    if path.suffix.lower() != ".html":
        raise ValueError(f"GUI template must be an HTML file: {path}")
    return path.read_text(encoding="utf-8")


def put_asset_widget(
    name: str,
    data: Mapping[str, Any] | None = None,
    *,
    scope: str | None = None,
    position: int = -1,
):
    return put_widget(asset_template(name), dict(data or {}), scope=scope, position=position)


def put_asset_icon(name: str, *, scope: str | None = None, position: int = -1):
    return put_widget(asset_icon(name), {}, scope=scope, position=position)


def _validate_client_api(name: str) -> str:
    if not _CLIENT_API.fullmatch(name):
        raise ValueError(f"Invalid client API name: {name!r}")
    return name


def client_call(api_name: str, **payload: Any) -> None:
    run_js(
        "window.Palsitter.invoke(api, payload)",
        api=_validate_client_api(api_name),
        payload=payload,
    )


def client_query(api_name: str, **payload: Any) -> Any:
    return eval_js(
        "window.Palsitter.query(api, payload)",
        api=_validate_client_api(api_name),
        payload=payload,
    )


def inject_css() -> None:
    client_call("dom.setTheme", theme=load_preferred_theme())


__all__ = [
    "ASSETS_DIR",
    "GUI_DIR",
    "asset_css",
    "asset_icon",
    "asset_manifest",
    "asset_template",
    "asset_urls",
    "client_call",
    "client_query",
    "inject_css",
    "put_asset_icon",
    "put_asset_widget",
]
