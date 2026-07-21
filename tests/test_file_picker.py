import os

from module.webui.file_browser import (
    ENTRY_LIMIT,
    _browse_normalize_path,
    autocomplete_suggestions,
    scan_directory,
    validate_selection,
)


def test_normalize_path_accepts_wsl_mount_paths_on_windows():
    if os.name != "nt":
        return

    normalized = _browse_normalize_path("/mnt/g/SteamLibrary/PalServer")

    assert str(normalized).casefold().startswith("g:\\steamlibrary\\palserver")


def _request(path, **overrides):
    request = {
        "request_id": 1,
        "target_path": str(path),
        "base_dir": None,
        "show_hidden": False,
        "filter_text": "",
        "allowed_extensions": set(),
    }
    request.update(overrides)
    return request


def test_scan_sorts_filters_and_hides_entries(tmp_path):
    (tmp_path / "Zoo").mkdir()
    (tmp_path / "alpha").mkdir()
    (tmp_path / "B.EXE").write_text("", encoding="utf-8")
    (tmp_path / "a.exe").write_text("", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")
    (tmp_path / ".hidden.exe").write_text("", encoding="utf-8")

    result = scan_directory(_request(tmp_path, allowed_extensions={".exe"}))

    assert result["status"] == "success"
    assert [(entry["kind"], entry["name"]) for entry in result["entries"]] == [
        ("dir", "alpha"),
        ("dir", "Zoo"),
        ("file", "a.exe"),
        ("file", "B.EXE"),
    ]

    filtered = scan_directory(
        _request(
            tmp_path,
            show_hidden=True,
            filter_text="hidden",
            allowed_extensions={".exe"},
        )
    )
    assert [entry["name"] for entry in filtered["entries"]] == [".hidden.exe"]


def test_scan_can_limit_files_to_exact_names_but_keeps_folders(tmp_path):
    (tmp_path / "world").mkdir()
    (tmp_path / "Level.sav").write_text("level", encoding="utf-8")
    (tmp_path / "level.sav.bak").write_text("backup", encoding="utf-8")
    (tmp_path / "WorldOption.sav").write_text("option", encoding="utf-8")

    result = scan_directory(_request(tmp_path, allowed_names={"level.sav"}))

    assert [(entry["kind"], entry["name"]) for entry in result["entries"]] == [
        ("dir", "world"),
        ("file", "Level.sav"),
    ]


def test_scan_caps_large_directories_after_filtering(tmp_path):
    for index in range(ENTRY_LIMIT + 5):
        (tmp_path / f"entry-{index:03}.txt").write_text("", encoding="utf-8")

    result = scan_directory(_request(tmp_path))

    assert result["status"] == "success"
    assert result["total"] == ENTRY_LIMIT + 5
    assert len(result["entries"]) == ENTRY_LIMIT


def test_scan_reports_invalid_targets_and_rejects_sandbox_escape(tmp_path):
    base = tmp_path / "base"
    outside = tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    file_path = base / "file.txt"
    file_path.write_text("", encoding="utf-8")

    assert scan_directory(_request(tmp_path / "missing"))["status"] == "missing"
    assert scan_directory(_request(file_path))["status"] == "not_directory"
    assert scan_directory(_request(outside, base_dir=base))["status"] == "outside_base"

    link = base / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pass
    else:
        assert scan_directory(_request(link, base_dir=base))["status"] == "outside_base"


def test_final_selection_revalidates_type_and_existence(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    file_path = tmp_path / "server.exe"
    file_path.write_text("", encoding="utf-8")

    assert validate_selection(folder, "dir", None) is None
    assert validate_selection(file_path, "file", None) is None
    assert validate_selection(folder, "file", None) == "select_file"
    assert validate_selection(file_path, "dir", None) == "select_folder"

    file_path.unlink()
    assert validate_selection(file_path, "file", None) == "selection_missing"


def test_autocomplete_suggestions_follow_mode_extensions_and_sandbox(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    folder = base / "Folder"
    folder.mkdir()
    exe = base / "PalServer.exe"
    exe.write_text("", encoding="utf-8")
    notes = base / "notes.txt"
    notes.write_text("", encoding="utf-8")
    outside = tmp_path / "outside.exe"
    outside.write_text("", encoding="utf-8")

    result = scan_directory(_request(base, allowed_extensions={".exe"}))
    state = {
        "base_dir": base,
        "current_dir": base,
        "visible_entries": result["entries"],
        "mode": "file",
    }

    suggestions = autocomplete_suggestions(state)

    assert f"{folder}{os.sep}" in suggestions
    assert str(exe) in suggestions
    assert str(notes) not in suggestions
    assert str(outside) not in suggestions

    state["mode"] = "dir"
    suggestions = autocomplete_suggestions(state)

    assert f"{folder}{os.sep}" in suggestions
    assert str(exe) not in suggestions
