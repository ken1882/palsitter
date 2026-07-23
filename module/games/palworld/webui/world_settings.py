from __future__ import annotations
from pywebio.output import clear, close_popup, popup, put_button, put_row, put_scope, put_text, put_warning, toast, use_scope
from pywebio.pin import pin, put_checkbox, put_input, put_select
from pywebio.session import local
from module.games.palworld.backup import BackupService
from module.games.palworld.config import load_profile, save_profile
from module.webui.i18n import t
from module.webui.session import page_context, register_page_cleanup
from module.webui.assets import client_call, put_asset_icon, put_asset_widget
from module.games.palworld.worldsettings import WORLD_OPTION_CATEGORIES, WORLD_OPTION_FIELDS, diagnose_ini, load_world_settings, recover_malformed_ini, resolve_ini_path, save_world_settings
from module.games.palworld.worldsettings.ini_codec import coerce_integer

def _clear_dirty_form(*args, **kwargs):
    from module.webui.forms import _clear_dirty_form as implementation
    return implementation(*args, **kwargs)

def _clear_field_errors(*args, **kwargs):
    from module.webui.forms import _clear_field_errors as implementation
    return implementation(*args, **kwargs)

def _field_error_scope(*args, **kwargs):
    from module.webui.forms import _field_error_scope as implementation
    return implementation(*args, **kwargs)

def _manager(*args, **kwargs):
    from module.webui.instance import _manager as implementation
    return implementation(*args, **kwargs)

def _mark_dirty_form(*args, **kwargs):
    from module.webui.forms import _mark_dirty_form as implementation
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

def _settings_field_row(*args, **kwargs):
    from module.webui.forms import _settings_field_row as implementation
    return implementation(*args, **kwargs)

def _show_field_error(*args, **kwargs):
    from module.webui.forms import _show_field_error as implementation
    return implementation(*args, **kwargs)

_WORLD_PASSWORD_FIELDS = {"ServerPassword", "AdminPassword"}

_WORLD_INPUT_TYPE_BY_FTYPE = {"int": "number", "float": "float", "string": "text"}

def render(name: str) -> None:
    profile = load_profile(name)
    previous_world_settings = dict(profile.world_settings)
    ini_error = diagnose_ini(resolve_ini_path(profile))
    if ini_error is not None:
        _render_world_recovery(name, ini_error)
        return
    try:
        loaded = load_world_settings(profile)
    except Exception as exc:
        _render_world_recovery(name, str(exc))
        return
    if profile.world_settings != previous_world_settings:
        save_profile(profile)
    local.world_toggles = {
        field_.key: loaded.values.get(field_.key, field_.default)
        for field_ in WORLD_OPTION_FIELDS
        if field_.ftype == "bool"
    }
    local.world_initial_values = dict(loaded.values)
    clear("content")
    with use_scope("content"):
        put_scope(
            "world_settings_panel",
            [
                put_asset_widget("shared.panel_title", {"title": t("world.title")}),
                put_scope("world_settings_toolbar"),
                put_scope("world_settings_form"),
                put_scope("world_settings_actions"),
            ],
        )
        client_call("dom.addClasses", scope="world_settings_panel", classes=["panel"])
        client_call("dom.addClasses", scope="world_settings_form", classes=["settings-view"])
        client_call("dom.addClasses", scope="world_settings_actions", classes=["settings-actions"])
        with use_scope("world_settings_toolbar"):
            _render_world_settings_toolbar()
        with use_scope("world_settings_form"):
            _render_world_settings_form(loaded.values)
        client_call(
            "palworld.worldSettings.configureNumeric",
            floatNames=[_world_pin(field_.key) for field_ in WORLD_OPTION_FIELDS if field_.ftype == "float"],
            intNames=[_world_pin(field_.key) for field_ in WORLD_OPTION_FIELDS if field_.ftype == "int"],
        )
        context = page_context()
        client_call(
            "palworld.worldSettings.mount",
            changedPrefix=t("world.changed_count", count=""),
            generation=context.generation if context else None,
        )
        register_page_cleanup(lambda: client_call("palworld.worldSettings.destroy"))
        with use_scope("world_settings_actions"):
            put_row(
                [
                    put_asset_widget("shared.strong_text", {"text": t("form.unsaved_bar")}),
                    put_asset_widget("shared.changed_count", {"text": t("world.changed_count", count=0)}),
                    put_button(t("common.reset"), onclick=lambda: _world_settings(name), color="secondary"),
                    put_button(t("common.save"), onclick=lambda: _save_world_settings(name), color="success"),
                ],
                size="auto 1fr auto auto",
            )
    _register_dirty_form(
        "pywebio-scope-world_settings_panel",
        lambda: _save_world_settings(name, rerender=False),
    )


def _world_settings(name: str) -> None:
    from module.webui.instance import open_instance
    open_instance(name, "world_settings")

def _render_world_recovery(name: str, error: str) -> None:
    clear("content")
    active = _manager(name).active
    action_key = "world.regenerate_ini"
    recovery_children = [
        put_asset_widget("shared.panel_title", {"title": t("world.recovery_ini_title")}),
        put_warning(t("world.parse_error", error=error)),
        put_text(t("world.recovery_ini_detail")),
    ]
    if active:
        recovery_children.append(put_warning(t("world.recovery_stop_required")))
    recovery_children.append(
        put_button(
            t(action_key),
            onclick=lambda: _confirm_world_recovery(name),
            color="warning",
            disabled=active,
        )
    )
    with use_scope("content"):
        put_scope("world_recovery_panel", recovery_children)
        client_call("dom.addClasses", scope="world_recovery_panel", classes=["panel"])

def _confirm_world_recovery(name: str) -> None:
    action_key = "world.regenerate_ini"
    with popup(t("world.recovery_confirm_title"), closable=True):
        put_text(t("world.recovery_confirm", action=t(action_key)))
        put_row(
            [
                put_button(t("common.cancel"), onclick=close_popup, color="secondary"),
                put_button(
                    t(action_key),
                    onclick=lambda: _run_world_recovery(name),
                    color="warning",
                ),
            ],
            size="1fr auto",
        )

def _run_world_recovery(name: str) -> None:
    close_popup()
    profile = load_profile(name)
    try:
        result = recover_malformed_ini(
            profile,
            is_server_active=lambda: _manager(name).active,
        )
        save_profile(profile)
        toast(t("world.ini_regenerated", file=result.malformed_copy.name))
        _world_settings(name)
    except Exception as exc:
        toast(t("world.recovery_failed", error=exc), color="error")

def _world_pin(key: str) -> str:
    return f"world_{key}"

def _render_world_settings_toolbar() -> None:
    categories = [{"id": "all", "label": t("world.category_all"), "active": True}]
    categories.extend(
        {"id": category_id, "label": t(category_key), "active": False}
        for category_id, category_key in WORLD_OPTION_CATEGORIES
    )
    put_asset_widget(
        "palworld.world_settings_toolbar",
        {
            "placeholder": t("world.search_placeholder"),
            "categories": categories,
            "changed_only": t("world.changed_only"),
        },
    )

def _world_toggle_scope(key: str) -> str:
    return f"world_toggle_{key}"

def _render_world_toggle(key: str) -> None:
    with use_scope(_world_toggle_scope(key), clear=True):
        on = local.world_toggles.get(key, False)
        put_button(
            t("common.on") if on else t("common.off"),
            onclick=lambda: _toggle_world_field(key),
            color="success" if on else "secondary",
        )

def _toggle_world_field(key: str) -> None:
    _mark_dirty_form()
    local.world_toggles[key] = not local.world_toggles.get(key, False)
    _render_world_toggle(key)
    client_call("palworld.worldSettings.syncByKey", key=_safe_dom_id(key))

def _world_password_input(key: str, value):
    pin_name = _world_pin(key)
    button = put_asset_widget(
        "shared.icon_button",
        {
            "color": "secondary",
            "classes": "password-eye",
            "label": "Show password",
            "icon": put_asset_icon("eye"),
        },
    )
    output = put_row(
        [put_input(pin_name, value=value, type="password"), button],
        size="1fr auto",
    )
    client_call(
        "palworld.worldSettings.mountPassword",
        name=pin_name,
        showLabel="Show password",
        hideLabel="Hide password",
    )
    return output

def _render_world_field(field_, value) -> None:
    label = put_asset_widget(
        "palworld.world_field_label",
        {"label": t(field_.i18n_key), "help": t(field_.help_i18n_key)},
    )
    if field_.ftype == "bool":
        _settings_field_row(label, put_scope(_world_toggle_scope(field_.key)), escape_label=False)
        _render_world_toggle(field_.key)
    elif field_.ftype == "enum":
        _settings_field_row(
            label,
            put_select(
                _world_pin(field_.key),
                value=value,
                options=[{"label": choice, "value": choice} for choice in field_.choices],
            ),
            error_scope=_field_error_scope(_world_pin(field_.key)),
            escape_label=False,
        )
    elif field_.ftype == "multiselect":
        selected = value
        if isinstance(selected, str):
            selected = [choice for choice in selected.strip().strip("()").split(",") if choice]
        _settings_field_row(
            label,
            put_checkbox(
                _world_pin(field_.key),
                value=list(selected),
                options=[{"label": choice, "value": choice} for choice in field_.choices],
                inline=True,
            ),
            error_scope=_field_error_scope(_world_pin(field_.key)),
            escape_label=False,
        )
    else:
        # Numeric step/spinner attributes are applied by the world-settings client module.
        # put_input()'s fixed signature doesn't forward arbitrary HTML attributes like
        # step, unlike the lower-level pywebio.input.input() it wraps.
        control = (
            _world_password_input(field_.key, value)
            if field_.key in _WORLD_PASSWORD_FIELDS
            else put_input(
                _world_pin(field_.key),
                value=value,
                type=_WORLD_INPUT_TYPE_BY_FTYPE[field_.ftype],
            )
        )
        _settings_field_row(
            label,
            control,
            error_scope=_field_error_scope(_world_pin(field_.key)),
            escape_label=False,
        )

def _render_world_settings_form(values) -> None:
    for category_id, category_key in WORLD_OPTION_CATEGORIES:
        fields = [field_ for field_ in WORLD_OPTION_FIELDS if field_.category == category_id]
        if not fields:
            continue
        put_asset_widget(
            "palworld.world_category",
            {"category": category_id, "label": t(category_key)},
        )
        for field_ in fields:
            scope = f"world_field_{_safe_dom_id(field_.key)}"
            put_scope(scope)
            with use_scope(scope):
                _render_world_field(field_, values.get(field_.key, field_.default))
            client_call(
                "palworld.worldSettings.decorateField",
                scope=scope,
                category=category_id,
                search=f"{t(field_.i18n_key)} {field_.key}",
            )

def _collect_world_values() -> dict:
    values = {}
    for field_ in WORLD_OPTION_FIELDS:
        if field_.ftype == "bool":
            values[field_.key] = bool(local.world_toggles.get(field_.key, field_.default))
        else:
            values[field_.key] = getattr(pin, _world_pin(field_.key))
    return values

def _validate_world_settings_form(values: dict) -> bool:
    pin_names = [_world_pin(field_.key) for field_ in WORLD_OPTION_FIELDS if field_.ftype != "bool"]
    _clear_field_errors(pin_names)
    valid = True
    for field_ in WORLD_OPTION_FIELDS:
        if field_.ftype == "int":
            try:
                values[field_.key] = coerce_integer(values.get(field_.key))
            except (TypeError, ValueError):
                _show_field_error(_world_pin(field_.key), t("validation.integer_required"))
                valid = False
        elif field_.ftype == "float":
            try:
                float(values.get(field_.key))
            except (TypeError, ValueError):
                _show_field_error(_world_pin(field_.key), t("validation.number_required"))
                valid = False
    return valid

def _save_world_settings(name: str, *, rerender: bool = True) -> bool:
    profile = load_profile(name)
    values = _collect_world_values()
    if not _validate_world_settings_form(values):
        toast(t("validation.fix_errors"), color="error")
        return False
    try:
        save_world_settings(
            profile,
            values,
            "ini",
            backup_service=BackupService(profile, logger=_manager(name).append_log),
        )
        save_profile(profile)
        _clear_dirty_form()
        toast(t("world.saved"))
    except Exception as exc:
        toast(t("world.save_failed", error=exc), color="error")
        return False
    if rerender:
        _world_settings(name)
    return True
