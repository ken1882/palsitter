import os
from pathlib import Path

import pytest

from module.config import Profile
from module.worldsettings.ini_codec import read_ini_option_settings
from module.worldsettings.sav_codec import WorldOptionSavCodec, extract_option_values
from module.worldsettings.schema import WORLD_OPTION_FIELDS_BY_KEY
from module.worldsettings.service import (
    find_world_sav_path,
    ensure_world_settings,
    load_world_settings,
    resolve_ini_path,
    save_world_settings,
)


class FakeBackupService:
    def __init__(self):
        self.calls = 0

    def create_backup(self):
        self.calls += 1


def _make_profile(tmp_path, name="test"):
    workdir = tmp_path / name
    backup_source = workdir / "Pal" / "Saved" / "SaveGames" / "0"
    backup_source.mkdir(parents=True)
    return Profile(
        name=name,
        workdir=str(workdir),
        backup_source=str(backup_source),
        backup_dir=str(tmp_path / f"{name}-backups"),
    )


def test_resolve_ini_path_prefers_windows_when_neither_exists_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr("module.games.palworld.config.WINDOWS", True)
    profile = _make_profile(tmp_path)
    path = resolve_ini_path(profile)
    assert path.parts[-2:] == ("WindowsServer", "PalWorldSettings.ini")


def test_resolve_ini_path_prefers_linux_when_neither_exists_on_linux(tmp_path, monkeypatch):
    monkeypatch.setattr("module.games.palworld.config.WINDOWS", False)
    profile = _make_profile(tmp_path)
    path = resolve_ini_path(profile)
    assert path.parts[-2:] == ("LinuxServer", "PalWorldSettings.ini")


def test_resolve_ini_path_uses_linux_when_only_linux_exists(tmp_path):
    profile = _make_profile(tmp_path)
    linux_dir = os.path.join(profile.workdir, "Pal", "Saved", "Config", "LinuxServer")
    os.makedirs(linux_dir)
    open(os.path.join(linux_dir, "PalWorldSettings.ini"), "w").close()

    path = resolve_ini_path(profile)
    assert path.parts[-2:] == ("LinuxServer", "PalWorldSettings.ini")


def test_find_world_sav_path_returns_none_without_save_dirs(tmp_path):
    profile = _make_profile(tmp_path)
    assert find_world_sav_path(profile) is None


def test_find_world_sav_path_uses_dedicated_server_name(tmp_path):
    profile = _make_profile(tmp_path)
    base = Path(profile.backup_source)
    unrelated = base / "BBBB2222"
    dedicated = base / profile.dedicated_server_name
    unrelated.mkdir()
    dedicated.mkdir()

    codec = WorldOptionSavCodec()
    template = codec.load_template()
    codec.write(unrelated / "WorldOption.sav", template)
    codec.write(dedicated / "WorldOption.sav", template)

    found = find_world_sav_path(profile)
    assert found == dedicated / "WorldOption.sav"


def test_load_world_settings_defaults_to_ini_when_no_sav(tmp_path):
    profile = _make_profile(tmp_path)
    profile.game_port = 9123
    profile.rest_port = 9124
    profile.rest_password = "secret"
    loaded = load_world_settings(profile)
    assert loaded.source_format == "ini"
    assert loaded.sav_path is None
    assert loaded.values["DayTimeSpeedRate"] == WORLD_OPTION_FIELDS_BY_KEY["DayTimeSpeedRate"].default
    assert loaded.values["PublicPort"] == 9123
    assert loaded.values["RESTAPIPort"] == 9124
    assert loaded.values["AdminPassword"] == "secret"
    assert set(loaded.values) == set(WORLD_OPTION_FIELDS_BY_KEY)


@pytest.mark.parametrize(
    ("windows", "directory"),
    ((True, "WindowsServer"), (False, "LinuxServer")),
)
def test_ensure_world_settings_creates_platform_ini_before_first_server_start(
    tmp_path, monkeypatch, windows, directory
):
    monkeypatch.setattr("module.games.palworld.config.WINDOWS", windows)
    profile = _make_profile(tmp_path)
    profile.game_port = 9123
    profile.rest_port = 9124
    profile.rest_password = "secret123"

    path = ensure_world_settings(profile)

    assert path.parts[-2:] == (directory, "PalWorldSettings.ini")
    values = read_ini_option_settings(path)
    assert values["RESTAPIEnabled"] is True
    assert values["PublicPort"] == 9123
    assert values["RESTAPIPort"] == 9124
    assert values["AdminPassword"] == "secret123"


def test_ensure_world_settings_does_not_reuse_other_platform_ini(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("module.games.palworld.config.WINDOWS", False)
    profile = _make_profile(tmp_path)
    other = Path(profile.workdir) / "Pal" / "Saved" / "Config" / "WindowsServer" / "PalWorldSettings.ini"
    other.parent.mkdir(parents=True)
    other.write_text("old Windows settings", encoding="utf-8")

    path = ensure_world_settings(profile)

    assert path.parts[-2:] == ("LinuxServer", "PalWorldSettings.ini")
    assert path.is_file()
    assert other.read_text(encoding="utf-8") == "old Windows settings"


def test_load_world_settings_syncs_rest_credentials_and_ports(tmp_path):
    profile = _make_profile(tmp_path)
    save_world_settings(
        profile,
        {
            "PublicPort": 9123,
            "RESTAPIPort": 9124,
            "AdminPassword": "effective-password",
        },
        "ini",
    )
    profile.game_port = 8211
    profile.rest_port = 8212
    profile.rest_password = "stale-password"

    load_world_settings(profile)

    assert profile.game_port == 9123
    assert profile.rest_port == 9124
    assert profile.rest_password == "effective-password"


def test_load_world_settings_prefers_sav_when_present(tmp_path):
    profile = _make_profile(tmp_path)
    save_dir = Path(profile.backup_source) / profile.dedicated_server_name
    save_dir.mkdir()
    codec = WorldOptionSavCodec()
    codec.write(save_dir / "WorldOption.sav", codec.load_template())

    loaded = load_world_settings(profile)
    assert loaded.source_format == "sav"
    assert loaded.sav_path == save_dir / "WorldOption.sav"


def test_save_world_settings_ini_never_backs_up(tmp_path):
    profile = _make_profile(tmp_path)
    fake_backup = FakeBackupService()

    save_world_settings(
        profile,
        {
            "DayTimeSpeedRate": 4.0,
            "PublicPort": 9123,
            "RESTAPIPort": 9124,
            "AdminPassword": "new-secret",
        },
        "ini",
        backup_service=fake_backup,
    )

    assert fake_backup.calls == 0
    values = read_ini_option_settings(resolve_ini_path(profile))
    assert values["DayTimeSpeedRate"] == 4.0
    assert profile.world_settings["DayTimeSpeedRate"] == 4.0
    assert profile.game_port == 9123
    assert profile.rest_host == "localhost"
    assert profile.rest_port == 9124
    assert profile.rest_username == "admin"
    assert profile.rest_password == "new-secret"


def test_load_world_settings_uses_profile_copy_when_target_is_missing(tmp_path):
    profile = _make_profile(tmp_path)
    profile.world_settings = {
        "ServerName": "Profile copy",
        "CrossplayPlatforms": ["Steam", "PS5"],
    }

    loaded = load_world_settings(profile)

    assert loaded.values["ServerName"] == "Profile copy"
    assert loaded.values["CrossplayPlatforms"] == ["Steam", "PS5"]


def test_save_world_settings_sav_backs_up_exactly_once(tmp_path):
    profile = _make_profile(tmp_path)
    save_dir = Path(profile.backup_source) / profile.dedicated_server_name
    save_dir.mkdir()
    codec = WorldOptionSavCodec()
    codec.write(save_dir / "WorldOption.sav", codec.load_template())

    fake_backup = FakeBackupService()
    save_world_settings(
        profile,
        {"DayTimeSpeedRate": 7.5},
        "sav",
        backup_service=fake_backup,
        sav_codec=codec,
    )

    assert fake_backup.calls == 1
    reread = extract_option_values(codec.read(save_dir / "WorldOption.sav"))
    assert reread["DayTimeSpeedRate"] == 7.5


def test_save_world_settings_sav_creates_dedicated_world_folder(tmp_path):
    profile = _make_profile(tmp_path)
    fake_backup = FakeBackupService()

    save_world_settings(profile, {"DayTimeSpeedRate": 1.0}, "sav", backup_service=fake_backup)

    assert fake_backup.calls == 1
    assert (
        Path(profile.backup_source) / profile.dedicated_server_name / "WorldOption.sav"
    ).is_file()


def test_save_world_settings_unknown_format_raises():
    profile = Profile(name="test")
    with pytest.raises(ValueError):
        save_world_settings(profile, {}, "yaml")


def test_saving_ini_does_not_clear_autodetected_sav_preference(tmp_path):
    profile = _make_profile(tmp_path)
    save_dir = Path(profile.backup_source) / profile.dedicated_server_name
    save_dir.mkdir()
    codec = WorldOptionSavCodec()
    codec.write(save_dir / "WorldOption.sav", codec.load_template())

    save_world_settings(profile, {"ServerName": "Switched to ini"}, "ini")

    # a stale WorldOption.sav still wins on the next load - Palsitter warns, never deletes it.
    loaded = load_world_settings(profile)
    assert loaded.source_format == "sav"
