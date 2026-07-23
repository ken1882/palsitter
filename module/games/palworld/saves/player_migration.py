from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from module.games.palworld.backup import BackupService
from module.games.palworld.config import PalworldProfile


GUID_RE = re.compile(r"^[0-9A-Fa-f]{32}$")
PLAYER_NAME_CACHE_FILENAME = ".palsitter-player-names.json"
ProgressCallback = Callable[[str, str | None], None]


class PlayerMigrationError(RuntimeError):
    """A player migration was rejected or could not be completed."""


class PlayerMigrationUnavailable(PlayerMigrationError):
    """The optional current Palworld save codec is not installed."""


class PlayerNameCacheError(RuntimeError):
    """A player-name cache could not be created."""


@dataclass(frozen=True)
class PlayerMigrationResult:
    world_path: Path
    old_player_file: Path
    new_player_file: Path
    safety_backup: Path


@dataclass(frozen=True)
class PlayerNameCacheResult:
    world_path: Path
    cache_path: Path
    player_count: int


@dataclass(frozen=True)
class _SaveDocument:
    document: dict[str, Any]
    save_type: int


class _PalSavCodec:
    """Small adapter around the current Oodle-capable Palworld codec."""

    def __init__(self) -> None:
        try:
            from palsav.core import compress_gvas_to_sav, decompress_sav_to_gvas
            from palsav.gvas import GvasFile
            from palsav.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS
        except ImportError as exc:
            raise PlayerMigrationUnavailable(
                "The current Palworld save codec is unavailable; install the "
                "PalworldSaveTools Oodle dependencies."
            ) from exc
        self._compress = compress_gvas_to_sav
        self._decompress = decompress_sav_to_gvas
        self._gvas_file = GvasFile
        self._custom_properties = PALWORLD_CUSTOM_PROPERTIES
        self._type_hints = PALWORLD_TYPE_HINTS

    def read(self, path: Path) -> _SaveDocument:
        try:
            raw_gvas, save_type = self._decompress(path.read_bytes())
            gvas = self._gvas_file.read(
                raw_gvas,
                self._type_hints,
                self._custom_properties,
            )
        except Exception as exc:
            raise PlayerMigrationError(f"Could not decode {path.name}: {exc}") from exc
        return _SaveDocument(gvas.dump(), save_type)

    def encode(self, save: _SaveDocument) -> bytes:
        try:
            gvas = self._gvas_file.load(save.document)
            return self._compress(gvas.write(self._custom_properties), save.save_type)
        except Exception as exc:
            raise PlayerMigrationError(f"Could not encode player migration: {exc}") from exc


def _normal_guid(value: object) -> str:
    text = str(value).strip().replace("-", "")
    if not GUID_RE.fullmatch(text):
        raise PlayerMigrationError(f"Invalid player GUID: {value}")
    return text.lower()


def _guid_from_player_file(path: Path) -> str:
    return _normal_guid(path.stem)


def list_player_files(world_path: str | Path) -> list[Path]:
    players = Path(world_path) / "Players"
    if not players.is_dir() or players.is_symlink():
        return []
    return sorted(
        (
            path
            for path in players.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.casefold() == ".sav"
            and GUID_RE.fullmatch(path.stem) is not None
        ),
        key=lambda path: path.name.casefold(),
    )


def _find_player_file(world_path: Path, token: str) -> Path:
    guid = _normal_guid(Path(token).stem)
    for path in list_player_files(world_path):
        if _guid_from_player_file(path) == guid:
            return path
    raise PlayerMigrationError(f"Player save not found for GUID {guid}")


def _find_sidecar(players_path: Path, guid: str) -> Optional[Path]:
    expected = f"{guid}_dps.sav"
    return next(
        (
            path
            for path in players_path.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and path.name.casefold() == expected.casefold()
        ),
        None,
    )


def _player_uid(document: dict[str, Any]) -> object:
    try:
        return document["properties"]["SaveData"]["value"]["PlayerUId"]["value"]
    except (KeyError, TypeError) as exc:
        raise PlayerMigrationError("Player save does not contain SaveData.PlayerUId") from exc


def _player_storage_uid(document: dict[str, Any]) -> Optional[object]:
    try:
        storage = document["properties"]["SaveData"]["value"][
            "PalStorageContainerId"
        ]
        return storage["value"]["ID"]["value"]
    except (KeyError, TypeError):
        return None


def _is_uuid_like(value: object) -> bool:
    return isinstance(value, (str, uuid.UUID)) or (
        hasattr(value, "raw_bytes") and hasattr(type(value), "from_str")
    )


def _guid_matches(value: object, guid: str) -> bool:
    if not _is_uuid_like(value):
        return False
    try:
        return _normal_guid(value) == guid
    except PlayerMigrationError:
        return False


def _replace_like(value: object, guid: str) -> object:
    if isinstance(value, str):
        replacement = guid.upper() if value.isupper() else guid
        if "-" in value:
            return (
                f"{replacement[:8]}-{replacement[8:12]}-{replacement[12:16]}-"
                f"{replacement[16:20]}-{replacement[20:]}"
            )
        return replacement
    if isinstance(value, uuid.UUID):
        return uuid.UUID(guid)
    value_type = type(value)
    from_str = getattr(value_type, "from_str", None)
    if callable(from_str):
        return from_str(
            f"{guid[:8]}-{guid[8:12]}-{guid[12:16]}-{guid[16:20]}-{guid[20:]}"
        )
    return value


def _swap_guids(node: object, old_guid: str, new_guid: str) -> None:
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if _guid_matches(value, old_guid):
                node[key] = _replace_like(value, new_guid)
            elif _guid_matches(value, new_guid):
                node[key] = _replace_like(value, old_guid)
            else:
                _swap_guids(value, old_guid, new_guid)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if _guid_matches(value, old_guid):
                node[index] = _replace_like(value, new_guid)
            elif _guid_matches(value, new_guid):
                node[index] = _replace_like(value, old_guid)
            else:
                _swap_guids(value, old_guid, new_guid)


def _replace_guid(node: object, source_guid: str, target_guid: str) -> None:
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if _guid_matches(value, source_guid):
                node[key] = _replace_like(value, target_guid)
            else:
                _replace_guid(value, source_guid, target_guid)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if _guid_matches(value, source_guid):
                node[index] = _replace_like(value, target_guid)
            else:
                _replace_guid(value, source_guid, target_guid)


def _count_guid(node: object, guid: str) -> int:
    count = 1 if _guid_matches(node, guid) else 0
    if isinstance(node, dict):
        return count + sum(_count_guid(value, guid) for value in node.values())
    if isinstance(node, list):
        return count + sum(_count_guid(value, guid) for value in node)
    return count


def _world_path(profile: PalworldProfile) -> Path:
    world = Path(profile.backup_source) / profile.dedicated_server_name
    if not world.is_dir() or world.is_symlink():
        raise PlayerMigrationError(f"Active Palworld world not found: {world}")
    if not (world / "Level.sav").is_file() or (world / "Level.sav").is_symlink():
        raise PlayerMigrationError(f"Active world is missing Level.sav: {world}")
    return world


def player_name_cache_path(world_path: str | Path) -> Path:
    return Path(world_path) / PLAYER_NAME_CACHE_FILENAME


def load_player_name_cache(world_path: str | Path) -> dict[str, str]:
    path = player_name_cache_path(world_path)
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key.casefold(): value.strip()
        for key, value in data.items()
        if isinstance(key, str)
        and GUID_RE.fullmatch(key) is not None
        and isinstance(value, str)
        and value.strip()
    }


def _property_value(value: object) -> object:
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _extract_player_names(document: dict[str, Any]) -> dict[str, str]:
    try:
        character_map = document["properties"]["worldSaveData"]["value"][
            "CharacterSaveParameterMap"
        ]["value"]
    except (KeyError, TypeError):
        return {}
    if not isinstance(character_map, list):
        return {}

    names: dict[str, str] = {}
    for entry in character_map:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        try:
            player_guid = _normal_guid(_property_value(key["PlayerUId"]))
            save_parameter = entry["value"]["RawData"]["value"]["object"][
                "SaveParameter"
            ]["value"]
        except (KeyError, TypeError, PlayerMigrationError):
            continue
        if not isinstance(save_parameter, dict):
            continue
        if _property_value(save_parameter.get("IsPlayer")) is not True:
            continue
        name = _property_value(save_parameter.get("NickName"))
        if not isinstance(name, str) or not name.strip():
            name = _property_value(save_parameter.get("FilteredNickName"))
        if isinstance(name, str) and name.strip():
            names[player_guid] = name.strip()
    return names


def invalidate_player_name_cache(world_path: str | Path) -> None:
    path = player_name_cache_path(world_path)
    if path.is_file() and not path.is_symlink():
        path.unlink()


def build_player_name_cache(
    profile: PalworldProfile,
    *,
    is_server_active: Callable[[], bool],
    codec: Optional[_PalSavCodec] = None,
    progress: Optional[ProgressCallback] = None,
) -> PlayerNameCacheResult:
    if is_server_active():
        raise PlayerNameCacheError("Stop the server before building the player name cache")
    world = _world_path(profile)
    codec = codec or _PalSavCodec()

    def report(phase: str, filename: str | None = None) -> None:
        if progress is not None:
            progress(phase, filename)

    report("unpack", "Level.sav")
    level = codec.read(world / "Level.sav")
    report("extract")
    names = _extract_player_names(level.document)
    cache_path = player_name_cache_path(world)
    report("write", cache_path.name)
    temporary = cache_path.with_name(f".{cache_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(names, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(cache_path)
    except OSError as exc:
        raise PlayerNameCacheError(
            f"Could not write player name cache {cache_path.name}: {exc}"
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return PlayerNameCacheResult(world, cache_path, len(names))


def migrate_player_ids(
    profile: PalworldProfile,
    old_player: str,
    new_player: str,
    *,
    is_server_active: Callable[[], bool],
    backup_service: Optional[BackupService] = None,
    codec: Optional[_PalSavCodec] = None,
    progress: Optional[ProgressCallback] = None,
) -> PlayerMigrationResult:
    if is_server_active():
        raise PlayerMigrationError("Stop the server before migrating player IDs")
    old_guid = _normal_guid(Path(old_player).stem)
    new_guid = _normal_guid(Path(new_player).stem)
    if old_guid == new_guid:
        raise PlayerMigrationError("Select two different player saves")

    world = _world_path(profile)
    old_path = _find_player_file(world, old_player)
    new_path = _find_player_file(world, new_player)
    codec = codec or _PalSavCodec()

    def report(phase: str, filename: str | None = None) -> None:
        if progress is not None:
            progress(phase, filename)

    backup_service = backup_service or BackupService(profile)
    report("backup")
    safety = backup_service.create_backup(enforce_retention=False)
    if safety.skipped or safety.path is None:
        raise PlayerMigrationError("Safety backup could not be created")

    report("unpack", old_path.name)
    old_doc = codec.read(old_path)
    report("unpack", new_path.name)
    new_doc = codec.read(new_path)
    report("unpack", "Level.sav")
    level = codec.read(world / "Level.sav")
    if _normal_guid(_player_uid(old_doc.document)) != old_guid:
        raise PlayerMigrationError(
            f"{old_path.name} does not contain its filename GUID"
        )
    if _normal_guid(_player_uid(new_doc.document)) != new_guid:
        raise PlayerMigrationError(
            f"{new_path.name} does not contain its filename GUID"
        )
    if _count_guid(level.document, old_guid) == 0:
        raise PlayerMigrationError(f"Level.sav does not reference {old_path.name}")
    if _count_guid(level.document, new_guid) == 0:
        raise PlayerMigrationError(
            f"Level.sav does not reference {new_path.name}; have the player join "
            "once and create the new character first"
        )

    _swap_guids(level.document, old_guid, new_guid)
    _swap_guids(old_doc.document, old_guid, new_guid)
    _swap_guids(new_doc.document, old_guid, new_guid)

    players_path = world / "Players"
    old_dps = _find_sidecar(players_path, old_guid)
    new_dps = _find_sidecar(players_path, new_guid)
    old_storage = _player_storage_uid(old_doc.document)
    new_storage = _player_storage_uid(new_doc.document)
    dps_documents: dict[Path, _SaveDocument] = {}
    if old_dps is not None:
        report("unpack", old_dps.name)
        old_dps_doc = codec.read(old_dps)
        _swap_guids(old_dps_doc.document, old_guid, new_guid)
        if old_storage is not None and new_storage is not None:
            _replace_guid(
                old_dps_doc.document,
                _normal_guid(old_storage),
                _normal_guid(new_storage),
            )
        dps_documents[players_path / f"{new_guid.upper()}_dps.sav"] = old_dps_doc
    if new_dps is not None:
        report("unpack", new_dps.name)
        new_dps_doc = codec.read(new_dps)
        _swap_guids(new_dps_doc.document, old_guid, new_guid)
        if old_storage is not None and new_storage is not None:
            _replace_guid(
                new_dps_doc.document,
                _normal_guid(new_storage),
                _normal_guid(old_storage),
            )
        dps_documents[players_path / f"{old_guid.upper()}_dps.sav"] = new_dps_doc

    report("update")
    staged: dict[Path, bytes] = {}
    report("repack", "Level.sav")
    staged[world / "Level.sav"] = codec.encode(level)
    report("repack", old_path.name)
    staged[old_path] = codec.encode(new_doc)
    report("repack", new_path.name)
    staged[new_path] = codec.encode(old_doc)
    for target, document in dps_documents.items():
        report("repack", target.name)
        staged[target] = codec.encode(document)
    original = {
        path: path.read_bytes() if path.exists() else None
        for path in staged
    }
    transaction = world / f".player-migration-{uuid.uuid4().hex}.tmp"
    transaction.mkdir()
    try:
        temporary_files = {}
        for target, data in staged.items():
            temporary = transaction / target.name
            temporary.write_bytes(data)
            temporary_files[target] = temporary
        for target, temporary in temporary_files.items():
            temporary.replace(target)
    except Exception:
        for target, data in original.items():
            if data is None:
                if target.exists():
                    target.unlink()
            else:
                target.write_bytes(data)
        raise
    finally:
        shutil.rmtree(transaction, ignore_errors=True)

    invalidate_player_name_cache(world)

    return PlayerMigrationResult(world, old_path, new_path, safety.path)


__all__ = [
    "PlayerMigrationError",
    "PlayerMigrationResult",
    "PlayerMigrationUnavailable",
    "PlayerNameCacheError",
    "PlayerNameCacheResult",
    "PLAYER_NAME_CACHE_FILENAME",
    "build_player_name_cache",
    "invalidate_player_name_cache",
    "list_player_files",
    "load_player_name_cache",
    "migrate_player_ids",
    "player_name_cache_path",
]
