from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping


MAP_SIZE = 8192
WORLD_X_MIN = -1_099_400.0
WORLD_X_MAX = 349_400.0
WORLD_Y_MIN = -724_400.0
WORLD_Y_MAX = 724_400.0

WORLD_TREE_X_MIN = 347_351.5
WORLD_TREE_X_MAX = 689_148.5
WORLD_TREE_Y_MIN = -818_197.0
WORLD_TREE_Y_MAX = -476_400.0

MAP_NAMES = ("palpagos", "world-tree")
_MAP_BOUNDS = {
    "palpagos": (WORLD_X_MIN, WORLD_X_MAX, WORLD_Y_MIN, WORLD_Y_MAX),
    "world-tree": (WORLD_TREE_X_MIN, WORLD_TREE_X_MAX, WORLD_TREE_Y_MIN, WORLD_TREE_Y_MAX),
}

_MAP_ASSETS_PATH = Path(__file__).resolve().parents[3] / "assets" / "gui" / "map"


def map_name_for_coordinates(world_x: Any, world_y: Any) -> str | None:
    """Return the map containing a REST coordinate, if it can be identified."""
    if isinstance(world_x, bool) or isinstance(world_y, bool):
        return None
    try:
        x = float(world_x)
        y = float(world_y)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None

    # The two source map rectangles overlap by a narrow seam. Prefer the
    # existing Palpagos interpretation there; World Tree points outside the
    # Palpagos bounds are unambiguous.
    if WORLD_X_MIN <= x <= WORLD_X_MAX and WORLD_Y_MIN <= y <= WORLD_Y_MAX:
        return "palpagos"
    if WORLD_TREE_X_MIN <= x <= WORLD_TREE_X_MAX and WORLD_TREE_Y_MIN <= y <= WORLD_TREE_Y_MAX:
        return "world-tree"
    return None


def world_to_map_pixel(
    world_x: Any, world_y: Any, map_name: str = "palpagos"
) -> tuple[float, float] | None:
    """Convert Palworld REST coordinates to pixels for a PalDB map."""
    if isinstance(world_x, bool) or isinstance(world_y, bool):
        return None
    try:
        x = float(world_x)
        y = float(world_y)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    bounds = _MAP_BOUNDS.get(map_name)
    if bounds is None:
        return None
    x_min, x_max, y_min, y_max = bounds
    if not (x_min <= x <= x_max and y_min <= y <= y_max):
        return None
    horizontal = (y - y_min) / (y_max - y_min)
    vertical = 1.0 - (x - x_min) / (x_max - x_min)
    return horizontal * MAP_SIZE, vertical * MAP_SIZE


def load_manifest(map_name: str = "palpagos") -> dict[str, Any]:
    return json.loads(
        (_MAP_ASSETS_PATH / map_name / "manifest.json").read_text(encoding="utf-8")
    )


def load_marker_labels(map_name: str = "palpagos") -> dict[str, Any]:
    return json.loads(
        (_MAP_ASSETS_PATH / map_name / "marker_labels.json").read_text(encoding="utf-8")
    )


def player_map_row(player: Mapping[str, Any], map_name: str = "palpagos") -> dict[str, Any] | None:
    userid = str(player.get("userId") or "").strip()
    if not userid:
        return None
    world_x = player.get("location_x", player.get("locationX"))
    world_y = player.get("location_y", player.get("locationY"))
    point = world_to_map_pixel(world_x, world_y, map_name)
    level = player.get("level")
    return {
        "userId": userid,
        "name": str(player.get("name") or userid),
        "level": level if level is not None and level != "" else "-",
        "x": point[0] if point else None,
        "y": point[1] if point else None,
        "valid": point is not None,
    }


__all__ = [
    "MAP_SIZE",
    "MAP_NAMES",
    "WORLD_TREE_X_MAX",
    "WORLD_TREE_X_MIN",
    "WORLD_TREE_Y_MAX",
    "WORLD_TREE_Y_MIN",
    "WORLD_X_MAX",
    "WORLD_X_MIN",
    "WORLD_Y_MAX",
    "WORLD_Y_MIN",
    "load_manifest",
    "load_marker_labels",
    "map_name_for_coordinates",
    "player_map_row",
    "world_to_map_pixel",
]
