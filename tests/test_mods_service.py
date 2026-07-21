import io
import json
import zipfile
from pathlib import Path

import pytest
import requests

from module.games.palworld.config import PalworldProfile, fixed_executable_path, fixed_palserver_dir
from module.games.palworld.mods import UE4SSService, default_release_tag, patch_object_cache_setting


class FakeResponse:
    def __init__(self, *, payload=None, content=b"", status=200):
        self.payload = payload
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload

    def iter_content(self, chunk_size=1):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses[url] if isinstance(self.responses, dict) else self.responses.pop(0)
        return response


def _release(tag, *assets, prerelease=False, draft=False):
    return {
        "tag_name": tag,
        "name": tag,
        "prerelease": prerelease,
        "draft": draft,
        "assets": [
            {
                "name": name,
                "browser_download_url": f"https://downloads.invalid/{name}",
                "updated_at": updated,
            }
            for name, updated in assets
        ],
    }


def _zip_bytes(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


def _installed_profile(tmp_path, monkeypatch, name="default"):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    executable = fixed_executable_path(name)
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b"server")
    return PalworldProfile(name=name)


def test_release_probe_limits_results_defaults_experimental_and_selects_newest_asset(tmp_path, monkeypatch):
    profile = _installed_profile(tmp_path, monkeypatch)
    payload = [
        _release(
            "experimental-latest",
            ("zDEV-UE4SS_v3.0.1.zip", "2026-01-03"),
            ("UE4SS_v3.0.1-100.zip", "2026-01-01"),
            ("UE4SS_v3.0.1-101.zip", "2026-01-02"),
            prerelease=True,
        )
    ]
    payload.extend(
        _release(f"v3.0.{number}", (f"UE4SS_v3.0.{number}.zip", f"2025-01-{number:02d}"))
        for number in range(1, 12)
    )
    session = FakeSession([FakeResponse(payload=payload)])

    releases = UE4SSService(profile, session=session, platform_supported=True).list_releases()

    assert len(releases) == 10
    assert releases[0].asset_name == "UE4SS_v3.0.1-101.zip"
    assert default_release_tag(releases) == "experimental-latest"
    assert session.calls[0][1]["params"] == {"per_page": 10}
    assert session.calls[0][1]["timeout"] == 15


def test_release_probe_skips_drafts_and_non_runtime_assets(tmp_path, monkeypatch):
    profile = _installed_profile(tmp_path, monkeypatch)
    payload = [
        _release("draft", ("UE4SS_v3.0.1.zip", "2026"), draft=True),
        _release("extras", ("zCustomGameConfigs.zip", "2026")),
        _release("old", ("UE4SS_Xinput_v2.5.2.zip", "2026")),
        _release("stable", ("UE4SS_Standard_v2.5.2.zip", "2026")),
    ]

    releases = UE4SSService(
        profile,
        session=FakeSession([FakeResponse(payload=payload)]),
        platform_supported=True,
    ).list_releases()

    assert [release.tag for release in releases] == ["stable"]


@pytest.mark.parametrize(
    ("layout", "expected_relative"),
    (("nested", "Pal/Binaries/Win64/ue4ss/UE4SS.log"), ("flat", "Pal/Binaries/Win64/UE4SS.log")),
)
def test_log_path_resolves_installed_nested_and_flat_layouts(
    tmp_path, monkeypatch, layout, expected_relative
):
    profile = _installed_profile(tmp_path, monkeypatch)
    win64 = fixed_palserver_dir(profile.name) / "Pal" / "Binaries" / "Win64"
    dll = win64 / ("ue4ss" if layout == "nested" else "") / "UE4SS.dll"
    dll.parent.mkdir(parents=True, exist_ok=True)
    dll.write_bytes(b"dll")

    assert UE4SSService(profile, platform_supported=True).log_path() == (
        fixed_palserver_dir(profile.name) / expected_relative
    )


def test_log_path_is_absent_without_ue4ss(tmp_path, monkeypatch):
    profile = _installed_profile(tmp_path, monkeypatch)

    assert UE4SSService(profile, platform_supported=True).log_path() is None


def test_install_nested_release_patches_settings_and_records_marker(tmp_path, monkeypatch):
    profile = _installed_profile(tmp_path, monkeypatch)
    archive = _zip_bytes(
        {
            "dwmapi.dll": b"proxy",
            "ue4ss/UE4SS.dll": b"dll",
            "ue4ss/UE4SS-settings.ini": b"Other = 1\r\nbUseUObjectArrayCache = true\r\n",
            "ue4ss/Mods/Builtin/Scripts/main.lua": b"print('ok')",
        }
    )
    release = _release(
        "experimental-latest",
        ("UE4SS_v3.0.1-101.zip", "2026-01-01"),
        prerelease=True,
    )
    session = FakeSession(
        {
            "https://api.github.com/repos/UE4SS-RE/RE-UE4SS/releases/tags/experimental-latest": FakeResponse(payload=release),
            "https://downloads.invalid/UE4SS_v3.0.1-101.zip": FakeResponse(content=archive),
        }
    )
    service = UE4SSService(
        profile,
        session=session,
        running_probe=lambda _: False,
        platform_supported=True,
    )

    installed = service.install("experimental-latest")

    win64 = fixed_palserver_dir("default") / "Pal" / "Binaries" / "Win64"
    settings = (win64 / "ue4ss" / "UE4SS-settings.ini").read_bytes()
    marker = json.loads((win64 / ".palsitter-mods.json").read_text(encoding="utf-8"))
    assert installed.tag == "experimental-latest"
    assert settings == b"Other = 1\r\nbUseUObjectArrayCache = false\r\n"
    assert marker["layout"] == "nested"
    assert marker["version"] == "experimental-latest"
    assert marker["paths"] == ["dwmapi.dll", "ue4ss"]


def test_layout_change_preserves_user_mods_and_removes_old_core(tmp_path, monkeypatch):
    profile = _installed_profile(tmp_path, monkeypatch)
    win64 = fixed_palserver_dir("default") / "Pal" / "Binaries" / "Win64"
    user_mod = win64 / "Mods" / "UserMod" / "Scripts" / "main.lua"
    user_mod.parent.mkdir(parents=True)
    user_mod.write_text("user", encoding="utf-8")
    (win64 / "UE4SS.dll").write_bytes(b"old")
    (win64 / "UE4SS-settings.ini").write_text("bUseUObjectArrayCache = true\n", encoding="utf-8")
    (win64 / ".palsitter-mods.json").write_text(
        json.dumps(
            {
                "version": "v3.0.1",
                "layout": "flat",
                "paths": ["dwmapi.dll", "UE4SS.dll", "UE4SS-settings.ini", "Mods"],
            }
        ),
        encoding="utf-8",
    )
    archive = _zip_bytes(
        {
            "dwmapi.dll": b"new proxy",
            "ue4ss/UE4SS.dll": b"new",
            "ue4ss/UE4SS-settings.ini": b"bUseUObjectArrayCache = true\n",
            "ue4ss/Mods/Builtin/main.lua": b"builtin",
        }
    )
    release = _release("experimental-latest", ("UE4SS_v3.0.1-101.zip", "2026"))
    service = UE4SSService(
        profile,
        session=FakeSession(
            {
                "https://api.github.com/repos/UE4SS-RE/RE-UE4SS/releases/tags/experimental-latest": FakeResponse(payload=release),
                "https://downloads.invalid/UE4SS_v3.0.1-101.zip": FakeResponse(content=archive),
            }
        ),
        running_probe=lambda _: False,
        platform_supported=True,
    )

    service.install("experimental-latest")

    assert (win64 / "ue4ss" / "Mods" / "UserMod" / "Scripts" / "main.lua").read_text() == "user"
    assert not (win64 / "UE4SS.dll").exists()
    assert not (win64 / "Mods").exists()


def test_status_lists_mods_and_manual_install_has_unknown_version(tmp_path, monkeypatch):
    profile = _installed_profile(tmp_path, monkeypatch)
    root = fixed_palserver_dir("default")
    win64 = root / "Pal" / "Binaries" / "Win64"
    mods = win64 / "Mods"
    for name in ("zebra", "Alpha", "shared"):
        (mods / name).mkdir(parents=True, exist_ok=True)
    (win64 / "UE4SS.dll").write_bytes(b"manual")
    paks = root / "Pal" / "Content" / "Paks"
    logic = paks / "LogicMods"
    tilde_mods = paks / "~mods"
    logic.mkdir(parents=True)
    tilde_mods.mkdir()
    (paks / "Custom.PAK").write_bytes(b"pak")
    (paks / "Pal-WindowsServer.pak").write_bytes(b"game")
    (paks / "disabled.pak.disabled").write_bytes(b"disabled")
    (logic / "Blueprint.pak").write_bytes(b"logic")
    (tilde_mods / "CreativeMenu_P.pak").write_bytes(b"tilde")

    status = UE4SSService(profile, platform_supported=True).status()

    assert status.ue4ss_installed is True
    assert status.ue4ss_version is None
    assert status.ue4ss_layout == "flat"
    assert [mod.name for mod in status.lua_mods] == ["Alpha", "zebra"]
    assert [(mod.name, mod.enabled) for mod in status.pak_mods] == [
        ("Custom.PAK", True),
        ("disabled.pak.disabled", False),
        ("LogicMods/Blueprint.pak", True),
        ("~mods/CreativeMenu_P.pak", True),
    ]


def test_linux_status_keeps_pak_management_available_and_ue4ss_unsupported(tmp_path, monkeypatch):
    monkeypatch.setattr("module.games.palworld.config.WINDOWS", False)
    profile = _installed_profile(tmp_path, monkeypatch)
    root = fixed_palserver_dir("default")
    paks = root / "Pal" / "Content" / "Paks"
    paks.mkdir(parents=True)
    (paks / "LinuxPak.pak").write_bytes(b"pak")
    service = UE4SSService(profile, platform_supported=False)

    status = service.status()

    assert status.server_installed is True
    assert status.supported is False
    assert "native Linux" in status.reason
    assert [(mod.name, mod.enabled) for mod in status.pak_mods] == [("LinuxPak.pak", True)]

    disabled = service.set_pak_enabled("LinuxPak.pak", False)

    assert (disabled.name, disabled.enabled) == ("LinuxPak.pak.disabled", False)
    assert (paks / "LinuxPak.pak.disabled").exists()
    with pytest.raises(RuntimeError, match="native Linux"):
        service.install("experimental-latest")


def test_pak_toggle_renames_disabled_suffix_and_delete_is_scoped(tmp_path, monkeypatch):
    profile = _installed_profile(tmp_path, monkeypatch)
    paks = fixed_palserver_dir("default") / "Pal" / "Content" / "Paks"
    tilde_mods = paks / "~mods"
    tilde_mods.mkdir(parents=True)
    enabled_path = tilde_mods / "CreativeMenu_P.pak"
    enabled_path.write_bytes(b"pak")
    service = UE4SSService(profile, platform_supported=True)

    disabled = service.set_pak_enabled("~mods/CreativeMenu_P.pak", False)

    disabled_path = tilde_mods / "CreativeMenu_P.pak.disabled"
    assert (disabled.name, disabled.enabled) == (
        "~mods/CreativeMenu_P.pak.disabled",
        False,
    )
    assert disabled_path.read_bytes() == b"pak"
    assert not enabled_path.exists()
    assert [(mod.name, mod.enabled) for mod in service.status().pak_mods] == [
        ("~mods/CreativeMenu_P.pak.disabled", False)
    ]

    enabled = service.set_pak_enabled(disabled.name, True)

    assert enabled.name == "~mods/CreativeMenu_P.pak"
    assert enabled.enabled is True
    assert enabled_path.exists()
    assert not disabled_path.exists()

    service.delete_pak(enabled.name)

    assert not enabled_path.exists()
    assert service.status().pak_mods == ()


def test_pak_toggle_rejects_collisions_game_files_and_unsafe_paths(tmp_path, monkeypatch):
    profile = _installed_profile(tmp_path, monkeypatch)
    paks = fixed_palserver_dir("default") / "Pal" / "Content" / "Paks"
    paks.mkdir(parents=True)
    (paks / "Collision.pak").write_bytes(b"enabled")
    (paks / "Collision.pak.disabled").write_bytes(b"disabled")
    (paks / "Pal-WindowsServer.pak").write_bytes(b"game")
    service = UE4SSService(profile, platform_supported=True)

    with pytest.raises(FileExistsError, match="already exists"):
        service.set_pak_enabled("Collision.pak", False)
    with pytest.raises(ValueError, match="Invalid"):
        service.delete_pak("Pal-WindowsServer.pak")
    with pytest.raises(ValueError, match="Invalid"):
        service.delete_pak("../outside.pak")


def test_uninstall_removes_ue4ss_and_lua_mods_but_preserves_paks(tmp_path, monkeypatch):
    profile = _installed_profile(tmp_path, monkeypatch)
    root = fixed_palserver_dir("default")
    win64 = root / "Pal" / "Binaries" / "Win64"
    (win64 / "ue4ss" / "Mods" / "UserMod").mkdir(parents=True)
    (win64 / "ue4ss" / "UE4SS.dll").write_bytes(b"dll")
    (win64 / "dwmapi.dll").write_bytes(b"proxy")
    (win64 / ".palsitter-mods.json").write_text(
        json.dumps({"version": "experimental-latest", "layout": "nested", "paths": ["dwmapi.dll", "ue4ss"]}),
        encoding="utf-8",
    )
    pak = root / "Pal" / "Content" / "Paks" / "Keep.pak"
    pak.parent.mkdir(parents=True)
    pak.write_bytes(b"keep")
    service = UE4SSService(profile, running_probe=lambda _: False, platform_supported=True)

    service.uninstall()

    assert not (win64 / "ue4ss").exists()
    assert not (win64 / "dwmapi.dll").exists()
    assert not (win64 / ".palsitter-mods.json").exists()
    assert pak.read_bytes() == b"keep"


def test_install_rejects_running_server_before_network_access(tmp_path, monkeypatch):
    profile = _installed_profile(tmp_path, monkeypatch)
    session = FakeSession([])
    service = UE4SSService(
        profile,
        session=session,
        running_probe=lambda _: True,
        platform_supported=True,
    )

    with pytest.raises(RuntimeError, match="Stop"):
        service.install("experimental-latest")

    assert session.calls == []


def test_install_rejects_unsafe_archive_without_writing_server_files(tmp_path, monkeypatch):
    profile = _installed_profile(tmp_path, monkeypatch)
    archive = _zip_bytes({"../escape.txt": b"bad", "UE4SS.dll": b"dll", "UE4SS-settings.ini": b"x"})
    release = _release("v3.0.1", ("UE4SS_v3.0.1.zip", "2026"))
    service = UE4SSService(
        profile,
        session=FakeSession(
            {
                "https://api.github.com/repos/UE4SS-RE/RE-UE4SS/releases/tags/v3.0.1": FakeResponse(payload=release),
                "https://downloads.invalid/UE4SS_v3.0.1.zip": FakeResponse(content=archive),
            }
        ),
        running_probe=lambda _: False,
        platform_supported=True,
    )

    with pytest.raises(ValueError, match="unsafe"):
        service.install("v3.0.1")

    assert not (fixed_palserver_dir("default") / "escape.txt").exists()
    assert not (fixed_palserver_dir("default") / "Pal" / "Binaries" / "Win64" / "UE4SS.dll").exists()


def test_release_and_download_http_failures_are_reported(tmp_path, monkeypatch):
    profile = _installed_profile(tmp_path, monkeypatch)
    with pytest.raises(requests.HTTPError):
        UE4SSService(
            profile,
            session=FakeSession([FakeResponse(status=503)]),
            platform_supported=True,
        ).list_releases()

    release = _release("v3.0.1", ("UE4SS_v3.0.1.zip", "2026"))
    service = UE4SSService(
        profile,
        session=FakeSession(
            {
                "https://api.github.com/repos/UE4SS-RE/RE-UE4SS/releases/tags/v3.0.1": FakeResponse(payload=release),
                "https://downloads.invalid/UE4SS_v3.0.1.zip": FakeResponse(status=500),
            }
        ),
        running_probe=lambda _: False,
        platform_supported=True,
    )
    with pytest.raises(requests.HTTPError):
        service.install("v3.0.1")


def test_object_cache_patch_is_case_insensitive_and_preserves_newlines():
    assert patch_object_cache_setting("A = 1\r\nBUSEUOBJECTARRAYCACHE=true\r\n") == (
        "A = 1\r\nbUseUObjectArrayCache = false\r\n"
    )
    assert patch_object_cache_setting("A = 1\n") == "A = 1\nbUseUObjectArrayCache = false\n"
