from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from module.games.palworld.backup.service import BackupService
from module.games.palworld.config import PalworldProfile, server_config_dir_name

from .ini_codec import read_ini_option_settings, write_ini_option_settings
from .sav_codec import WorldOptionSavCodec, extract_option_values, merge_option_values
from .schema import WORLD_OPTION_FIELDS

SAV_FILENAME = "WorldOption.sav"


def preferred_ini_path(profile: PalworldProfile) -> Path:
    return (
        Path(profile.workdir)
        / "Pal"
        / "Saved"
        / "Config"
        / server_config_dir_name()
        / "PalWorldSettings.ini"
    )


def resolve_ini_path(profile: PalworldProfile) -> Path:
    base = Path(profile.workdir) / "Pal" / "Saved" / "Config"
    windows = base / "WindowsServer" / "PalWorldSettings.ini"
    linux = base / "LinuxServer" / "PalWorldSettings.ini"
    preferred = preferred_ini_path(profile)
    fallback = windows if preferred == linux else linux
    if preferred.exists() or not fallback.exists():
        return preferred
    return fallback


def dedicated_world_dir(profile: PalworldProfile) -> Path:
    return Path(profile.backup_source) / profile.dedicated_server_name


def find_world_sav_path(profile: PalworldProfile) -> Optional[Path]:
    path = dedicated_world_dir(profile) / SAV_FILENAME
    return path if path.exists() else None


def _fill_defaults(values: Dict[str, Any]) -> Dict[str, Any]:
    filled = {field_.key: field_.default for field_ in WORLD_OPTION_FIELDS}
    filled.update(values)
    crossplay = filled.get("CrossplayPlatforms", [])
    if isinstance(crossplay, str):
        filled["CrossplayPlatforms"] = [
            choice.strip()
            for choice in crossplay.strip().strip("()").split(",")
            if choice.strip()
        ]
    else:
        filled["CrossplayPlatforms"] = list(crossplay)
    return filled


def _sav_values(values: Dict[str, Any]) -> Dict[str, Any]:
    serialized = dict(values)
    crossplay = serialized.get("CrossplayPlatforms", [])
    serialized["CrossplayPlatforms"] = f"({','.join(crossplay)})"
    return serialized


@dataclass
class LoadedWorldSettings:
    values: Dict[str, Any]
    source_format: str
    sav_path: Optional[Path]


def ensure_world_settings(profile: PalworldProfile) -> Path:
    """Create the platform-specific INI once when a server has no world config."""
    path = preferred_ini_path(profile)
    if path.exists():
        return path
    provided = dict(profile.world_settings or {})
    values = _fill_defaults(provided)
    if "PublicPort" not in provided:
        values["PublicPort"] = profile.game_port
    if "RESTAPIPort" not in provided:
        values["RESTAPIPort"] = profile.rest_port
    if "AdminPassword" not in provided:
        values["AdminPassword"] = profile.rest_password
    write_ini_option_settings(path, values)
    return path


def load_world_settings(
    profile: PalworldProfile,
    sav_codec: Optional[WorldOptionSavCodec] = None,
) -> LoadedWorldSettings:
    sav_path = find_world_sav_path(profile)
    if sav_path is not None:
        codec = sav_codec or WorldOptionSavCodec()
        values = extract_option_values(codec.read(sav_path))
        filled = _fill_defaults(values)
        profile.world_settings = filled
        profile._sync_world_network_settings()
        return LoadedWorldSettings(filled, "sav", sav_path)
    values = read_ini_option_settings(resolve_ini_path(profile))
    if not values and profile.world_settings:
        values = dict(profile.world_settings)
    filled = _fill_defaults(values)
    if not values:
        filled["PublicPort"] = profile.game_port
        filled["RESTAPIPort"] = profile.rest_port
        filled["AdminPassword"] = profile.rest_password
    profile.world_settings = filled
    profile._sync_world_network_settings()
    return LoadedWorldSettings(filled, "ini", None)


def save_world_settings(
    profile: PalworldProfile,
    values: Dict[str, Any],
    fmt: str,
    backup_service: Optional[BackupService] = None,
    sav_codec: Optional[WorldOptionSavCodec] = None,
) -> None:
    normalized = _fill_defaults(values)
    if fmt == "sav":
        (backup_service or BackupService(profile)).create_backup()
        codec = sav_codec or WorldOptionSavCodec()
        target = dedicated_world_dir(profile) / SAV_FILENAME
        target.parent.mkdir(parents=True, exist_ok=True)
        base_dump = codec.read(target) if target.exists() else codec.load_template()
        codec.write(target, merge_option_values(base_dump, _sav_values(normalized)))
    elif fmt == "ini":
        write_ini_option_settings(resolve_ini_path(profile), normalized)
    else:
        raise ValueError(f"Unknown world settings format: {fmt}")
    profile.world_settings = normalized
    profile._sync_world_network_settings()
