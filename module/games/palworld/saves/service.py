from __future__ import annotations

import datetime as dt
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from module.games.palworld.backup import BackupResult, BackupService
from module.games.palworld.config import (
    DEDICATED_SERVER_NAME_RE,
    PalworldProfile,
    server_config_dir_name,
    save_profile,
)


LEVEL_FILENAME = "Level.sav"
HOST_PLAYER_FILENAME = "00000000000000000000000000000001.sav"
PALWORLD_SETTINGS_RELATIVE_PATHS = (
    Path("Pal") / "Saved" / "Config" / "WindowsServer" / "PalWorldSettings.ini",
    Path("Pal") / "Saved" / "Config" / "LinuxServer" / "PalWorldSettings.ini",
)


@dataclass(frozen=True)
class WorldSaveInfo:
    world_id: str
    path: Path
    size_bytes: int
    modified_at: dt.datetime
    player_save_count: int
    active: bool = False


@dataclass(frozen=True)
class WorldImportResult:
    world: WorldSaveInfo
    activated: bool


@dataclass(frozen=True)
class ImportSourceInfo:
    kind: str
    world: WorldSaveInfo
    player_files: tuple[Path, ...]
    missing_files: tuple[str, ...]


@dataclass(frozen=True)
class WorldSwitchResult:
    previous_world_id: str
    active_world: WorldSaveInfo
    safety_backup: Path


class WorldSwitchError(RuntimeError):
    pass


def _regular_tree_files(root: Path) -> Iterable[Path]:
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = [
            name for name in dirs if not (current_path / name).is_symlink()
        ]
        for name in files:
            path = current_path / name
            if path.is_file() and not path.is_symlink():
                yield path


def _describe_world(path: Path, *, active_world_id: str = "") -> WorldSaveInfo:
    files = list(_regular_tree_files(path))
    level_path = path / LEVEL_FILENAME
    if level_path not in files:
        raise ValueError(f"Dedicated world is missing {LEVEL_FILENAME}: {path}")
    players_dir = path / "Players"
    player_count = 0
    if players_dir.is_dir() and not players_dir.is_symlink():
        player_count = sum(
            1
            for player_path in _regular_tree_files(players_dir)
            if player_path.suffix.casefold() == ".sav"
        )
    modified_timestamp = max(file_path.stat().st_mtime for file_path in files)
    return WorldSaveInfo(
        world_id=path.name,
        path=path,
        size_bytes=sum(file_path.stat().st_size for file_path in files),
        modified_at=dt.datetime.fromtimestamp(modified_timestamp),
        player_save_count=player_count,
        active=path.name == active_world_id,
    )


def _is_dedicated_world(path: Path) -> bool:
    if path.is_symlink() or not path.is_dir():
        return False
    if DEDICATED_SERVER_NAME_RE.fullmatch(path.name) is None:
        return False
    level = path / LEVEL_FILENAME
    return level.is_file() and not level.is_symlink()


def scan_dedicated_worlds(
    source: str | Path,
    *,
    active_world_id: str = "",
) -> list[WorldSaveInfo]:
    """Scan a direct world folder or its immediate parent without following links."""
    root = Path(source)
    if not root.exists():
        raise FileNotFoundError(f"Save source does not exist: {root}")
    if root.is_symlink() or not root.is_dir():
        return []
    if _is_dedicated_world(root):
        candidates = [root]
    else:
        candidates = [
            child
            for child in root.iterdir()
            if _is_dedicated_world(child)
        ]
    return sorted(
        (
            _describe_world(path, active_world_id=active_world_id)
            for path in candidates
        ),
        key=lambda world: world.world_id,
    )


def inspect_import_source(level_path: str | Path) -> ImportSourceInfo:
    """Classify a Palworld world save without changing the source files."""
    level = Path(level_path)
    if level.name != LEVEL_FILENAME or not level.is_file() or level.is_symlink():
        raise ValueError(f"Expected a regular {LEVEL_FILENAME} file")
    world = _describe_world(level.parent)
    players_dir = level.parent / "Players"
    player_files = tuple(
        sorted(
            path
            for path in players_dir.iterdir()
            if path.is_file() and not path.is_symlink() and path.suffix.casefold() == ".sav"
        )
    ) if players_dir.is_dir() and not players_dir.is_symlink() else ()

    save_root = level.parent.parent
    is_dedicated = (
        save_root.name == "0"
        and save_root.parent.name == "SaveGames"
        and save_root.parent.parent.name == "Saved"
        and save_root.parent.parent.parent.name == "Pal"
    )
    is_local = (
        save_root.name.isdigit()
        and save_root.parent.name == "SaveGames"
        and save_root.parent.parent.name == "Saved"
        and save_root.parent.parent.parent.name == "Pal"
    )
    if is_dedicated:
        kind = "dedicated"
    elif is_local and len(player_files) == 1 and player_files[0].name == HOST_PLAYER_FILENAME:
        kind = "single_player"
    elif is_local and player_files:
        kind = "coop"
    else:
        kind = "unknown"

    missing = tuple(
        filename
        for filename in ("LevelMeta.sav", "LocalData.sav", "Players")
        if not (level.parent / filename).exists()
    )
    return ImportSourceInfo(kind, world, player_files, missing)


def find_import_world_settings_ini(level_path: str | Path) -> Optional[Path]:
    """Find the server-level settings file associated with an imported Level.sav."""
    level = Path(level_path)
    for save_root in level.parents:
        if (
            save_root.name == "0"
            and save_root.parent.name == "SaveGames"
            and save_root.parent.parent.name == "Saved"
            and save_root.parent.parent.parent.name == "Pal"
        ):
            server_root = save_root.parent.parent.parent.parent
            preferred = server_config_dir_name()
            relative_paths = sorted(
                PALWORLD_SETTINGS_RELATIVE_PATHS,
                key=lambda path: path.parts[-2] != preferred,
            )
            return next(
                (
                    server_root / relative
                    for relative in relative_paths
                    if (server_root / relative).is_file()
                    and not (server_root / relative).is_symlink()
                ),
                None,
            )
    return None


def import_world_settings_ini(profile: PalworldProfile, source: str | Path) -> Optional[Path]:
    """Copy a detected source PalWorldSettings.ini into the new profile."""
    source_path = find_import_world_settings_ini(source)
    if source_path is None:
        return None
    target = (
        Path(profile.workdir)
        / "Pal"
        / "Saved"
        / "Config"
        / server_config_dir_name()
        / "PalWorldSettings.ini"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target)
    return target


def _ignore_symlinks(directory: str, names: list[str]) -> set[str]:
    base = Path(directory)
    return {
        name
        for name in names
        if name.casefold() == "backup" or (base / name).is_symlink()
    }


def _copy_world_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, ignore=_ignore_symlinks)


class ManagedWorldService:
    def __init__(
        self,
        profile: PalworldProfile,
        *,
        backup_service: Optional[BackupService] = None,
        save_profile_fn: Callable[[PalworldProfile], None] = save_profile,
        is_server_active: Callable[[], bool],
    ) -> None:
        self.profile = profile
        self.backup_service = backup_service or BackupService(profile)
        self.save_profile = save_profile_fn
        self.is_server_active = is_server_active

    @property
    def root(self) -> Path:
        return Path(self.profile.backup_source)

    def managed_worlds(self) -> list[WorldSaveInfo]:
        if not self.root.exists():
            return []
        return scan_dedicated_worlds(
            self.root,
            active_world_id=self.profile.dedicated_server_name,
        )

    def _select_import(
        self,
        source: str | Path,
        world_id: Optional[str],
    ) -> WorldSaveInfo:
        worlds = scan_dedicated_worlds(source)
        if world_id is not None:
            if DEDICATED_SERVER_NAME_RE.fullmatch(world_id) is None:
                raise ValueError("World ID must be 32 uppercase letters or digits")
            worlds = [world for world in worlds if world.world_id == world_id]
        if not worlds:
            raise ValueError(f"No dedicated {LEVEL_FILENAME} world found in {source}")
        if len(worlds) > 1:
            raise ValueError("More than one dedicated world was found; select a world ID")
        return worlds[0]

    def import_world(
        self,
        source: str | Path,
        *,
        world_id: Optional[str] = None,
        activate: bool = True,
    ) -> WorldImportResult:
        selected = self._select_import(source, world_id)
        if activate and self.is_server_active():
            raise RuntimeError("Stop the server before importing and activating a world")

        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / selected.world_id
        if target.exists():
            raise FileExistsError(f"Managed world already exists: {selected.world_id}")
        staging = self.root / f".import-{selected.world_id}-{uuid.uuid4().hex}.tmp"
        old_world_id = self.profile.dedicated_server_name
        moved = False
        profile_rollback_failed = False
        try:
            _copy_world_tree(selected.path, staging)
            level = staging / LEVEL_FILENAME
            if not level.is_file() or level.is_symlink():
                raise ValueError(f"Imported world is missing {LEVEL_FILENAME}")
            os.replace(staging, target)
            moved = True
            imported = _describe_world(
                target,
                active_world_id=(selected.world_id if activate else old_world_id),
            )
            if activate:
                self.profile.dedicated_server_name = selected.world_id
                try:
                    self.save_profile(self.profile)
                except Exception as save_error:
                    self.profile.dedicated_server_name = old_world_id
                    try:
                        self.save_profile(self.profile)
                    except Exception as rollback_error:
                        profile_rollback_failed = True
                        raise RuntimeError(
                            "World import activation failed and profile rollback also "
                            f"failed: {rollback_error}"
                        ) from save_error
                    raise save_error
            return WorldImportResult(
                world=imported,
                activated=activate,
            )
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            # If profile rollback failed, retain the completed world so a profile
            # which may have persisted the new ID never points at missing data.
            if moved and target.exists() and not profile_rollback_failed:
                shutil.rmtree(target)
            raise

    def switch_world(self, world_id: str) -> WorldSwitchResult:
        if DEDICATED_SERVER_NAME_RE.fullmatch(world_id) is None:
            raise ValueError("World ID must be 32 uppercase letters or digits")
        if self.is_server_active():
            raise RuntimeError("Stop the server before switching worlds")
        target = self.root / world_id
        if not _is_dedicated_world(target):
            raise FileNotFoundError(f"Managed world not found: {world_id}")
        previous = self.profile.dedicated_server_name
        if world_id == previous:
            raise ValueError("The selected world is already active")

        safety: BackupResult = self.backup_service.create_backup(
            enforce_retention=False
        )
        if safety.skipped or safety.path is None:
            raise RuntimeError("Safety backup could not be created; world was not switched")

        self.profile.dedicated_server_name = world_id
        try:
            self.save_profile(self.profile)
        except Exception as switch_error:
            self.profile.dedicated_server_name = previous
            try:
                self.save_profile(self.profile)
            except Exception as rollback_error:
                raise WorldSwitchError(
                    "World switch failed and the profile rollback also failed: "
                    f"{rollback_error}"
                ) from switch_error
            raise WorldSwitchError(f"World switch failed: {switch_error}") from switch_error

        return WorldSwitchResult(
            previous_world_id=previous,
            active_world=_describe_world(target, active_world_id=world_id),
            safety_backup=safety.path,
        )
