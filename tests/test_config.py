import json
from pathlib import Path

import pytest

from module.config import (
    ADMIN_PASSWORD_RE,
    DEDICATED_SERVER_NAME_RE,
    Profile,
    clone_profile,
    delete_profile,
    ensure_default_profile,
    executable_workdir,
    fixed_backup_dir,
    fixed_backup_source,
    fixed_executable_path,
    fixed_palserver_dir,
    fixed_steamcmd_path,
    game_user_settings_path,
    list_profiles,
    load_profile,
    profile_path,
    save_profile,
    server_config_dir_name,
    server_executable_relative_path,
)
from module.instances import (
    InstanceRecord,
    create_instance,
    delete_instance,
    initialize_instances,
    list_instances,
    load_instance,
    next_instance_name,
    profile_dir,
    save_instance,
)
from module.games.palworld.config import PALWORLD_CONFIG_VERSION, new_profile


def test_profile_bootstrap_and_clone(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))

    ensure_default_profile()
    assert list_profiles() == []

    create_instance("palworld", "palworld")
    clone_profile("palworld2", "palworld")
    profile = load_profile("palworld2")

    assert profile.name == "palworld2"
    raw = json.loads((tmp_path / "profile" / "palworld2" / "profile.json").read_text(encoding="utf-8"))
    assert raw["name"] == "palworld2"
    assert raw["game"] == "palworld"
    assert "game_config" in raw
    assert profile.backup_dir == str(fixed_backup_dir("palworld2"))
    # default's ports (8211/27015/8212) are taken; server2 gets the next free ones.
    assert profile.game_port == 8212
    assert profile.query_port == 27016
    assert profile.rest_port == 8213
    default_profile = load_profile("palworld")
    assert DEDICATED_SERVER_NAME_RE.fullmatch(default_profile.dedicated_server_name)
    assert DEDICATED_SERVER_NAME_RE.fullmatch(profile.dedicated_server_name)
    assert profile.dedicated_server_name != default_profile.dedicated_server_name
    assert ADMIN_PASSWORD_RE.fullmatch(default_profile.world_settings["AdminPassword"])
    assert ADMIN_PASSWORD_RE.fullmatch(profile.world_settings["AdminPassword"])
    assert profile.world_settings["AdminPassword"] != default_profile.world_settings["AdminPassword"]
    assert profile.rest_password == profile.world_settings["AdminPassword"]
    assert profile.world_settings["PublicPort"] == profile.game_port
    assert profile.world_settings["RESTAPIPort"] == profile.rest_port
    assert profile.world_settings["RESTAPIEnabled"] is True


def test_clone_profile_allocates_free_ports(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))

    create_instance("palworld", "palworld")
    server2 = clone_profile("server2", "palworld")
    assert (server2.game_port, server2.query_port, server2.rest_port) == (8212, 27016, 8213)

    # Cloning from a non-template existing profile must still get fresh ports,
    # not copy server2's ports verbatim.
    server3 = clone_profile("server3", "server2")
    assert (server3.game_port, server3.query_port, server3.rest_port) == (8213, 27017, 8214)


def test_clone_profile_reuses_ports_freed_by_deleted_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))

    create_instance("palworld", "palworld")
    server2 = clone_profile("server2", "palworld")
    delete_profile("server2")

    server3 = clone_profile("server3", "palworld")
    assert (server3.game_port, server3.query_port, server3.rest_port) == (
        server2.game_port,
        server2.query_port,
        server2.rest_port,
    )


def test_restart_on_crash_defaults_true():
    assert Profile(name="x").restart_on_crash is True


def test_self_heal_enabled_defaults_true():
    profile = Profile(name="x")
    assert profile.self_heal_enabled is True
    assert profile.self_heal_trigger_frame_minutes == 30
    assert profile.self_heal_trigger_crash_times == 2


def test_backup_defaults_use_minutes_and_twenty_files():
    profile = Profile(name="x")
    assert profile.backup_interval_minutes == 60
    assert profile.backup_retention_count == 20
    assert profile.skip_backup_when_no_players is True


def test_new_profile_launch_defaults_use_cpu_count_minus_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("module.games.palworld.config.os.cpu_count", lambda: 8)

    profile = new_profile("test")

    assert profile.launch_useperfthreads is True
    assert profile.launch_no_async_loading_thread is False
    assert profile.launch_use_multithread_for_ds is True
    assert profile.launch_worker_threads_server == 7
    assert profile.build_executable_args() == [
        "-useperfthreads",
        "-UseMultithreadForDS",
        "-NumberOfWorkerThreadsServer=7",
    ]


def test_new_profile_worker_threads_has_single_cpu_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("module.games.palworld.config.os.cpu_count", lambda: 1)

    assert new_profile("test").launch_worker_threads_server == 1


def test_profile_load_uses_world_settings_for_rest_credentials_and_ports():
    profile = Profile.from_dict(
        {
            "name": "test",
            "game_port": 8211,
            "rest_port": 8212,
            "rest_password": "stale-password",
            "world_settings": {
                "PublicPort": 9123,
                "RESTAPIPort": 9124,
                "AdminPassword": "effective-password",
            },
        }
    )

    assert profile.game_port == 9123
    assert profile.rest_host == "localhost"
    assert profile.rest_port == 9124
    assert profile.rest_username == "admin"
    assert profile.rest_password == "effective-password"


def test_legacy_backup_interval_seconds_migrates_to_minutes():
    profile = Profile.from_dict({"name": "x", "backup_interval_seconds": 1800})
    assert profile.backup_interval_minutes == 30
    assert "backup_interval_seconds" not in profile.to_dict()


def test_executable_workdir_uses_executable_parent():
    assert executable_workdir(r"C:\servers\pal\PalServer.exe") == r"C:\servers\pal"
    assert executable_workdir("PalServer.exe") is None


def test_saved_profile_derives_workdir_from_executable_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))

    save_profile(Profile(name="default", workdir=str(tmp_path / "old"), executable=str(tmp_path / "ignored.exe")))

    raw = json.loads(profile_path("default").read_text(encoding="utf-8"))
    assert "workdir" not in raw
    assert "executable" not in raw
    assert load_profile("default").workdir == str(fixed_palserver_dir("default"))


def test_profile_paths_are_fixed_under_profile_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))

    save_profile(Profile(name="default"))
    profile = load_profile("default")

    assert profile.server_name == "default"
    assert profile.executable == str(fixed_executable_path("default"))
    assert profile.steamcmd == str(fixed_steamcmd_path("default"))
    assert profile.backup_source == str(fixed_backup_source("default"))
    assert profile.backup_dir == str(fixed_backup_dir("default"))


@pytest.mark.parametrize(
    ("windows", "directory"),
    ((True, "WindowsServer"), (False, "LinuxServer")),
)
def test_new_instance_creates_server_and_world_settings_before_install(
    tmp_path, monkeypatch, windows, directory
):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("module.games.palworld.config.WINDOWS", windows)

    create_instance("default", "palworld")

    assert game_user_settings_path("default").is_file()
    world_path = (
        fixed_palserver_dir("default")
        / "Pal"
        / "Saved"
        / "Config"
        / directory
        / "PalWorldSettings.ini"
    )
    assert world_path.is_file()
    assert "RESTAPIEnabled=True" in world_path.read_text(encoding="utf-8")


def test_palworld_fixed_paths_are_windows_aware(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("module.games.palworld.config.WINDOWS", True)

    assert server_executable_relative_path() == Path("PalServer.exe")
    assert fixed_executable_path("default").parts[-1:] == ("PalServer.exe",)
    assert server_config_dir_name() == "WindowsServer"
    assert game_user_settings_path("default").parts[-2:] == (
        "WindowsServer",
        "GameUserSettings.ini",
    )


def test_palworld_fixed_paths_are_linux_aware(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("module.games.palworld.config.WINDOWS", False)

    assert server_executable_relative_path() == (
        Path("Pal") / "Binaries" / "Linux" / "PalServer-Linux-Shipping"
    )
    assert fixed_executable_path("default").parts[-4:] == (
        "Pal",
        "Binaries",
        "Linux",
        "PalServer-Linux-Shipping",
    )
    assert server_config_dir_name() == "LinuxServer"
    assert game_user_settings_path("default").parts[-2:] == (
        "LinuxServer",
        "GameUserSettings.ini",
    )


def test_save_profile_syncs_dedicated_server_name_without_replacing_other_settings(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    profile = Profile(name="default", dedicated_server_name="A1" * 16)
    path = game_user_settings_path("default")
    path.parent.mkdir(parents=True)
    path.write_text(
        "[/Script/Pal.PalGameLocalSettings]\n"
        "AudioSettings=(Master=0.500000)\n"
        "DedicatedServerName=OLD\n"
        "GraphicsCommonQuality=0\n",
        encoding="utf-8",
    )

    save_profile(profile)

    text = path.read_text(encoding="utf-8")
    assert "AudioSettings=(Master=0.500000)" in text
    assert "GraphicsCommonQuality=0" in text
    assert "DedicatedServerName=" + "A1" * 16 in text
    assert "DedicatedServerName=OLD" not in text


def test_legacy_steam_app_id_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    data = Profile(name="default").to_dict()
    data["steam_app_id"] = "legacy"
    profile_path("default").parent.mkdir(parents=True)
    profile_path("default").write_text(json.dumps(data), encoding="utf-8")

    profile = load_profile("default")

    assert not hasattr(profile, "steam_app_id")
    assert "steam_app_id" not in profile.to_dict()


def test_delete_profile_removes_reference_only(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))

    create_instance("palworld", "palworld")
    clone_profile("server2", "palworld")
    assert "server2" in list_profiles()

    # On-disk server data that must survive a reference deletion.
    data_dir = tmp_path / "server2-data"
    data_dir.mkdir()
    save_file = data_dir / "save.sav"
    save_file.write_text("save-data", encoding="utf-8")

    delete_profile("server2")

    assert "server2" not in list_profiles()
    assert not (tmp_path / "profile" / "server2" / "profile.json").exists()
    assert save_file.exists()
    assert save_file.read_text(encoding="utf-8") == "save-data"

    with pytest.raises(FileNotFoundError):
        delete_profile("server2")


def test_delete_instance_can_wipe_profile_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))

    create_instance("server2", "palworld")
    data_file = profile_dir("server2") / "server-data" / "save.sav"
    data_file.parent.mkdir()
    data_file.write_text("save-data", encoding="utf-8")

    delete_instance("server2", wipe_data=True)

    assert not profile_dir("server2").exists()
    with pytest.raises(FileNotFoundError):
        delete_instance("server2", wipe_data=True)


def test_satisfactory_uses_empty_template_and_names_are_global(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))

    satisfactory = create_instance("satisfactory", "satisfactory")
    assert satisfactory.game_config == {}
    assert next_instance_name("satisfactory") == "satisfactory2"

    save_instance(InstanceRecord("palworld", "satisfactory", {}))
    assert next_instance_name("palworld") == "palworld2"
    with pytest.raises(FileExistsError):
        create_instance("PALWORLD", "palworld")


def test_cross_game_clone_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    create_instance("satisfactory", "satisfactory")

    with pytest.raises(ValueError, match="different game"):
        create_instance("palworld", "palworld", "satisfactory")


def test_palworld_port_allocation_ignores_satisfactory(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    create_instance("satisfactory", "satisfactory")
    palworld = create_instance("palworld", "palworld")

    profile = load_profile(palworld.name)
    assert (profile.game_port, profile.query_port, profile.rest_port) == (8211, 27015, 8212)


def test_legacy_profile_migration_uses_os_replace(tmp_path, monkeypatch):
    config = tmp_path / "config"
    path = tmp_path / "profile" / "old" / "profile.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"name": "old", "game_port": 9000}), encoding="utf-8")
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(config))
    calls = []
    real_replace = __import__("os").replace
    monkeypatch.setattr("module.instances.os.replace", lambda source, target: (calls.append((source, target)), real_replace(source, target))[1])

    initialize_instances()

    assert calls
    assert load_instance("OLD").game == "palworld"
    assert load_instance("old").game_config["game_port"] == 9000


def test_failed_legacy_migration_preserves_original(tmp_path, monkeypatch):
    config = tmp_path / "config"
    path = tmp_path / "profile" / "old" / "profile.json"
    path.parent.mkdir(parents=True)
    original = json.dumps({"name": "old", "game_port": 9000})
    path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(config))
    monkeypatch.setattr("module.instances.os.replace", lambda *_: (_ for _ in ()).throw(OSError("replace failed")))

    with pytest.raises(OSError, match="replace failed"):
        initialize_instances()

    assert path.read_text(encoding="utf-8") == original


def test_preexisting_case_only_names_are_rejected_without_filesystem_assumptions():
    from pathlib import Path
    from module.instances import _assert_no_case_conflicts

    paths = [Path("profiles/Test/profile.json"), Path("profiles/test/profile.json")]
    with pytest.raises(ValueError, match="differ only by case"):
        _assert_no_case_conflicts(paths)


def test_satisfactory_creation_does_not_load_palworld_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(
        "module.games.palworld.config.new_profile",
        lambda *_: (_ for _ in ()).throw(AssertionError("Palworld must not load")),
    )

    assert create_instance("satisfactory", "satisfactory").game_config == {}


def test_legacy_config_profiles_are_migrated(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "default.json").write_text(json.dumps({"name": "default", "rest_port": 9000}), encoding="utf-8")

    assert list_profiles() == ["default"]
    assert not (config_dir / "default.json").exists()
    assert profile_path("default").exists()
    assert load_profile("default").rest_port == 9000


def test_current_profile_defaults_disable_optional_launch_and_memory_policies():
    profile = Profile(name="x")

    assert profile.config_version == PALWORLD_CONFIG_VERSION
    assert profile.update_on_start is True
    assert profile.auto_update is True
    assert profile.auto_update_idle_minutes == 30
    assert profile.build_executable_args() == []
    assert profile.memory_restart_mb == 0
    assert profile.crash_restart_limit_per_hour == 5
    assert profile.self_heal_trigger_frame_minutes == 30
    assert profile.self_heal_trigger_crash_times == 2
    assert profile.planned_restart_mode == "off"


def test_auto_update_idle_minutes_must_be_positive():
    with pytest.raises(ValueError, match="Idle shutdown for update"):
        Profile(name="x", auto_update_idle_minutes=0).to_game_config()


def test_legacy_launch_and_memory_config_migrates_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(
        "module.games.palworld.config.psutil.virtual_memory",
        lambda: type("Memory", (), {"total": 10 * 1024 * 1024})(),
    )
    save_instance(
        InstanceRecord(
            "legacy",
            "palworld",
            {
                "executable_args": [
                    "-USEPERFTHREADS",
                    "-NumberOfWorkerThreadsServer=4",
                    "-custom=value",
                ],
                "memory_restart_percent": 25,
            },
        )
    )

    profile = load_profile("legacy")
    raw = load_instance("legacy").game_config

    assert profile.build_executable_args() == [
        "-useperfthreads",
        "-NumberOfWorkerThreadsServer=4",
        "-custom=value",
    ]
    assert profile.memory_restart_mb == 3
    assert raw["config_version"] == PALWORLD_CONFIG_VERSION
    assert raw["launch_useperfthreads"] is True
    assert raw["launch_worker_threads_server"] == 4
    assert raw["extra_args"] == ["-custom=value"]
    assert raw["memory_restart_mb"] == 3
    assert raw["self_heal_trigger_frame_minutes"] == 30
    assert raw["self_heal_trigger_crash_times"] == 2
    assert "memory_restart_percent" not in raw
    assert "executable_args" not in raw


def test_structured_launch_arguments_have_stable_order_and_reject_duplicates():
    profile = Profile(
        name="x",
        launch_useperfthreads=True,
        launch_no_async_loading_thread=True,
        launch_use_multithread_for_ds=True,
        launch_worker_threads_server=6,
        launch_public_lobby=True,
        launch_log_format=True,
        extra_args=["-custom=value"],
    )

    assert profile.build_executable_args() == [
        "-useperfthreads",
        "-NoAsyncLoadingThread",
        "-UseMultithreadForDS",
        "-NumberOfWorkerThreadsServer=6",
        "-publiclobby",
        "-logformat",
        "-custom=value",
    ]

    profile.extra_args = ["-PUBLICLOBBY"]
    with pytest.raises(ValueError, match="cannot duplicate"):
        profile.build_executable_args()


def test_legacy_executable_args_assignment_remains_compatible():
    profile = Profile(name="x")
    profile.executable_args = ["-logformat", "-custom"]

    saved = profile.to_game_config()

    assert saved["launch_log_format"] is True
    assert saved["extra_args"] == ["-custom"]
    assert "executable_args" not in saved


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"planned_restart_mode": "weekly"}, "mode"),
        (
            {"planned_restart_mode": "interval", "planned_restart_interval_hours": 0},
            "interval",
        ),
        ({"planned_restart_mode": "daily", "planned_restart_daily_time": "24:00"}, "HH:MM"),
        ({"crash_restart_limit_per_hour": 0}, "limit"),
        ({"self_heal_trigger_frame_minutes": 0}, "frame"),
        ({"self_heal_trigger_crash_times": 0}, "crash times"),
    ],
)
def test_reliability_config_rejects_invalid_values(changes, message):
    with pytest.raises(ValueError, match=message):
        Profile(name="x", **changes).to_game_config()
