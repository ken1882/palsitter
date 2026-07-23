from __future__ import annotations

from module.webui.assets import client_call
from module.webui.session import register_page_cleanup


def mount_checkbox_group(scope: str) -> None:
    client_call("checkboxGroups.mount", scope=scope)
    register_page_cleanup(
        lambda: client_call("checkboxGroups.destroy", scope=scope)
    )


__all__ = ["mount_checkbox_group"]
