from __future__ import annotations

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

from module.games.palworld.config import load_profile
from module.games.palworld.firewall import (
    FirewallError,
    FirewallRepairUnavailable,
    FirewallService,
    FirewallStatus,
    resolve_executable,
)
from module.webui.assets import client_call, put_asset_widget
from module.webui.i18n import t
from module.webui.session import page_context, run_if_current


def _manager(name: str):
    from module.webui.instance import _manager as implementation

    return implementation(name)


def _service() -> FirewallService:
    return FirewallService()


def _log(name: str, message: str) -> None:
    _manager(name).append_log(f"Firewall: {message}")


def _render_status(status: FirewallStatus) -> None:
    with use_scope("tools_status", clear=True):
        if status.error:
            put_warning(t("tools.check_failed", error=status.error))
            return
        if not status.supported:
            put_text(t("tools.unsupported"))
            return
        if status.allowed:
            put_text(t("tools.open"))
        else:
            put_warning(t("tools.blocked"))
        put_row(
            [
                put_text(t("tools.executable_status")),
                put_text(t("tools.allowed") if status.executable_allowed else t("tools.not_allowed")),
            ],
            size="auto 1fr",
        )
        put_row(
            [
                put_text(t("tools.port_status")),
                put_text(t("tools.allowed") if status.port_allowed else t("tools.not_allowed")),
            ],
            size="auto 1fr",
        )
        if status.external_block_rule_names:
            put_warning(t("tools.manual_block", rules=", ".join(status.external_block_rule_names)))


def _check(name: str, *, ask_to_fix: bool = True, context=None) -> None:
    context = context or page_context()
    profile = load_profile(name)
    status = _service().check(profile)
    if status.error:
        _log(name, f"check failed: {status.error}")
    elif status.allowed:
        _log(name, "check passed")
    else:
        _log(name, "check blocked")
    run_if_current(
        context,
        lambda: _apply_check_result(name, status, ask_to_fix, context),
    )


def _apply_check_result(name: str, status: FirewallStatus, ask_to_fix: bool, context) -> None:
    _render_status(status)
    if ask_to_fix and status.repairable:
        _confirm_fix(name, status, context)


def _confirm_fix(name: str, status: FirewallStatus, context) -> None:
    with popup(t("tools.fix_title"), closable=True):
        put_text(t("tools.fix_prompt"))
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(
                    t("tools.fix"),
                    onclick=lambda: _fix(name, status, context),
                    color="warning",
                ),
            ],
            size="1fr auto",
        )


def _fix(name: str, status: FirewallStatus, context=None) -> None:
    context = context or page_context()
    close_popup()
    try:
        _service().fix(load_profile(name), status)
    except FirewallRepairUnavailable as exc:
        _log(name, f"repair unavailable: {exc}")
        run_if_current(
            context,
            lambda: _render_fix_error("tools.fix_unavailable", exc),
        )
        return
    except (FirewallError, OSError) as exc:
        _log(name, f"repair failed: {exc}")
        run_if_current(
            context,
            lambda: _render_fix_error("tools.fix_failed", exc),
        )
        return
    _log(name, "repair completed")
    run_if_current(context, lambda: toast(t("tools.fixed")))
    _check(name, ask_to_fix=False, context=context)


def _render_fix_error(key: str, error: BaseException) -> None:
    with use_scope("tools_status", clear=True):
        put_warning(t(key, error=error))


def render(name: str) -> None:
    profile = load_profile(name)
    service = _service()
    executable = str(resolve_executable(profile))
    children = [
        put_asset_widget("shared.panel_title", {"title": t("tools.heading")}),
        put_text(t("tools.description")),
        put_row(
            [put_text(f"{t('tools.executable')}: "), put_text(executable)],
            size="auto 1fr",
        ),
        put_row(
            [put_text(f"{t('tools.udp_port')}: "), put_text(str(profile.game_port))],
            size="auto 1fr",
        ),
        put_scope(
            "tools_status",
            [put_text(t("tools.not_checked") if service.supported else t("tools.unsupported"))],
        ),
    ]
    if service.supported:
        children.append(
            put_scope(
                "tools_actions",
                [put_button(t("tools.check"), onclick=lambda: _check(name))],
            )
        )
    with use_scope("content", clear=True):
        put_scope("tools_panel", children)
    client_call("dom.addClasses", scope="tools_panel", classes=["panel"])


__all__ = ["render"]
