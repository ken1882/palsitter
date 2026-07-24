from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path
from pywebio.output import clear, popup, put_button, put_markdown, put_row, put_scope, put_text, put_warning, toast, use_scope
from pywebio.pin import pin
from pywebio.session import local
from module.games.palworld.config import PalworldProfile, fixed_steamcmd_path, load_profile, save_profile
from module.steamcmd import ensure_steamcmd, steamcmd_download_url
from module.webui.i18n import t
from module.webui.session import page_context, register_page_cleanup
from module.webui.assets import client_call, put_asset_widget

def _clear_dirty_form(*args, **kwargs):
    from module.webui.forms import _clear_dirty_form as implementation
    return implementation(*args, **kwargs)

def _delete_instance(*args, **kwargs):
    from module.webui.instance import _delete_instance as implementation
    return implementation(*args, **kwargs)

def _register_dirty_form(*args, **kwargs):
    from module.webui.forms import _register_dirty_form as implementation
    return implementation(*args, **kwargs)

def _render_instance_menu(*args, **kwargs):
    from module.webui.instance import _render_instance_menu as implementation
    return implementation(*args, **kwargs)

def _safe_dom_id(*args, **kwargs):
    from module.webui.forms import _safe_dom_id as implementation
    return implementation(*args, **kwargs)

def _set_frame(*args, **kwargs):
    from module.webui.instance import _set_frame as implementation
    return implementation(*args, **kwargs)

def _settings_field(*args, **kwargs):
    from module.games.palworld.webui.forms import _settings_field as implementation
    return implementation(*args, **kwargs)

def _settings_dependent_field(*args, **kwargs):
    from module.games.palworld.webui.forms import _settings_dependent_field as implementation
    return implementation(*args, **kwargs)

def _settings_field_row(*args, **kwargs):
    from module.webui.forms import _settings_field_row as implementation
    return implementation(*args, **kwargs)

def _settings_label(*args, **kwargs):
    from module.webui.forms import _settings_label as implementation
    return implementation(*args, **kwargs)

def _settings_textarea(*args, **kwargs):
    from module.games.palworld.webui.forms import _settings_textarea as implementation
    return implementation(*args, **kwargs)

def _argument_list_values(*args, **kwargs):
    from module.games.palworld.webui.forms import _argument_list_values as implementation
    return implementation(*args, **kwargs)

def _put_argument_list(*args, **kwargs):
    from module.games.palworld.webui.forms import _put_argument_list as implementation
    return implementation(*args, **kwargs)

def _render_argument_list(*args, **kwargs):
    from module.games.palworld.webui.forms import _render_argument_list as implementation
    return implementation(*args, **kwargs)

def _clear_field_error(*args, **kwargs):
    from module.games.palworld.webui.forms import _clear_field_error as implementation
    return implementation(*args, **kwargs)

def _field_error_scope(*args, **kwargs):
    from module.games.palworld.webui.forms import _field_error_scope as implementation
    return implementation(*args, **kwargs)

def _settings_toggle(*args, **kwargs):
    from module.games.palworld.webui.forms import _settings_toggle as implementation
    return implementation(*args, **kwargs)

def _show_field_error(*args, **kwargs):
    from module.webui.forms import _show_field_error as implementation
    return implementation(*args, **kwargs)

def _validate_settings_form(*args, **kwargs):
    from module.games.palworld.webui.forms import _validate_settings_form as implementation
    return implementation(*args, **kwargs)

Profile = PalworldProfile

SETTINGS_TOGGLE_KEYS = (
    "steam_validate",
    "update_on_start",
    "auto_update",
    "launch_useperfthreads",
    "launch_no_async_loading_thread",
    "launch_use_multithread_for_ds",
    "launch_enable_gamedata_api",
    "launch_public_lobby",
    "launch_log_format",
)

def render(name: str) -> None:
    profile = load_profile(name)
    local.settings_toggles = {
        "steam_validate": profile.steam_validate,
        "update_on_start": profile.update_on_start,
        "auto_update": profile.auto_update,
        "launch_useperfthreads": profile.launch_useperfthreads,
        "launch_no_async_loading_thread": profile.launch_no_async_loading_thread,
        "launch_use_multithread_for_ds": profile.launch_use_multithread_for_ds,
        "launch_enable_gamedata_api": profile.launch_enable_gamedata_api,
        "launch_public_lobby": profile.launch_public_lobby,
        "launch_log_format": profile.launch_log_format,
    }
    local.settings_argument_list_refresh = lambda: _render_extra_args_list()
    clear("content")
    with use_scope("content"):
        put_scope(
            "settings_panel",
            [
                put_asset_widget("shared.panel_title", {"title": t("settings.title")}),
                put_scope("settings_filter_toolbar"),
                put_scope("settings_form"),
                put_scope("settings_actions"),
                put_scope("settings_delete"),
            ],
        )
        client_call("dom.addClasses", scope="settings_panel", classes=["panel"])
        client_call("dom.addClasses", scope="settings_form", classes=["settings-view"])
        client_call("dom.addClasses", scope="settings_actions", classes=["settings-actions"])
        with use_scope("settings_filter_toolbar"):
            _render_server_settings_toolbar()
        with use_scope("settings_form"):
            _settings_category(t("settings.category_installation"), "installation")
            _settings_steamcmd_action(name)
            _settings_toggle(_settings_label("update_on_start"), "update_on_start", escape_label=False)
            _settings_toggle(_settings_label("auto_update"), "auto_update", escape_label=False)
            _settings_dependent_field(
                _settings_label("auto_update_idle_minutes"),
                "auto_update_idle_minutes",
                profile.auto_update_idle_minutes,
                type="number",
                escape_label=False,
            )
            _settings_toggle(_settings_label("steam_validate"), "steam_validate", escape_label=False)
            _settings_category(t("settings.category_launch"), "launch")
            put_asset_widget(
                "shared.external_link",
                {
                    "href": "https://docs.palworldgame.com/0.5.0/settings-and-operation/arguments/",
                    "label": t("settings.official_docs"),
                },
            )
            _settings_toggle(_settings_label("launch_useperfthreads"), "launch_useperfthreads", escape_label=False)
            _settings_toggle(
                _settings_label("launch_no_async_loading_thread"),
                "launch_no_async_loading_thread",
                escape_label=False,
            )
            _settings_toggle(
                _settings_label("launch_use_multithread_for_ds"),
                "launch_use_multithread_for_ds",
                escape_label=False,
            )
            _settings_field(
                _settings_label("launch_worker_threads_server"),
                "launch_worker_threads_server",
                profile.launch_worker_threads_server or "",
                type="number",
                escape_label=False,
            )
            _settings_toggle(
                _settings_label("launch_enable_gamedata_api"),
                "launch_enable_gamedata_api",
                escape_label=False,
            )
            _settings_toggle(_settings_label("launch_public_lobby"), "launch_public_lobby", escape_label=False)
            _settings_toggle(_settings_label("launch_log_format"), "launch_log_format", escape_label=False)
            _settings_field_row(
                _settings_label("extra_args"),
                _put_argument_list(
                    _settings_pin("extra_args"),
                    profile.extra_args,
                    controlled=_launch_controlled_arguments(),
                    tooltip=t("settings.argument_controlled"),
                    add_label=t("settings.argument_add"),
                    remove_label=t("settings.argument_remove"),
                ),
                error_scope=_field_error_scope(_settings_pin("extra_args")),
                escape_label=False,
            )
            _render_extra_args_list()
            _settings_category(t("settings.category_instance"), "instance")
            _settings_field(
                _settings_label("dedicated_server_name"),
                "dedicated_server_name",
                profile.dedicated_server_name,
                escape_label=False,
            )
            _settings_field(_settings_label("query_port"), "query_port", profile.query_port, type="number", escape_label=False)
        context = page_context()
        client_call(
            "palworld.serverSettings.mount",
            generation=context.generation if context else None,
        )
        register_page_cleanup(lambda: client_call("palworld.serverSettings.destroy"))
        with use_scope("settings_actions"):
            put_row(
                [
                    put_asset_widget("shared.strong_text", {"text": t("form.unsaved_bar")}),
                    None,
                    put_button(t("common.reset"), onclick=lambda: _settings(name), color="secondary"),
                    put_button(t("common.save"), onclick=lambda: _save_settings(name), color="success"),
                ],
                size="auto 1fr auto auto",
            )
        with use_scope("settings_delete"):
            put_asset_widget("shared.horizontal_rule")
            put_button(t("settings.delete"), onclick=lambda: _delete_instance(name), color="danger")
    _register_dirty_form(
        "pywebio-scope-settings_panel",
        lambda: _save_settings(name, rerender=False),
    )


def _settings(name: str) -> None:
    from module.webui.instance import open_instance
    open_instance(name, "server_settings")

def _settings_pin(key: str) -> str:
    return f"settings_{key}"


def _launch_controlled_arguments() -> list[str]:
    toggles = local.settings_toggles
    arguments: list[str] = []
    if toggles.get("launch_useperfthreads"):
        arguments.append("-useprefthreads")
    if toggles.get("launch_no_async_loading_thread"):
        arguments.append("-NoAsyncLoadingThread")
    if toggles.get("launch_use_multithread_for_ds"):
        arguments.append("-UseMultithreadForDS")
    worker_threads = str(getattr(pin, _settings_pin("launch_worker_threads_server"), "") or "").strip()
    if worker_threads:
        arguments.append(f"-NumberOfWorkerThreadsServer={worker_threads}")
    if toggles.get("launch_enable_gamedata_api"):
        arguments.append("-enable-gamedata-api")
    if toggles.get("launch_public_lobby"):
        arguments.append("-publiclobby")
    if toggles.get("launch_log_format"):
        arguments.append("-logformat")
    return arguments


def _render_extra_args_list() -> None:
    _render_argument_list(
        _settings_pin("extra_args"),
        controlled=_launch_controlled_arguments(),
        tooltip=t("settings.argument_controlled"),
        add_label=t("settings.argument_add"),
        remove_label=t("settings.argument_remove"),
    )

def _settings_category(label: str, category: str) -> None:
    put_asset_widget("palworld.settings_category", {"category": category, "label": label})

def _render_server_settings_toolbar() -> None:
    categories = [
        ("all", t("world.category_all")),
        ("installation", t("settings.category_installation")),
        ("launch", t("settings.category_launch")),
        ("instance", t("settings.category_instance")),
    ]
    put_asset_widget(
        "palworld.server_settings_toolbar",
        {
            "placeholder": t("settings.search_placeholder"),
            "categories": [
                {"id": category, "label": label, "active": category == "all"}
                for category, label in categories
            ],
        },
    )

def _steamcmd_action_scope(name: str) -> str:
    return f"settings_steamcmd_action_{_safe_dom_id(name)}"

def _settings_steamcmd_action(name: str) -> None:
    scope = _steamcmd_action_scope(name)
    _settings_field_row(_settings_label("steamcmd"), put_scope(scope), escape_label=False)
    _render_steamcmd_action(name)

def _render_steamcmd_action(name: str) -> None:
    steamcmd = fixed_steamcmd_path(name)
    installed = steamcmd.exists()
    with use_scope(_steamcmd_action_scope(name), clear=True):
        put_row(
            [
                put_text(str(steamcmd.parent)),
                None,
                put_button(
                    t("settings.steamcmd_show") if installed else t("settings.steamcmd_download"),
                    onclick=lambda: _open_steamcmd_folder(name) if installed else _download_steamcmd(name),
                    color="secondary" if installed else "primary",
                ),
            ],
            size="1fr .5rem auto",
        )

def _open_steamcmd_folder(name: str) -> None:
    folder = fixed_steamcmd_path(name).parent
    try:
        if os.name == "nt":
            os.startfile(str(folder))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except Exception as exc:
        toast(t("settings.steamcmd_show_failed", error=exc), color="error")

def _download_steamcmd(name: str) -> None:
    url = steamcmd_download_url()
    with popup(t("settings.steamcmd_download_title"), closable=True) as scope:
        put_markdown(t("settings.steamcmd_download_source"), scope=scope)
        put_scope("steamcmd_download_status", scope=scope)
        with use_scope("steamcmd_download_status", clear=True):
            put_text(t("settings.steamcmd_downloading", url=url))
        try:
            def report(message: str) -> None:
                with use_scope("steamcmd_download_status", clear=True):
                    put_text(message)

            steamcmd = ensure_steamcmd(name, log=report)
            with use_scope("steamcmd_download_status", clear=True):
                put_text(t("settings.steamcmd_downloaded", path=steamcmd))
            toast(t("settings.steamcmd_ready"))
        except Exception as exc:
            with use_scope("steamcmd_download_status", clear=True):
                put_warning(t("settings.steamcmd_download_failed", error=exc))
    _render_steamcmd_action(name)

def _save_settings(name: str, *, rerender: bool = True) -> bool:
    profile = load_profile(name)
    data = profile.to_dict()
    server_form_fields = {
        "dedicated_server_name", "query_port", "launch_worker_threads_server",
        "auto_update_idle_minutes",
    }
    for key in server_form_fields:
        pin_name = _settings_pin(key)
        data[key] = getattr(pin, pin_name)
    _clear_field_error(_settings_pin("extra_args"))
    if not _validate_settings_form(
        data,
        {
            "query_port", "launch_worker_threads_server",
            "dedicated_server_name", "auto_update_idle_minutes",
        },
    ):
        toast(t("validation.fix_errors"), color="error")
        return False
    for key in SETTINGS_TOGGLE_KEYS:
        data[key] = bool(local.settings_toggles.get(key, data[key]))
    data["name"] = name
    worker_threads = str(data.get("launch_worker_threads_server", "") or "").strip()
    data["launch_worker_threads_server"] = int(worker_threads) if worker_threads else None
    data["auto_update_idle_minutes"] = int(data["auto_update_idle_minutes"])
    data["extra_args"] = _argument_list_values(_settings_pin("extra_args"))
    try:
        updated = Profile.from_dict(data)
        updated.to_game_config()
    except (TypeError, ValueError) as exc:
        _show_field_error(_settings_pin("extra_args"), str(exc))
        toast(t("validation.fix_errors"), color="error")
        return False
    save_profile(updated)
    _clear_dirty_form()
    toast(t("settings.saved"))
    if rerender:
        _settings(name)
    return True

def _open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    fake_log = os.getenv("PALSITTER_FAKE_OPEN_FOLDER_LOG")
    if fake_log:
        Path(fake_log).write_text(str(path), encoding="utf-8")
        return
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
