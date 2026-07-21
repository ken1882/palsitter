from __future__ import annotations

"""Palworld SAV world-settings codec."""

import copy
import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from palworld_save_tools.gvas import GvasFile
from palworld_save_tools.palsav import compress_gvas_to_sav, decompress_sav_to_gvas
from palworld_save_tools.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

from .schema import WORLD_OPTION_FIELDS_BY_KEY


TEMPLATE_PATH = Path(__file__).with_name("template") / "world_option_template.json"

# GVAS scalar property shapes, as produced by palworld_save_tools.archive.FArchiveReader.property():
#   BoolProperty:          {"value": bool, "id": ..., "type": "BoolProperty"}
#   Int/Float/Str/Name...: {"id": ..., "value": <native>, "type": "<TypeName>"}
#   EnumProperty/ByteProp: {"id": ..., "value": {"type": "<EnumTypeName>", "value": "<Enum::Choice>"}, "type": "EnumProperty"}
_INT_TYPES = ("IntProperty", "UInt16Property", "UInt32Property", "Int64Property", "FixedPoint64Property")


def _is_properties_map(node: Any) -> bool:
    return (
        isinstance(node, dict)
        and len(node) > 0
        and all(isinstance(v, dict) and "type" in v for v in node.values())
    )


def find_option_container(properties: Dict[str, Any], min_matches: int = 5) -> Optional[Dict[str, Any]]:
    """Locate the properties-map inside a GVAS ``dump()`` dict that holds the
    world option fields, regardless of how deeply it's nested inside wrapper
    StructPropertys. Self-locating rather than a hardcoded path, since the
    exact internal nesting of WorldOption.sav has not been verified against a
    real game-generated file (no Palworld server was available while writing
    this) - matching on which properties-map actually contains our known
    field names is robust to that uncertainty.
    """
    if _is_properties_map(properties):
        matches = sum(1 for key in properties if key in WORLD_OPTION_FIELDS_BY_KEY)
        if matches >= min_matches:
            return properties
    for prop in properties.values():
        if isinstance(prop, dict) and prop.get("type") == "StructProperty":
            inner = prop.get("value")
            if isinstance(inner, dict):
                found = find_option_container(inner, min_matches)
                if found is not None:
                    return found
    return None


def _unwrap_scalar_value(prop: Dict[str, Any]) -> Any:
    ptype = prop.get("type")
    if ptype == "BoolProperty":
        return bool(prop.get("value"))
    if ptype in _INT_TYPES:
        return int(prop.get("value"))
    if ptype == "FloatProperty":
        return float(prop.get("value"))
    if ptype in ("StrProperty", "NameProperty"):
        return prop.get("value")
    if ptype in ("EnumProperty", "ByteProperty"):
        inner = prop.get("value")
        if isinstance(inner, dict):
            raw = inner.get("value")
            if isinstance(raw, str) and "::" in raw:
                return raw.rsplit("::", 1)[-1]
            return raw
        return inner
    return None


def _rewrap_scalar_value(prop: Dict[str, Any], new_value: Any) -> Dict[str, Any]:
    ptype = prop.get("type")
    updated = dict(prop)
    if ptype == "BoolProperty":
        updated["value"] = bool(new_value)
    elif ptype in _INT_TYPES:
        updated["value"] = int(new_value)
    elif ptype == "FloatProperty":
        updated["value"] = float(new_value)
    elif ptype in ("StrProperty", "NameProperty"):
        updated["value"] = str(new_value)
    elif ptype in ("EnumProperty", "ByteProperty"):
        inner = dict(prop["value"]) if isinstance(prop.get("value"), dict) else {}
        existing_raw = inner.get("value")
        if isinstance(existing_raw, str) and "::" in existing_raw:
            prefix = existing_raw.rsplit("::", 1)[0]
            inner["value"] = f"{prefix}::{new_value}"
        else:
            inner["value"] = new_value
        updated["value"] = inner
    else:
        return prop
    return updated


def extract_option_values(dumped: Dict[str, Any]) -> Dict[str, Any]:
    container = find_option_container(dumped.get("properties", {}))
    if container is None:
        return {}
    values: Dict[str, Any] = {}
    for key, prop in container.items():
        if key not in WORLD_OPTION_FIELDS_BY_KEY or not isinstance(prop, dict):
            continue
        value = _unwrap_scalar_value(prop)
        if value is not None:
            values[key] = value
    return values


def merge_option_values(dumped: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(dumped)
    container = find_option_container(result.get("properties", {}))
    if container is None:
        raise ValueError(
            "Could not locate the world option settings inside this WorldOption.sav; "
            "it may use a Palworld version this build doesn't recognize."
        )
    for key, value in values.items():
        if key not in WORLD_OPTION_FIELDS_BY_KEY:
            continue
        existing = container.get(key)
        if isinstance(existing, dict):
            container[key] = _rewrap_scalar_value(existing, value)
        # Fields absent from the struct are left alone rather than fabricating
        # a GVAS property of unverified type.
    return result


class WorldOptionSavCodec:
    def __init__(
        self,
        decompress: Callable[[bytes], tuple] = decompress_sav_to_gvas,
        compress: Callable[[bytes, int], bytes] = compress_gvas_to_sav,
        gvas_read: Callable = GvasFile.read,
        gvas_load: Callable = GvasFile.load,
        type_hints: Optional[Dict[str, str]] = None,
        custom_properties: Optional[Dict[str, tuple]] = None,
        template_path: Path = TEMPLATE_PATH,
    ) -> None:
        self.decompress = decompress
        self.compress = compress
        self.gvas_read = gvas_read
        self.gvas_load = gvas_load
        self.type_hints = type_hints if type_hints is not None else PALWORLD_TYPE_HINTS
        self.custom_properties = custom_properties if custom_properties is not None else PALWORLD_CUSTOM_PROPERTIES
        self.template_path = template_path
        self._save_type_by_path: Dict[Path, int] = {}

    def read(self, path: Path) -> Dict[str, Any]:
        raw_bytes = path.read_bytes()
        raw_gvas, save_type = self.decompress(raw_bytes)
        gvas_file = self.gvas_read(raw_gvas, self.type_hints, self.custom_properties)
        self._save_type_by_path[path] = save_type
        return gvas_file.dump()

    def load_template(self) -> Dict[str, Any]:
        return json.loads(self.template_path.read_text(encoding="utf-8"))

    def write(self, path: Path, dumped: Dict[str, Any], save_type: Optional[int] = None) -> None:
        if save_type is None:
            save_type = self._save_type_by_path.get(path, 0x31)
        gvas_file = self.gvas_load(dumped)
        raw_gvas = gvas_file.write(self.custom_properties)
        sav_bytes = self.compress(raw_gvas, save_type)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(sav_bytes)
