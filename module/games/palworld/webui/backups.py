from __future__ import annotations
import datetime as dt
import threading
import time
from pathlib import Path
from pywebio.output import clear, close_popup, popup, put_button, put_row, put_scope, put_table, put_text, put_warning, toast, use_scope
from pywebio.pin import pin
from pywebio.session import local, register_thread
from module.games.palworld.backup import BackupService, ServerOwnershipState
from module.games.palworld.config import PalworldProfile, load_profile, save_profile
from module.games.palworld.server import PalRestClient
from module.games.palworld.saves import ManagedWorldService
from module.webui.i18n import t
from module.webui.assets import client_call, client_query, put_asset_widget

def _backup_now(*args, **kwargs):
    from module.games.palworld.webui.overview import _backup_now as implementation
    return implementation(*args, **kwargs)

def _clear_dirty_form(*args, **kwargs):
    from module.webui.forms import _clear_dirty_form as implementation
    return implementation(*args, **kwargs)

def _manager(*args, **kwargs):
    from module.webui.instance import _manager as implementation
    return implementation(*args, **kwargs)

def _mark_dirty_form(*args, **kwargs):
    from module.webui.forms import _mark_dirty_form as implementation
    return implementation(*args, **kwargs)

def _open_folder(*args, **kwargs):
    from module.games.palworld.webui.server_settings import _open_folder as implementation
    return implementation(*args, **kwargs)

def _register_dirty_form(*args, **kwargs):
    from module.webui.forms import _register_dirty_form as implementation
    return implementation(*args, **kwargs)

def _render_instance_menu(*args, **kwargs):
    from module.webui.instance import _render_instance_menu as implementation
    return implementation(*args, **kwargs)

def _set_frame(*args, **kwargs):
    from module.webui.instance import _set_frame as implementation
    return implementation(*args, **kwargs)

def _settings_field(*args, **kwargs):
    from module.games.palworld.webui.forms import _settings_field as implementation
    return implementation(*args, **kwargs)

def _settings_field_row(*args, **kwargs):
    from module.webui.forms import _settings_field_row as implementation
    return implementation(*args, **kwargs)

def _settings_field_with_browse(*args, **kwargs):
    from module.games.palworld.webui.forms import _settings_field_with_browse as implementation
    return implementation(*args, **kwargs)

def _settings_label(*args, **kwargs):
    from module.webui.forms import _settings_label as implementation
    return implementation(*args, **kwargs)

def _settings_pin(*args, **kwargs):
    from module.games.palworld.webui.server_settings import _settings_pin as implementation
    return implementation(*args, **kwargs)

def _validate_settings_form(*args, **kwargs):
    from module.games.palworld.webui.forms import _validate_settings_form as implementation
    return implementation(*args, **kwargs)

Profile = PalworldProfile

def render(name: str) -> None:
    profile = load_profile(name)
    local.backup_skip_no_players = profile.skip_backup_when_no_players
    clear("content")
    with use_scope("content"):
        put_scope(
            "backup_settings_panel",
            [
                put_asset_widget("shared.panel_title", {"title": t("backups.title")}),
                put_scope("managed_worlds"),
                put_scope("backup_settings_form"),
                put_scope("backup_settings_actions"),
                put_scope("builtin_backup_files"),
                put_scope("backup_files"),
            ],
        )
        client_call("dom.addClasses", scope="backup_settings_panel", classes=["panel"])
        client_call("dom.addClasses", scope="backup_settings_form", classes=["settings-view"])
        client_call("dom.addClasses", scope="backup_settings_actions", classes=["settings-actions"])
        with use_scope("backup_settings_form"):
            _settings_field_with_browse(
                _settings_label("backup_dir"), "backup_dir", profile.backup_dir, escape_label=False
            )
            _settings_field(
                _settings_label("backup_interval_minutes"),
                "backup_interval_minutes",
                profile.backup_interval_minutes,
                type="float",
                escape_label=False,
            )
            _settings_field(
                _settings_label("backup_retention_count"),
                "backup_retention_count",
                profile.backup_retention_count,
                type="number",
                escape_label=False,
            )
            _settings_field_row(
                _settings_label("skip_backup_when_no_players"),
                put_scope("backup_skip_no_players_toggle"),
                escape_label=False,
            )
            _render_backup_skip_toggle()
            _settings_field_row(
                t("backups.backup_now"), put_scope("backup_now_button")
            )
            _render_backup_now_button(name)
        with use_scope("backup_settings_actions"):
            put_row(
                [
                    put_asset_widget("shared.strong_text", {"text": t("form.unsaved_bar")}),
                    None,
                    put_button(t("common.reset"), onclick=lambda: _backups(name), color="secondary"),
                    put_button(t("common.save"), onclick=lambda: _save_backup_settings(name), color="success"),
                ],
                size="auto 1fr auto auto",
            )
        _render_managed_worlds(name)
        _render_builtin_backup_files(name)
        _render_backup_files(name)
    _register_dirty_form(
        "pywebio-scope-backup_settings_panel",
        lambda: _save_backup_settings(name, rerender=False),
    )


def _backups(name: str) -> None:
    from module.webui.instance import open_instance
    open_instance(name, "backups")

@use_scope("backup_skip_no_players_toggle", clear=True)
def _render_backup_skip_toggle() -> None:
    enabled = bool(getattr(local, "backup_skip_no_players", True))
    put_button(
        t("common.on") if enabled else t("common.off"),
        onclick=_toggle_backup_skip,
        color="success" if enabled else "secondary",
    )

def _toggle_backup_skip() -> None:
    _mark_dirty_form()
    local.backup_skip_no_players = not bool(getattr(local, "backup_skip_no_players", True))
    _render_backup_skip_toggle()

@use_scope("backup_now_button", clear=True)
def _render_backup_now_button(name: str, disabled: bool = False) -> None:
    put_button(t("backups.backup_now"), onclick=lambda: _backup_now(name), disabled=disabled)

def _managed_world_service(name: str) -> ManagedWorldService:
    profile = load_profile(name)
    manager = _manager(name)
    return ManagedWorldService(
        profile,
        backup_service=BackupService(profile, logger=manager.append_log),
        is_server_active=lambda: manager.active,
    )

@use_scope("managed_worlds", clear=True)
def _render_managed_worlds(name: str) -> None:
    try:
        worlds = _managed_world_service(name).managed_worlds()
    except (FileNotFoundError, ValueError) as exc:
        put_warning(t("backups.worlds_failed", error=exc))
        return
    put_asset_widget("palworld.backup_title", {"title": t("backups.worlds_title")})
    if not worlds:
        put_text(t("backups.no_worlds"))
        return
    rows = []
    manager = _manager(name)
    for world in worlds:
        marker = t("backups.active_world") if world.active else ""
        details = put_asset_widget(
            "palworld.managed_world_details",
            {
                "world": world.world_id,
                "marker": marker,
                "metadata": " · ".join(
                    (
                        world.modified_at.strftime("%Y-%m-%d %H:%M:%S"),
                        _human_size(world.size_bytes),
                        t("backups.player_saves", count=world.player_save_count),
                    )
                ),
            },
        )
        rows.append(
            [
                details,
                put_button(
                    t("backups.switch_world"),
                    onclick=lambda world_id=world.world_id: _confirm_world_switch(name, world_id),
                    color="secondary",
                    disabled=world.active or manager.active,
                ),
            ]
        )
    put_table(rows, header=[t("backups.world"), ""])

def _confirm_world_switch(name: str, world_id: str) -> None:
    with popup(t("backups.switch_title"), closable=True):
        put_text(t("backups.switch_confirm", world=world_id))
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(
                    t("backups.switch_world"),
                    onclick=lambda: _start_world_switch(name, world_id),
                    color="warning",
                ),
            ],
            size="1fr auto",
        )

def _start_world_switch(name: str, world_id: str) -> None:
    close_popup()

    def run() -> None:
        try:
            result = _managed_world_service(name).switch_world(world_id)
            toast(
                t(
                    "backups.switch_complete",
                    world=result.active_world.world_id,
                    backup=result.safety_backup.name,
                )
            )
        except Exception as exc:
            toast(t("backups.switch_failed", error=exc), color="error")
        if client_query("dom.scopeExists", scope="managed_worlds"):
            _render_managed_worlds(name)
            _render_backup_files(name)

    task = threading.Thread(target=run, daemon=True)
    register_thread(task)
    task.start()

def _save_backup_settings(name: str, *, rerender: bool = True) -> bool:
    profile = load_profile(name)
    data = profile.to_dict()
    for key in ("backup_dir", "backup_interval_minutes", "backup_retention_count"):
        data[key] = getattr(pin, _settings_pin(key))
    if not _validate_settings_form(
        data, {"backup_dir", "backup_interval_minutes", "backup_retention_count"}
    ):
        toast(t("validation.fix_errors"), color="error")
        return False
    data["skip_backup_when_no_players"] = bool(local.backup_skip_no_players)
    updated = Profile.from_dict(data)
    save_profile(updated)
    _clear_dirty_form()
    toast(t("settings.saved"))
    if rerender:
        _backups(name)
    return True

def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"

def _icon_button(label: str, glyph: str, onclick, *, danger: bool = False, folder: bool = False):
    return put_asset_widget(
        "palworld.backup_icon_button",
        {"label": label, "glyph": glyph, "danger": danger, "folder": folder},
    ).onclick(onclick)

@use_scope("builtin_backup_files", clear=True)
def _render_builtin_backup_files(name: str) -> None:
    backups = BackupService(load_profile(name)).list_builtin_backups()
    folder_button = _icon_button(
        t("backups.open_builtin_folder"),
        "📁",
        lambda: _open_builtin_backup_folder(name),
        folder=True,
    )
    put_scope(
        "builtin_backup_title_row",
        [put_row(
            [
                put_asset_widget("palworld.backup_title", {"title": t("backups.builtin_title", count=len(backups))}),
                folder_button,
                None,
            ],
            size="auto auto 1fr",
        )],
    )
    if not backups:
        put_text(t("backups.no_builtin_files"))
        return
    rows = []
    for backup in backups:
        details = put_asset_widget(
            "palworld.backup_file_details",
            {"name": backup.name, "metadata": f"{backup.modified_at:%Y-%m-%d %H:%M:%S} · {_human_size(backup.size_bytes)}"},
        )
        rows.append(
            [
                details,
                _icon_button(
                    t("backups.rollback"),
                    "↻",
                    lambda p=backup.path: _confirm_backup_rollback(name, p),
                ),
            ]
        )
    put_table(
        rows,
        header=[
            t("backups.builtin_file"),
            "",
        ],
    )

@use_scope("backup_files", clear=True)
def _render_backup_files(name: str) -> None:
    profile = load_profile(name)
    backups = BackupService(profile).list_backups()
    folder_button = _icon_button(
        t("backups.open_folder"), "📁", lambda: _open_backup_folder(name), folder=True
    )
    put_scope(
        "backup_title_row",
        [put_row(
            [
                put_asset_widget("palworld.backup_title", {"title": t("backups.files_title", count=len(backups), max=profile.backup_retention_count)}),
                folder_button,
                None,
            ],
            size="auto auto 1fr",
        )],
    )
    if not backups:
        put_text(t("backups.no_files"))
        return
    rows = []
    for path in backups:
        timestamp = dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        details = put_asset_widget(
            "palworld.backup_file_details",
            {"name": path.name, "metadata": f"{timestamp} · {_human_size(path.stat().st_size)}"},
        )
        rows.append(
            [
                details,
                _icon_button(t("backups.rollback"), "↻", lambda p=path: _confirm_backup_rollback(name, p)),
                _icon_button(t("backups.delete"), "🗑", lambda p=path: _confirm_backup_delete(name, p), danger=True),
            ]
        )
    put_table(rows, header=[t("backups.file"), "", ""])

def _open_backup_folder(name: str) -> None:
    try:
        _open_folder(Path(load_profile(name).backup_dir))
    except Exception as exc:
        toast(t("backups.open_failed", error=exc), color="error")

def _open_builtin_backup_folder(name: str) -> None:
    try:
        profile = load_profile(name)
        backups = BackupService(profile).list_builtin_backups()
        folder = (
            backups[0].path.parent
            if backups
            else Path(profile.backup_source)
            / profile.dedicated_server_name
            / "backup"
            / "world"
        )
        _open_folder(folder)
    except Exception as exc:
        toast(t("backups.open_failed", error=exc), color="error")

def _confirm_backup_delete(name: str, path: Path) -> None:
    with popup(t("backups.delete_title"), closable=True):
        put_text(t("backups.delete_confirm", file=path.name))
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(
                    t("backups.delete"),
                    onclick=lambda: _delete_backup(name, path),
                    color="danger",
                ),
            ],
            size="1fr auto",
        )

def _delete_backup(name: str, path: Path) -> None:
    close_popup()
    try:
        BackupService(load_profile(name), logger=_manager(name).append_log).delete_backup(path)
        toast(t("backups.deleted", file=path.name))
    except Exception as exc:
        toast(t("backups.delete_failed", error=exc), color="error")
    _render_backup_files(name)

def _confirm_backup_rollback(name: str, path: Path) -> None:
    with popup(t("backups.rollback_title"), closable=True):
        put_text(t("backups.rollback_confirm", file=path.name))
        put_warning(t("backups.safety_required"))
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(
                    t("backups.rollback"),
                    onclick=lambda: _start_backup_rollback(name, path),
                    color="danger",
                ),
            ],
            size="1fr auto",
        )

def _start_backup_rollback(name: str, path: Path) -> None:
    close_popup()
    toast(t("backups.rollback_started"))
    task = threading.Thread(
        target=lambda: _run_backup_rollback(name, path, True), daemon=True
    )
    register_thread(task)
    task.start()

def _run_backup_rollback(
    name: str,
    path: Path,
    backup_current: bool,
    *,
    manager=None,
    service=None,
    sleep=time.sleep,
) -> None:
    manager = manager or _manager(name)
    profile = load_profile(name)
    service = service or BackupService(
        profile,
        logger=manager.append_log,
        rest_client=PalRestClient(profile),
    )
    if manager.ownership == "external":
        initial_state = ServerOwnershipState.EXTERNAL_ATTACHED
    elif manager.active and manager.ownership == "managed":
        initial_state = ServerOwnershipState.OWNED_RUNNING
    else:
        initial_state = ServerOwnershipState.INACTIVE

    def wait_until_inactive(timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while manager.active and time.monotonic() < deadline:
            sleep(0.25)
        return not manager.active

    try:
        result = service.restore_preserving_state(
            path,
            initial_state=initial_state,
            stop_server=manager.stop if initial_state is not ServerOwnershipState.INACTIVE else None,
            wait_until_inactive=(
                wait_until_inactive
                if initial_state is not ServerOwnershipState.INACTIVE
                else None
            ),
            kill_server=(
                manager.kill
                if initial_state is ServerOwnershipState.OWNED_RUNNING
                else None
            ),
            start_server=(
                manager.start
                if initial_state is ServerOwnershipState.OWNED_RUNNING
                else None
            ),
        )
        toast(t("backups.rollback_complete", file=path.name))
        manager.append_log(
            f"Restore completed with safety backup {result.safety_backup_path.name}; "
            f"restarted={result.restarted}"
        )
    except Exception as exc:
        manager.append_log(f"Backup rollback failed: {exc}")
        toast(t("backups.rollback_failed", error=exc), color="error")
    if client_query("dom.scopeExists", scope="backup_files"):
        _render_backup_files(name)
        _render_managed_worlds(name)
