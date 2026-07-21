import datetime as dt
import os
import zipfile

from module.backup import BackupService
from module.config import Profile


def test_backup_includes_entire_world_and_excludes_builtin_backups(tmp_path):
    source = tmp_path / "saves"
    save = source / "ABC123"
    player = save / "Players" / "001.sav"
    player.parent.mkdir(parents=True)
    nested_backup = save / "backup" / "local" / "2026.01.02-03.04.05"
    nested_backup.mkdir(parents=True)
    (save / "Level.sav").write_text("world", encoding="utf-8")
    (save / "LevelMeta.sav").write_text("metadata", encoding="utf-8")
    player.write_text("player", encoding="utf-8")
    (save / "notes.txt").write_text("other world data", encoding="utf-8")
    (nested_backup / "old.sav").write_text("old", encoding="utf-8")
    (source / "not-a-save").mkdir()

    profile = Profile(
        name="test",
        backup_source=str(source),
        backup_dir=str(tmp_path / "backups"),
        backup_retention_count=10,
    )

    result = BackupService(profile, logger=lambda _: None).create_backup(
        dt.datetime(2026, 1, 2, 3, 4, 5)
    )

    assert result.files_added == 4
    with zipfile.ZipFile(result.path) as archive:
        assert sorted(archive.namelist()) == [
            "ABC123/Level.sav",
            "ABC123/LevelMeta.sav",
            "ABC123/Players/001.sav",
            "ABC123/notes.txt",
        ]


def test_list_builtin_backups_only_for_active_world_and_world_type(tmp_path):
    source = tmp_path / "saves"
    active_id = "A" * 32
    inactive_id = "B" * 32
    active_world = source / active_id / "backup" / "world" / "2026.01.03-04.05.06"
    active_local = source / active_id / "backup" / "local" / "2026.01.02-03.04.05"
    inactive_world = source / inactive_id / "backup" / "world" / "2026.01.04-05.06.07"
    active_world.mkdir(parents=True)
    active_local.mkdir(parents=True)
    inactive_world.mkdir(parents=True)
    (active_world / "Level.sav").write_text("world data", encoding="utf-8")
    (active_local / "LocalData.sav").write_text("local", encoding="utf-8")
    (inactive_world / "Level.sav").write_text("inactive", encoding="utf-8")

    profile = Profile(
        name="test",
        backup_source=str(source),
        dedicated_server_name=active_id,
    )
    backups = BackupService(profile, logger=lambda _: None).list_builtin_backups()

    assert [(backup.world_id, backup.category, backup.name) for backup in backups] == [
        (active_id, "world", "2026.01.03-04.05.06")
    ]
    assert backups[0].size_bytes == 10


def test_list_builtin_backups_returns_empty_for_missing_save_root(tmp_path):
    profile = Profile(name="test", backup_source=str(tmp_path / "missing"))

    assert BackupService(profile).list_builtin_backups() == []


def test_backup_skips_when_no_save_files_exist(tmp_path):
    source = tmp_path / "saves"
    source.mkdir()
    backup_dir = tmp_path / "backups"
    logged = []

    profile = Profile(
        name="test",
        backup_source=str(source),
        backup_dir=str(backup_dir),
    )

    result = BackupService(profile, logger=logged.append).create_backup(
        dt.datetime(2026, 1, 2, 3, 4, 5)
    )

    assert result.skipped is True
    assert result.path is None
    assert result.files_added == 0
    assert result.deleted == []
    assert not backup_dir.exists()
    assert logged == [f"Backup skipped: no save files found in {source}"]


def test_backup_skips_when_only_nested_backup_files_exist(tmp_path):
    source = tmp_path / "saves"
    nested_backup = source / "ABC123" / "backup"
    nested_backup.mkdir(parents=True)
    (nested_backup / "old.sav").write_text("old", encoding="utf-8")
    backup_dir = tmp_path / "backups"

    profile = Profile(
        name="test",
        backup_source=str(source),
        backup_dir=str(backup_dir),
    )

    result = BackupService(profile, logger=lambda _: None).create_backup(
        dt.datetime(2026, 1, 2, 3, 4, 5)
    )

    assert result.skipped is True
    assert list(backup_dir.glob("*.zip")) == []


def test_backup_retention_deletes_oldest(tmp_path):
    source = tmp_path / "saves" / "A"
    source.mkdir(parents=True)
    (source / "Level.sav").write_text("world", encoding="utf-8")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old = backup_dir / "2026.01.01.000000.zip"
    older = backup_dir / "2025.01.01.000000.zip"
    old.write_text("old", encoding="utf-8")
    older.write_text("older", encoding="utf-8")

    profile = Profile(
        name="test",
        backup_source=str(tmp_path / "saves"),
        backup_dir=str(backup_dir),
        backup_retention_count=2,
    )

    result = BackupService(profile, logger=lambda _: None).create_backup(
        dt.datetime(2026, 1, 2, 0, 0, 0)
    )

    assert len(result.deleted) == 1
    assert not older.exists()
    assert old.exists()


def test_backup_before_picks_most_recent_backup_strictly_before_cutoff(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    early = backup_dir / "2026.01.01.000000.zip"
    late = backup_dir / "2026.01.02.000000.zip"
    early.write_text("early", encoding="utf-8")
    late.write_text("late", encoding="utf-8")

    early_ts = dt.datetime(2026, 1, 1).timestamp()
    late_ts = dt.datetime(2026, 1, 2).timestamp()
    os.utime(early, (early_ts, early_ts))
    os.utime(late, (late_ts, late_ts))

    profile = Profile(name="test", backup_dir=str(backup_dir))
    service = BackupService(profile, logger=lambda _: None)

    assert service.backup_before(dt.datetime(2026, 1, 1, 12, 0, 0)) == early
    assert service.backup_before(dt.datetime(2026, 1, 2, 0, 0, 0)) == early
    assert service.backup_before(dt.datetime(2026, 1, 3, 0, 0, 0)) == late
    assert service.backup_before(dt.datetime(2025, 12, 31, 0, 0, 0)) is None


def test_backup_before_returns_none_when_backup_dir_missing(tmp_path):
    profile = Profile(name="test", backup_dir=str(tmp_path / "missing"))
    service = BackupService(profile, logger=lambda _: None)

    assert service.backup_before(dt.datetime(2026, 1, 1)) is None


def test_restore_extracts_backup_into_backup_source(tmp_path):
    source = tmp_path / "saves"
    save = source / "ABC123"
    save.mkdir(parents=True)
    (save / "Level.sav").write_text("original", encoding="utf-8")

    backup_dir = tmp_path / "backups"
    profile = Profile(name="test", backup_source=str(source), backup_dir=str(backup_dir))
    service = BackupService(profile, logger=lambda _: None)
    result = service.create_backup(dt.datetime(2026, 1, 1))

    (save / "Level.sav").write_text("corrupted", encoding="utf-8")

    service.restore(result.path)

    assert (save / "Level.sav").read_text(encoding="utf-8") == "original"


def test_restore_builtin_backup_into_active_world(tmp_path):
    source = tmp_path / "saves"
    active_id = "A" * 32
    world = source / active_id
    snapshot = world / "backup" / "world" / "2026.01.03-04.05.06"
    snapshot.mkdir(parents=True)
    (world / "Level.sav").write_text("current", encoding="utf-8")
    (snapshot / "Level.sav").write_text("restored", encoding="utf-8")
    (snapshot / "Players" / "001.sav").parent.mkdir()
    (snapshot / "Players" / "001.sav").write_text("player", encoding="utf-8")
    profile = Profile(
        name="test",
        backup_source=str(source),
        dedicated_server_name=active_id,
    )

    BackupService(profile, logger=lambda _: None).restore(snapshot)

    assert (world / "Level.sav").read_text(encoding="utf-8") == "restored"
    assert (world / "Players" / "001.sav").read_text(encoding="utf-8") == "player"


def test_restore_builtin_backup_rejects_inactive_world(tmp_path):
    source = tmp_path / "saves"
    active_id = "A" * 32
    snapshot = source / ("B" * 32) / "backup" / "world" / "2026.01.03"
    snapshot.mkdir(parents=True)
    (snapshot / "Level.sav").write_text("other world", encoding="utf-8")
    profile = Profile(
        name="test",
        backup_source=str(source),
        dedicated_server_name=active_id,
    )

    try:
        BackupService(profile).restore(snapshot)
    except ValueError as exc:
        assert "active world's backup/world" in str(exc)
    else:
        raise AssertionError("an inactive world's built-in backup should be rejected")


class FakeEvent:
    def __init__(self, trigger_after):
        self.calls = 0
        self.trigger_after = trigger_after
        self.waits = []

    def is_set(self):
        return self.calls >= self.trigger_after

    def wait(self, timeout):
        self.waits.append(timeout)
        self.calls += 1
        return False


def test_scheduled_loop_runs_until_stopped(tmp_path):
    source = tmp_path / "saves" / "A"
    source.mkdir(parents=True)
    (source / "Level.sav").write_text("world", encoding="utf-8")

    profile = Profile(
        name="test",
        backup_source=str(tmp_path / "saves"),
        backup_dir=str(tmp_path / "backups"),
        backup_interval_minutes=0.7,
    )
    calls = []
    service = BackupService(profile, logger=lambda _: None)
    service.create_backup = lambda: calls.append(1)
    service._stop_event = FakeEvent(2)

    service.scheduled_loop()

    assert len(calls) == 2
    assert service._stop_event.waits == [42, 42]


def test_scheduled_loop_backs_off_then_recovers_full_interval(tmp_path):
    profile = Profile(
        name="test",
        backup_source=str(tmp_path / "saves"),
        backup_dir=str(tmp_path / "backups"),
        backup_interval_minutes=1.65,
    )
    logged = []
    service = BackupService(profile, logger=logged.append)
    service.create_backup = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    service._stop_event = FakeEvent(6)

    service.scheduled_loop()

    assert service._stop_event.waits == [99, 10, 10, 10, 10, 10]
    assert sum(1 for line in logged if "Error during backup" in line) == 6


def test_scheduled_loop_waits_before_first_backup(tmp_path):
    class StopDuringInitialWait:
        def is_set(self):
            return False

        def wait(self, timeout):
            assert timeout == 60
            return True

    profile = Profile(name="test", backup_interval_minutes=1)
    calls = []
    service = BackupService(profile)
    service.create_backup = lambda: calls.append("backup")
    service._stop_event = StopDuringInitialWait()

    service.scheduled_loop()

    assert calls == []


def test_scheduled_loop_waits_quietly_when_backup_source_missing(tmp_path):
    profile = Profile(
        name="test",
        backup_source=str(tmp_path / "missing-saves"),
        backup_dir=str(tmp_path / "backups"),
        backup_interval_minutes=77 / 60,
    )
    logged = []
    service = BackupService(profile, logger=logged.append)
    service._stop_event = FakeEvent(3)

    service.scheduled_loop()

    assert service._stop_event.waits == [77, 77, 77]
    assert logged == []


def test_scheduled_loop_skips_when_no_players_online(tmp_path):
    profile = Profile(
        name="test",
        backup_source=str(tmp_path / "saves"),
        backup_dir=str(tmp_path / "backups"),
        backup_interval_minutes=1,
        skip_backup_when_no_players=True,
    )
    calls = []
    rest = type("Rest", (), {"metrics": lambda self: {"currentplayernum": 0}})()
    service = BackupService(profile, logger=lambda _: None, rest_client=rest)
    service.create_backup = lambda: calls.append(1)
    service._stop_event = FakeEvent(1)

    service.scheduled_loop()

    assert calls == []
    assert service._stop_event.waits == [60]


def test_delete_backup_rejects_files_outside_backup_dir(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    outside = tmp_path / "outside.zip"
    outside.write_text("keep", encoding="utf-8")
    service = BackupService(Profile(name="test", backup_dir=str(backup_dir)))

    try:
        service.delete_backup(outside)
    except ValueError:
        pass
    else:
        raise AssertionError("outside backup should be rejected")

    assert outside.exists()


def test_restore_rejects_archive_paths_outside_save_directory(tmp_path):
    backup = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(backup, "w") as archive:
        archive.writestr("../outside.sav", "unsafe")
    profile = Profile(name="test", backup_source=str(tmp_path / "saves"))

    try:
        BackupService(profile).restore(backup)
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe archive member should be rejected")

    assert not (tmp_path / "outside.sav").exists()
