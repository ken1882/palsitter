from __future__ import annotations

from pathlib import Path
import shlex
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
from pywebio.pin import pin, put_input, put_select
from pywebio.session import local, register_thread

from module.games.palworld.backup import BackupService
from module.games.palworld.config import load_profile, rename_profile
from module.games.palworld.firewall import (
    FirewallError,
    FirewallPermissionDenied,
    FirewallRepairUnavailable,
    FirewallService,
    FirewallStatus,
    resolve_executable,
)
from module.games.palworld.saves import (
    PlayerMigrationError,
    PlayerMigrationDetails,
    PlayerMigrationUnavailable,
    PlayerNameCacheError,
    build_player_name_cache,
    list_player_files,
    load_player_details_cache,
    load_player_name_cache,
    migrate_player_ids,
)
from module.games.palworld.server.agent import agent_server_is_running, stop_idle_agent
from module.webui.assets import client_call, put_asset_widget
from module.webui.i18n import t
from module.webui.section_layout import SectionSpec, put_section_layout
from module.webui.session import page_context, run_if_current
from module.webui.settings import load_web_settings


def _manager(name: str):
    from module.webui.instance import _manager as implementation

    return implementation(name)


def _delete_instance(name: str) -> None:
    from module.webui.instance import confirm_delete_instance

    confirm_delete_instance(
        name,
        delete_guard=lambda: _delete_guard(name),
        delete_prepare=lambda: _prepare_delete(name),
    )


def _open_instance(name: str, page_id: str = "overview") -> None:
    from module.webui.instance import open_instance

    open_instance(name, page_id)


def _service(name: str | None = None) -> FirewallService:
    logger = (lambda message: _log(name, message)) if name is not None else None
    return FirewallService(logger=logger, debug=load_web_settings().debug_mode)


def _log(name: str, message: str) -> None:
    _manager(name).append_log(f"Firewall: {message}")


def _instance_runtime_active(name: str) -> bool:
    manager = _manager(name)
    return manager.active or manager.display_state == "running" or agent_server_is_running(name)


def _delete_guard(name: str) -> str | None:
    return t("tools.delete_running") if _instance_runtime_active(name) else None


def _prepare_delete(name: str) -> str | None:
    try:
        stop_idle_agent(name)
    except (OSError, RuntimeError, TimeoutError) as exc:
        return t("settings.delete_failed", error=exc)
    return None


def _render_instance_management(name: str) -> None:
    running = _instance_runtime_active(name)
    with use_scope("tools_instance", clear=True):
        put_asset_widget("shared.panel_title", {"title": t("tools.instance_heading")})
        put_text(t("tools.instance_description"))
        put_scope("tools_instance_status")
        put_row(
            [
                put_button(
                    t("tools.rename"),
                    onclick=lambda: _confirm_rename(name),
                    color="primary",
                    disabled=running,
                ),
                put_button(
                    t("settings.delete"),
                    onclick=lambda: _delete_instance(name),
                    color="danger",
                    disabled=running,
                ),
            ],
            size="auto auto",
        )


def _confirm_rename(name: str) -> None:
    if _instance_runtime_active(name):
        with use_scope("tools_instance_status", clear=True):
            put_warning(t("tools.rename_running"))
        return
    with popup(t("tools.rename_title"), closable=True):
        put_text(t("tools.rename_confirm", name=name))
        put_input("tools_rename_name", label=t("tools.rename_name"), value=name)
        put_scope("tools_rename_error")
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(
                    t("tools.rename"),
                    onclick=lambda: _rename(name),
                    color="primary",
                ),
            ],
            size="1fr auto",
        )


def _rename(name: str) -> None:
    replacement = str(getattr(pin, "tools_rename_name", "") or "").strip()
    if replacement == name:
        with use_scope("tools_rename_error", clear=True):
            put_warning(t("tools.rename_same"))
        return
    if _instance_runtime_active(name):
        with use_scope("tools_rename_error", clear=True):
            put_warning(t("tools.rename_running"))
        return
    try:
        stop_idle_agent(name)
        profile = rename_profile(name, replacement)
    except (FileExistsError, OSError, RuntimeError, ValueError) as exc:
        with use_scope("tools_rename_error", clear=True):
            put_warning(t("tools.rename_failed", error=exc))
        return
    close_popup()
    toast(t("tools.renamed", old=name, new=profile.name))
    _open_instance(profile.name, "tools")


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
                put_text(t("tools.executable_status")+': '),
                put_text(
                    t("tools.not_applicable")
                    if not status.executable_supported
                    else t("tools.allowed")
                    if status.executable_allowed
                    else t("tools.not_allowed")
                ),
            ],
            size="auto 1fr",
        )
        put_row(
            [
                put_text(t("tools.port_status")+': '),
                put_text(t("tools.allowed") if status.port_allowed else t("tools.not_allowed")),
            ],
            size="auto 1fr",
        )
        if status.external_block_rule_names:
            put_warning(t("tools.manual_block", rules=", ".join(status.external_block_rule_names)))


def _status_log_details(status: FirewallStatus) -> str:
    details = []
    if status.executable_supported:
        details.append(
            f"executable rule: {'allowed' if status.executable_allowed else 'not allowed'}"
        )
    else:
        details.append("executable rule: not applicable")
    details.append(f"UDP port rule: {'allowed' if status.port_allowed else 'not allowed'}")
    block_rules = tuple(
        dict.fromkeys(
            [*status.owned_block_rule_names, *status.external_block_rule_names]
        )
    )
    if block_rules:
        details.append(f"matching block rules: {', '.join(block_rules)}")
    return "; ".join(details)


def _set_tools_firewall_check_disabled(disabled: bool) -> None:
    client_call(
        "dom.setControlDisabled",
        selector="#pywebio-scope-tools_check_button button",
        disabled=disabled,
    )


def _begin_tools_firewall_check() -> bool:
    if bool(getattr(local, "tools_firewall_check_busy", False)):
        return False
    local.tools_firewall_check_busy = True
    _set_tools_firewall_check_disabled(True)
    return True


def _end_tools_firewall_check() -> None:
    local.tools_firewall_check_busy = False
    _set_tools_firewall_check_disabled(False)


def _finish_tools_check(
    name: str,
    status: FirewallStatus,
    ask_to_fix: bool,
    context,
) -> None:
    _end_tools_firewall_check()
    _apply_check_result(name, status, ask_to_fix, context)


def _finish_tools_check_auth(
    name: str,
    ask_to_fix: bool,
    context,
    command: tuple[str, ...],
) -> None:
    _end_tools_firewall_check()
    _request_check_root_password(name, ask_to_fix, context, command)


def _check(name: str, *, ask_to_fix: bool = True, context=None) -> None:
    context = context or page_context()
    if not _begin_tools_firewall_check():
        return
    profile = load_profile(name)
    try:
        status = _service(name).check(profile)
    except FirewallPermissionDenied as exc:
        _log(name, "check requires administrator authentication")
        command = exc.command
        run_if_current(
            context,
            lambda: _finish_tools_check_auth(name, ask_to_fix, context, command),
        )
        return
    if status.error:
        _log(name, f"check failed: {status.error}")
    elif status.allowed:
        _log(name, f"check passed ({_status_log_details(status)})")
    else:
        _log(name, f"check blocked ({_status_log_details(status)})")
    run_if_current(
        context,
        lambda: _finish_tools_check(name, status, ask_to_fix, context),
    )


def _apply_check_result(name: str, status: FirewallStatus, ask_to_fix: bool, context) -> None:
    _render_status(status)
    if ask_to_fix and status.repairable:
        _confirm_fix(name, status, context)


def _confirm_fix(name: str, status: FirewallStatus, context) -> None:
    with popup(t("tools.fix_title"), closable=True):
        put_text(
            t(
                "tools.fix_block_prompt",
                rules=", ".join(status.external_block_rule_names),
            )
            if status.external_block_rule_names
            else t("tools.fix_prompt")
        )
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
        _service(name).fix(load_profile(name), status)
    except FirewallPermissionDenied as exc:
        _log(name, "repair requires administrator authentication")
        command = exc.command
        run_if_current(
            context,
            lambda: _request_root_password(name, status, context, command),
        )
        return
    except FirewallRepairUnavailable as exc:
        _log(name, f"repair unavailable: {exc}")
        run_if_current(
            context,
            lambda exc=exc: _render_fix_error("tools.fix_unavailable", exc),
        )
        return
    except (FirewallError, OSError) as exc:
        _log(name, f"repair failed: {exc}")
        run_if_current(
            context,
            lambda exc=exc: _render_fix_error("tools.fix_failed", exc),
        )
        return
    _log(name, "repair command completed; rechecking firewall")
    run_if_current(context, lambda: toast(t("tools.fixed")))
    _check(name, ask_to_fix=False, context=context)


def _root_command_text(command: tuple[str, ...]) -> str:
    return shlex.join(("sudo", *command)) if command else "sudo <firewall command>"


def _request_root_password(
    name: str,
    status: FirewallStatus,
    context,
    command: tuple[str, ...] = (),
) -> None:
    with popup(t("tools.root_password_title"), closable=True):
        put_text(t("tools.root_password_description"))
        put_text(t("tools.root_password_command", command=_root_command_text(command)))
        put_input(
            "tools_root_password",
            type="password",
            label=t("tools.root_password_label"),
        )
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(
                    t("tools.fix"),
                    onclick=lambda: _retry_fix_with_password(name, status, context),
                    color="warning",
                ),
            ],
            size="1fr auto",
        )


def _request_check_root_password(
    name: str,
    ask_to_fix: bool,
    context,
    command: tuple[str, ...] = (),
) -> None:
    with popup(t("tools.root_password_title"), closable=True):
        put_text(t("tools.root_password_description"))
        put_text(t("tools.root_password_command", command=_root_command_text(command)))
        put_input(
            "tools_root_password",
            type="password",
            label=t("tools.root_password_label"),
        )
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(
                    t("tools.check"),
                    onclick=lambda: _retry_check_with_password(name, ask_to_fix, context),
                    color="warning",
                ),
            ],
            size="1fr auto",
        )


def _retry_check_with_password(name: str, ask_to_fix: bool, context) -> None:
    password = str(getattr(pin, "tools_root_password", "") or "")
    close_popup()
    if not password:
        run_if_current(
            context,
            lambda: _render_fix_error("tools.root_password_required", ""),
        )
        return
    if not _begin_tools_firewall_check():
        return
    try:
        status = _service(name).check(load_profile(name), root_password=password)
    except FirewallPermissionDenied:
        _log(name, "check failed: administrator authentication was rejected")
        _end_tools_firewall_check()
        run_if_current(
            context,
            lambda: _render_fix_error(
                "tools.check_failed", "administrator authentication was rejected"
            ),
        )
        return
    except (FirewallError, OSError) as exc:
        _log(name, f"check failed: {exc}")
        _end_tools_firewall_check()
        run_if_current(context, lambda exc=exc: _render_fix_error("tools.check_failed", exc))
        return
    finally:
        password = ""
    if status.allowed:
        _log(name, f"check passed ({_status_log_details(status)})")
    else:
        _log(name, f"check blocked ({_status_log_details(status)})")
    run_if_current(
        context,
        lambda: _finish_tools_check(name, status, ask_to_fix, context),
    )


def _retry_fix_with_password(name: str, status: FirewallStatus, context) -> None:
    password = str(getattr(pin, "tools_root_password", "") or "")
    close_popup()
    if not password:
        run_if_current(
            context,
            lambda: _render_fix_error("tools.root_password_required", ""),
        )
        return
    try:
        _service(name).fix(load_profile(name), status, root_password=password)
    except FirewallPermissionDenied:
        _log(name, "repair failed: administrator authentication was rejected")
        run_if_current(
            context,
            lambda: _render_fix_error(
                "tools.fix_failed", "administrator authentication was rejected"
            ),
        )
        return
    except (FirewallError, OSError) as exc:
        _log(name, f"repair failed: {exc}")
        run_if_current(context, lambda exc=exc: _render_fix_error("tools.fix_failed", exc))
        return
    finally:
        password = ""
    _log(name, "repair command completed; rechecking firewall")
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
        details_cache = load_player_details_cache(_migration_world(name))
        options = [
            {
                "label": _migration_player_label(
                    path.name, details_cache.get(path.stem.casefold())
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
                    color="primary",
                    disabled=_manager(name).active,
                ),
            ],
            size="auto 1fr",
        )


def _migration_player_label(
    filename: str, details: PlayerMigrationDetails | None
) -> str:
    if details is None:
        return filename
    if details.name is None:
        if details.owned_pal_count is None:
            return filename
        return t(
            "tools.migration_player_option_without_name",
            pal_count=details.owned_pal_count,
            id=filename,
        )
    return t(
        "tools.migration_player_option",
        name=details.name,
        pal_count=(
            details.owned_pal_count
            if details.owned_pal_count is not None
            else "?"
        ),
        id=filename,
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


def _confirm_decoded_name_mismatch(
    context,
    source_expected: str,
    source_actual: str,
    destination_expected: str,
    destination_actual: str,
) -> bool:
    decision = {"continue": False}
    resolved = threading.Event()

    def resolve(continue_migration: bool) -> None:
        decision["continue"] = continue_migration
        if continue_migration:
            _show_migration_progress()
        else:
            close_popup()
        resolved.set()

    run_if_current(
        context,
        lambda: _open_decoded_name_mismatch(
            source_expected,
            source_actual,
            destination_expected,
            destination_actual,
            resolve,
        ),
    )
    while not resolved.wait(0.1):
        if context is not None and context.stop_event.is_set():
            return False
    return bool(decision["continue"])


def _open_decoded_name_mismatch(
    source_expected: str,
    source_actual: str,
    destination_expected: str,
    destination_actual: str,
    resolve,
) -> None:
    with use_scope("tools_migration_dialog", clear=True):
        put_text(t("tools.migration_name_mismatch_title"))
        put_text(
            t(
                "tools.migration_name_mismatch",
                source_expected=source_expected,
                source_actual=source_actual,
                destination_expected=destination_expected,
                destination_actual=destination_actual,
            )
        )
        put_row(
            [
                put_button(
                    t("common.cancel"),
                    onclick=lambda: resolve(False),
                    color="secondary",
                ),
                put_button(
                    t("tools.migration_continue"),
                    onclick=lambda: resolve(True),
                    color="warning",
                ),
            ],
            size="1fr auto",
        )


def _confirm_destination_pal_count(
    context,
    source_pal_count: int,
    destination_pal_count: int,
) -> bool:
    decision = {"continue": False}
    resolved = threading.Event()

    def resolve(continue_migration: bool) -> None:
        decision["continue"] = continue_migration
        if continue_migration:
            _show_migration_progress()
        else:
            close_popup()
        resolved.set()

    run_if_current(
        context,
        lambda: _open_destination_pal_count_warning(
            source_pal_count,
            destination_pal_count,
            resolve,
        ),
    )
    while not resolved.wait(0.1):
        if context is not None and context.stop_event.is_set():
            return False
    return bool(decision["continue"])


def _open_destination_pal_count_warning(
    source_pal_count: int,
    destination_pal_count: int,
    resolve,
) -> None:
    with use_scope("tools_migration_dialog", clear=True):
        put_text(t("tools.migration_destination_pals_title"))
        put_text(
            t(
                "tools.migration_destination_pals",
                source_count=source_pal_count,
                destination_count=destination_pal_count,
            )
        )
        put_row(
            [
                put_button(
                    t("common.cancel"),
                    onclick=lambda: resolve(False),
                    color="secondary",
                ),
                put_button(
                    t("tools.migration_continue"),
                    onclick=lambda: resolve(True),
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
                    color="primary",
                ),
            ],
            size="1fr auto",
        )


def _migration_progress_label(phase: str, filename: str | None) -> str:
    if phase == "backup":
        return t("tools.migration_progress_backup")
    if phase == "unpack":
        return t("tools.migration_progress_unpack", filename=filename or "")
    if phase == "cache":
        return t("tools.migration_progress_cache")
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
    with popup(
        t("tools.migration_progress_title"),
        closable=False,
        implicit_close=False,
    ):
        put_scope("tools_migration_dialog")
    _show_migration_progress()


def _show_migration_progress() -> None:
    with use_scope("tools_migration_dialog", clear=True):
        put_scope(
            "tools_migration_progress",
            [put_text(t("tools.migration_progress_starting"))],
        )


def _migrate(name: str, old_player: str, new_player: str) -> None:
    context = page_context()
    close_popup()
    _open_migration_progress()
    progress_events: list[tuple[str, str | None]] = []
    expected_names = load_player_name_cache(_migration_world(name))

    def progress(phase: str, filename: str | None) -> None:
        if (
            phase == "unpack"
            and progress_events
            and progress_events[-1][0] == "unpack"
        ):
            progress_events[-1] = (phase, filename)
        else:
            progress_events.append((phase, filename))
        run_if_current(
            context,
            lambda: _render_migration_progress(progress_events),
        )

    with use_scope("tools_migration_status", clear=True):
        put_text(t("tools.migration_working"))
    task = threading.Thread(
        target=lambda: _run_migration(
            name, old_player, new_player, context, progress, expected_names
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
    expected_names,
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
            expected_names=expected_names,
            confirm_name_mismatch=lambda source_expected, source_actual, destination_expected, destination_actual: _confirm_decoded_name_mismatch(
                context,
                source_expected,
                source_actual,
                destination_expected,
                destination_actual,
            ),
            confirm_destination_pal_count=lambda source_count, destination_count: _confirm_destination_pal_count(
                context,
                source_count,
                destination_count,
            ),
        )
    except PlayerMigrationUnavailable as exc:
        _manager(name).append_log(f"Player migration unavailable: {exc}")
        run_if_current(
            context,
            lambda exc=exc: _render_migration_error(
                name, "tools.migration_unavailable", exc
            ),
        )
        return
    except (PlayerMigrationError, OSError) as exc:
        _manager(name).append_log(f"Player migration failed: {exc}")
        run_if_current(
            context,
            lambda exc=exc: _render_migration_error(
                name, "tools.migration_failed", exc
            ),
        )
        return
    except Exception as exc:
        _manager(name).append_log(f"Player migration failed: {exc}")
        run_if_current(
            context,
            lambda exc=exc: _render_migration_error(
                name, "tools.migration_failed", exc
            ),
        )
        return
    _manager(name).append_log(
        f"Player migration completed: {result.old_player_file.name} -> "
        f"{result.new_player_file.name}; safety backup: {result.safety_backup}"
    )
    run_if_current(
        context,
        lambda: _render_migration_success(name, result.safety_backup),
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
            lambda exc=exc: _render_name_cache_error("tools.name_cache_unavailable", exc),
        )
        return
    except (PlayerNameCacheError, OSError) as exc:
        _manager(name).append_log(f"Player name cache failed: {exc}")
        run_if_current(
            context,
            lambda exc=exc: _render_name_cache_error("tools.name_cache_failed", exc),
        )
        return
    except Exception as exc:
        _manager(name).append_log(f"Player name cache failed: {exc}")
        run_if_current(
            context,
            lambda exc=exc: _render_name_cache_error("tools.name_cache_failed", exc),
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


def _render_migration_error(name: str, key: str, error: BaseException) -> None:
    close_popup()
    _render_migration(name)
    with use_scope("tools_migration_status", clear=True):
        put_warning(t(key, error=error))


def _render_migration_success(name: str, backup: Path) -> None:
    close_popup()
    _render_migration(name)
    with use_scope("tools_migration_status", clear=True):
        put_text(t("tools.migration_completed", backup=backup))


def render(name: str) -> None:
    local.tools_firewall_check_busy = False
    profile = load_profile(name)
    service = _service()
    executable = str(resolve_executable(profile))
    firewall_children = [
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
        firewall_children.append(
            put_scope(
                "tools_actions",
                [
                    put_scope(
                        "tools_check_button",
                        [put_button(t("tools.check"), onclick=lambda: _check(name))],
                    )
                ],
            )
        )
    with use_scope("content", clear=True):
        put_section_layout(
            "tools_panel",
            [
                SectionSpec("firewall", t("tools.heading"), "tools_firewall"),
                SectionSpec("instance", t("tools.instance_heading"), "tools_instance"),
                SectionSpec(
                    "migration",
                    t("tools.migration_heading"),
                    "tools_migration",
                ),
            ],
            groups_scope="tools_sections",
            header=[
                put_asset_widget("shared.panel_title", {"title": t("tools.title")}),
            ],
        )
        with use_scope("tools_firewall"):
            put_scope("tools_firewall_content", firewall_children)
    _render_instance_management(name)
    _render_migration(name)


__all__ = ["render"]
