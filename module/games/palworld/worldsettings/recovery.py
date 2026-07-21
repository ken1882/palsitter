from __future__ import annotations

import datetime as dt
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from module.games.palworld.config import PalworldProfile

from .ini_codec import (
    OPTION_PREFIX,
    SECTION,
    read_ini_option_settings,
    split_top_level,
    write_ini_option_settings,
)
from .sav_codec import WorldOptionSavCodec
from .schema import WORLD_OPTION_FIELDS
from .service import find_world_sav_path, resolve_ini_path


@dataclass(frozen=True)
class IniRecoveryResult:
    ini_path: Path
    malformed_copy: Path
    parse_error: str
    regenerated_values: dict[str, Any]


@dataclass(frozen=True)
class SavDisableResult:
    original_path: Path
    disabled_path: Path
    decode_error: str


def _timestamp_suffix(timestamp: Optional[dt.datetime]) -> str:
    return (timestamp or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")


def _unique_sibling(path: Path, suffix: str) -> Path:
    candidate = path.with_name(f"{path.name}.{suffix}")
    index = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.{suffix}-{index}")
        index += 1
    return candidate


def _balanced_struct(text: str) -> bool:
    depth = 0
    quoted = False
    escaped = False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            continue
        if character == '"':
            quoted = True
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not quoted


def diagnose_ini(path: str | Path) -> Optional[str]:
    """Return a user-facing parse error, or ``None`` for a valid/missing file."""
    ini_path = Path(path)
    if not ini_path.exists():
        return None
    try:
        text = ini_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return str(exc)
    lines = [line.strip() for line in text.splitlines()]
    if SECTION not in lines:
        return f"Missing section {SECTION}"
    option_lines = [line for line in lines if line.startswith(OPTION_PREFIX)]
    if not option_lines:
        return f"Missing {OPTION_PREFIX} entry"
    if len(option_lines) > 1:
        return f"Multiple {OPTION_PREFIX} entries were found"
    raw = option_lines[0][len(OPTION_PREFIX):].strip()
    if not raw.startswith("(") or not raw.endswith(")"):
        return "OptionSettings must be enclosed in parentheses"
    if not _balanced_struct(raw):
        return "OptionSettings contains unbalanced parentheses or quotes"
    for part in split_top_level(raw[1:-1]):
        if part and ("=" not in part or not part.partition("=")[0].strip()):
            return f"Malformed OptionSettings entry: {part}"
    try:
        read_ini_option_settings(ini_path)
    except (TypeError, ValueError) as exc:
        return str(exc)
    return None


def _managed_defaults(profile: PalworldProfile) -> dict[str, Any]:
    values = {field.key: field.default for field in WORLD_OPTION_FIELDS}
    values.update(dict(profile.world_settings or {}))
    values.update(
        {
            "PublicPort": profile.game_port,
            "RESTAPIEnabled": True,
            "RESTAPIPort": profile.rest_port,
            "AdminPassword": profile.rest_password,
        }
    )
    return values


def recover_malformed_ini(
    profile: PalworldProfile,
    *,
    is_server_active: Callable[[], bool],
    timestamp: Optional[dt.datetime] = None,
) -> IniRecoveryResult:
    if is_server_active():
        raise RuntimeError("Stop the server before recovering PalWorldSettings.ini")
    path = resolve_ini_path(profile)
    if not path.exists():
        raise FileNotFoundError(f"PalWorldSettings.ini does not exist: {path}")
    parse_error = diagnose_ini(path)
    if parse_error is None:
        raise ValueError("PalWorldSettings.ini is valid; recovery was not performed")

    malformed_copy = _unique_sibling(
        path,
        f"malformed-{_timestamp_suffix(timestamp)}.bak",
    )
    malformed_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, malformed_copy)
    values = _managed_defaults(profile)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        write_ini_option_settings(temporary, values)
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    profile.world_settings = dict(values)
    return IniRecoveryResult(path, malformed_copy, parse_error, values)


def diagnose_world_option_sav(
    profile: PalworldProfile,
    *,
    sav_codec: Optional[WorldOptionSavCodec] = None,
) -> Optional[str]:
    path = find_world_sav_path(profile)
    if path is None:
        return None
    try:
        (sav_codec or WorldOptionSavCodec()).read(path)
    except Exception as exc:
        return str(exc)
    return None


def disable_undecodable_world_option_sav(
    profile: PalworldProfile,
    *,
    sav_codec: Optional[WorldOptionSavCodec] = None,
    is_server_active: Callable[[], bool],
    timestamp: Optional[dt.datetime] = None,
) -> SavDisableResult:
    if is_server_active():
        raise RuntimeError("Stop the server before disabling WorldOption.sav")
    path = find_world_sav_path(profile)
    if path is None:
        raise FileNotFoundError("WorldOption.sav does not exist for the active world")
    decode_error = diagnose_world_option_sav(profile, sav_codec=sav_codec)
    if decode_error is None:
        raise ValueError("WorldOption.sav is readable; it was not disabled")
    disabled = _unique_sibling(
        path,
        f"disabled-{_timestamp_suffix(timestamp)}",
    )
    os.replace(path, disabled)
    return SavDisableResult(path, disabled, decode_error)
