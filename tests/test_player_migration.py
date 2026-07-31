from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from module.config import Profile
from module.games.palworld.saves.player_migration import (
    PlayerMigrationDetails,
    PlayerMigrationError,
    _SaveDocument,
    build_player_name_cache,
    list_player_files,
    load_player_details_cache,
    load_player_name_cache,
    migrate_player_ids,
)


OLD = "00000000000000000000000000000001"
NEW = "8E910AC2000000000000000000000000"
WORLD = "A" * 32


class FakeCodec:
    def __init__(self, documents):
        self.documents = documents
        self.reads = []

    def read(self, path: Path):
        self.reads.append(path.name)
        return _SaveDocument(copy.deepcopy(self.documents[path.name]), 49)

    def encode(self, save):
        return json.dumps(save.document, sort_keys=True).encode()


class FailingEncodeCodec(FakeCodec):
    def encode(self, save):
        raise PlayerMigrationError("test encode failure")


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


def _documents_with_names():
    documents = _documents()
    documents["Level.sav"] = _level_with_names()
    return documents


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
        OLD.lower(): {"name": "Original", "owned_pal_count": 0},
        NEW.lower(): {"name": "New", "owned_pal_count": 0},
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


def test_build_player_name_cache_counts_owned_level_and_dps_pals(tmp_path):
    profile = _profile(tmp_path)
    world = Path(profile.backup_source) / WORLD
    players = world / "Players"
    players.mkdir(parents=True)
    (world / "Level.sav").write_bytes(b"level")
    (players / f"{OLD}.sav").write_bytes(b"old")
    (players / f"{NEW}.sav").write_bytes(b"new")
    (players / f"{OLD}_dps.sav").write_bytes(b"dps")
    old_level_pal = "10000000000000000000000000000001"
    old_duplicate = "10000000000000000000000000000002"
    new_level_pal = "20000000000000000000000000000001"
    guild_pal = "30000000000000000000000000000001"
    dps_pal = "40000000000000000000000000000001"

    def pal(owner, instance_id):
        return {
            "key": {
                "PlayerUId": {"value": owner},
                "InstanceId": {"value": instance_id},
            },
            "value": {
                "RawData": {
                    "value": {
                        "object": {
                            "SaveParameter": {
                                "value": {
                                    "IsPlayer": {"value": False},
                                    "OwnerPlayerUId": {"value": owner},
                                    "CharacterID": {"value": "SheepBall"},
                                }
                            }
                        }
                    }
                }
            },
        }

    level = _level_with_names()
    character_map = level["properties"]["worldSaveData"]["value"][
        "CharacterSaveParameterMap"
    ]["value"]
    character_map.extend(
        [
            pal(OLD, old_level_pal),
            pal(OLD, old_duplicate),
            pal(OLD, old_duplicate),
            pal(NEW, new_level_pal),
            pal("0" * 32, guild_pal),
        ]
    )
    dps = {
        "properties": {
            "SaveParameterArray": {
                "value": {
                    "values": [
                        {
                            "InstanceId": {
                                "value": {"InstanceId": {"value": dps_pal}}
                            },
                            "SaveParameter": {
                                "value": {"CharacterID": {"value": "PinkCat"}}
                            },
                        },
                        {
                            "InstanceId": {
                                "value": {"InstanceId": {"value": old_level_pal}}
                            },
                            "SaveParameter": {
                                "value": {"CharacterID": {"value": "SheepBall"}}
                            },
                        },
                        {
                            "InstanceId": {
                                "value": {
                                    "InstanceId": {
                                        "value": "50000000000000000000000000000001"
                                    }
                                }
                            },
                            "SaveParameter": {
                                "value": {"CharacterID": {"value": "None"}}
                            },
                        },
                    ]
                }
            }
        }
    }
    progress = []

    build_player_name_cache(
        profile,
        is_server_active=lambda: False,
        codec=FakeCodec(
            {
                "Level.sav": level,
                f"{OLD}_dps.sav": dps,
            }
        ),
        progress=lambda phase, filename: progress.append((phase, filename)),
    )

    details = load_player_details_cache(world)
    assert details[OLD.lower()].name == "Original"
    assert details[OLD.lower()].owned_pal_count == 3
    assert details[NEW.lower()].owned_pal_count == 1
    assert "0" * 32 not in details
    assert progress == [
        ("unpack", "Level.sav"),
        ("extract", None),
        ("unpack", f"{OLD}_dps.sav"),
        ("write", ".palsitter-player-names.json"),
    ]


def test_load_player_details_cache_accepts_legacy_name_values(tmp_path):
    (tmp_path / ".palsitter-player-names.json").write_text(
        json.dumps({OLD: "Original"}),
        encoding="utf-8",
    )

    details = load_player_details_cache(tmp_path)

    assert details[OLD.lower()].name == "Original"
    assert details[OLD.lower()].owned_pal_count is None
    assert load_player_name_cache(tmp_path) == {OLD.lower(): "Original"}


def test_migrate_player_ids_creates_backup_and_swaps_documents(tmp_path):
    profile = _profile(tmp_path)
    world = Path(profile.backup_source) / WORLD
    (world / "Players").mkdir(parents=True)
    (world / "Level.sav").write_bytes(b"level")
    (world / "Players" / f"{OLD}.sav").write_bytes(b"old")
    (world / "Players" / f"{NEW}.sav").write_bytes(b"new")
    (world / ".palsitter-player-names.json").write_text("{}", encoding="utf-8")
    codec = FakeCodec(_documents_with_names())
    progress = []

    result = migrate_player_ids(
        profile,
        f"{OLD}.sav",
        f"{NEW}.sav",
        is_server_active=lambda: False,
        backup_service=FakeBackup(tmp_path / "safety.zip"),
        codec=codec,
        progress=lambda phase, filename: progress.append((phase, filename)),
        confirm_name_mismatch=lambda *values: True,
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
        ("cache", None),
        ("update", None),
        ("repack", "Level.sav"),
        ("repack", f"{OLD}.sav"),
        ("repack", f"{NEW}.sav"),
    ]
    assert load_player_details_cache(world) == {
        OLD.lower(): PlayerMigrationDetails("New", 0),
        NEW.lower(): PlayerMigrationDetails("Original", 0),
    }
    assert codec.reads == [f"{OLD}.sav", f"{NEW}.sav", "Level.sav"]


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


def test_migrate_player_ids_keeps_refreshed_cache_when_repack_fails(tmp_path):
    profile = _profile(tmp_path)
    world = Path(profile.backup_source) / WORLD
    players = world / "Players"
    players.mkdir(parents=True)
    (world / "Level.sav").write_bytes(b"level")
    (players / f"{OLD}.sav").write_bytes(b"old")
    (players / f"{NEW}.sav").write_bytes(b"new")

    with pytest.raises(PlayerMigrationError, match="test encode failure"):
        migrate_player_ids(
            profile,
            f"{OLD}.sav",
            f"{NEW}.sav",
            is_server_active=lambda: False,
            backup_service=FakeBackup(tmp_path / "safety.zip"),
            codec=FailingEncodeCodec(_documents_with_names()),
            confirm_name_mismatch=lambda *values: True,
        )

    assert load_player_details_cache(world) == {
        OLD.lower(): PlayerMigrationDetails("Original", 0),
        NEW.lower(): PlayerMigrationDetails("New", 0),
    }
    assert (players / f"{OLD}.sav").read_bytes() == b"old"
    assert (players / f"{NEW}.sav").read_bytes() == b"new"


def test_migrate_player_ids_confirms_level_names_before_modifying(tmp_path):
    profile = _profile(tmp_path)
    world = Path(profile.backup_source) / WORLD
    (world / "Players").mkdir(parents=True)
    (world / "Level.sav").write_bytes(b"level")
    (world / "Players" / f"{OLD}.sav").write_bytes(b"old")
    (world / "Players" / f"{NEW}.sav").write_bytes(b"new")
    old_bytes = (world / "Players" / f"{OLD}.sav").read_bytes()
    new_bytes = (world / "Players" / f"{NEW}.sav").read_bytes()
    confirmation = []

    with pytest.raises(PlayerMigrationError, match="names do not match"):
        migrate_player_ids(
            profile,
            f"{OLD}.sav",
            f"{NEW}.sav",
            is_server_active=lambda: False,
            backup_service=FakeBackup(tmp_path / "safety.zip"),
            codec=FakeCodec(_documents_with_names()),
            expected_names={OLD: "Stale source", NEW: "New"},
            confirm_name_mismatch=lambda *values: confirmation.append(values) or False,
        )

    assert confirmation == [("Stale source", "Original", "New", "New")]
    assert (world / "Players" / f"{OLD}.sav").read_bytes() == old_bytes
    assert (world / "Players" / f"{NEW}.sav").read_bytes() == new_bytes
    assert load_player_details_cache(world) == {
        OLD.lower(): PlayerMigrationDetails("Original", 0),
        NEW.lower(): PlayerMigrationDetails("New", 0),
    }


def test_migrate_player_ids_confirms_when_source_and_destination_names_differ(tmp_path):
    profile = _profile(tmp_path)
    world = Path(profile.backup_source) / WORLD
    (world / "Players").mkdir(parents=True)
    (world / "Level.sav").write_bytes(b"level")
    (world / "Players" / f"{OLD}.sav").write_bytes(b"old")
    (world / "Players" / f"{NEW}.sav").write_bytes(b"new")
    confirmation = []

    with pytest.raises(PlayerMigrationError, match="names do not match"):
        migrate_player_ids(
            profile,
            f"{OLD}.sav",
            f"{NEW}.sav",
            is_server_active=lambda: False,
            backup_service=FakeBackup(tmp_path / "safety.zip"),
            codec=FakeCodec(_documents_with_names()),
            expected_names={OLD: "Original", NEW: "New"},
            confirm_name_mismatch=lambda *values: confirmation.append(values) or False,
        )

    assert confirmation == [("Original", "Original", "New", "New")]


def test_migrate_player_ids_confirms_destination_dps_count_after_name_check(
    tmp_path,
):
    profile = _profile(tmp_path)
    world = Path(profile.backup_source) / WORLD
    players = world / "Players"
    players.mkdir(parents=True)
    (world / "Level.sav").write_bytes(b"level")
    (players / f"{OLD}.sav").write_bytes(b"old")
    (players / f"{NEW}.sav").write_bytes(b"new")
    (players / f"{NEW}_dps.sav").write_bytes(b"dps")
    original_level = (world / "Level.sav").read_bytes()
    dps_pal = "40000000000000000000000000000001"
    documents = _documents_with_names()
    documents[f"{NEW}_dps.sav"] = {
        "properties": {
            "SaveParameterArray": {
                "value": {
                    "values": [
                        {
                            "InstanceId": {
                                "value": {"InstanceId": {"value": dps_pal}}
                            },
                            "SaveParameter": {
                                "value": {"CharacterID": {"value": "PinkCat"}}
                            },
                        }
                    ]
                }
            }
        }
    }
    confirmations = []

    def confirm_names(*values):
        confirmations.append(("names", *values))
        return True

    def confirm_pal_counts(source, destination):
        confirmations.append(("pals", source, destination))
        return False

    codec = FakeCodec(documents)
    with pytest.raises(PlayerMigrationError, match="owns more Pals"):
        migrate_player_ids(
            profile,
            f"{OLD}.sav",
            f"{NEW}.sav",
            is_server_active=lambda: False,
            backup_service=FakeBackup(tmp_path / "safety.zip"),
            codec=codec,
            expected_names={OLD: "Original", NEW: "New"},
            confirm_name_mismatch=confirm_names,
            confirm_destination_pal_count=confirm_pal_counts,
        )

    assert confirmations == [
        ("names", "Original", "Original", "New", "New"),
        ("pals", 0, 1),
    ]
    assert (world / "Level.sav").read_bytes() == original_level
    assert (players / f"{OLD}.sav").read_bytes() == b"old"
    assert (players / f"{NEW}.sav").read_bytes() == b"new"
    assert load_player_details_cache(world) == {
        OLD.lower(): PlayerMigrationDetails("Original", 0),
        NEW.lower(): PlayerMigrationDetails("New", 1),
    }
    assert codec.reads.count(f"{NEW}_dps.sav") == 1
