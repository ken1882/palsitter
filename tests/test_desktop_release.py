from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"


def test_desktop_package_keeps_palsitter_source_unpacked():
    package = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))

    assert package["build"]["asar"] is False
    assert "main.js" in package["build"]["files"]
    resources = package["build"]["extraResources"]
    source = next(item for item in resources if item["to"] == "backend")
    assert source["from"] == "source"
    history = next(item for item in resources if item["to"] == "backend/.git")
    assert history["from"] == "git-metadata"
    assert {item["to"] for item in resources} >= {
        "backend/.git",
        "python",
        "git",
    }
    assert package["build"]["win"]["electronLanguages"] == ["en-US", "ja", "zh-CN", "zh-TW"]


def test_desktop_source_and_release_icon_exist():
    assert (DESKTOP / "main.js").is_file()
    assert (DESKTOP / "assets" / "palsitter.png").is_file()
    assert (DESKTOP / "build-resources" / "palsitter.ico").is_file()
    assert (ROOT / "profile" / "template" / "palworld.json").is_file()
    assert (ROOT / "profile" / "template" / "satisfactory.json").is_file()
    prepare_source = (DESKTOP / "scripts" / "prepare-source.ps1").read_text(
        encoding="utf-8"
    )
    assert '"/profile/template/"' in prepare_source


def test_desktop_runtime_build_checks_python_abi_and_packaged_imports():
    build_batch = (ROOT / "build.bat").read_text(encoding="utf-8")
    build_runtime = (DESKTOP / "scripts" / "build-runtime.ps1").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT / ".github" / "workflows" / "windows-release.yml").read_text(
        encoding="utf-8"
    )

    assert 'if not "%PYTHON_ABI%"=="3.12"' in build_batch
    assert "npm.cmd --prefix desktop ci" in build_batch
    assert "desktop\\scripts\\build-runtime.ps1" in build_batch
    assert "desktop\\scripts\\build-git.ps1" in build_batch
    assert "desktop\\scripts\\prepare-source.ps1" in build_batch
    assert "npm.cmd --prefix desktop run build:win" in build_batch
    assert "desktop\\scripts\\archive-release.ps1" in build_batch
    assert (
        '"desktop\\dist\\win-unpacked\\resources\\python\\python.exe" '
        '-c "import psutil, pywebio, requests, winpty"'
    ) in build_batch
    assert "$buildPythonAbi -ne $expectedPythonAbi" in build_runtime
    assert "import psutil, pywebio, requests, winpty" in build_runtime
    assert (
        "win-unpacked\\resources\\python\\python.exe -c "
        '"import psutil, pywebio, requests, winpty"'
    ) in workflow


def test_desktop_exits_electron_after_backend_and_only_taskkills_backend():
    source = (DESKTOP / "main.js").read_text(encoding="utf-8")

    assert "taskkill.exe', ['/PID', String(backend.pid), '/T', '/F']" in source
    assert "await waitForBackendExit();\n    finishExit();" in source
    finish = source[source.index("function finishExit()") :]
    assert "mainWindow.close()" in finish
    assert "tray.destroy()" in finish
    assert "app.quit()" in finish
