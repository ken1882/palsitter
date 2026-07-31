from __future__ import annotations

import os
from pathlib import Path


NO_UPDATE_MARKER = ".palsitter-noupdate"


def self_update_available() -> bool:
    """Return whether this distribution includes Palsitter's Git updater."""
    backend_root = Path(__file__).resolve().parents[2]
    if (backend_root / NO_UPDATE_MARKER).is_file():
        return False
    override = os.getenv("PALSITTER_SELF_UPDATE")
    if override is not None:
        return override.strip().casefold() not in {"0", "false", "no", "off"}
    return True


__all__ = ["NO_UPDATE_MARKER", "self_update_available"]
