from __future__ import annotations
from pywebio.output import clear, close_popup, popup, put_button, put_row, put_scope, put_text, use_scope
from pywebio.pin import pin, put_input
from pywebio.session import local
from module.webui.assets import client_call, client_query, put_asset_icon, put_asset_widget
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


def _argument_list_scope(key: str) -> str:
    return f"{key}_argument_list"


def _argument_list_state() -> dict[str, dict[str, object]]:
    state = getattr(local, "argument_lists", None)
    if state is None:
        state = {}
        local.argument_lists = state
    return state


def _capture_argument_list(key: str) -> list[str]:
    state = _argument_list_state().get(key, {})
    values = list(state.get("values", []))
    for index, value in enumerate(values):
        values[index] = str(getattr(pin, f"{key}_{index}", value) or "")
    state["values"] = values
    return values


def _argument_icon_button(
    label: str,
    icon: str,
    onclick=None,
    *,
    disabled: bool = False,
    tooltip: str | None = None,
):
    button = put_asset_widget(
        "shared.icon_button",
        {
            "color": "secondary",
            "classes": "argument-list-icon-button",
            "label": label,
            "tooltip": tooltip,
            "disabled": disabled,
            "icon": [put_asset_icon(icon)],
        },
    )
    if onclick is not None and not disabled:
        button.onclick(onclick)
    return button


def _render_argument_list(key: str, *, capture: bool = True) -> None:
    state = _argument_list_state()[key]
    if capture:
        _capture_argument_list(key)
    values = list(state["values"])
    if not values:
        values = [""]
        state["values"] = values
    controlled = list(state.get("controlled", []))
    tooltip = str(state.get("tooltip", ""))
    remove_label = str(state.get("remove_label", "Remove argument"))
    add_label = str(state.get("add_label", "Add argument"))
    scope = _argument_list_scope(key)
    with use_scope(scope, clear=True):
        for index, value in enumerate(controlled):
            pin_name = f"{key}_controlled_{index}"
            control = put_asset_widget(
                "shared.argument_controlled_input",
                {
                    "tooltip": tooltip,
                    "control": [put_input(pin_name, value=value)],
                },
            )
            put_row(
                [
                    control,
                    _argument_icon_button(
                        remove_label,
                        "remove",
                        disabled=True,
                        tooltip=tooltip,
                    ),
                ],
                size="1fr auto",
            )
            client_call(
                "dom.setControlDisabled",
                selector=f'input[name="{pin_name}"]',
                disabled=True,
            )
        for index, value in enumerate(values):
            put_row(
                [
                    put_input(f"{key}_{index}", value=value),
                    None
                    if len(values) <= 1
                    else _argument_icon_button(
                        remove_label,
                        "remove",
                        lambda index=index: _remove_argument(key, index),
                    ),
                ],
                size="1fr auto",
            )
        put_row(
            [
                None,
                _argument_icon_button(
                    add_label,
                    "add",
                    lambda: _add_argument(key),
                ),
            ],
            size="1fr auto",
        )


def put_argument_list(
    key: str,
    values: list[str],
    *,
    controlled: list[str] | None = None,
    tooltip: str = "",
    add_label: str = "Add argument",
    remove_label: str = "Remove argument",
):
    _argument_list_state()[key] = {
        "values": [str(value) for value in values],
        "controlled": list(controlled or []),
        "tooltip": tooltip,
        "add_label": add_label,
        "remove_label": remove_label,
    }
    output = put_scope(_argument_list_scope(key))
    return output


def render_argument_list(
    key: str,
    *,
    controlled: list[str] | None = None,
    tooltip: str | None = None,
    add_label: str | None = None,
    remove_label: str | None = None,
) -> None:
    state = _argument_list_state()[key]
    _capture_argument_list(key)
    if controlled is not None:
        state["controlled"] = list(controlled)
    if tooltip is not None:
        state["tooltip"] = tooltip
    if add_label is not None:
        state["add_label"] = add_label
    if remove_label is not None:
        state["remove_label"] = remove_label
    _render_argument_list(key, capture=False)


def argument_list_values(key: str) -> list[str]:
    return [value.strip() for value in _capture_argument_list(key) if value.strip()]


def _add_argument(key: str) -> None:
    _capture_argument_list(key).append("")
    _mark_dirty_form()
    _render_argument_list(key, capture=False)


def _remove_argument(key: str, index: int) -> None:
    values = _capture_argument_list(key)
    if len(values) <= 1:
        values[0] = ""
    else:
        values.pop(index)
    _mark_dirty_form()
    _render_argument_list(key, capture=False)

def _safe_dom_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)


__all__ = ["argument_list_values", "put_argument_list", "register_dirty_form", "render_argument_list"]
