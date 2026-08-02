from __future__ import annotations

import threading
from pathlib import Path, PurePosixPath

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
    put_success,
    put_warning,
    toast,
    use_scope,
)
from pywebio.pin import pin, put_file_upload, put_select
from pywebio.session import local, register_thread

from module.games.palworld.config import load_profile
from module.games.palworld.mods import (
    PALWORLD_UE4SS_RELEASE_PAGE,
    BatchResult,
    ItemResult,
    ModUploadService,
    PAK_LOGIC_MODS,
    PAK_ROOT,
    PAK_TILDE_MODS,
    PALSCHEMA_REQUIRED,
    UNSUPPORTED_FORMAT,
    UE4SSRelease,
    UE4SSService,
    UploadCancelled,
    UploadFile,
    UploadItem,
    default_release_tag,
)
from module.webui.i18n import t
from module.webui.operation_progress import render_operation_progress
from module.webui.section_layout import SectionSpec, put_section_layout
from module.webui.session import (
    is_current,
    is_local_browser_session,
    page_context,
    register_page_cleanup,
    register_page_stop_event,
    run_if_current,
)
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
    service = _service(name)
    sections = [
        SectionSpec("upload", t("mods.upload_title"), "mods_upload"),
        SectionSpec("ue4ss", t("mods.ue4ss"), "ue4ss_section"),
    ]
    if service.platform_supported:
        sections.append(SectionSpec("lua", t("mods.lua_title"), "lua_mods"))
    sections.append(SectionSpec("pak", t("mods.pak_title"), "pak_mods"))
    sections.append(SectionSpec("palschema", t("mods.palschema_title"), "palschema_mods"))
    clear("content")
    local.ue4ss_releases = ()
    local.ue4ss_busy = False
    with use_scope("content"):
        put_section_layout(
            "mods_panel",
            sections,
            groups_scope="mods_sections",
            header=[
                put_asset_widget("shared.panel_title", {"title": t("mods.title")}),
            ],
        )
        with use_scope("mods_upload"):
            put_scope("mods_upload_controls")
        with use_scope("ue4ss_section"):
            put_scope(
                "ue4ss_loader",
                [
                put_scope("ue4ss_summary"),
                put_scope("ue4ss_release_controls"),
                put_scope("ue4ss_operation"),
                ],
            )
        with use_scope("palschema_mods"):
            put_scope("palschema_mods_content")
    local.mods_upload_busy = False
    local.mods_upload_cancel = None
    _render_upload_controls(name)
    _render_status(name, service.platform_supported)
    if service.platform_supported:
        releases = service.list_releases()
        local.ue4ss_releases = releases
        _render_release_controls(name, releases)


_UPLOAD_FILE_PIN = "mods_upload_files"
_UPLOAD_FOLDER_PIN = "mods_upload_folder"
_UPLOAD_PINS = [_UPLOAD_FILE_PIN, _UPLOAD_FOLDER_PIN]


@use_scope("mods_upload_controls", clear=True)
def _render_upload_controls(name: str) -> None:
    put_asset_widget("palworld.backup_title", {"title": t("mods.upload_title")})
    put_text(t("mods.upload_description"))
    put_file_upload(
        _UPLOAD_FILE_PIN,
        label=t("mods.upload_files"),
        accept=[".zip", ".7z", ".tar", ".gz", ".tgz", ".pak", ".utoc", ".ucas"],
        multiple=True,
        max_size="200M",
        max_total_size="200M",
        help_text=t("mods.upload_files_help"),
    )
    put_file_upload(
        _UPLOAD_FOLDER_PIN,
        label=t("mods.upload_folder"),
        multiple=True,
        max_size="200M",
        max_total_size="200M",
        help_text=t("mods.upload_folder_help"),
    )
    put_button(
        t("mods.upload_button"),
        onclick=lambda: _start_upload(name),
        color="primary",
        disabled=bool(getattr(local, "mods_upload_busy", False)),
    )
    client_call("palworld.modsUpload.mountFolder", name=_UPLOAD_FOLDER_PIN)
    register_page_cleanup(
        lambda: client_call("palworld.modsUpload.destroy", name=_UPLOAD_FOLDER_PIN)
    )


def _uploaded_items() -> tuple[UploadItem, ...]:
    has_files = bool(client_query("palworld.modsUpload.hasSelection", name=_UPLOAD_FILE_PIN))
    has_folder = bool(client_query("palworld.modsUpload.hasSelection", name=_UPLOAD_FOLDER_PIN))
    folder_paths = tuple(
        str(value)
        for value in (client_query("palworld.modsUpload.relativePaths", name=_UPLOAD_FOLDER_PIN) or ())
    ) if has_folder else ()
    file_records = list(getattr(pin, _UPLOAD_FILE_PIN) or ()) if has_files else []
    folder_records = list(getattr(pin, _UPLOAD_FOLDER_PIN) or ()) if has_folder else []
    items = [
        UploadItem.archive(str(record.get("filename") or "upload"), bytes(record.get("content") or b""))
        for record in file_records
    ]
    if folder_records:
        if len(folder_paths) != len(folder_records):
            raise ValueError(t("mods.upload_folder_paths_failed"))
        files = tuple(
            UploadFile(path, bytes(record.get("content") or b""))
            for path, record in zip(folder_paths, folder_records)
        )
        first_parts = PurePosixPath(folder_paths[0]).parts
        folder_name = first_parts[0] if first_parts else t("mods.upload_folder")
        items.append(UploadItem.folder(folder_name, files))
    return tuple(items)


def _reset_upload_selection() -> None:
    client_call("palworld.modsUpload.reset", names=_UPLOAD_PINS)


def _start_upload(name: str) -> None:
    context = page_context()
    with popup(t("mods.upload_progress_title"), closable=False, implicit_close=False):
        put_scope("mods_upload_dialog", [put_text(t("mods.upload_receiving"))])
        put_scope("mods_upload_actions")
    register_page_cleanup(close_popup)
    try:
        items = _uploaded_items()
    except Exception as exc:
        _reset_upload_selection()
        _render_upload_terminal(BatchResult((ItemResult(t("mods.upload_folder"), "failed", str(exc)),)))
        return
    _reset_upload_selection()
    if not items:
        _render_upload_terminal(BatchResult((ItemResult(t("mods.upload_title"), "failed", t("mods.upload_select")),)))
        return

    cancel_event = threading.Event()
    local.mods_upload_busy = True
    local.mods_upload_cancel = cancel_event
    register_page_stop_event(cancel_event)
    client_call("palworld.modsUpload.setBusy", names=_UPLOAD_PINS, busy=True)
    _render_upload_cancel()
    platform_supported = _service(name).platform_supported
    task = threading.Thread(
        target=lambda: _run_upload(name, items, context, cancel_event, platform_supported),
        daemon=True,
    )
    register_thread(task)
    task.start()


@use_scope("mods_upload_actions", clear=True)
def _render_upload_cancel(*, cancelling: bool = False) -> None:
    put_button(
        t("mods.upload_cancelling") if cancelling else t("common.cancel"),
        onclick=_cancel_upload,
        color="secondary",
        disabled=cancelling,
    )


def _cancel_upload() -> None:
    cancel_event = getattr(local, "mods_upload_cancel", None)
    if cancel_event is not None:
        cancel_event.set()
    _render_upload_cancel(cancelling=True)


def _upload_progress_label(phase: str, item: str | None) -> str:
    return t(f"mods.upload_phase_{phase}", item=item or "")


def _render_upload_progress(
    events: list[tuple[str, str | None]], *, palschema_warning: bool = False
) -> None:
    with use_scope("mods_upload_dialog", clear=True):
        put_scope("mods_upload_progress")
    render_operation_progress("mods_upload_progress", events, _upload_progress_label)
    if palschema_warning:
        with use_scope("mods_upload_dialog"):
            put_warning(t("mods.upload_palschema_missing"))


def _wait_for_upload_choice(context, cancel_event, render_choice):
    ready = threading.Event()
    answer: dict[str, str] = {}

    def resolve(value: str) -> None:
        answer["value"] = value
        ready.set()

    run_if_current(context, lambda: render_choice(resolve))
    while not ready.wait(0.1):
        if cancel_event.is_set() or not is_current(context):
            raise UploadCancelled(t("mods.upload_cancelled"))
    return answer["value"]


def _choose_pak_destination(
    context, cancel_event, unit, *, palschema_warning: bool = False
) -> str:
    def render_choice(resolve) -> None:
        with use_scope("mods_upload_dialog", clear=True):
            if palschema_warning:
                put_warning(t("mods.upload_palschema_missing"))
            put_warning(t("mods.upload_choose_pak", name=unit.name))
            put_row(
                [
                    put_button(t("mods.upload_pak_root"), onclick=lambda: resolve(PAK_ROOT)),
                    put_button(
                        "LogicMods", onclick=lambda: resolve(PAK_LOGIC_MODS), color="primary"
                    ),
                    put_button("~mods", onclick=lambda: resolve(PAK_TILDE_MODS)),
                ],
                size="auto auto auto",
            )

    return _wait_for_upload_choice(context, cancel_event, render_choice)


def _confirm_upload_conflicts(
    context,
    cancel_event,
    conflicts: tuple[str, ...],
    *,
    palschema_warning: bool = False,
) -> None:
    def render_choice(resolve) -> None:
        with use_scope("mods_upload_dialog", clear=True):
            if palschema_warning:
                put_warning(t("mods.upload_palschema_missing"))
            put_warning(t("mods.upload_conflict_warning"))
            put_table([[name] for name in conflicts], header=[t("mods.mod_name")])
            put_button(
                t("mods.upload_continue"), onclick=lambda: resolve("continue"), color="danger"
            )

    _wait_for_upload_choice(context, cancel_event, render_choice)


def _run_upload(name: str, items, context, cancel_event, platform_supported: bool) -> None:
    events: list[tuple[str, str | None]] = []
    palschema_warning = False

    def progress(index: int, total: int, phase: str, item_name: str) -> None:
        detail = f"{index + 1}/{total} {item_name}"
        events.append((phase, detail))
        run_if_current(
            context,
            lambda: _render_upload_progress(
                events, palschema_warning=palschema_warning
            ),
        )

    service = ModUploadService(
        load_profile(name),
        platform_supported=platform_supported,
        logger=_manager(name).append_log,
    )
    try:
        inspection = service.inspect(items, cancel_event=cancel_event, progress=progress)
        palschema_warning = bool(
            any(unit.kind == "palschema" for unit in inspection.units)
            and not service.palschema_installed()
        )
        if palschema_warning:
            run_if_current(
                context,
                lambda: _render_upload_progress(events, palschema_warning=True),
            )
        decisions = {}
        for unit in inspection.units:
            if unit.needs_pak_destination:
                decisions[unit.id] = _choose_pak_destination(
                    context,
                    cancel_event,
                    unit,
                    palschema_warning=palschema_warning,
                )
        conflicts = service.conflicts(inspection, decisions)
        if conflicts:
            _confirm_upload_conflicts(
                context,
                cancel_event,
                conflicts,
                palschema_warning=palschema_warning,
            )
        result = service.install(
            inspection,
            decisions,
            cancel_event=cancel_event,
            progress=progress,
        )
    except UploadCancelled:
        result = BatchResult(tuple(ItemResult(item.name, "skipped", t("mods.upload_cancelled")) for item in items))
    except Exception as exc:
        _manager(name).append_log(f"Mod upload failed: {exc}")
        result = BatchResult((ItemResult(t("mods.upload_title"), "failed", str(exc)),))

    def finish() -> None:
        local.mods_upload_busy = False
        local.mods_upload_cancel = None
        client_call("palworld.modsUpload.setBusy", names=_UPLOAD_PINS, busy=False)
        _reset_upload_selection()
        _render_upload_terminal(result, palschema_warning=palschema_warning)
        _render_status(name)

    run_if_current(context, finish)


@use_scope("mods_upload_dialog", clear=True)
def _render_upload_terminal(result: BatchResult, *, palschema_warning: bool = False) -> None:
    if palschema_warning:
        put_warning(t("mods.upload_palschema_missing"))
    rows = []
    for item in result.items:
        status = t(f"mods.upload_status_{item.status}")
        message_keys = {
            UNSUPPORTED_FORMAT: "mods.upload_unsupported",
            PALSCHEMA_REQUIRED: "mods.upload_palschema_missing",
            "This mod type is not supported on native Linux": "mods.upload_linux_unsupported",
            "UE4SS loader detected; use the UE4SS mod loader section": "mods.upload_loader_detected",
            "Install UE4SS before uploading this mod": "mods.upload_ue4ss_required",
            "This package does not support Palworld dedicated servers": "mods.upload_server_unsupported",
        }
        message = t(message_keys[item.message]) if item.message in message_keys else item.message
        rows.append([item.name, status, message])
    put_table(
        rows,
        header=[t("mods.upload_item"), t("mods.upload_status"), t("mods.upload_message")],
    )
    if result.installed_count:
        put_success(t("mods.upload_restart_required"))
    with use_scope("mods_upload_actions", clear=True):
        put_button(t("common.close"), onclick=close_popup, color="primary")


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
    _render_mod_table(name, "palschema")


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
            "href": PALWORLD_UE4SS_RELEASE_PAGE,
            "link": t("mods.github_releases"),
        },
    )


def _start_install(name: str) -> None:
    context = page_context()
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
            run_if_current(
                context,
                lambda: toast(t("mods.install_complete", version=release.tag), color="success"),
            )
        except Exception as exc:
            run_if_current(context, lambda exc=exc: toast(t("mods.install_failed", error=exc), color="error"))
        finally:
            try:
                def finish() -> None:
                    local.ue4ss_busy = False
                    if client_query("dom.scopeExists", scope="mods_panel"):
                        clear("ue4ss_operation")
                        _render_status(name)
                        _render_release_controls(name, releases)
                run_if_current(context, finish)
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
    context = page_context()
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
            run_if_current(context, lambda: toast(t("mods.remove_complete"), color="success"))
        except Exception as exc:
            run_if_current(context, lambda exc=exc: toast(t("mods.remove_failed", error=exc), color="error"))
        finally:
            try:
                def finish() -> None:
                    local.ue4ss_busy = False
                    if client_query("dom.scopeExists", scope="mods_panel"):
                        clear("ue4ss_operation")
                        _render_status(name)
                        _render_release_controls(name, releases)
                run_if_current(context, finish)
            except SessionException:
                return

    thread = threading.Thread(target=remove, daemon=True)
    register_thread(thread)
    thread.start()


def _icon_button(
    label: str,
    glyph: str,
    onclick,
    *,
    disabled: bool = False,
    danger: bool = False,
    title: str | None = None,
):
    button = put_asset_widget(
        "palworld.backup_icon_button",
        {
            "label": label,
            "title": title or label,
            "glyph": glyph,
            "disabled": disabled,
            "danger": danger,
        },
    )
    if not disabled:
        button.onclick(onclick)
    return button


def _render_mod_table(name: str, kind: str) -> None:
    scope = {
        "lua": "lua_mods",
        "pak": "pak_mods",
        "palschema": "palschema_mods_content",
    }[kind]
    with use_scope(scope, clear=True):
        status = _service(name).status()
        is_lua = kind == "lua"
        is_palschema = kind == "palschema"
        title = (
            t("mods.lua_title")
            if is_lua
            else t("mods.palschema_title")
            if is_palschema
            else t("mods.pak_title")
        )
        label = (
            t("mods.open_lua_folder")
            if is_lua
            else t("mods.open_palschema_folder")
            if is_palschema
            else t("mods.open_pak_folder")
        )
        directory = (
            status.lua_dir
            if is_lua
            else status.palschema_dir
            if is_palschema
            else status.pak_dir
        )
        mods = (
            status.lua_mods
            if is_lua
            else status.palschema_mods
            if is_palschema
            else status.pak_mods
        )
        folder = None
        if is_local_browser_session():
            folder = _icon_button(
                label,
                "📁",
                lambda path=directory, pak=kind == "pak": _open_mod_folder(name, path, pak=pak),
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
            put_text(
                t("mods.no_lua")
                if is_lua
                else t("mods.no_palschema")
                if is_palschema
                else t("mods.no_pak")
            )
            return
        if is_lua:
            rows = []
            for mod in mods:
                checkbox = put_asset_widget(
                    "palworld.pak_checkbox",
                    {"label": t("mods.lua_enabled_checkbox", name=mod.name), "checked": mod.enabled},
                ).onclick(
                    lambda mod_name=mod.name, enabled=not mod.enabled: _toggle_lua(
                        name, mod_name, enabled
                    )
                )
                rows.append(
                    [
                        put_asset_widget(
                            "palworld.backup_file_details",
                            {"name": mod.name, "metadata": ""},
                        ),
                        checkbox,
                        _icon_button(
                            t("mods.delete_lua", name=mod.name),
                            "×",
                            lambda mod_name=mod.name: _confirm_delete_lua(name, mod_name),
                            danger=True,
                        ),
                    ]
                )
            put_table(rows, header=[t("mods.mod_name"), t("mods.enabled"), t("mods.delete")])
            return
        if is_palschema:
            rows = []
            for mod in mods:
                checkbox = put_asset_widget(
                    "palworld.pak_checkbox",
                    {
                        "label": t("mods.palschema_enabled_checkbox", name=mod.name),
                        "checked": mod.enabled,
                    },
                ).onclick(
                    lambda mod_name=mod.name, enabled=not mod.enabled: _toggle_palschema(
                        name, mod_name, enabled
                    )
                )
                rows.append(
                    [
                        put_asset_widget(
                            "palworld.backup_file_details",
                            {"name": mod.name, "metadata": ""},
                        ),
                        checkbox,
                        _icon_button(
                            t("mods.delete_palschema", name=mod.name),
                            "×",
                            lambda mod_name=mod.name: _confirm_delete_palschema(name, mod_name),
                            danger=True,
                        ),
                    ]
                )
            put_table(rows, header=[t("mods.mod_name"), t("mods.enabled"), t("mods.delete")])
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
                        "×",
                        lambda mod_name=mod.name: _confirm_delete_pak(name, mod_name),
                        danger=True,
                    ),
                ]
            )
        put_table(rows, header=[t("mods.mod_name"), t("mods.enabled"), t("mods.delete")])


def _open_mod_folder(name: str, path: Path | None, *, pak: bool = False) -> None:
    if path is None:
        return
    try:
        path.mkdir(parents=True, exist_ok=True)
        if pak:
            (path / "~mods").mkdir(exist_ok=True)
            (path / "LogicMods").mkdir(exist_ok=True)
        _manager(name).append_log(f"Opening folder: {path}")
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


def _toggle_palschema(name: str, mod_name: str, enabled: bool) -> None:
    try:
        _service(name).set_palschema_enabled(mod_name, enabled)
        toast(t("mods.palschema_enabled" if enabled else "mods.palschema_disabled", name=mod_name))
        _render_mod_table(name, "palschema")
    except Exception as exc:
        toast(t("mods.palschema_toggle_failed", error=exc), color="error")
        _render_mod_table(name, "palschema")


def _toggle_lua(name: str, mod_name: str, enabled: bool) -> None:
    try:
        _service(name).set_lua_enabled(mod_name, enabled)
        toast(t("mods.lua_enabled" if enabled else "mods.lua_disabled", name=mod_name))
        _render_mod_table(name, "lua")
    except Exception as exc:
        toast(t("mods.lua_toggle_failed", error=exc), color="error")
        _render_mod_table(name, "lua")


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
        _render_status(name)
    except Exception as exc:
        toast(t("mods.delete_failed", error=exc), color="error")


def _confirm_delete_palschema(name: str, mod_name: str) -> None:
    with popup(t("mods.delete_palschema_title"), closable=True):
        put_warning(t("mods.delete_palschema_confirm", name=mod_name))
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(
                    t("mods.delete"),
                    onclick=lambda: _delete_palschema(name, mod_name),
                    color="danger",
                ),
            ],
            size="auto auto",
        )


def _delete_palschema(name: str, mod_name: str) -> None:
    close_popup()
    try:
        _service(name).delete_palschema(mod_name)
        toast(t("mods.delete_complete", name=mod_name), color="success")
        _render_status(name)
    except Exception as exc:
        toast(t("mods.delete_palschema_failed", error=exc), color="error")


def _confirm_delete_lua(name: str, mod_name: str) -> None:
    with popup(t("mods.delete_lua_title"), closable=True):
        put_warning(t("mods.delete_lua_confirm", name=mod_name))
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(
                    t("mods.delete"),
                    onclick=lambda: _delete_lua(name, mod_name),
                    color="danger",
                ),
            ],
            size="auto auto",
        )


def _delete_lua(name: str, mod_name: str) -> None:
    close_popup()
    try:
        _service(name).delete_lua(mod_name)
        toast(t("mods.delete_complete", name=mod_name), color="success")
        _render_status(name)
    except Exception as exc:
        toast(t("mods.lua_delete_failed", error=exc), color="error")


__all__ = ["render"]
