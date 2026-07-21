from __future__ import annotations

from pywebio.output import put_button, put_scope, put_text, use_scope

from module.webui.game_ui import GameWebUI, InstancePage
from module.webui.i18n import t
from module.webui.assets import client_call, put_asset_widget


def instance_pages() -> tuple[InstancePage, ...]:
    return (InstancePage("overview", "nav.overview", "nav.overview", render_placeholder),)


def get_webui() -> GameWebUI:
    return GameWebUI(pages=instance_pages())


def render_placeholder(name: str) -> None:
    from module.webui.instance import confirm_delete_instance

    with use_scope("content"):
        put_scope(
            "unsupported_instance",
            [
                put_asset_widget("shared.panel_title", {"title": t("satisfactory.title")}),
                put_text(t("satisfactory.not_implemented")),
                put_asset_widget("shared.horizontal_rule"),
                put_button(t("settings.delete"), onclick=lambda: confirm_delete_instance(name), color="danger"),
            ],
        )
        client_call("dom.addClasses", scope="unsupported_instance", classes=["panel"])
