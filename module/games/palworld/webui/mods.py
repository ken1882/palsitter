from __future__ import annotations

import threading
from pathlib import Path

from pywebio.exceptions import SessionException
from pywebio.output import (
    clear,
    close_popup,
    popup,
    put_button,
    put_loading,
    put_row,
    put_scope,
    put_table,
    put_text,
    put_warning,
    toast,
    use_scope,
)
from pywebio.pin import pin, put_select
from pywebio.session import local, register_thread

from module.games.palworld.config import load_profile
from module.games.palworld.mods import UE4SSRelease, UE4SSService, default_release_tag
from module.webui.i18n import t
from module.webui.assets import client_call, client_query, put_asset_widget


def _manager(*args, **kwargs):
    from module.webui.instance import _manager as implementation

    return implementation(*args, **kwargs)


def _open_folder(*args, **kwargs):
    from module.games.palworld.webui.server_settings import _open_folder as implementation

    return implementation(*args, **kwargs)


def _service(name: str) -> UE4SSService:
    return UE4SSService(load_profile(name))


def render(name: str) -> None:
    clear("content")
    local.ue4ss_releases = ()
    local.ue4ss_busy = False
    with use_scope("content"):
        put_scope(
            "mods_panel",
            [
                put_asset_widget("shared.panel_title", {"title": t("mods.title")}),
                put_warning(t("mods.compatibility_warning")),
                put_scope("ue4ss_summary"),
                put_scope("ue4ss_release_controls"),
                put_scope("ue4ss_operation"),
                put_scope("lua_mods"),
                put_scope("pak_mods"),
            ],
        )
        client_call("dom.addClasses", scope="mods_panel", classes=["panel"])
    service = _service(name)
    _render_status(name, service.platform_supported)
    if service.platform_supported:
        _load_releases(name)


@use_scope("ue4ss_summary", clear=True)
def _render_summary(name: str) -> None:
    status = _service(name).status()
    version = status.ue4ss_version or t("mods.version_unknown")
    installed_text = (
        t("mods.installed_version", version=version)
        if status.ue4ss_installed
        else t("mods.not_installed")
    )
    put_asset_widget(
        "palworld.mods_summary",
        {"title": t("mods.ue4ss"), "installed": installed_text, "description": t("mods.ue4ss_description")},
    )
    if status.reason:
        reason = t(status.reason_key) if status.reason_key else status.reason
        put_warning(t("mods.unsupported", reason=reason))


def _render_status(name: str, platform_supported: bool | None = None) -> None:
    if platform_supported is None:
        platform_supported = _service(name).platform_supported
    _render_summary(name)
    if platform_supported:
        _render_mod_table(name, "lua")
    _render_mod_table(name, "pak")


def _load_releases(name: str) -> None:
    with use_scope("ue4ss_release_controls", clear=True):
        put_row(
            [put_loading("border", "primary"), put_text(t("mods.loading_releases"))],
            size="auto 1fr",
        )

    def load() -> None:
        try:
            releases = _service(name).list_releases()
            if not releases:
                raise RuntimeError(t("mods.no_releases"))
            local.ue4ss_releases = releases
            _render_release_controls(name, releases)
        except Exception as exc:
            try:
                _render_release_error(name, exc)
            except SessionException:
                return

    thread = threading.Thread(target=load, daemon=True)
    register_thread(thread)
    thread.start()


@use_scope("ue4ss_release_controls", clear=True)
def _render_release_controls(name: str, releases: tuple[UE4SSRelease, ...]) -> None:
    status = _service(name).status()
    busy = bool(getattr(local, "ue4ss_busy", False))
    stopped = _manager(name).display_state in ("inactive", "warning")
    disabled = busy or not status.supported or not stopped
    selected = default_release_tag(releases)
    options = [{"label": release.label, "value": release.tag} for release in releases]
    put_select(
        "ue4ss_release",
        label=t("mods.release"),
        options=options,
        value=selected,
    )
    client_call(
        "dom.setControlDisabled",
        selector='select[name="ue4ss_release"]',
        disabled=disabled,
    )
    actions = [
        put_button(
            t("mods.install_selected") if status.ue4ss_installed else t("mods.install"),
            onclick=lambda: _start_install(name),
            color="primary",
            disabled=disabled,
        )
    ]
    if status.ue4ss_installed:
        actions.append(
            put_button(
                t("mods.remove"),
                onclick=lambda: _confirm_remove(name),
                color="danger",
                disabled=disabled,
            )
        )
    put_row(actions, size="auto auto")
    if not stopped:
        put_warning(t("mods.stop_server"))
    put_asset_widget(
        "palworld.mods_release_source",
        {
            "source": t("mods.release_source"),
            "href": "https://github.com/UE4SS-RE/RE-UE4SS/releases",
            "link": t("mods.github_releases"),
        },
    )


@use_scope("ue4ss_release_controls", clear=True)
def _render_release_error(name: str, error: Exception) -> None:
    put_warning(t("mods.release_failed", error=error))
    put_button(t("mods.retry"), onclick=lambda: _load_releases(name), color="primary")


def _start_install(name: str) -> None:
    releases = tuple(getattr(local, "ue4ss_releases", ()))
    selected = str(pin.ue4ss_release or "")
    if selected not in {release.tag for release in releases}:
        toast(t("mods.select_release"), color="error")
        return
    local.ue4ss_busy = True
    _render_release_controls(name, releases)
    with use_scope("ue4ss_operation", clear=True):
        put_row(
            [put_loading("border", "primary"), put_text(t("mods.installing", version=selected))],
            size="auto 1fr",
        )

    def install() -> None:
        try:
            release = _service(name).install(selected)
            toast(t("mods.install_complete", version=release.tag), color="success")
        except Exception as exc:
            toast(t("mods.install_failed", error=exc), color="error")
        finally:
            try:
                local.ue4ss_busy = False
                if client_query("dom.scopeExists", scope="mods_panel"):
                    clear("ue4ss_operation")
                    _render_status(name)
                    _render_release_controls(name, releases)
            except SessionException:
                return

    thread = threading.Thread(target=install, daemon=True)
    register_thread(thread)
    thread.start()


def _confirm_remove(name: str) -> None:
    with popup(t("mods.remove_title"), closable=True):
        put_warning(t("mods.remove_warning"))
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(t("mods.remove"), onclick=lambda: _start_remove(name), color="danger"),
            ],
            size="auto auto",
        )


def _start_remove(name: str) -> None:
    close_popup()
    releases = tuple(getattr(local, "ue4ss_releases", ()))
    local.ue4ss_busy = True
    _render_release_controls(name, releases)
    with use_scope("ue4ss_operation", clear=True):
        put_row(
            [put_loading("border", "danger"), put_text(t("mods.removing"))],
            size="auto 1fr",
        )

    def remove() -> None:
        try:
            _service(name).uninstall()
            toast(t("mods.remove_complete"), color="success")
        except Exception as exc:
            toast(t("mods.remove_failed", error=exc), color="error")
        finally:
            try:
                local.ue4ss_busy = False
                if client_query("dom.scopeExists", scope="mods_panel"):
                    clear("ue4ss_operation")
                    _render_status(name)
                    _render_release_controls(name, releases)
            except SessionException:
                return

    thread = threading.Thread(target=remove, daemon=True)
    register_thread(thread)
    thread.start()


def _icon_button(label: str, glyph: str, onclick, *, disabled: bool = False, danger: bool = False):
    button = put_asset_widget(
        "palworld.backup_icon_button",
        {"label": label, "glyph": glyph, "disabled": disabled, "danger": danger},
    )
    if not disabled:
        button.onclick(onclick)
    return button


def _render_mod_table(name: str, kind: str) -> None:
    scope = "lua_mods" if kind == "lua" else "pak_mods"
    with use_scope(scope, clear=True):
        status = _service(name).status()
        is_lua = kind == "lua"
        title = t("mods.lua_title") if is_lua else t("mods.pak_title")
        label = t("mods.open_lua_folder") if is_lua else t("mods.open_pak_folder")
        directory = status.lua_dir if is_lua else status.pak_dir
        mods = status.lua_mods if is_lua else status.pak_mods
        folder = _icon_button(
            label,
            "📁",
            lambda path=directory: _open_mod_folder(path),
            disabled=directory is None,
        )
        put_scope(
            f"mods_{kind}_title_row",
            [put_row(
                [put_asset_widget("palworld.backup_title", {"title": title}), folder, None],
                size="auto auto 1fr",
            )],
        )
        if not mods:
            put_text(t("mods.no_lua") if is_lua else t("mods.no_pak"))
            return
        if is_lua:
            rows = [
                [put_asset_widget("palworld.backup_file_details", {"name": mod.name, "metadata": ""})]
                for mod in mods
            ]
            put_table(rows, header=[t("mods.mod_name")])
            return
        rows = []
        for mod in mods:
            checkbox_label = t("mods.pak_enabled_checkbox", name=mod.name)
            checkbox = put_asset_widget(
                "palworld.pak_checkbox",
                {"label": checkbox_label, "checked": mod.enabled},
            ).onclick(
                lambda mod_name=mod.name, enabled=not mod.enabled: _toggle_pak(
                    name, mod_name, enabled
                )
            )
            rows.append(
                [
                    put_asset_widget("palworld.backup_file_details", {"name": mod.name, "metadata": ""}),
                    checkbox,
                    _icon_button(
                        t("mods.delete_pak", name=mod.name),
                        "🗑",
                        lambda mod_name=mod.name: _confirm_delete_pak(name, mod_name),
                        danger=True,
                    ),
                ]
            )
        put_table(rows, header=[t("mods.mod_name"), t("mods.enabled"), t("mods.delete")])


def _open_mod_folder(path: Path | None) -> None:
    if path is None:
        return
    try:
        _open_folder(path)
    except Exception as exc:
        toast(t("mods.open_failed", error=exc), color="error")


def _toggle_pak(name: str, mod_name: str, enabled: bool) -> None:
    try:
        _service(name).set_pak_enabled(mod_name, enabled)
        toast(t("mods.pak_enabled" if enabled else "mods.pak_disabled", name=mod_name))
        _render_mod_table(name, "pak")
    except Exception as exc:
        toast(t("mods.pak_toggle_failed", error=exc), color="error")
        _render_mod_table(name, "pak")


def _confirm_delete_pak(name: str, mod_name: str) -> None:
    with popup(t("mods.delete_pak_title"), closable=True):
        put_warning(t("mods.delete_pak_confirm", name=mod_name))
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(
                    t("mods.delete"),
                    onclick=lambda: _delete_pak(name, mod_name),
                    color="danger",
                ),
            ],
            size="auto auto",
        )


def _delete_pak(name: str, mod_name: str) -> None:
    close_popup()
    try:
        _service(name).delete_pak(mod_name)
        toast(t("mods.delete_complete", name=mod_name), color="success")
        _render_mod_table(name, "pak")
    except Exception as exc:
        toast(t("mods.delete_failed", error=exc), color="error")


__all__ = ["render"]
