from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from module.config import Profile
from module.games.palworld.saves.player_migration import (
    PlayerMigrationError,
    _SaveDocument,
    build_player_name_cache,
    list_player_files,
    load_player_name_cache,
    migrate_player_ids,
)


OLD = "00000000000000000000000000000001"
NEW = "8E910AC2000000000000000000000000"
WORLD = "A" * 32


class FakeCodec:
    def __init__(self, documents):
        self.documents = documents

    def read(self, path: Path):
        return _SaveDocument(copy.deepcopy(self.documents[path.name]), 49)

    def encode(self, save):
        return json.dumps(save.document, sort_keys=True).encode()


class FakeBackup:
    def __init__(self, path: Path):
        self.path = path

    def create_backup(self, **kwargs):
        self.path.write_bytes(b"safety backup")
        return type("BackupResult", (), {"skipped": False, "path": self.path})()


def _profile(tmp_path: Path) -> Profile:
    return Profile(
        name="test",
        backup_source=str(tmp_path / "managed"),
        backup_dir=str(tmp_path / "backups"),
        dedicated_server_name=WORLD,
    )


def _documents():
    def player(uid):
        return {
            "properties": {
                "SaveData": {
                    "value": {
                        "PlayerUId": {"value": uid},
                        "IndividualId": {"value": {"PlayerUId": {"value": uid}}},
                    }
                }
            }
        }

    return {
        "Level.sav": {"properties": {"world": {"players": [OLD, NEW]}}},
        f"{OLD}.sav": player(OLD),
        f"{NEW}.sav": player(NEW),
    }


def _level_with_names():
    def entry(uid, name):
        return {
            "key": {"PlayerUId": {"value": uid}},
            "value": {
                "RawData": {
                    "value": {
                        "object": {
                            "SaveParameter": {
                                "value": {
                                    "IsPlayer": {"value": True},
                                    "NickName": {"value": name},
                                }
                            }
                        }
                    }
                }
            },
        }

    return {
        "properties": {
            "worldSaveData": {
                "value": {
                    "CharacterSaveParameterMap": {
                        "value": [entry(OLD, "Original"), entry(NEW, "New")]
                    }
                }
            }
        }
    }


def test_list_player_files_excludes_dps_and_invalid_names(tmp_path):
    players = tmp_path / "Players"
    players.mkdir()
    (players / f"{OLD}.sav").write_bytes(b"old")
    (players / f"{NEW}_dps.sav").write_bytes(b"dps")
    (players / "not-a-guid.sav").write_bytes(b"bad")

    assert [path.name for path in list_player_files(tmp_path)] == [f"{OLD}.sav"]


def test_build_player_name_cache_extracts_names_and_reports_progress(tmp_path):
    profile = _profile(tmp_path)
    world = Path(profile.backup_source) / WORLD
    world.mkdir(parents=True)
    (world / "Level.sav").write_bytes(b"level")
    progress = []

    result = build_player_name_cache(
        profile,
        is_server_active=lambda: False,
        codec=FakeCodec({"Level.sav": _level_with_names()}),
        progress=lambda phase, filename: progress.append((phase, filename)),
    )

    assert result.cache_path == world / ".palsitter-player-names.json"
    assert json.loads(result.cache_path.read_text(encoding="utf-8")) == {
        OLD.lower(): "Original",
        NEW.lower(): "New",
    }
    assert load_player_name_cache(world) == {
        OLD.lower(): "Original",
        NEW.lower(): "New",
    }
    assert progress == [
        ("unpack", "Level.sav"),
        ("extract", None),
        ("write", ".palsitter-player-names.json"),
    ]


def test_migrate_player_ids_creates_backup_and_swaps_documents(tmp_path):
    profile = _profile(tmp_path)
    world = Path(profile.backup_source) / WORLD
    (world / "Players").mkdir(parents=True)
    (world / "Level.sav").write_bytes(b"level")
    (world / "Players" / f"{OLD}.sav").write_bytes(b"old")
    (world / "Players" / f"{NEW}.sav").write_bytes(b"new")
    (world / ".palsitter-player-names.json").write_text("{}", encoding="utf-8")
    codec = FakeCodec(_documents())
    progress = []

    result = migrate_player_ids(
        profile,
        f"{OLD}.sav",
        f"{NEW}.sav",
        is_server_active=lambda: False,
        backup_service=FakeBackup(tmp_path / "safety.zip"),
        codec=codec,
        progress=lambda phase, filename: progress.append((phase, filename)),
    )

    assert result.safety_backup == tmp_path / "safety.zip"
    assert json.loads((world / "Players" / f"{NEW}.sav").read_bytes())[
        "properties"
    ]["SaveData"]["value"]["PlayerUId"]["value"] == NEW.lower()
    assert json.loads((world / "Players" / f"{OLD}.sav").read_bytes())[
        "properties"
    ]["SaveData"]["value"]["PlayerUId"]["value"] == OLD.lower()
    assert progress == [
        ("backup", None),
        ("unpack", f"{OLD}.sav"),
        ("unpack", f"{NEW}.sav"),
        ("unpack", "Level.sav"),
        ("update", None),
        ("repack", "Level.sav"),
        ("repack", f"{OLD}.sav"),
        ("repack", f"{NEW}.sav"),
    ]
    assert not (world / ".palsitter-player-names.json").exists()


def test_migrate_player_ids_requires_stopped_server(tmp_path):
    profile = _profile(tmp_path)
    world = Path(profile.backup_source) / WORLD
    (world / "Players").mkdir(parents=True)
    (world / "Level.sav").write_bytes(b"level")
    (world / "Players" / f"{OLD}.sav").write_bytes(b"old")
    (world / "Players" / f"{NEW}.sav").write_bytes(b"new")

    with pytest.raises(PlayerMigrationError, match="Stop the server"):
        migrate_player_ids(
            profile,
            OLD,
            NEW,
            is_server_active=lambda: True,
            codec=FakeCodec(_documents()),
        )
