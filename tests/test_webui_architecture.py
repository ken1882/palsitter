import ast
import json
import re
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from module.webui import forms, game_ui, session
from module.webui.game_ui import GameWebUI, InstancePage


ROOT = Path(__file__).parents[1]
GUI_ASSETS = ROOT / "assets" / "gui"
MANIFEST = GUI_ASSETS / "manifest.json"


def test_builtin_game_ui_manifests_are_typed_and_ordered():
    palworld = game_ui.get_game_ui("palworld")
    satisfactory = game_ui.get_game_ui("satisfactory")

    assert [page.id for page in palworld.pages] == [
        "overview",
        "players",
        "server_settings",
        "auto_restart",
        "world_settings",
        "mods",
            "backups",
            "map",
            "audit",
            "tools",
        ]
    assert palworld.creation is not None
    assert [page.id for page in satisfactory.pages] == ["overview"]
    assert satisfactory.creation is None


@pytest.mark.parametrize(
    ("pages", "message"),
    [
        ((InstancePage("players", "nav.players", "nav.players", lambda _: None),), "overview"),
        (
            (
                InstancePage("overview", "nav.overview", "nav.overview", lambda _: None),
                InstancePage("overview", "nav.overview", "nav.overview", lambda _: None),
            ),
            "duplicate",
        ),
    ],
)
def test_game_ui_rejects_invalid_page_manifests(monkeypatch, pages, message):
    module_name = "tests.fake_game_webui"
    fake_module = ModuleType(module_name)
    fake_module.get_webui = lambda: GameWebUI(pages=pages)
    monkeypatch.setitem(sys.modules, module_name, fake_module)
    monkeypatch.setattr(
        game_ui,
        "get_game",
        lambda _: SimpleNamespace(webui_module=module_name),
    )

    with pytest.raises(ValueError, match=message):
        game_ui.get_game_ui("fake")


def test_page_cleanup_runs_in_reverse_and_session_cleanup_is_safe_only(monkeypatch):
    fake_local = SimpleNamespace()
    monkeypatch.setattr(session, "local", fake_local)
    calls = []
    cleanups = session.initialize_page_lifecycle()
    session.register_page_cleanup(lambda: calls.append("interactive"))
    session.register_page_cleanup(lambda: calls.append("safe"), session_safe=True)

    session.cleanup_session(cleanups)
    assert calls == ["safe"]

    session.register_page_cleanup(lambda: calls.append("first"))
    session.register_page_cleanup(lambda: calls.append("second"))
    session.cleanup_page()
    assert calls[-2:] == ["second", "first"]

    cleanups = session.initialize_page_lifecycle()
    session.register_page_cleanup(lambda: calls.append("page"))
    session.register_page_cleanup(lambda: calls.append("session"), session_safe=True)
    session.cleanup_page()
    assert calls[-1] == "page"
    session.cleanup_session(cleanups)
    assert calls[-1] == "session"


def test_page_context_invalidates_stale_updates_and_preserves_session_cleanup(monkeypatch):
    fake_local = SimpleNamespace()
    monkeypatch.setattr(session, "local", fake_local)
    cleanups = session.initialize_page_lifecycle()
    page_event = threading.Event()
    session_event = threading.Event()
    session.register_page_stop_event(page_event)
    session.register_stop_event(session_event)

    first_request = session.request_navigation()
    first = session.begin_page_navigation(first_request)
    assert first is not None
    calls = []
    session.register_page_cleanup(lambda: calls.append("page"))
    assert session.is_current(first)
    assert session.run_if_current(first, lambda: calls.append("update")) is None
    assert calls == ["update"]

    second_request = session.request_navigation()
    second = session.begin_page_navigation(second_request)
    assert second is not None
    assert first.stop_event.is_set()
    assert page_event.is_set()
    assert calls == ["update", "page"]
    assert not session.is_current(first)
    assert session.run_if_current(first, lambda: calls.append("stale")) is None
    assert calls == ["update", "page"]
    assert session.is_current(second)
    assert not session_event.is_set()

    session.cleanup_session(cleanups)
    assert session_event.is_set()


def test_latest_navigation_request_skips_older_render(monkeypatch):
    fake_local = SimpleNamespace()
    monkeypatch.setattr(session, "local", fake_local)
    session.initialize_page_lifecycle()
    first = session.request_navigation()
    second = session.request_navigation()

    assert session.begin_page_navigation(first) is None
    context = session.begin_page_navigation(second)
    assert context is not None
    assert context.generation == 1


def test_dirty_form_uses_registered_save_callback(monkeypatch):
    calls = []
    fake_local = SimpleNamespace(
        dirty_form_context={"save": lambda: calls.append("save") or True},
        pending_navigation=lambda: calls.append("navigate"),
    )
    monkeypatch.setattr(forms, "local", fake_local)
    monkeypatch.setattr(forms, "close_popup", lambda: calls.append("close"))

    forms._save_dirty_then_continue()

    assert calls == ["save", "close", "navigate"]
    assert fake_local.dirty_form_context is None
    assert fake_local.pending_navigation is None


def test_gui_manifest_registers_every_safe_frontend_asset_once():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    registered = []
    for kind, suffix in (("css", ".css"), ("js", ".js")):
        values = manifest[kind]
        assert len(values) == len(set(values))
        assert all(Path(value).suffix == suffix for value in values)
        registered.extend(values)
    templates = manifest["templates"]
    assert len(templates) == len(set(templates.values()))
    registered.extend(templates.values())
    for value in registered:
        path = (ROOT / "assets" / value).resolve()
        assert ROOT / "assets" in path.parents
        assert path.is_file(), value

    discovered = {
        path.relative_to(ROOT / "assets").as_posix()
        for suffix in ("*.css", "*.js", "*.html")
        for path in GUI_ASSETS.rglob(suffix)
    }
    assert discovered == set(registered)


def test_gui_templates_are_passive_and_autoescaped():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    forbidden = re.compile(
        r"<script\b|<style\b|\sstyle\s*=|\son[a-z]+\s*=|javascript:",
        re.IGNORECASE,
    )
    unsafe = re.compile(r"{{\s*[&{]([^}]+)")
    for value in manifest["templates"].values():
        text = (ROOT / "assets" / value).read_text(encoding="utf-8")
        assert not forbidden.search(text), value
        assert all("pywebio_output_parse" in match for match in unsafe.findall(text)), value


def test_production_python_has_no_embedded_frontend_or_direct_browser_calls():
    bridge = ROOT / "module" / "webui" / "assets.py"
    frontend_source = re.compile(
        r"<\/?(?:div|span|button|style|script|svg|pre|table|header|label|input)\b"
        r"|\b(?:window|document)\.|\$\(",
        re.IGNORECASE,
    )
    for path in (ROOT / "module").rglob("*.py"):
        if "webui" not in path.parts and path.name != "webui.py":
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        if path != bridge:
            assert not frontend_source.search(source), path
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "id", None)
                    assert name not in {"put_html", "run_js", "eval_js"}, path
                    assert not (
                        isinstance(node.func, ast.Attribute) and node.func.attr == "style"
                    ), path
