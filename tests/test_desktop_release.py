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
    assert {item["to"] for item in resources} >= {
        "backend/gui.py",
        "backend/module",
        "backend/assets",
        "python",
    }


def test_desktop_source_and_release_icon_exist():
    assert (DESKTOP / "main.js").is_file()
    assert (DESKTOP / "assets" / "palsitter.png").is_file()
    assert (DESKTOP / "build-resources" / "palsitter.ico").is_file()


def test_runtime_builder_exposes_backend_to_embedded_python():
    script = (DESKTOP / "scripts" / "build-runtime.ps1").read_text(encoding="utf-8")

    assert '"../backend"' in script
    assert '"python312.zip"' in script
