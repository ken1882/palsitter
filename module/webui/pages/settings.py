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
from pywebio.pin import pin, put_input, put_select
from pywebio.session import info, local, register_thread

from module.firewall import (
    FirewallError,
    FirewallPermissionDenied,
    FirewallService,
    PortFirewallStatus,
)
from module.webui.assets import client_call, put_asset_widget
from module.webui.forms import (
    _mark_dirty_form,
    _settings_field_row,
    clear_dirty_form,
    register_dirty_form,
    set_dirty_form_busy,
    update_form_values,
)
from module.webui.i18n import t
from module.webui.section_layout import SectionSpec, put_section_layout
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


def _configure_automatic_update_checker(enabled: bool) -> None:
    from module.webui.pages.updater import configure_automatic_update_checker

    configure_automatic_update_checker(enabled)


def _render_firewall_status(message: str, *, warning: bool = False) -> None:
    with use_scope("webui_firewall_status", clear=True):
        if warning:
            put_warning(message)
        else:
            put_text(message)


def _set_firewall_check_disabled(disabled: bool) -> None:
    client_call(
        "dom.setControlDisabled",
        selector="#pywebio-scope-webui_firewall_check button",
        disabled=disabled,
    )


def _begin_firewall_check() -> bool:
    if bool(getattr(local, "webui_firewall_check_busy", False)):
        return False
    local.webui_firewall_check_busy = True
    _set_firewall_check_disabled(True)
    return True


def _end_firewall_check() -> None:
    local.webui_firewall_check_busy = False
    _set_firewall_check_disabled(False)


def _finish_firewall_check(status: PortFirewallStatus, ask_to_fix: bool, context) -> None:
    _end_firewall_check()
    _apply_firewall_result(status, ask_to_fix, context)


def _finish_firewall_check_auth(command: tuple[str, ...], context) -> None:
    _end_firewall_check()
    _request_firewall_root_password("check", None, context, command)


def _web_port() -> int:
    return int(str(info.server_host).rsplit(":", 1)[-1])


def _set_auth_fields_disabled() -> None:
    disabled = not bool(getattr(local, "web_auth_enabled", False))
    for name in ("web_auth_username", "web_auth_password"):
        client_call(
            "dom.setControlDisabled",
            selector=f'input[name="{name}"]',
            disabled=disabled,
        )


def _render_auth_toggle() -> None:
    enabled = bool(getattr(local, "web_auth_enabled", False))
    with use_scope("web_auth_toggle", clear=True):
        put_button(
            t("common.on") if enabled else t("common.off"),
            onclick=_toggle_auth,
            color="success" if enabled else "secondary",
        )


def _render_auto_update_toggle() -> None:
    enabled = bool(getattr(local, "web_auto_update", False))
    with use_scope("web_auto_update_toggle", clear=True):
        put_button(
            t("common.on") if enabled else t("common.off"),
            onclick=_toggle_auto_update,
            color="success" if enabled else "secondary",
        )


def _toggle_auto_update() -> None:
    local.web_auto_update = not bool(getattr(local, "web_auto_update", False))
    _mark_dirty_form()
    _render_auto_update_toggle()


def _toggle_auth() -> None:
    local.web_auth_enabled = not bool(getattr(local, "web_auth_enabled", False))
    _mark_dirty_form()
    _render_auth_toggle()
    _set_auth_fields_disabled()


def _apply_firewall_result(
    status: PortFirewallStatus,
    ask_to_fix: bool,
    context,
) -> None:
    if status.error:
        _render_firewall_status(
            t("webui_settings.firewall_error", error=status.error), warning=True
        )
    elif not status.supported:
        _render_firewall_status(t("webui_settings.firewall_unsupported"))
    elif status.blocked:
        names = ", ".join(status.external_block_rule_names) or t("common.unknown")
        _render_firewall_status(
            t("webui_settings.firewall_blocked", rules=names), warning=True
        )
    elif status.allowed:
        _render_firewall_status(
            t("webui_settings.firewall_allowed", port=status.port)
        )
    else:
        _render_firewall_status(
            t("webui_settings.firewall_not_allowed", port=status.port), warning=True
        )
    if ask_to_fix and status.repairable:
        _confirm_firewall_fix(status, context)


def _request_firewall_root_password(
    action: str,
    status: PortFirewallStatus | None,
    context,
    command: tuple[str, ...] = (),
) -> None:
    with popup(t("webui_settings.firewall_root_password_title"), closable=True):
        put_text(t("webui_settings.firewall_root_password_description"))
        if command:
            put_text(t("webui_settings.firewall_root_password_command", command=" ".join(command)))
        put_input(
            "webui_firewall_root_password",
            type="password",
            label=t("webui_settings.firewall_root_password_label"),
        )
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(
                    t("webui_settings.firewall_fix")
                    if action == "fix"
                    else t("webui_settings.firewall_check"),
                    onclick=lambda: _retry_firewall_with_password(
                        action, status, context
                    ),
                    color="warning",
                ),
            ],
            size="1fr auto",
        )


def _retry_firewall_with_password(
    action: str,
    status: PortFirewallStatus | None,
    context,
) -> None:
    password = str(getattr(pin, "webui_firewall_root_password", "") or "")
    close_popup()
    if not password:
        _render_firewall_status(
            t("webui_settings.firewall_root_password_required"), warning=True
        )
        return
    if action == "check" and not _begin_firewall_check():
        return
    try:
        service = FirewallService()
        if action == "fix":
            assert status is not None
            service.ensure_port(
                status.port,
                status.protocol,
                root_password=password,
            )
        else:
            status = service.check_port(_web_port(), protocol="tcp", root_password=password)
    except FirewallPermissionDenied:
        if action == "check":
            _end_firewall_check()
        _render_firewall_status(
            t("webui_settings.firewall_error", error="administrator authentication was rejected"),
            warning=True,
        )
        return
    except (FirewallError, OSError, ValueError) as exc:
        if action == "check":
            _end_firewall_check()
        _render_firewall_status(
            t("webui_settings.firewall_error", error=exc), warning=True
        )
        return
    finally:
        password = ""
    if action == "fix":
        toast(t("webui_settings.firewall_fixed"))
        _check_firewall(ask_to_fix=False, context=context)
    else:
        _end_firewall_check()
        _apply_firewall_result(status, True, context)


def _confirm_firewall_fix(status: PortFirewallStatus, context) -> None:
    with popup(t("webui_settings.firewall_fix_title"), closable=True):
        put_text(
            t(
                "webui_settings.firewall_fix_block_prompt",
                rules=", ".join(status.external_block_rule_names),
            )
            if status.external_block_rule_names
            else t("webui_settings.firewall_fix_prompt", port=status.port)
        )
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(
                    t("webui_settings.firewall_fix"),
                    onclick=lambda: _fix_firewall(status, context),
                    color="warning",
                ),
            ],
            size="1fr auto",
        )


def _fix_firewall(status: PortFirewallStatus, context) -> None:
    close_popup()
    try:
        FirewallService().ensure_port(status.port, status.protocol)
    except FirewallPermissionDenied as exc:
        _request_firewall_root_password("fix", status, context, exc.command)
        return
    except (FirewallError, OSError) as exc:
        _render_firewall_status(
            t("webui_settings.firewall_fix_failed", error=exc), warning=True
        )
        return
    toast(t("webui_settings.firewall_fixed"))
    _check_firewall(ask_to_fix=False, context=context)


def _check_firewall(*, ask_to_fix: bool = True, context=None, root_password=None) -> None:
    address = str(pin.webui_bind_address or "").strip()
    if is_localhost(address):
        _render_firewall_status(t("webui_settings.firewall_localhost"))
        return
    if not _begin_firewall_check():
        return
    _render_firewall_status(t("webui_settings.firewall_checking"))
    stop_event = threading.Event()
    register_page_stop_event(stop_event)
    context = context or page_context()

    def check() -> None:
        try:
            status = FirewallService().check_port(
                _web_port(), protocol="tcp", root_password=root_password
            )
        except FirewallPermissionDenied as exc:
            if stop_event.is_set():
                return
            try:
                run_if_current(
                    context,
                    lambda: _finish_firewall_check_auth(exc.command, context),
                )
            except SessionException:
                pass
            return
        except (OSError, ValueError) as exc:
            message = t("webui_settings.firewall_error", error=exc)
            status = PortFirewallStatus(False, _web_port(), "tcp", error=message)
        if stop_event.is_set():
            return
        try:
            run_if_current(
                context,
                lambda: _finish_firewall_check(status, ask_to_fix, context),
            )
        except SessionException:
            return

    thread = threading.Thread(target=check, daemon=True)
    register_thread(thread)
    thread.start()


def _form_values() -> tuple[str, bool, bool, str, str] | None:
    address = str(pin.webui_bind_address or "").strip()
    try:
        address = str(ipaddress.IPv4Address(address))
    except ipaddress.AddressValueError:
        put_warning(t("webui_settings.invalid_address"), scope="webui_settings_error")
        return None
    enabled = bool(getattr(local, "web_auth_enabled", False))
    auto_update = bool(getattr(local, "web_auto_update", False))
    username = str(pin.web_auth_username or "").strip()
    password = str(pin.web_auth_password or "")
    current = load_web_settings()
    if enabled and not username:
        put_warning(t("webui_settings.username_required"), scope="webui_settings_error")
        return None
    if enabled and not password and not current.auth_enabled:
        put_warning(t("webui_settings.password_required"), scope="webui_settings_error")
        return None
    return address, auto_update, enabled, username, password


def _save_settings(*, save_anyway: bool = False) -> bool:
    clear("webui_settings_error")
    values = _form_values()
    if values is None:
        set_dirty_form_busy(False)
        return False
    address, auto_update, enabled, username, password = values
    current = load_web_settings()
    auth_changed = (
        enabled != current.auth_enabled
        or (enabled and username != current.auth_username)
        or (enabled and bool(password))
    )
    restart_required = address != current.bind_address or auth_changed
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
        set_dirty_form_busy(False)
        return False
    if not enabled:
        salt, password_hash = "", ""
    elif password:
        salt, password_hash = hash_password(password)
    else:
        salt, password_hash = current.auth_salt, current.auth_password_hash
    save_web_settings(
        WebUISettings(
            bind_address=address,
            auto_update=auto_update,
            auth_enabled=enabled,
            auth_username=username if enabled else "",
            auth_salt=salt,
            auth_password_hash=password_hash,
        )
    )
    close_popup()
    clear_dirty_form()
    _configure_automatic_update_checker(auto_update)
    if restart_required:
        _force_restart()
    return True


def _reset_settings() -> None:
    settings = load_web_settings()
    local.web_auto_update = settings.auto_update
    local.web_auth_enabled = settings.auth_enabled
    update_form_values(
        {
            "webui_bind_address": settings.bind_address,
            "web_auth_username": settings.auth_username,
            "web_auth_password": "",
        }
    )
    _render_auth_toggle()
    _render_auto_update_toggle()
    _set_auth_fields_disabled()
    clear("webui_settings_error")
    _render_firewall_status(t("webui_settings.firewall_not_checked"))
    clear_dirty_form()


def _render_settings() -> None:
    if _set_frame(t("nav.settings"), "Home") is None:
        return
    local.webui_firewall_check_busy = False
    clear("menu")
    with use_scope("menu"):
        _menu_button(t("nav.home"), _home)
        _menu_button(t("nav.updater"), _updater)
        _menu_button(t("nav.settings"), _render_settings, True)
        _menu_button(t("nav.utils"), _utils)
    settings = load_web_settings()
    local.web_auto_update = settings.auto_update
    local.web_auth_enabled = settings.auth_enabled
    clear("content")
    with use_scope("content"):
        put_section_layout(
            "webui_settings_panel",
            [
                SectionSpec(
                    "network",
                    t("webui_settings.network_title"),
                    "webui_settings_network",
                    ("settings-view",),
                ),
                SectionSpec(
                    "authentication",
                    t("webui_settings.auth_title"),
                    "webui_settings_auth",
                    ("settings-view",),
                ),
                SectionSpec(
                    "updates",
                    t("webui_settings.updates_title"),
                    "webui_settings_updates",
                    ("settings-view",),
                ),
            ],
            groups_scope="webui_settings_form",
            header=[
                put_asset_widget("shared.panel_title", {"title": t("webui_settings.title")}),
                put_scope("webui_settings_error"),
            ],
            footer=[put_scope("webui_settings_actions")],
        )
        client_call("dom.addClasses", scope="webui_settings_actions", classes=["settings-actions"])
        with use_scope("webui_settings_network"):
            put_asset_widget("shared.panel_title", {"title": t("webui_settings.network_title")})
            put_text(t("webui_settings.bind_help"))
            put_select(
                "webui_bind_address",
                label=t("webui_settings.bind_address"),
                options=interface_options(settings.bind_address),
                value=settings.bind_address,
            )
            put_scope("webui_firewall_status", [put_text(t("webui_settings.firewall_not_checked"))])
            put_scope(
                "webui_firewall_check",
                [put_button(t("webui_settings.firewall_check"), onclick=_check_firewall, color="secondary")],
            )
        with use_scope("webui_settings_auth"):
            put_asset_widget("shared.panel_title", {"title": t("webui_settings.auth_title")})
            put_text(t("webui_settings.auth_help"))
            _settings_field_row(
                t("webui_settings.enable_auth"),
                put_scope("web_auth_toggle"),
            )
            _render_auth_toggle()
            put_input("web_auth_username", label=t("webui_settings.username"), value=settings.auth_username)
            put_input("web_auth_password", label=t("webui_settings.password"), type="password", value="")
            put_text(t("webui_settings.password_help"))
            _set_auth_fields_disabled()
        with use_scope("webui_settings_updates"):
            put_asset_widget("shared.panel_title", {"title": t("webui_settings.updates_title")})
            _settings_field_row(
                t("webui_settings.automatic_update"),
                put_scope("web_auto_update_toggle"),
            )
            put_text(t("webui_settings.automatic_update_help"))
            _render_auto_update_toggle()
        with use_scope("webui_settings_actions"):
            put_row(
                [
                    put_asset_widget("shared.strong_text", {"text": t("form.unsaved_bar")}),
                    None,
                    put_button(t("common.reset"), onclick=_reset_settings, color="secondary"),
                    put_button(t("common.save"), onclick=_save_settings, color="success"),
                ],
                size="auto 1fr auto auto",
            )
    register_dirty_form("pywebio-scope-webui_settings_panel", _save_settings)


def _settings() -> None:
    return _run_navigation(_render_settings)


__all__ = ["_settings"]
