from __future__ import annotations

import json
import subprocess
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


def test_packaged_git_refresh_allows_the_backend_repository_owner_to_differ():
    source = (DESKTOP / "main.js").read_text(encoding="utf-8")

    assert "`safe.directory=${path.resolve(backendRoot())}`" in source


def test_exit_uses_the_shared_shutdown_workflow():
    package = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))
    files = set(package["build"]["files"])
    source = (DESKTOP / "main.js").read_text(encoding="utf-8")

    assert "main.js" in files
    assert "http://${WEB_HOST}:${controlPort}/desktop/shutdown" in source
    assert "http://${WEB_HOST}:${controlPort}/desktop/gui-only" in source
    assert "http://${WEB_HOST}:${controlPort}/desktop/force-shutdown" in source
    assert "buttons: ['Cancel', 'GUI only', 'Stop all']" in source
    assert "taskkill.exe" in source
    assert "forceExitAfterShutdownFailure" in source


def test_startup_handles_port_conflicts_with_kill_and_alternate_port_prompts():
    source = (DESKTOP / "main.js").read_text(encoding="utf-8")

    assert "reservePortWithPrompt" in source
    assert "--kill-port" in source
    assert "startupText('conflictTitle')" in source
    assert "startupText('alternateTitle')" in source
    assert "return reservePort(0);" in source
    assert "class StartupCancelledError" in source


def test_electron_reloads_after_the_shared_restart_exit():
    source = (DESKTOP / "main.js").read_text(encoding="utf-8")

    assert "async function restartBackend()" in source
    assert "reserveRestartPort" in source
    assert "backend.on('close', (code)" in source
    assert "if (code === 75 && !exiting && !backendRestarting)" in source
    assert "await mainWindow.loadURL(`http://${WEB_HOST}:${webPort}/`)" in source


def test_runtime_builder_exposes_backend_to_embedded_python():
    script = (DESKTOP / "scripts" / "build-runtime.ps1").read_text(encoding="utf-8")

    assert '"../backend"' in script
    assert '"python312.zip"' in script


def test_release_scripts_stage_source_and_bundle_git():
    assert (DESKTOP / "scripts" / "prepare-source.ps1").is_file()
    assert (DESKTOP / "scripts" / "build-git.ps1").is_file()


def test_packaged_git_metadata_removes_credentials_and_rejects_credentialed_remotes():
    script = (DESKTOP / "scripts" / "prepare-source.ps1").read_text(encoding="utf-8")
    copied = script.index("Copy-Item -LiteralPath (Join-Path $repositoryRoot '.git')")
    remote = script.index("remote set-url origin", copied)
    assert copied < remote
    assert "$repositoryRoot remote set-url" not in script

    for key in (
        "credential.helper",
        "http.extraheader",
        "http.https://github.com/.extraheader",
    ):
        assert f'"{key}"' in script
    assert "config --local --unset-all" in script
    assert "embedded credentials" in script
    assert "https://github.com/ken1882/palsitter.git" in script
    assert "contains credential material" in script


def test_generated_git_metadata_has_no_credential_config(tmp_path):
    metadata = tmp_path / "git-metadata"
    subprocess.run(["git", "init", "--quiet", str(metadata)], check=True)
    for key, value in (
        ("credential.helper", "store"),
        ("http.extraheader", "Authorization: Basic redacted"),
        ("http.https://github.com/.extraheader", "Authorization: Bearer redacted"),
    ):
        subprocess.run(["git", "-C", str(metadata), "config", key, value], check=True)

    for key in (
        "credential.helper",
        "http.extraheader",
        "http.https://github.com/.extraheader",
    ):
        subprocess.run(
            ["git", "-C", str(metadata), "config", "--unset-all", key],
            check=True,
        )

    result = subprocess.run(
        [
            "git",
            "-C",
            str(metadata),
            "config",
            "--get-regexp",
            r"(^credential\.helper$|^http\..*\.extraheader$)",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert result.stdout == ""
