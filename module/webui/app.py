from __future__ import annotations
from pywebio import config
from pywebio.output import put_scope, use_scope
from pywebio.session import defer_call, info, set_env
from module.instances import initialize_instances
from module.webui.assets import asset_urls
from module.webui.i18n import init_language
from module.webui.session import cleanup_session, initialize_page_lifecycle
from module.webui.restart import mount_overlay

def _home(*args, **kwargs):
    from module.webui.pages.home import _home as implementation
    return implementation(*args, **kwargs)

def _inject_css(*args, **kwargs):
    from module.webui.assets import inject_css as implementation
    return implementation(*args, **kwargs)

@config(title="Palsitter", css_file=asset_urls("css"), js_file=asset_urls("js"))
def app() -> None:
    set_env(title="Palsitter", output_animation=False)
    initialize_instances()
    init_language(getattr(info, "user_language", None))
    page_cleanups = initialize_page_lifecycle()
    defer_call(lambda: cleanup_session(page_cleanups))
    _inject_css()
    put_scope("ROOT")
    _home()
    with use_scope("ROOT"):
        mount_overlay()


__all__ = ["app"]
