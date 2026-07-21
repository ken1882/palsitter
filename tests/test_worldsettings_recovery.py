import datetime as dt
from pathlib import Path

import pytest

from module.config import Profile
from module.games.palworld.worldsettings import (
    diagnose_ini,
    diagnose_world_option_sav,
    disable_undecodable_world_option_sav,
    read_ini_option_settings,
    recover_malformed_ini,
    resolve_ini_path,
)


WORLD_ID = "A1" * 16


def _profile(tmp_path) -> Profile:
    workdir = tmp_path / "PalServer"
    return Profile(
        name="test",
        workdir=str(workdir),
        backup_source=str(workdir / "Pal" / "Saved" / "SaveGames" / "0"),
        dedicated_server_name=WORLD_ID,
        game_port=9123,
        rest_port=9124,
        rest_password="secret12",
    )


def test_diagnose_and_recover_malformed_ini_with_timestamped_copy(tmp_path):
    profile = _profile(tmp_path)
    path = resolve_ini_path(profile)
    path.parent.mkdir(parents=True)
    malformed = (
        "[/Script/Pal.PalGameWorldSettings]\r\n"
        "OptionSettings=(PublicPort=not-a-number,ServerName=broken\r\n"
    )
    path.write_text(malformed, encoding="utf-8", newline="")

    assert "parentheses" in diagnose_ini(path)
    result = recover_malformed_ini(
        profile,
        is_server_active=lambda: False,
        timestamp=dt.datetime(2026, 1, 2, 3, 4, 5),
    )

    assert result.ini_path == path
    assert result.malformed_copy.name == (
        "PalWorldSettings.ini.malformed-20260102-030405.bak"
    )
    assert result.malformed_copy.read_bytes() == malformed.encode("utf-8")
    assert diagnose_ini(path) is None
    values = read_ini_option_settings(path)
    assert values["PublicPort"] == 9123
    assert values["RESTAPIPort"] == 9124
    assert values["AdminPassword"] == "secret12"
    assert profile.world_settings["PublicPort"] == 9123


def test_recover_ini_requires_stopped_server_and_malformed_file(tmp_path):
    profile = _profile(tmp_path)
    path = resolve_ini_path(profile)
    path.parent.mkdir(parents=True)
    path.write_text("invalid", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Stop the server"):
        recover_malformed_ini(profile, is_server_active=lambda: True)
    assert path.read_text(encoding="utf-8") == "invalid"

    path.write_text(
        "[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(PublicPort=8211)\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="is valid"):
        recover_malformed_ini(profile, is_server_active=lambda: False)


class BrokenSavCodec:
    def read(self, path):
        raise ValueError("unsupported save payload")


class ReadableSavCodec:
    def read(self, path):
        return {"properties": {}}


def test_disable_undecodable_sav_renames_without_replacing_it(tmp_path):
    profile = _profile(tmp_path)
    path = Path(profile.backup_source) / WORLD_ID / "WorldOption.sav"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"unreadable payload")

    assert diagnose_world_option_sav(profile, sav_codec=BrokenSavCodec()) == (
        "unsupported save payload"
    )
    result = disable_undecodable_world_option_sav(
        profile,
        sav_codec=BrokenSavCodec(),
        is_server_active=lambda: False,
        timestamp=dt.datetime(2026, 1, 2, 3, 4, 5),
    )

    assert result.original_path == path
    assert not path.exists()
    assert result.disabled_path.name == "WorldOption.sav.disabled-20260102-030405"
    assert result.disabled_path.read_bytes() == b"unreadable payload"
    assert result.decode_error == "unsupported save payload"


def test_disable_sav_requires_stopped_server_and_decode_failure(tmp_path):
    profile = _profile(tmp_path)
    path = Path(profile.backup_source) / WORLD_ID / "WorldOption.sav"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"payload")

    with pytest.raises(RuntimeError, match="Stop the server"):
        disable_undecodable_world_option_sav(
            profile,
            sav_codec=BrokenSavCodec(),
            is_server_active=lambda: True,
        )
    assert path.exists()

    with pytest.raises(ValueError, match="is readable"):
        disable_undecodable_world_option_sav(
            profile,
            sav_codec=ReadableSavCodec(),
            is_server_active=lambda: False,
        )
    assert path.exists()
