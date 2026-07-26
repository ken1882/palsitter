from __future__ import annotations

import ipaddress
import threading

from pywebio.exceptions import SessionException
from pywebio.output import (
    clear,
    close_popup,
    popup,
    put_button,
    put_row,
    put_scope,
    put_text,
    put_warning,
    toast,
    use_scope,
)
from pywebio.pin import pin, put_checkbox, put_input, put_select
from pywebio.session import info, register_thread

from module.games.palworld.firewall import FirewallPermissionDenied, FirewallService
from module.webui.assets import client_call, put_asset_widget
from module.webui.forms import _clear_dirty_form, register_dirty_form
from module.webui.i18n import t
from module.webui.session import page_context, register_page_stop_event, run_if_current
from module.webui.settings import (
    WebUISettings,
    hash_password,
    interface_options,
    is_localhost,
    load_web_settings,
    save_web_settings,
)


def _home(*args, **kwargs):
    from module.webui.pages.home import _home as implementation

    return implementation(*args, **kwargs)


def _menu_button(*args, **kwargs):
    from module.webui.instance import _menu_button as implementation

    return implementation(*args, **kwargs)


def _set_frame(*args, **kwargs):
    from module.webui.instance import _set_frame as implementation

    return implementation(*args, **kwargs)


def _run_navigation(*args, **kwargs):
    from module.webui.instance import _run_navigation as implementation

    return implementation(*args, **kwargs)


def _updater(*args, **kwargs):
    from module.webui.pages.updater import _updater as implementation

    return implementation(*args, **kwargs)


def _utils(*args, **kwargs):
    from module.webui.pages.utils import _utils as implementation

    return implementation(*args, **kwargs)


def _force_restart(*args, **kwargs):
    from module.webui.pages.utils import _force_restart as implementation

    return implementation(*args, **kwargs)


def _render_firewall_status(message: str, *, warning: bool = False) -> None:
    with use_scope("webui_firewall_status", clear=True):
        if warning:
            put_warning(message)
        else:
            put_text(message)


def _web_port() -> int:
    return int(str(info.server_host).rsplit(":", 1)[-1])


def _check_firewall() -> None:
    _render_firewall_status(t("webui_settings.firewall_checking"))
    stop_event = threading.Event()
    register_page_stop_event(stop_event)
    context = page_context()

    def check() -> None:
        try:
            status = FirewallService().check_port(_web_port(), protocol="tcp")
            if status.error:
                message = t("webui_settings.firewall_error", error=status.error)
                warning = True
            elif not status.supported:
                message = t("webui_settings.firewall_unsupported")
                warning = False
            elif status.blocked:
                names = ", ".join(status.external_block_rule_names) or t("common.unknown")
                message = t("webui_settings.firewall_blocked", rules=names)
                warning = True
            elif status.allowed:
                message = t("webui_settings.firewall_allowed", port=status.port)
                warning = False
            else:
                message = t("webui_settings.firewall_not_allowed", port=status.port)
                warning = True
        except FirewallPermissionDenied:
            message = t("webui_settings.firewall_permission")
            warning = True
        except (OSError, ValueError) as exc:
            message = t("webui_settings.firewall_error", error=exc)
            warning = True
        if stop_event.is_set():
            return
        try:
            run_if_current(context, lambda: _render_firewall_status(message, warning=warning))
        except SessionException:
            return

    thread = threading.Thread(target=check, daemon=True)
    register_thread(thread)
    thread.start()


def _form_values() -> tuple[str, bool, str, str] | None:
    address = str(pin.webui_bind_address or "").strip()
    try:
        address = str(ipaddress.IPv4Address(address))
    except ipaddress.AddressValueError:
        put_warning(t("webui_settings.invalid_address"), scope="webui_settings_error")
        return None
    enabled = bool(pin.web_auth_enabled)
    username = str(pin.web_auth_username or "").strip()
    password = str(pin.web_auth_password or "")
    if enabled and not username:
        put_warning(t("webui_settings.username_required"), scope="webui_settings_error")
        return None
    if enabled and not password:
        put_warning(t("webui_settings.password_required"), scope="webui_settings_error")
        return None
    return address, enabled, username, password


def _save_settings(*, save_anyway: bool = False) -> bool:
    clear("webui_settings_error")
    values = _form_values()
    if values is None:
        return False
    address, enabled, username, password = values
    if not enabled and not is_localhost(address) and not save_anyway:
        with popup(t("webui_settings.exposure_title"), closable=True) as scope:
            put_warning(t("webui_settings.exposure_warning"), scope=scope)
            put_scope(
                "webui_exposure_actions",
                put_row(
                    [
                        put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                        put_scope("webui_exposure_spacer"),
                        put_button(
                            t("webui_settings.save_anyway"),
                            onclick=lambda: _save_settings(save_anyway=True),
                            color="danger",
                        ),
                    ],
                    size="auto .5rem auto",
                ),
            )
        return False
    salt, password_hash = hash_password(password) if enabled else ("", "")
    save_web_settings(
        WebUISettings(
            bind_address=address,
            auth_enabled=enabled,
            auth_username=username if enabled else "",
            auth_salt=salt,
            auth_password_hash=password_hash,
        )
    )
    close_popup()
    _clear_dirty_form()
    _force_restart()
    return True


def _render_settings() -> None:
    if _set_frame(t("nav.settings"), "Home") is None:
        return
    clear("menu")
    with use_scope("menu"):
        _menu_button(t("nav.home"), _home)
        _menu_button(t("nav.updater"), _updater)
        _menu_button(t("nav.settings"), _render_settings, True)
        _menu_button(t("nav.utils"), _utils)
    settings = load_web_settings()
    clear("content")
    with use_scope("content"):
        put_scope(
            "webui_settings_panel",
            [
                put_asset_widget("shared.panel_title", {"title": t("webui_settings.title")}),
                put_scope("webui_settings_error"),
                put_scope("webui_settings_form"),
                put_scope("webui_settings_actions"),
            ],
        )
        client_call("dom.addClasses", scope="webui_settings_panel", classes=["panel"])
        client_call("dom.addClasses", scope="webui_settings_form", classes=["settings-view"])
        client_call("dom.addClasses", scope="webui_settings_actions", classes=["settings-actions"])
        with use_scope("webui_settings_form"):
            put_asset_widget("shared.panel_title", {"title": t("webui_settings.network_title")})
            put_text(t("webui_settings.bind_help"))
            put_select(
                "webui_bind_address",
                label=t("webui_settings.bind_address"),
                options=interface_options(settings.bind_address),
                value=settings.bind_address,
            )
            put_scope("webui_firewall_status", [put_text(t("webui_settings.firewall_not_checked"))])
            put_button(t("webui_settings.firewall_check"), onclick=_check_firewall, color="secondary")
            put_asset_widget("shared.panel_title", {"title": t("webui_settings.auth_title")})
            put_checkbox(
                "web_auth_enabled",
                label=t("webui_settings.enable_auth"),
                options=[{"label": t("webui_settings.enable_auth"), "value": "enabled"}],
                value=["enabled"] if settings.auth_enabled else [],
            )
            put_input("web_auth_username", label=t("webui_settings.username"), value=settings.auth_username)
            put_input("web_auth_password", label=t("webui_settings.password"), type="password", value="")
            put_text(t("webui_settings.password_help"))
        with use_scope("webui_settings_actions"):
            put_button(t("common.reset"), onclick=_render_settings, color="secondary")
            put_button(t("common.save"), onclick=_save_settings, color="primary")
    register_dirty_form("pywebio-scope-webui_settings_panel", _save_settings)


def _settings() -> None:
    return _run_navigation(_render_settings)


__all__ = ["_settings"]
