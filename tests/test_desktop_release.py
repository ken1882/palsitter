from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"


def test_desktop_package_keeps_palsitter_source_unpacked():
    package = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))

    assert package["build"]["asar"] is False
    assert "main.js" in package["build"]["files"]
    assert "self-updater.js" in package["build"]["files"]
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


def test_noupdate_package_omits_all_git_updater_payloads():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to evaluate the Electron build configuration")
    env = {**os.environ, "PALSITTER_BUILD_VARIANT": "noupdate"}
    result = subprocess.run(
        [
            node,
            "-e",
            "const config=require('./electron-builder.config.js')();"
            "console.log(JSON.stringify(config));",
        ],
        cwd=DESKTOP,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    config = json.loads(result.stdout)

    assert "self-updater.js" not in config["files"]
    assert {item["to"] for item in config["extraResources"]}.isdisjoint(
        {"backend/.git", "git"}
    )
    assert {item["to"] for item in config["extraResources"]} >= {"backend", "python"}


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


def test_noupdate_build_selects_variant_and_validates_archive_contents():
    wrapper = (ROOT / "build-noupdate.bat").read_text(encoding="utf-8")
    build_batch = (ROOT / "build.bat").read_text(encoding="utf-8")
    prepare_source = (DESKTOP / "scripts" / "prepare-source.ps1").read_text(
        encoding="utf-8"
    )
    main = (DESKTOP / "main.js").read_text(encoding="utf-8")

    assert 'set "PALSITTER_BUILD_VARIANT=noupdate"' in wrapper
    assert 'call "%~dp0build.bat"' in wrapper
    assert "Palsitter-win-x64-noupdate.7z" in build_batch
    assert '"desktop\\scripts\\prepare-source.ps1" -NoUpdate' in build_batch
    assert "resources\\backend\\.git" in build_batch
    assert "resources\\git" in build_batch
    assert "module\\webui\\pages\\updater.py" in build_batch
    assert "__pycache__\\updater.*.pyc" in build_batch
    assert "resources\\app\\self-updater.js" in build_batch
    assert "[switch]$NoUpdate" in prepare_source
    assert "module\\webui\\pages\\updater.py" in prepare_source
    assert ".palsitter-noupdate" in prepare_source
    assert "PALSITTER_GIT" not in main
    assert (DESKTOP / "self-updater.js").is_file()


def test_desktop_exits_electron_after_backend_and_only_taskkills_backend():
    source = (DESKTOP / "main.js").read_text(encoding="utf-8")

    assert "taskkill.exe', ['/PID', String(backend.pid), '/T', '/F']" in source
    assert "await waitForBackendExit();\n    finishExit();" in source
    finish = source[source.index("function finishExit()") :]
    assert "mainWindow.close()" in finish
    assert "tray.destroy()" in finish
    assert "app.quit()" in finish
