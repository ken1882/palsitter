from __future__ import annotations

from pathlib import Path
import threading

from pywebio.output import (
    clear,
    close_popup,
    popup,
    put_button,
    put_loading,
    put_row,
    put_scope,
    put_text,
    put_warning,
    toast,
    use_scope,
)
from pywebio.pin import pin, put_select
from pywebio.session import register_thread

from module.games.palworld.backup import BackupService
from module.games.palworld.config import load_profile
from module.games.palworld.firewall import (
    FirewallError,
    FirewallRepairUnavailable,
    FirewallService,
    FirewallStatus,
    resolve_executable,
)
from module.games.palworld.saves import (
    PlayerMigrationError,
    PlayerMigrationUnavailable,
    PlayerNameCacheError,
    build_player_name_cache,
    list_player_files,
    load_player_name_cache,
    migrate_player_ids,
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


def _migration_world(name: str):
    profile = load_profile(name)
    return Path(profile.backup_source) / profile.dedicated_server_name


def _render_migration(name: str) -> None:
    with use_scope("tools_migration", clear=True):
        put_asset_widget("shared.panel_title", {"title": t("tools.migration_heading")})
        put_text(t("tools.migration_description"))
        try:
            player_files = list_player_files(_migration_world(name))
        except OSError as exc:
            put_warning(t("tools.migration_failed", error=exc))
            return
        name_cache = load_player_name_cache(_migration_world(name))
        options = [
            {
                "label": (
                    f"{name_cache.get(path.stem.casefold())} — {path.name}"
                    if name_cache.get(path.stem.casefold())
                    else path.name
                ),
                "value": path.name,
            }
            for path in player_files
        ]
        if len(options) < 2:
            put_warning(t("tools.migration_needs_two"))
        else:
            put_select(
                "tools_migration_old",
                options=options,
                label=t("tools.migration_old_player"),
            )
            put_select(
                "tools_migration_new",
                options=options,
                label=t("tools.migration_new_player"),
            )
        put_scope("tools_migration_status")
        put_row(
            [
                put_button(
                    t("tools.migration_button"),
                    onclick=lambda: _confirm_migration(name),
                    color="warning",
                    disabled=_manager(name).active or len(options) < 2,
                ),
                put_button(
                    t("tools.name_cache_button"),
                    onclick=lambda: _confirm_name_cache(name),
                    color="secondary",
                    disabled=_manager(name).active,
                ),
            ],
            size="auto 1fr",
        )


def _confirm_migration(name: str) -> None:
    if _manager(name).active:
        with use_scope("tools_migration_status", clear=True):
            put_warning(t("tools.migration_server_running"))
        return
    old_player = str(getattr(pin, "tools_migration_old", "") or "")
    new_player = str(getattr(pin, "tools_migration_new", "") or "")
    with popup(t("tools.migration_confirm_title"), closable=True):
        put_text(t("tools.migration_confirm", old=old_player, new=new_player))
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(
                    t("tools.migration_button"),
                    onclick=lambda: _migrate(name, old_player, new_player),
                    color="warning",
                ),
            ],
            size="1fr auto",
        )


def _confirm_name_cache(name: str) -> None:
    if _manager(name).active:
        with use_scope("tools_migration_status", clear=True):
            put_warning(t("tools.migration_server_running"))
        return
    with popup(t("tools.name_cache_confirm_title"), closable=True):
        put_text(t("tools.name_cache_confirm"))
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(
                    t("tools.name_cache_button"),
                    onclick=lambda: _build_name_cache(name),
                    color="secondary",
                ),
            ],
            size="1fr auto",
        )


def _migration_progress_label(phase: str, filename: str | None) -> str:
    if phase == "backup":
        return t("tools.migration_progress_backup")
    if phase == "unpack":
        return t("tools.migration_progress_unpack", filename=filename or "")
    if phase == "update":
        return t("tools.migration_progress_update")
    if phase == "repack":
        return t("tools.migration_progress_repack", filename=filename or "")
    return phase


def _render_migration_progress(
    events: list[tuple[str, str | None]], *, complete: bool = False
) -> None:
    _render_operation_progress(
        "tools_migration_progress", events, _migration_progress_label, complete=complete
    )


def _render_operation_progress(
    scope: str,
    events: list[tuple[str, str | None]],
    label,
    *,
    complete: bool = False,
) -> None:
    with use_scope(scope, clear=True):
        for index, (phase, filename) in enumerate(events):
            finished = complete or index < len(events) - 1
            put_row(
                [
                    put_text("✓" if finished else ""),
                    put_loading(shape="border", color="primary") if not finished else None,
                    put_text(label(phase, filename)),
                ],
                size="auto auto 1fr",
            )


def _open_operation_progress(title: str, scope: str, starting: str) -> None:
    with popup(title, closable=False, implicit_close=False):
        put_scope(
            scope,
            [put_text(starting)],
        )


def _open_migration_progress() -> None:
    _open_operation_progress(
        t("tools.migration_progress_title"),
        "tools_migration_progress",
        t("tools.migration_progress_starting"),
    )


def _migrate(name: str, old_player: str, new_player: str) -> None:
    context = page_context()
    close_popup()
    _open_migration_progress()
    progress_events: list[tuple[str, str | None]] = []

    def progress(phase: str, filename: str | None) -> None:
        progress_events.append((phase, filename))
        run_if_current(
            context,
            lambda: _render_migration_progress(progress_events),
        )

    with use_scope("tools_migration_status", clear=True):
        put_text(t("tools.migration_working"))
    task = threading.Thread(
        target=lambda: _run_migration(
            name, old_player, new_player, context, progress
        ),
        daemon=True,
    )
    register_thread(task)
    task.start()


def _run_migration(
    name: str,
    old_player: str,
    new_player: str,
    context,
    progress,
) -> None:
    try:
        result = migrate_player_ids(
            load_profile(name),
            old_player,
            new_player,
            is_server_active=lambda: _manager(name).active,
            backup_service=BackupService(
                load_profile(name), logger=_manager(name).append_log
            ),
            progress=progress,
        )
    except PlayerMigrationUnavailable as exc:
        _manager(name).append_log(f"Player migration unavailable: {exc}")
        run_if_current(
            context,
            lambda: _render_migration_error("tools.migration_unavailable", exc),
        )
        return
    except (PlayerMigrationError, OSError) as exc:
        _manager(name).append_log(f"Player migration failed: {exc}")
        run_if_current(
            context,
            lambda: _render_migration_error("tools.migration_failed", exc),
        )
        return
    except Exception as exc:
        _manager(name).append_log(f"Player migration failed: {exc}")
        run_if_current(
            context,
            lambda: _render_migration_error("tools.migration_failed", exc),
        )
        return
    _manager(name).append_log(
        f"Player migration completed: {result.old_player_file.name} -> "
        f"{result.new_player_file.name}; safety backup: {result.safety_backup}"
    )
    run_if_current(
        context,
        lambda: _render_migration_success(result.safety_backup),
    )


def _name_cache_progress_label(phase: str, filename: str | None) -> str:
    if phase == "unpack":
        return t("tools.name_cache_progress_unpack", filename=filename or "")
    if phase == "extract":
        return t("tools.name_cache_progress_extract")
    if phase == "write":
        return t("tools.name_cache_progress_write", filename=filename or "")
    return phase


def _build_name_cache(name: str) -> None:
    context = page_context()
    close_popup()
    _open_operation_progress(
        t("tools.name_cache_progress_title"),
        "tools_name_cache_progress",
        t("tools.name_cache_progress_starting"),
    )
    progress_events: list[tuple[str, str | None]] = []

    def progress(phase: str, filename: str | None) -> None:
        progress_events.append((phase, filename))
        run_if_current(
            context,
            lambda: _render_operation_progress(
                "tools_name_cache_progress",
                progress_events,
                _name_cache_progress_label,
            ),
        )

    with use_scope("tools_migration_status", clear=True):
        put_text(t("tools.name_cache_working"))
    task = threading.Thread(
        target=lambda: _run_name_cache(name, context, progress),
        daemon=True,
    )
    register_thread(task)
    task.start()


def _run_name_cache(name: str, context, progress) -> None:
    try:
        result = build_player_name_cache(
            load_profile(name),
            is_server_active=lambda: _manager(name).active,
            progress=progress,
        )
    except PlayerMigrationUnavailable as exc:
        _manager(name).append_log(f"Player name cache unavailable: {exc}")
        run_if_current(
            context,
            lambda: _render_name_cache_error("tools.name_cache_unavailable", exc),
        )
        return
    except (PlayerNameCacheError, OSError) as exc:
        _manager(name).append_log(f"Player name cache failed: {exc}")
        run_if_current(
            context,
            lambda: _render_name_cache_error("tools.name_cache_failed", exc),
        )
        return
    except Exception as exc:
        _manager(name).append_log(f"Player name cache failed: {exc}")
        run_if_current(
            context,
            lambda: _render_name_cache_error("tools.name_cache_failed", exc),
        )
        return
    _manager(name).append_log(
        f"Player name cache completed: {result.cache_path} ({result.player_count} players)"
    )
    run_if_current(
        context,
        lambda: _render_name_cache_success(
            name, result.cache_path, result.player_count
        ),
    )


def _render_name_cache_error(key: str, error: BaseException) -> None:
    close_popup()
    with use_scope("tools_migration_status", clear=True):
        put_warning(t(key, error=error))


def _render_name_cache_success(name: str, path: Path, player_count: int) -> None:
    close_popup()
    _render_migration(name)
    with use_scope("tools_migration_status", clear=True):
        put_text(t("tools.name_cache_completed", path=path, count=player_count))


def _render_migration_error(key: str, error: BaseException) -> None:
    close_popup()
    with use_scope("tools_migration_status", clear=True):
        put_warning(t(key, error=error))


def _render_migration_success(backup: Path) -> None:
    close_popup()
    with use_scope("tools_migration_status", clear=True):
        put_text(t("tools.migration_completed", backup=backup))


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
        put_scope("tools_panel", children + [put_scope("tools_migration")])
    client_call("dom.addClasses", scope="tools_panel", classes=["panel"])
    _render_migration(name)


__all__ = ["render"]
