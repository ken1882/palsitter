import datetime as dt
from pathlib import Path

import pytest

from module.backup import BackupService, ServerOwnershipState
from module.config import Profile
from module.games.palworld.backup import service as backup_service_module


def _profile_with_world(tmp_path):
    source = tmp_path / "saves"
    world = source / ("A1" * 16)
    world.mkdir(parents=True)
    (world / "Level.sav").write_bytes(b"current")
    return Profile(
        name="test",
        backup_source=str(source),
        backup_dir=str(tmp_path / "backups"),
    ), world


class SavingRest:
    def __init__(self, error=None):
        self.calls = 0
        self.error = error

    def save(self):
        self.calls += 1
        if self.error:
            raise self.error


def test_manual_backup_reports_successful_flush(tmp_path):
    profile, _ = _profile_with_world(tmp_path)
    rest = SavingRest()
    service = BackupService(profile, logger=lambda _: None, rest_client=rest)

    result = service.create_backup_with_flush(dt.datetime(2026, 1, 1))

    assert result.status == "created"
    assert result.backup.path.is_file()
    assert result.flush_attempted is True
    assert result.flush_succeeded is True
    assert result.may_be_stale is False
    assert rest.calls == 1


def test_backups_created_in_the_same_second_do_not_overwrite_each_other(tmp_path):
    profile, _ = _profile_with_world(tmp_path)
    service = BackupService(profile, logger=lambda _: None)
    timestamp = dt.datetime(2026, 1, 1)

    first = service.create_backup(timestamp)
    second = service.create_backup(timestamp)

    assert first.path.name == "2026.01.01.000000.zip"
    assert second.path.name == "2026.01.01.000000-2.zip"
    assert first.path.is_file()
    assert second.path.is_file()


def test_failed_backup_removes_incomplete_archive(tmp_path, monkeypatch):
    profile, _ = _profile_with_world(tmp_path)
    backup_dir = Path(profile.backup_dir)

    class BrokenArchive:
        def __init__(self, path, *args, **kwargs):
            Path(path).write_bytes(b"partial")

        def __enter__(self):
            raise OSError("disk full")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(backup_service_module.zipfile, "ZipFile", BrokenArchive)

    with pytest.raises(OSError, match="disk full"):
        BackupService(profile, logger=lambda _: None).create_backup(
            dt.datetime(2026, 1, 1)
        )

    assert list(backup_dir.iterdir()) == []


def test_manual_backup_continues_and_marks_stale_when_flush_fails(tmp_path):
    profile, _ = _profile_with_world(tmp_path)
    logged = []
    rest = SavingRest(RuntimeError("REST unavailable"))
    service = BackupService(profile, logger=logged.append, rest_client=rest)

    result = service.create_backup_with_flush(dt.datetime(2026, 1, 1))

    assert result.status == "created"
    assert result.backup.path.is_file()
    assert result.may_be_stale is True
    assert result.flush_error == "REST unavailable"
    assert any("continuing with on-disk data" in line for line in logged)


def test_scheduled_backup_returns_created_skipped_and_failed_results(tmp_path):
    profile, _ = _profile_with_world(tmp_path)
    service = BackupService(profile, logger=lambda _: None)
    assert service.run_scheduled_backup().status == "created"

    profile.skip_backup_when_no_players = True
    service.rest_client = type(
        "Rest", (), {"metrics": lambda self: {"currentplayernum": 0}}
    )()
    skipped = service.run_scheduled_backup()
    assert skipped.status == "skipped"
    assert skipped.reason == "no_players"

    service.rest_client = None
    service.create_backup = lambda: (_ for _ in ()).throw(OSError("disk full"))
    failed = service.run_scheduled_backup()
    assert failed.status == "failed"
    assert failed.error == "disk full"


def test_restore_preserves_inactive_state_and_requires_safety_backup(tmp_path):
    profile, world = _profile_with_world(tmp_path)
    service = BackupService(profile, logger=lambda _: None)
    restore_source = service.create_backup(dt.datetime(2026, 1, 1))
    (world / "Level.sav").write_bytes(b"changed")
    callbacks = []

    result = service.restore_preserving_state(
        restore_source.path,
        initial_state=ServerOwnershipState.INACTIVE,
        stop_server=lambda: callbacks.append("stop"),
        start_server=lambda: callbacks.append("start"),
    )

    assert (world / "Level.sav").read_bytes() == b"current"
    assert result.restarted is False
    assert result.safety_backup_path.is_file()
    assert callbacks == []


def test_restore_owned_server_stops_kills_when_needed_and_restarts(tmp_path):
    profile, world = _profile_with_world(tmp_path)
    service = BackupService(profile, logger=lambda _: None)
    restore_source = service.create_backup(dt.datetime(2026, 1, 1))
    (world / "Level.sav").write_bytes(b"changed")
    callbacks = []
    waits = iter([False, True])

    result = service.restore_preserving_state(
        restore_source.path,
        initial_state=ServerOwnershipState.OWNED_RUNNING,
        stop_server=lambda: callbacks.append("stop"),
        wait_until_inactive=lambda timeout: (callbacks.append(("wait", timeout)), next(waits))[1],
        kill_server=lambda: callbacks.append("kill"),
        start_server=lambda: callbacks.append("start"),
        stop_timeout=12,
    )

    assert result.restarted is True
    assert callbacks == ["stop", ("wait", 12.0), "kill", ("wait", 5.0), "start"]
    assert (world / "Level.sav").read_bytes() == b"current"


def test_restore_external_server_never_kills_or_restarts(tmp_path):
    profile, _ = _profile_with_world(tmp_path)
    service = BackupService(profile, logger=lambda _: None)
    restore_source = service.create_backup(dt.datetime(2026, 1, 1))
    callbacks = []

    result = service.restore_preserving_state(
        restore_source.path,
        initial_state=ServerOwnershipState.EXTERNAL_ATTACHED,
        stop_server=lambda: callbacks.append("stop"),
        wait_until_inactive=lambda timeout: True,
        kill_server=lambda: callbacks.append("kill"),
        start_server=lambda: callbacks.append("start"),
    )

    assert result.restarted is False
    assert callbacks == ["stop"]


def test_restore_external_server_aborts_if_it_remains_running(tmp_path):
    profile, _ = _profile_with_world(tmp_path)
    service = BackupService(profile, logger=lambda _: None)
    restore_source = service.create_backup(dt.datetime(2026, 1, 1))
    callbacks = []

    with pytest.raises(RuntimeError, match="still running"):
        service.restore_preserving_state(
            restore_source.path,
            initial_state=ServerOwnershipState.EXTERNAL_ATTACHED,
            stop_server=lambda: callbacks.append("stop"),
            wait_until_inactive=lambda timeout: False,
            kill_server=lambda: callbacks.append("kill"),
            start_server=lambda: callbacks.append("start"),
        )

    assert callbacks == ["stop"]
