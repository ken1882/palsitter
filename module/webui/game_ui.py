from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Callable

from module.games import get_game


@dataclass(frozen=True)
class InstancePage:
    id: str
    label_key: str
    title_key: str
    render: Callable[[str], None]


@dataclass(frozen=True)
class InstanceCreationUI:
    render_fields: Callable[[], None]
    create: Callable[[str, str], None | bool]


@dataclass(frozen=True)
class GameWebUI:
    pages: tuple[InstancePage, ...]
    creation: InstanceCreationUI | None = None


def get_game_ui(game_id: str) -> GameWebUI:
    adapter = get_game(game_id)
    module = import_module(adapter.webui_module)
    factory = getattr(module, "get_webui", None)
    if factory is None:
        raise TypeError(f"{adapter.webui_module} does not define get_webui()")
    result = factory()
    if not isinstance(result, GameWebUI):
        raise TypeError(f"{adapter.webui_module}.get_webui() did not return GameWebUI")
    page_ids = [page.id for page in result.pages]
    if len(page_ids) != len(set(page_ids)):
        raise ValueError(f"duplicate page id in {game_id} web UI")
    if "overview" not in page_ids:
        raise ValueError(f"{game_id} web UI must define an overview page")
    return result


__all__ = ["GameWebUI", "InstanceCreationUI", "InstancePage", "get_game_ui"]
