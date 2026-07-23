from __future__ import annotations

import shutil
from pathlib import Path

from pywebio.output import put_button, put_row, put_scope, toast, use_scope
from pywebio.pin import pin, put_input
from pywebio.session import local

from module.games.palworld.backup import BackupService
from module.games.palworld.config import DEDICATED_SERVER_NAME_RE, load_profile, save_profile
from module.games.palworld.saves import (
    LEVEL_FILENAME,
    ManagedWorldService,
    import_world_settings_ini,
    inspect_import_source,
)
from module.games.palworld.worldsettings import migrate_world_option_sav_to_ini
from module.instances import create_instance, delete_instance, profile_dir
from module.webui.game_ui import InstanceCreationUI
from module.webui.i18n import t
from module.webui.assets import client_call
from module.webui.file_browser import _browse_normalize_path


def render_fields() -> None:
    import_path = str(
        getattr(
            local,
            "add_import_reopen_path",
            getattr(pin, "add_import_path", ""),
        )
        or ""
    )
    with use_scope("add_server_import"):
        put_scope(
            "add_import_panel",
            [
                put_row(
                    [
                        put_input(
                            "add_import_path",
                            label=t("add.import_path"),
                            value=import_path,
                            placeholder=t("add.import_path_placeholder"),
                        ),
                        None,
                        put_button(
                            t("add.browse_save"),
                            onclick=lambda: _open_import_browser(),
                            color="secondary",
                        ),
                    ],
                    size="1fr .5rem auto",
                ),
            ],
        )
        client_call("dom.addClasses", scope="add_import_panel", classes=["add-import-panel"])


def _open_import_browser() -> None:
    from module.webui.file_browser import open_browser
    from module.webui.add_instance import reopen_add_server

    local.add_server_reopen_values = {
        "name": str(getattr(pin, "add_server_name", "") or ""),
        "game": str(getattr(pin, "add_server_game", "palworld") or "palworld"),
        "origin": str(getattr(pin, "add_server_origin", "template") or "template"),
    }
    local.add_import_reopen_path = str(getattr(pin, "add_import_path", "") or "")

    open_browser(
        "add_import_path",
        mode="file",
        label=t("add.import_path"),
        allowed_names=(LEVEL_FILENAME,),
        on_close=_reopen_import_add_server,
    )


def _reopen_import_add_server() -> None:
    from module.webui.add_instance import reopen_add_server

    browse_state = getattr(local, "browse", {})
    local.add_import_reopen_path = str(
        browse_state.get("selected_path", local.add_import_reopen_path)
    )
    reopen_add_server()
    local.add_import_reopen_path = None


def create(name: str, origin: str) -> bool | None:
    import_source = str(pin.add_import_path or "").strip()
    if not import_source:
        create_instance(name, "palworld", origin)
        return None
    try:
        level_path = _browse_normalize_path(import_source)
    except (OSError, ValueError):
        raise RuntimeError(t("add.select_level_save"))
    if level_path.name != LEVEL_FILENAME or not level_path.is_file():
        raise RuntimeError(t("add.select_level_save"))
    source_info = inspect_import_source(level_path)
    world_id = source_info.world.world_id
    if DEDICATED_SERVER_NAME_RE.fullmatch(world_id) is None:
        raise RuntimeError(t("add.invalid_import_layout"))
    created = False
    try:
        create_instance(name, "palworld", "template")
        created = True
        profile = load_profile(name)
        ManagedWorldService(
            profile,
            backup_service=BackupService(profile),
            is_server_active=lambda: False,
        ).import_world(level_path.parent, world_id=world_id, activate=True)
        if migrate_world_option_sav_to_ini(profile) is None:
            import_world_settings_ini(profile, level_path)
        save_profile(profile)
        if source_info.kind == "single_player":
            toast(t("add.single_player_message"), color="warning")
        elif source_info.kind == "coop":
            toast(t("add.coop_message"), color="warning")
    except Exception:
        if created:
            try:
                delete_instance(name)
            except FileNotFoundError:
                pass
            created_root = profile_dir(name)
            if created_root.exists():
                shutil.rmtree(created_root)
        raise
    return True


CREATION_UI = InstanceCreationUI(render_fields=render_fields, create=create)

__all__ = ["CREATION_UI"]
