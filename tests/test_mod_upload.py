import gzip
import io
import json
import tarfile
import threading
import zipfile
from pathlib import Path

import py7zr
import pytest

import module.games.palworld.mods.upload as upload_module
from module.games.palworld.config import PalworldProfile, fixed_palserver_dir
from module.games.palworld.mods import (
    ModUploadService,
    PAK_LOGIC_MODS,
    PAK_ROOT,
    PALSCHEMA_REQUIRED,
    UNSUPPORTED_FORMAT,
    UploadFile,
    UploadItem,
)


def _profile(tmp_path, monkeypatch, *, ue4ss=True, palschema=False):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    profile = PalworldProfile(name="default")
    root = fixed_palserver_dir(profile.name)
    root.mkdir(parents=True)
    if ue4ss:
        loader = root / "Pal" / "Binaries" / "Win64" / "ue4ss" / "UE4SS.dll"
        loader.parent.mkdir(parents=True)
        loader.write_bytes(b"loader")
        if palschema:
            runtime = loader.parent / "Mods" / "PalSchema" / "dlls" / "main.dll"
            runtime.parent.mkdir(parents=True)
            runtime.write_bytes(b"palschema")
    return profile, root


def _zip(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


def _tar(entries, mode="w"):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode=mode) as archive:
        for name, content in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def _seven_zip(tmp_path, entries):
    source = tmp_path / "seven-source"
    source.mkdir()
    for name, content in entries.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    archive = tmp_path / "mod.7z"
    with py7zr.SevenZipFile(archive, "w") as output:
        output.writeall(source, arcname="ExampleLua")
    return archive.read_bytes()


@pytest.mark.parametrize("archive_name", ["mod.zip", "mod.tar", "mod.tar.gz", "mod.tgz"])
def test_upload_installs_lua_from_zip_and_tar_formats(
    tmp_path, monkeypatch, archive_name
):
    profile, root = _profile(tmp_path, monkeypatch)
    entries = {
        "wrapper/ExampleLua/Scripts/main.lua": b"print('ok')",
        "wrapper/ExampleLua/config.json": b"{}",
    }
    payload = _zip(entries) if archive_name.endswith(".zip") else _tar(entries)
    service = ModUploadService(profile, platform_supported=True)

    inspection = service.inspect([UploadItem.archive(archive_name, payload)])
    result = service.install(inspection, {})

    installed = root / "Pal" / "Binaries" / "Win64" / "ue4ss" / "Mods" / "ExampleLua"
    assert result.installed_count == 1
    assert (installed / "Scripts" / "main.lua").read_bytes() == b"print('ok')"
    assert (installed / "enabled.txt").is_file()


def test_upload_installs_lua_from_7z_and_browser_folder(tmp_path, monkeypatch):
    profile, root = _profile(tmp_path, monkeypatch)
    service = ModUploadService(profile, platform_supported=True)
    seven = UploadItem.archive(
        "mod.7z", _seven_zip(tmp_path, {"Scripts/main.lua": b"seven"})
    )
    folder = UploadItem.folder(
        "FolderLua",
        [
            UploadFile("FolderLua/Scripts/main.lua", b"folder"),
            UploadFile("FolderLua/settings.json", b"{}"),
        ],
    )

    inspection = service.inspect([seven, folder])
    result = service.install(inspection, {})

    mods = root / "Pal" / "Binaries" / "Win64" / "ue4ss" / "Mods"
    assert result.installed_count == 2
    assert (mods / "ExampleLua" / "Scripts" / "main.lua").read_bytes() == b"seven"
    assert (mods / "FolderLua" / "Scripts" / "main.lua").read_bytes() == b"folder"


def test_standalone_gzip_and_ambiguous_raw_paks_require_destination(
    tmp_path, monkeypatch
):
    profile, root = _profile(tmp_path, monkeypatch)
    service = ModUploadService(profile, platform_supported=True)
    sources = [
        UploadItem.archive("Compressed.pak.gz", gzip.compress(b"compressed")),
        UploadItem.archive("Raw.pak", b"raw"),
    ]

    inspection = service.inspect(sources)
    assert all(unit.needs_pak_destination for unit in inspection.units)
    decisions = {
        inspection.units[0].id: PAK_LOGIC_MODS,
        inspection.units[1].id: PAK_ROOT,
    }
    result = service.install(inspection, decisions)

    paks = root / "Pal" / "Content" / "Paks"
    assert result.installed_count == 2
    assert (paks / "LogicMods" / "Compressed.pak").read_bytes() == b"compressed"
    assert (paks / "Raw.pak").read_bytes() == b"raw"


def test_official_package_is_preserved_and_enabled(tmp_path, monkeypatch):
    profile, root = _profile(tmp_path, monkeypatch)
    info = {
        "PackageName": "OfficialExample",
        "InstallRule": [{"Type": "Paks", "IsServer": True}],
    }
    payload = _zip(
        {
            "OfficialExample/Info.json": json.dumps(info).encode(),
            "OfficialExample/Paks/OfficialExample.pak": b"pak",
        }
    )
    service = ModUploadService(profile, platform_supported=True)

    inspection = service.inspect([UploadItem.archive("official.zip", payload)])
    result = service.install(inspection, {})

    package = root / "Mods" / "Workshop" / "OfficialExample"
    settings = (root / "Mods" / "PalModSettings.ini").read_text(encoding="utf-8")
    assert result.installed_count == 1
    assert (package / "Info.json").is_file()
    assert "bGlobalEnableMod=true" in settings
    assert "ActiveModList=OfficialExample" in settings


def test_existing_folder_conflict_is_reported_and_replaced_whole(
    tmp_path, monkeypatch
):
    profile, root = _profile(tmp_path, monkeypatch)
    existing = root / "Pal" / "Binaries" / "Win64" / "ue4ss" / "Mods" / "ReplaceMe"
    existing.mkdir(parents=True)
    (existing / "old-config.json").write_text("old", encoding="utf-8")
    source = UploadItem.archive(
        "replace.zip", _zip({"ReplaceMe/Scripts/main.lua": b"new"})
    )
    service = ModUploadService(profile, platform_supported=True)

    inspection = service.inspect([source])
    assert service.conflicts(inspection, {}) == ("ReplaceMe",)
    result = service.install(inspection, {})

    assert result.installed_count == 1
    assert not (existing / "old-config.json").exists()
    assert (existing / "Scripts" / "main.lua").read_bytes() == b"new"


def test_batch_continues_after_unsupported_item_and_cancel_skips_remaining(
    tmp_path, monkeypatch
):
    profile, root = _profile(tmp_path, monkeypatch)
    service = ModUploadService(profile, platform_supported=True)
    sources = [
        UploadItem.archive("manual.rar", b"not-rar"),
        UploadItem.archive("Good.pak", b"good"),
        UploadItem.archive("Later.pak", b"later"),
    ]
    inspection = service.inspect(sources)
    assert inspection.items[0].error == UNSUPPORTED_FORMAT
    decisions = {unit.id: PAK_ROOT for unit in inspection.units}
    cancel = threading.Event()

    def progress(index, total, phase, name):
        if name == "Good.pak" and phase == "cleanup":
            cancel.set()

    result = service.install(
        inspection, decisions, cancel_event=cancel, progress=progress
    )

    assert [item.status for item in result.items] == ["failed", "success", "skipped"]
    paks = root / "Pal" / "Content" / "Paks"
    assert (paks / "Good.pak").is_file()
    assert not (paks / "Later.pak").exists()


def test_upload_rejects_traversal_and_nested_archives(tmp_path, monkeypatch):
    profile, _ = _profile(tmp_path, monkeypatch)
    service = ModUploadService(profile, platform_supported=True)
    traversal = UploadItem.archive("bad.zip", _zip({"../escape.pak": b"bad"}))
    nested = UploadItem.archive(
        "nested.zip", _zip({"inner.zip": _zip({"Mod.pak": b"pak"})})
    )

    inspection = service.inspect([traversal, nested])

    assert "Unsafe upload path" in inspection.items[0].error
    assert inspection.items[1].error == UNSUPPORTED_FORMAT


def test_native_linux_accepts_pak_but_rejects_lua(tmp_path, monkeypatch):
    profile, root = _profile(tmp_path, monkeypatch)
    service = ModUploadService(profile, platform_supported=False)
    sources = [
        UploadItem.archive("Linux.pak", b"pak"),
        UploadItem.archive(
            "Lua.zip", _zip({"ExampleLua/Scripts/main.lua": b"lua"})
        ),
    ]
    inspection = service.inspect(sources)
    decisions = {inspection.items[0].units[0].id: PAK_ROOT}

    result = service.install(inspection, decisions)

    assert [item.status for item in result.items] == ["success", "failed"]
    assert (root / "Pal" / "Content" / "Paks" / "Linux.pak").is_file()
    assert "native Linux" in result.items[1].message


def test_composite_explicit_layout_installs_cpp_palschema_and_pak(
    tmp_path, monkeypatch
):
    profile, root = _profile(tmp_path, monkeypatch, palschema=True)
    payload = _zip(
        {
            "Pal/Binaries/Win64/ue4ss/Mods/CppMod/dlls/main.dll": b"dll",
            "Pal/Binaries/Win64/ue4ss/Mods/PalSchema/mods/SchemaMod/data.json": b"{}",
            "Pal/Content/Paks/~mods/Composite_P.pak": b"pak",
        }
    )
    service = ModUploadService(profile, platform_supported=True)

    inspection = service.inspect([UploadItem.archive("composite.zip", payload)])
    assert {unit.kind for unit in inspection.units} == {"ue4ss", "palschema", "pak"}
    assert all(not unit.needs_pak_destination for unit in inspection.units)
    result = service.install(inspection, {})

    mods = root / "Pal" / "Binaries" / "Win64" / "ue4ss" / "Mods"
    assert result.installed_count == 1
    assert (mods / "CppMod" / "dlls" / "main.dll").read_bytes() == b"dll"
    assert (mods / "PalSchema" / "mods" / "SchemaMod" / "data.json").is_file()
    assert (root / "Pal" / "Content" / "Paks" / "~mods" / "Composite_P.pak").is_file()


def test_palschema_documented_folder_and_hybrid_pak_layout(
    tmp_path, monkeypatch
):
    profile, root = _profile(tmp_path, monkeypatch, palschema=True)
    payload = _zip(
        {
            "Wrapper/SchemaMod/raw/tables.json": b"{}",
            "Wrapper/SchemaMod/paks/SchemaMod.pak": b"pak",
        }
    )
    service = ModUploadService(profile, platform_supported=True)

    inspection = service.inspect([UploadItem.archive("schema.zip", payload)])

    assert len(inspection.units) == 1
    unit = inspection.units[0]
    assert unit.kind == "palschema"
    assert unit.name == "SchemaMod"
    assert not unit.needs_pak_destination

    result = service.install(inspection, {})

    assert result.installed_count == 1
    target = root / "Pal" / "Binaries" / "Win64" / "ue4ss" / "Mods" / "PalSchema" / "mods" / "SchemaMod"
    assert (target / "raw" / "tables.json").is_file()
    assert (target / "paks" / "SchemaMod.pak").is_file()


def test_palschema_upload_is_blocked_until_runtime_is_installed(tmp_path, monkeypatch):
    profile, root = _profile(tmp_path, monkeypatch)
    payload = _zip({"SchemaMod/raw/tables.json": b"{}"})
    service = ModUploadService(profile, platform_supported=True)

    inspection = service.inspect([UploadItem.archive("schema.zip", payload)])
    result = service.install(inspection, {})

    assert result.items[0].status == "failed"
    assert result.items[0].message == PALSCHEMA_REQUIRED
    assert not (
        root
        / "Pal"
        / "Binaries"
        / "Win64"
        / "ue4ss"
        / "Mods"
        / "PalSchema"
        / "mods"
        / "SchemaMod"
    ).exists()


def test_failed_multi_file_commit_rolls_back_and_removes_transaction_temp(
    tmp_path, monkeypatch
):
    profile, root = _profile(tmp_path, monkeypatch)
    destination = root / "Pal" / "Content" / "Paks" / "LogicMods"
    destination.mkdir(parents=True)
    (destination / "Bundle.pak").write_bytes(b"old-pak")
    (destination / "Bundle.utoc").write_bytes(b"old-utoc")
    source = UploadItem.archive(
        "bundle.zip",
        _zip(
            {
                "Pal/Content/Paks/LogicMods/Bundle.pak": b"new-pak",
                "Pal/Content/Paks/LogicMods/Bundle.utoc": b"new-utoc",
            }
        ),
    )
    service = ModUploadService(profile, platform_supported=True)
    inspection = service.inspect([source])
    original_replace = upload_module._SwapTransaction.replace
    calls = 0

    def fail_second(self, staged, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated commit failure")
        return original_replace(self, staged, target)

    monkeypatch.setattr(upload_module._SwapTransaction, "replace", fail_second)

    result = service.install(inspection, {})

    assert result.items[0].status == "failed"
    assert (destination / "Bundle.pak").read_bytes() == b"old-pak"
    assert (destination / "Bundle.utoc").read_bytes() == b"old-utoc"
    assert list(root.glob(".palsitter-upload-*")) == []


def test_tar_links_and_case_colliding_folder_paths_are_rejected(tmp_path, monkeypatch):
    profile, _ = _profile(tmp_path, monkeypatch)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        link = tarfile.TarInfo("Linked.pak")
        link.type = tarfile.SYMTYPE
        link.linkname = "target.pak"
        archive.addfile(link)
    service = ModUploadService(profile, platform_supported=True)
    folder = UploadItem.folder(
        "Collision",
        [UploadFile("Collision.pak", b"one"), UploadFile("collision.PAK", b"two")],
    )

    inspection = service.inspect(
        [UploadItem.archive("link.tar", output.getvalue()), folder]
    )

    assert "links" in inspection.items[0].error
    assert "Duplicate upload path" in inspection.items[1].error
