from __future__ import annotations

"""Palworld INI world-settings codec."""

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Optional

from .schema import WorldOptionField, WORLD_OPTION_FIELDS, WORLD_OPTION_FIELDS_BY_KEY


SECTION = "[/Script/Pal.PalGameWorldSettings]"
OPTION_PREFIX = "OptionSettings="
LAUNCH_ONLY_OPTION_KEYS = frozenset({"EnableGameDataAPI"})


def coerce_integer(value: Any) -> int:
    """Parse an integer setting, including whole numbers formatted as decimals."""
    raw = str(value).strip()
    try:
        number = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"invalid literal for int() with base 10: {value!r}") from None
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError(f"invalid literal for int() with base 10: {value!r}")
    return int(number)


def split_top_level(text: str) -> list[str]:
    """Split on commas at paren-depth 0, outside double quotes.

    Needed because values can themselves be quoted strings (which may embed
    escaped quotes) or nested parenthesized lists (e.g. CrossplayPlatforms).
    """
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    in_quotes = False
    escaped = False
    for ch in text:
        if in_quotes:
            buf.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_quotes = False
            continue
        if ch == '"':
            in_quotes = True
            buf.append(ch)
            continue
        if ch == "(":
            depth += 1
            buf.append(ch)
            continue
        if ch == ")":
            depth -= 1
            buf.append(ch)
            continue
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def parse_option_settings(raw_struct_text: str) -> Dict[str, str]:
    text = raw_struct_text.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    pairs: Dict[str, str] = {}
    for part in split_top_level(text):
        if not part:
            continue
        key, _, value = part.partition("=")
        pairs[key.strip()] = value.strip()
    return pairs


def _unescape(text: str) -> str:
    return text.replace('\\"', '"').replace("\\\\", "\\")


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def coerce_ini_value(field_: Optional[WorldOptionField], raw: str) -> Any:
    if field_ is None:
        return raw
    if field_.ftype == "bool":
        return raw == "True"
    if field_.ftype == "int":
        return coerce_integer(raw)
    if field_.ftype == "float":
        return float(raw)
    if field_.ftype in ("string", "enum"):
        if raw.startswith('"') and raw.endswith('"'):
            return _unescape(raw[1:-1])
        return raw
    if field_.ftype == "multiselect":
        text = raw.strip()
        if text.startswith("(") and text.endswith(")"):
            text = text[1:-1]
        return [choice.strip() for choice in text.split(",") if choice.strip()]
    return raw


def format_ini_value(field_: Optional[WorldOptionField], value: Any) -> str:
    if field_ is None:
        return str(value)
    if field_.ftype == "bool":
        return "True" if value else "False"
    if field_.ftype == "int":
        return str(int(value))
    if field_.ftype == "float":
        return f"{float(value):.6f}"
    if field_.ftype == "enum":
        return str(value)
    if field_.ftype == "string":
        text = str(value)
        if text.startswith("(") and text.endswith(")"):
            # Already a parenthesized list literal (e.g. CrossplayPlatforms) - pass through.
            return text
        return f'"{_escape(text)}"'
    if field_.ftype == "multiselect":
        return f"({','.join(str(choice) for choice in value)})"
    return str(value)


def _read_raw(path: Path) -> str:
    """Read without newline translation, so on-disk line endings are preserved."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_raw(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def read_ini_option_settings(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    for line in _read_raw(path).splitlines():
        stripped = line.strip()
        if stripped.startswith(OPTION_PREFIX):
            raw = stripped[len(OPTION_PREFIX):]
            pairs = parse_option_settings(raw)
            return {
                key: coerce_ini_value(WORLD_OPTION_FIELDS_BY_KEY.get(key), raw_value)
                for key, raw_value in pairs.items()
                if key not in LAUNCH_ONLY_OPTION_KEYS
            }
    return {}


def write_ini_option_settings(path: Path, values: Dict[str, Any]) -> None:
    values = {
        key: value
        for key, value in values.items()
        if (key not in LAUNCH_ONLY_OPTION_KEYS
            and (WORLD_OPTION_FIELDS_BY_KEY.get(key) is None
            or WORLD_OPTION_FIELDS_BY_KEY[key].persisted)
        )
    }
    existing_raw: Dict[str, str] = {}
    lines: Optional[list[str]] = None
    newline = "\r\n"
    option_line_index: Optional[int] = None

    if path.exists():
        text = _read_raw(path)
        if "\r\n" not in text and "\n" in text:
            newline = "\n"
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if line.strip().startswith(OPTION_PREFIX):
                existing_raw = parse_option_settings(line.strip()[len(OPTION_PREFIX):])
                option_line_index = index
                break

    merged_keys = [
        key
        for key in existing_raw
        if (key not in LAUNCH_ONLY_OPTION_KEYS
            and (WORLD_OPTION_FIELDS_BY_KEY.get(key) is None
            or WORLD_OPTION_FIELDS_BY_KEY[key].persisted)
        )
    ]
    for key in values:
        if key not in existing_raw:
            merged_keys.append(key)

    parts = []
    for key in merged_keys:
        field_ = WORLD_OPTION_FIELDS_BY_KEY.get(key)
        if key in values:
            parts.append(f"{key}={format_ini_value(field_, values[key])}")
        else:
            parts.append(f"{key}={existing_raw.get(key, '')}")
    new_line = f"{OPTION_PREFIX}({','.join(parts)})"

    if lines is not None and option_line_index is not None:
        lines[option_line_index] = new_line
        _write_raw(path, newline.join(lines) + newline)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_raw(path, f"{SECTION}{newline}{new_line}{newline}")
