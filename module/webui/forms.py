from __future__ import annotations
from pywebio.output import clear, close_popup, popup, put_button, put_row, put_scope, put_text, use_scope
from pywebio.session import local
from module.webui.assets import client_call, client_query, put_asset_widget
from module.webui.i18n import t

def _guard_unsaved_navigation(action) -> None:
    context = getattr(local, "dirty_form_context", None)
    if not context:
        action()
        return
    is_dirty = bool(client_query("forms.isDirty"))
    if not is_dirty:
        local.dirty_form_context = None
        action()
        return
    local.pending_navigation = action
    with popup(t("form.unsaved_title"), closable=True):
        put_text(t("form.unsaved_message"))
        put_row(
            [
                put_button(t("form.unsaved_save"), onclick=_save_dirty_then_continue, color="success"),
                None,
                put_button(t("form.unsaved_discard"), onclick=_discard_dirty_then_continue, color="danger"),
                None,
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
            ],
            size="auto .5rem auto .5rem auto",
        )

def _save_dirty_then_continue() -> None:
    context = getattr(local, "dirty_form_context", None)
    action = getattr(local, "pending_navigation", None)
    if not context or action is None:
        close_popup()
        return
    saved = context["save"]()
    if not saved:
        close_popup()
        return
    local.dirty_form_context = None
    local.pending_navigation = None
    close_popup()
    action()

def _discard_dirty_then_continue() -> None:
    action = getattr(local, "pending_navigation", None)
    local.dirty_form_context = None
    local.pending_navigation = None
    close_popup()
    if action is not None:
        action()

def register_dirty_form(scope_id: str, save_callback) -> None:
    local.dirty_form_context = {"save": save_callback}
    client_call("forms.register", scopeId=scope_id)

def _mark_dirty_form() -> None:
    client_call("forms.mark")

def _clear_dirty_form() -> None:
    local.dirty_form_context = None
    client_call("forms.clear")


_register_dirty_form = register_dirty_form

def _field_error_scope(pin_name: str) -> str:
    return f"{pin_name}_error"

def _settings_label(key: str):
    return put_asset_widget(
        "shared.field_label_help",
        {"label": t(f"settings.{key}"), "help": t(f"settings.help.{key}")},
    )

def _clear_field_error(pin_name: str) -> None:
    clear(_field_error_scope(pin_name))
    client_call("forms.setFieldInvalid", name=pin_name, invalid=False)

def _show_field_error(pin_name: str, message: str) -> None:
    with use_scope(_field_error_scope(pin_name), clear=True):
        put_asset_widget("shared.field_error", {"message": message})
    client_call("forms.setFieldInvalid", name=pin_name, invalid=True)

def _clear_field_errors(pin_names: list[str]) -> None:
    for pin_name in pin_names:
        _clear_field_error(pin_name)

def _settings_field_label(label: str, *, escape_label: bool = True):
    if not isinstance(label, str):
        return label
    return put_asset_widget("shared.field_label", {"label": label})

def _settings_field_row(label: str, control, *, error_scope: str | None = None, escape_label: bool = True) -> None:
    put_row(
        [
            _settings_field_label(label, escape_label=escape_label),
            None,
            control,
        ],
        size="minmax(14rem, 1fr) .75rem minmax(22rem, 2fr)",
    )
    if error_scope is not None:
        put_row(
            [
                put_text(""),
                None,
                put_scope(error_scope),
            ],
            size="minmax(14rem, 1fr) .75rem minmax(22rem, 2fr)",
        )

def _safe_dom_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)


__all__ = ["register_dirty_form"]
