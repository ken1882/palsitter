from __future__ import annotations

from module.webui.game_ui import GameWebUI, InstancePage


def get_webui() -> GameWebUI:
    from . import audit, auto_restart, backups, map, mods, overview, players, server_settings, world_settings
    from .add_instance import CREATION_UI

    return GameWebUI(
        pages=(
            InstancePage("overview", "nav.overview", "nav.overview", overview.render),
            InstancePage("players", "nav.players", "nav.players", players.render),
            InstancePage(
                "server_settings",
                "nav.server_settings",
                "scheduler.settings",
                server_settings.render,
            ),
            InstancePage(
                "auto_restart",
                "nav.auto_restart",
                "nav.auto_restart",
                auto_restart.render,
            ),
            InstancePage(
                "world_settings",
                "nav.world_settings",
                "nav.world_settings",
                world_settings.render,
            ),
            InstancePage("mods", "nav.mods", "nav.mods", mods.render),
            InstancePage("backups", "nav.backups", "nav.backups", backups.render),
            InstancePage("map", "nav.map", "map.page_title", map.render),
            InstancePage("audit", "nav.audit", "audit.title", audit.render),
        ),
        creation=CREATION_UI,
    )


def instance_pages() -> tuple[InstancePage, ...]:
    return get_webui().pages


__all__ = ["get_webui", "instance_pages"]
