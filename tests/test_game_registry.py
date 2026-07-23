import multiprocessing as mp
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from module.games import InstanceStatusSummary, get_game, list_games
from module.config import Profile
from module.games.palworld.server.history import LifecycleEvent
from module.instances import InstanceRecord, create_instance
from module.webui.process_manager import _run_profile


def _spawn_game_lookup(output):
    adapter = get_game("palworld")
    output.put((adapter.id, adapter.runnable))


def test_builtin_games_and_capabilities():
    assert [(game.id, game.runnable) for game in list_games()] == [
        ("palworld", True),
        ("satisfactory", False),
    ]
    palworld = get_game("palworld").capabilities
    assert palworld.lifecycle is True
    assert palworld.updates is True
    assert palworld.backups is True
    assert palworld.players is True
    assert palworld.world_settings is True
    assert palworld.save_import is True
    assert get_game("satisfactory").capabilities.lifecycle is False


def test_unsupported_status_summary_is_typed_without_dispatching_palworld(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    record = create_instance("factory", "satisfactory")

    summary = get_game("satisfactory").status_summary(record)

    assert isinstance(summary, InstanceStatusSummary)
    assert summary.state == "unsupported"
    assert summary["players"] == "-"


def test_palworld_status_summary_populates_typed_operator_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    record = create_instance("default", "palworld")
    record.game_config["world_settings"]["BaseCampMaxNum"] = 77
    adapter = get_game("palworld")
    profile = adapter.load_typed_profile(record.name, record.game_config)
    backup_dir = Path(profile.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "2026.07.16.010203.zip").write_bytes(b"zip")

    snapshot = SimpleNamespace(
        metrics={
                "currentplayernum": "3",
                "maxplayernum": "32",
                "serverfps": "60",
                "serverfpsaverage": "59.5",
                "uptime": "120",
                "days": "4",
                "basecampnum": "3",
            },
        info={"version": "v1.2.3"},
    )

    monkeypatch.setattr(
        "module.games.palworld.server.get_pal_rest_cache",
        lambda name: SimpleNamespace(
            snapshot=lambda: snapshot,
            poll_once=lambda: None,
            ensure_started=lambda: None,
        ),
    )
    monkeypatch.setattr(
        "module.games.palworld.server.status.instance_is_running",
        lambda profile: True,
    )
    monkeypatch.setattr(
        "module.games.palworld.server.status.endpoint_status",
        lambda profile, **kwargs: {"udp": "open", "rest": "open", "rcon": "closed"},
    )

    summary = adapter.status_summary(
        record,
        {"cpu_percent": 12.5, "memory_bytes": 256 * 1024 * 1024},
    )

    assert isinstance(summary, InstanceStatusSummary)
    assert summary.server_name == profile.server_name
    assert summary.state == "running"
    assert (summary.current_players, summary.max_players) == (3, 32)
    assert (summary.current_fps, summary.average_fps) == (60.0, 59.5)
    assert (summary.uptime_seconds, summary.days) == (120, 4)
    assert (summary.basecamp_num, summary.basecamp_max_num) == (3, 77)
    assert summary["palbox"] == "3 / 77"
    assert summary.cpu_percent == 12.5
    assert summary.memory_bytes == 256 * 1024 * 1024
    assert summary.game_version == "v1.2.3"
    assert summary.latest_backup == "2026.07.16.010203.zip"
    assert summary.endpoint_states["rest"] == "open"


def test_palworld_status_summary_uses_cached_game_version_when_server_is_stopped(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    record = create_instance("default", "palworld")
    snapshot = SimpleNamespace(
        metrics=None,
        info={"version": "v1.2.3"},
    )

    monkeypatch.setattr(
        "module.games.palworld.server.get_pal_rest_cache",
        lambda name: SimpleNamespace(
            snapshot=lambda: snapshot,
            poll_once=lambda: None,
            ensure_started=lambda: None,
        ),
    )
    monkeypatch.setattr(
        "module.games.palworld.server.status.instance_is_running",
        lambda profile: False,
    )
    monkeypatch.setattr(
        "module.games.palworld.server.status.endpoint_status",
        lambda profile, **kwargs: {"udp": "closed", "rest": "closed", "rcon": "disabled"},
    )

    summary = get_game("palworld").status_summary(record)

    assert summary.state == "inactive"
    assert summary.game_version == "v1.2.3"


def test_registry_lookup_works_in_spawned_child():
    context = mp.get_context("spawn")
    output = context.Queue()
    process = context.Process(target=_spawn_game_lookup, args=(output,))
    process.start()
    process.join(timeout=10)

    assert process.exitcode == 0
    assert output.get(timeout=2) == ("palworld", True)


def test_restart_history_write_failure_does_not_block_event_delivery(monkeypatch):
    record = InstanceRecord(
        "test",
        "palworld",
        Profile(name="test", backup_interval_minutes=0).to_game_config(),
    )
    logs = []
    delivered = []

    class FakeBackupService:
        def __init__(self, *args, **kwargs):
            pass

    class FakeHistory:
        def __init__(self, name):
            pass

        def append(self, event):
            raise OSError("disk full")

    class FakeManager:
        def __init__(self, *args, event_callback, **kwargs):
            self.event_callback = event_callback

        def supervise_loop(self, interval_seconds):
            self.event_callback(
                LifecycleEvent(dt.datetime(2026, 7, 16), "crash", "restarted")
            )

    monkeypatch.setattr("module.games.palworld.backup.BackupService", FakeBackupService)
    monkeypatch.setattr("module.games.palworld.server.PalRestClient", lambda profile: object())
    monkeypatch.setattr("module.games.palworld.server.PalServerManager", FakeManager)
    monkeypatch.setattr("module.games.palworld.server.RestartHistoryStore", FakeHistory)

    get_game("palworld").supervise(
        record,
        logs.append,
        lambda: False,
        lambda state: None,
        event_callback=delivered.append,
    )

    assert delivered[0].outcome == "restarted"
    assert any("disk full" in line for line in logs)


def test_generic_process_target_resolves_unsupported_game_after_spawn(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    create_instance("satisfactory", "satisfactory")
    context = mp.get_context("spawn")
    logs = context.Queue()
    states = context.Queue()
    stop = context.Event()
    process = context.Process(
        target=_run_profile,
        args=("satisfactory", logs, stop, states),
    )
    process.start()
    process.join(timeout=10)

    assert process.exitcode not in (None, 0)
    messages = [logs.get(timeout=2), logs.get(timeout=2)]
    assert any("Supervisor crashed" in message for message in messages)


def test_entrypoint_imports_have_no_profile_or_server_side_effects(tmp_path):
    config = tmp_path / "config"
    env = {**os.environ, "PALSITTER_CONFIG_DIR": str(config)}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import gui, run, backup, module.games.registry, module.webui.app; print('ok')",
        ],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
    assert not config.exists()
