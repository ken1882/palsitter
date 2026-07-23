from pathlib import Path

import pytest

from module.config import Profile
from module.games.palworld.backup import BackupResult, BackupService
from module.games.palworld.saves import (
    ManagedWorldService,
    find_import_world_settings_ini,
    import_world_settings_ini,
    inspect_import_source,
    scan_dedicated_worlds,
)


WORLD_A = "A1" * 16
WORLD_B = "B2" * 16


def _world(root: Path, world_id: str, *, players: int = 0) -> Path:
    path = root / world_id
    path.mkdir(parents=True)
    (path / "Level.sav").write_bytes(b"level")
    for index in range(players):
        player = path / "Players" / f"{index:032X}.sav"
        player.parent.mkdir(exist_ok=True)
        player.write_bytes(b"player")
    return path


def _profile(tmp_path, active=WORLD_A) -> Profile:
    return Profile(
        name="test",
        backup_source=str(tmp_path / "managed"),
        backup_dir=str(tmp_path / "backups"),
        dedicated_server_name=active,
    )


def test_scan_dedicated_worlds_supports_parent_and_direct_world(tmp_path):
    root = tmp_path / "source"
    first = _world(root, WORLD_A, players=2)
    _world(root, WORLD_B)
    invalid = root / "not-a-dedicated-id"
    invalid.mkdir()
    (invalid / "Level.sav").write_bytes(b"ignored")

    worlds = scan_dedicated_worlds(root, active_world_id=WORLD_B)

    assert [world.world_id for world in worlds] == [WORLD_A, WORLD_B]
    assert worlds[0].size_bytes == len(b"level") + 2 * len(b"player")
    assert worlds[0].player_save_count == 2
    assert worlds[0].active is False
    assert worlds[1].active is True
    assert scan_dedicated_worlds(first)[0].world_id == WORLD_A


def test_scan_dedicated_worlds_ignores_symlinked_worlds_and_files(tmp_path):
    source = tmp_path / "source"
    world = _world(source, WORLD_A)
    outside = tmp_path / "outside.sav"
    outside.write_bytes(b"outside")
    try:
        (world / "linked.sav").symlink_to(outside)
        (source / WORLD_B).symlink_to(world, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")

    worlds = scan_dedicated_worlds(source)

    assert [item.world_id for item in worlds] == [WORLD_A]
    assert worlds[0].size_bytes == len(b"level")


def test_import_world_uses_staging_preserves_source_and_activates(tmp_path):
    source_root = tmp_path / "source"
    source = _world(source_root, WORLD_B, players=1)
    (source / "WorldOption.sav").write_bytes(b"world options")
    (source / "backup" / "world" / "old" / "Level.sav").parent.mkdir(parents=True)
    (source / "backup" / "world" / "old" / "Level.sav").write_bytes(b"old level")
    source_snapshot = {
        path.relative_to(source): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    }
    profile = _profile(tmp_path)
    saved_ids = []
    service = ManagedWorldService(
        profile,
        save_profile_fn=lambda value: saved_ids.append(value.dedicated_server_name),
        is_server_active=lambda: False,
    )

    result = service.import_world(source)

    target = Path(profile.backup_source) / WORLD_B
    assert result.activated is True
    assert result.world.path == target
    assert result.world.active is True
    assert profile.dedicated_server_name == WORLD_B
    assert saved_ids == [WORLD_B]
    assert (target / "Level.sav").read_bytes() == b"level"
    assert (target / "WorldOption.sav").read_bytes() == b"world options"
    assert not (target / "backup").exists()
    assert {
        path.relative_to(source): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    } == source_snapshot
    assert list(Path(profile.backup_source).glob(".import-*.tmp")) == []


def test_import_world_settings_ini_uses_windows_or_linux_server_relative_path(
    tmp_path, monkeypatch
):
    server_root = tmp_path / "source-server"
    level = server_root / "Pal" / "Saved" / "SaveGames" / "0" / WORLD_B / "Level.sav"
    level.parent.mkdir(parents=True)
    level.write_bytes(b"level")
    linux_ini = server_root / "Pal" / "Saved" / "Config" / "LinuxServer" / "PalWorldSettings.ini"
    linux_ini.parent.mkdir(parents=True)
    linux_ini.write_text("linux", encoding="utf-8")
    profile = _profile(tmp_path)

    monkeypatch.setattr("module.games.palworld.config.WINDOWS", False)

    assert find_import_world_settings_ini(level) == linux_ini
    target = import_world_settings_ini(profile, level)

    assert target == (
        Path(profile.workdir)
        / "Pal"
        / "Saved"
        / "Config"
        / "LinuxServer"
        / "PalWorldSettings.ini"
    )
    assert target.read_text(encoding="utf-8") == "linux"


@pytest.mark.parametrize(
    ("save_root_name", "players", "expected"),
    [
        ("76561198170852193", ["00000000000000000000000000000001.sav"], "single_player"),
        ("76561198170852193", ["00000000000000000000000000000001.sav", f"{'A' * 32}.sav"], "coop"),
        ("0", ["76561198170852193.sav"], "dedicated"),
    ],
)
def test_inspect_import_source_classifies_local_and_dedicated_layouts(
    tmp_path, save_root_name, players, expected
):
    world = tmp_path / "Pal" / "Saved" / "SaveGames" / save_root_name / WORLD_B
    (world / "Players").mkdir(parents=True)
    (world / "Level.sav").write_bytes(b"level")
    for player in players:
        (world / "Players" / player).write_bytes(b"player")

    info = inspect_import_source(world / "Level.sav")

    assert info.kind == expected
    assert info.world.world_id == WORLD_B
    assert info.missing_files == ("LevelMeta.sav", "LocalData.sav")


def test_import_world_requires_selection_for_multiple_candidates(tmp_path):
    source = tmp_path / "source"
    _world(source, WORLD_A)
    _world(source, WORLD_B)
    profile = _profile(tmp_path)
    service = ManagedWorldService(
        profile,
        save_profile_fn=lambda _: None,
        is_server_active=lambda: False,
    )

    with pytest.raises(ValueError, match="select a world ID"):
        service.import_world(source)

    result = service.import_world(source, world_id=WORLD_B)
    assert result.world.world_id == WORLD_B


def test_import_world_rejects_collision_without_modifying_source(tmp_path):
    source = _world(tmp_path / "source", WORLD_B)
    profile = _profile(tmp_path)
    existing = _world(Path(profile.backup_source), WORLD_B)
    (existing / "Level.sav").write_bytes(b"managed")
    service = ManagedWorldService(
        profile,
        save_profile_fn=lambda _: None,
        is_server_active=lambda: False,
    )

    with pytest.raises(FileExistsError):
        service.import_world(source)

    assert (source / "Level.sav").read_bytes() == b"level"
    assert (existing / "Level.sav").read_bytes() == b"managed"


def test_import_world_cleans_staging_and_target_when_profile_save_fails(tmp_path):
    source = _world(tmp_path / "source", WORLD_B)
    profile = _profile(tmp_path)
    calls = 0

    def save(value):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("profile write failed")

    service = ManagedWorldService(
        profile,
        save_profile_fn=save,
        is_server_active=lambda: False,
    )

    with pytest.raises(OSError, match="profile write failed"):
        service.import_world(source)

    assert profile.dedicated_server_name == WORLD_A
    assert not (Path(profile.backup_source) / WORLD_B).exists()
    assert list(Path(profile.backup_source).glob(".import-*.tmp")) == []


def test_switch_world_creates_mandatory_safety_backup_then_persists(tmp_path):
    profile = _profile(tmp_path)
    root = Path(profile.backup_source)
    _world(root, WORLD_A)
    _world(root, WORLD_B)
    saved_ids = []
    backups = BackupService(profile, logger=lambda _: None)
    service = ManagedWorldService(
        profile,
        backup_service=backups,
        save_profile_fn=lambda value: saved_ids.append(value.dedicated_server_name),
        is_server_active=lambda: False,
    )

    result = service.switch_world(WORLD_B)

    assert result.previous_world_id == WORLD_A
    assert result.active_world.world_id == WORLD_B
    assert result.active_world.active is True
    assert result.safety_backup.is_file()
    assert profile.dedicated_server_name == WORLD_B
    assert saved_ids == [WORLD_B]


def test_switch_world_rejects_active_server_before_backup(tmp_path):
    profile = _profile(tmp_path)
    _world(Path(profile.backup_source), WORLD_B)

    class NoBackup:
        def create_backup(self, **kwargs):
            raise AssertionError("backup must not run")

    service = ManagedWorldService(
        profile,
        backup_service=NoBackup(),
        save_profile_fn=lambda _: None,
        is_server_active=lambda: True,
    )

    with pytest.raises(RuntimeError, match="Stop the server"):
        service.switch_world(WORLD_B)


def test_switch_world_rolls_profile_back_when_persist_fails(tmp_path):
    profile = _profile(tmp_path)
    root = Path(profile.backup_source)
    _world(root, WORLD_A)
    _world(root, WORLD_B)
    backup = tmp_path / "safety.zip"
    backup.write_bytes(b"zip")
    saved_ids = []

    class FakeBackup:
        def create_backup(self, **kwargs):
            return BackupResult(backup, 2, [])

    def save(value):
        saved_ids.append(value.dedicated_server_name)
        if len(saved_ids) == 1:
            raise OSError("sync failed")

    service = ManagedWorldService(
        profile,
        backup_service=FakeBackup(),
        save_profile_fn=save,
        is_server_active=lambda: False,
    )

    with pytest.raises(RuntimeError, match="World switch failed"):
        service.switch_world(WORLD_B)

    assert profile.dedicated_server_name == WORLD_A
    assert saved_ids == [WORLD_B, WORLD_A]


def test_switch_world_aborts_when_safety_backup_is_skipped(tmp_path):
    profile = _profile(tmp_path)
    _world(Path(profile.backup_source), WORLD_B)

    class SkippedBackup:
        def create_backup(self, **kwargs):
            return BackupResult(None, 0, [], skipped=True)

    service = ManagedWorldService(
        profile,
        backup_service=SkippedBackup(),
        save_profile_fn=lambda _: pytest.fail("profile must not be saved"),
        is_server_active=lambda: False,
    )

    with pytest.raises(RuntimeError, match="Safety backup"):
        service.switch_world(WORLD_B)

    assert profile.dedicated_server_name == WORLD_A
