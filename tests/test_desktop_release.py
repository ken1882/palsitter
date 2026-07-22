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
        "backend/config/.gitkeep",
        "python",
        "git",
    }


def test_desktop_source_and_release_icon_exist():
    assert (DESKTOP / "main.js").is_file()
    assert (DESKTOP / "assets" / "palsitter.png").is_file()
    assert (DESKTOP / "build-resources" / "palsitter.ico").is_file()


def test_packaged_data_stays_next_to_the_portable_executable():
    source = (DESKTOP / "main.js").read_text(encoding="utf-8")

    assert "app.setPath('userData'" in source
    assert "path.dirname(process.execPath)" in source
    assert "path.join(path.dirname(process.execPath), 'data')" in source


def test_exit_uses_the_shared_shutdown_workflow():
    package = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))
    files = set(package["build"]["files"])
    source = (DESKTOP / "main.js").read_text(encoding="utf-8")

    assert "main.js" in files
    assert "http://${WEB_HOST}:${controlPort}/desktop/shutdown" in source


def test_startup_handles_port_conflicts_with_kill_and_alternate_port_prompts():
    source = (DESKTOP / "main.js").read_text(encoding="utf-8")

    assert "reservePortWithPrompt" in source
    assert "--kill-port" in source
    assert "startupText('conflictTitle')" in source
    assert "startupText('alternateTitle')" in source
    assert "return reservePort(0);" in source
    assert "class StartupCancelledError" in source


def test_runtime_builder_exposes_backend_to_embedded_python():
    script = (DESKTOP / "scripts" / "build-runtime.ps1").read_text(encoding="utf-8")

    assert '"../backend"' in script
    assert '"python312.zip"' in script


def test_release_scripts_stage_source_and_bundle_git():
    assert (DESKTOP / "scripts" / "prepare-source.ps1").is_file()
    assert (DESKTOP / "scripts" / "build-git.ps1").is_file()
