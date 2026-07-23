import os
import json
import re
import datetime as dt
import shutil
import socket
import subprocess
import sys
import threading
import time
import zipfile
import io
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import psutil

from module.config import Profile, game_user_settings_path, list_profiles, load_profile, save_profile
from module.instances import create_instance, load_instance, profile_dir, profile_log_path
from module.games.palworld.server.history import (
    LifecycleEvent,
    RestartHistoryStore,
    classify_process_exit,
)
from module.games.palworld.audit import AuditEvent, AuditStore
from module.games.palworld.players_cache import PlayerCache
from module.games.palworld.version_cache import update_version_cache
from module.worldsettings.ini_codec import read_ini_option_settings
from module.worldsettings.sav_codec import WorldOptionSavCodec, extract_option_values, merge_option_values
from module.worldsettings.service import resolve_ini_path


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _launch_browser(playwright):
    try:
        return playwright.chromium.launch(headless=True)
    except Exception as first_error:
        for channel in ("msedge", "chrome"):
            try:
                return playwright.chromium.launch(channel=channel, headless=True)
            except Exception:
                continue
        pytest.skip(f"No Playwright browser available: {first_error}")


def _goto(page, port):
    deadline = time.time() + 20
    while True:
        try:
            page.goto(f"http://127.0.0.1:{port}", wait_until="networkidle", timeout=2000)
            return
        except Exception:
            if time.time() > deadline:
                raise
            time.sleep(0.5)


def _profile_dir(tmp_path, name="default"):
    return tmp_path / "profile" / name


def _fixed_steamcmd(tmp_path, name="default"):
    executable = "steamcmd.exe" if os.name == "nt" else "steamcmd"
    return _profile_dir(tmp_path, name) / "steamcmd" / executable


def _fixed_palserver_executable(tmp_path, name="default"):
    root = _fixed_palserver_dir(tmp_path, name)
    if os.name == "nt":
        return root / "PalServer.exe"
    return root / "Pal" / "Binaries" / "Linux" / "PalServer-Linux-Shipping"


def _fixed_palserver_dir(tmp_path, name="default"):
    return _profile_dir(tmp_path, name) / "steamcmd" / "steamapps" / "common" / "PalServer"


def _fixed_backup_source(tmp_path, name="default"):
    return _fixed_palserver_dir(tmp_path, name) / "Pal" / "Saved" / "SaveGames" / "0"


def _prepare_fixed_palserver_python(tmp_path, name="default"):
    exe = _fixed_palserver_executable(tmp_path, name)
    exe.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(sys.executable, exe)
    try:
        exe.chmod(0o755)
    except OSError:
        pass
    return exe


@contextmanager
def _running_palserver_process(tmp_path, name="default"):
    exe = _prepare_fixed_palserver_python(tmp_path, name)
    process = subprocess.Popen(
        [str(exe), "-c", "import time; time.sleep(120)"],
        cwd=str(exe.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                if Path(psutil.Process(process.pid).exe()).resolve() == exe.resolve():
                    break
            except psutil.Error:
                pass
            time.sleep(0.05)
        else:
            raise AssertionError("Fake PalServer process did not start from the instance path")
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def _prepare_fixed_steamcmd(tmp_path, name="default"):
    steamcmd = _fixed_steamcmd(tmp_path, name)
    steamcmd.parent.mkdir(parents=True, exist_ok=True)
    steamcmd.write_text("stub", encoding="utf-8")
    try:
        steamcmd.chmod(0o755)
    except OSError:
        pass
    return steamcmd


@contextmanager
def _mock_metrics_server(
    delay=0,
    players_delay=0,
    players_fail_after=None,
    players_override=None,
    players_sequence=None,
    game_data_override=None,
    get_calls=None,
    shutdown_executable=None,
    banlist_path=None,
    post_delay=0,
    post_started=None,
    post_completed=None,
):
    calls = []
    player_get_count = 0

    class Handler(BaseHTTPRequestHandler):
        def _write_json(self, body):
            payload = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            nonlocal player_get_count
            if get_calls is not None:
                get_calls.append(self.path)
            if self.path == "/v1/api/metrics":
                time.sleep(delay)
                self._write_json(
                    {
                        "currentplayernum": 3,
                        "maxplayernum": 32,
                        "serverfps": 60,
                        "serverfpsaverage": 59.25,
                        "days": 4,
                        "uptime": 120,
                        "basecampnum": 3,
                    }
                )
                return
            if self.path == "/v1/api/players":
                player_get_count += 1
                if players_fail_after is not None and player_get_count > players_fail_after:
                    self.send_error(503)
                    return
                if player_get_count > 1:
                    time.sleep(players_delay)
                level = 17 if player_get_count == 1 else 18
                if players_sequence is not None:
                    rows = players_sequence[min(player_get_count - 1, len(players_sequence) - 1)]
                elif players_override is not None:
                    rows = players_override
                else:
                    rows = [
                        {
                            "name": "Alice",
                            "userId": "steam_1",
                            "level": level,
                            "ping": 23.9,
                            "location_x": 120.5,
                            "location_y": -44.25,
                            "building_count": 7,
                            "ip": "203.0.113.5",
                        }
                    ]
                self._write_json(
                    {
                        "players": rows
                    }
                )
                return
            if self.path == "/v1/api/info":
                self._write_json({"version": "v1.2.3"})
                return
            if self.path == "/v1/api/game-data":
                self._write_json(
                    game_data_override
                    if game_data_override is not None
                    else {"ActorData": []}
                )
                return
            self.send_error(404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            if self.path in (
                "/v1/api/kick", "/v1/api/ban", "/v1/api/unban",
                "/v1/api/shutdown", "/v1/api/stop", "/v1/api/announce",
                "/v1/api/save",
            ):
                request_body = json.loads(body) if body else {}
                calls.append((self.path, request_body))
                if banlist_path is not None and self.path in (
                    "/v1/api/ban",
                    "/v1/api/unban",
                ):
                    path = Path(banlist_path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        banned_ids = [
                            line.strip()
                            for line in path.read_text(encoding="utf-8").splitlines()
                            if line.strip()
                        ]
                    except FileNotFoundError:
                        banned_ids = []
                    userid = str(request_body["userid"])
                    if self.path == "/v1/api/ban" and userid not in banned_ids:
                        banned_ids.append(userid)
                    elif self.path == "/v1/api/unban":
                        banned_ids = [value for value in banned_ids if value != userid]
                    path.write_text(
                        "".join(f"{value}\n" for value in banned_ids),
                        encoding="utf-8",
                    )
                if post_started is not None:
                    post_started.set()
                time.sleep(post_delay)
                self._write_json({})
                if post_completed is not None:
                    post_completed.set()
                if self.path == "/v1/api/shutdown" and shutdown_executable is not None:
                    expected = Path(shutdown_executable).resolve()
                    for process in psutil.process_iter(["exe"]):
                        try:
                            executable = process.info.get("exe")
                            if executable and Path(executable).resolve() == expected:
                                process.terminate()
                        except (OSError, psutil.Error):
                            continue
                return
            self.send_error(404)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def _mock_ue4ss_server():
    calls = []
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("dwmapi.dll", b"proxy")
        archive.writestr("ue4ss/UE4SS.dll", b"dll")
        archive.writestr(
            "ue4ss/UE4SS-settings.ini",
            b"Other = 1\r\nbUseUObjectArrayCache = true\r\n",
        )
        archive.writestr("ue4ss/Mods/ExampleLua/Scripts/main.lua", b"print('ok')")
        archive.writestr("ue4ss/Mods/shared/helper.lua", b"shared")
    archive_bytes = archive_buffer.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def _write(self, payload, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            calls.append(self.path)
            route = self.path.split("?", 1)[0]
            if route.startswith("/download/"):
                self._write(archive_bytes, "application/zip")
                return
            self.send_error(404)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def _gui_page(
    tmp_path,
    monkeypatch,
    *,
    preferred_language="en-US",
    browser_locale="en-US",
    rest_port=8212,
    profile_overrides=None,
    extra_env=None,
    seed_profile=True,
):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(config_dir))
    if preferred_language is not None:
        settings_dir = config_dir / "webui"
        settings_dir.mkdir(exist_ok=True)
        (settings_dir / "settings.json").write_text(
            json.dumps({"language": preferred_language}),
            encoding="utf-8",
        )
    profile_values = {
        "name": "default",
        "server_name": "Test Palworld",
        "rest_port": rest_port,
        "backup_source": str(tmp_path / "missing"),
        "backup_dir": str(tmp_path / "backups"),
    }
    profile_values.update(profile_overrides or {})
    if seed_profile:
        save_profile(Profile(**profile_values))

    port = _free_port()
    env = {**os.environ, "PALSITTER_CONFIG_DIR": str(config_dir)}
    env.update(extra_env or {})
    proc = subprocess.Popen(
        [sys.executable, "gui.py", "--host", "127.0.0.1", "--port", str(port)],
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = ""
    try:
        with sync_playwright() as p:
            browser = _launch_browser(p)
            page = browser.new_page(
                viewport={"width": 1400, "height": 900},
                locale=browser_locale,
            )
            page_errors = []
            failed_assets = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on(
                "requestfailed",
                lambda request: failed_assets.append(
                    f"{request.url}: {request.failure}"
                )
                if "/static/gui/" in request.url
                else None,
            )
            page.on(
                "response",
                lambda response: failed_assets.append(
                    f"{response.url}: HTTP {response.status}"
                )
                if "/static/gui/" in response.url and response.status >= 400
                else None,
            )
            _goto(page, port)
            page.locator("#pywebio-scope-brand").get_by_text("Palsitter").wait_for(timeout=5000)
            yield page, config_dir
            page.wait_for_timeout(250)
            assert page_errors == []
            assert failed_assets == []
            browser.close()
    finally:
        try:
            parent = psutil.Process(proc.pid)
            children = parent.children(recursive=True)
            for child in children:
                child.terminate()
            psutil.wait_procs(children, timeout=3)
            for child in children:
                if child.is_running():
                    child.kill()
        except psutil.Error:
            pass
        proc.terminate()
        try:
            output, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            output, _ = proc.communicate()
            assert "Unhandled error in pywebio app" not in output
        assert "Traceback" not in output


@pytest.mark.playwright
def test_home_has_home_updater_utils_and_no_add_server(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.locator("#pywebio-scope-menu").get_by_text("Home").wait_for(timeout=5000)
        page.locator("#pywebio-scope-menu").get_by_text("Updater").wait_for(timeout=5000)
        page.locator("#pywebio-scope-menu").get_by_text("Utils").wait_for(timeout=5000)
        assert page.locator("#pywebio-scope-aside svg.icon-develop").count() == 1
        assert page.locator("#pywebio-scope-aside svg.icon-run").count() == 1
        assert page.locator("#pywebio-scope-aside svg.aside-icon-add").count() == 1
        assert page.locator("#pywebio-scope-content").get_by_text("Add server").count() == 0
        assert page.locator("#pywebio-scope-menu").get_by_text("Add Server").count() == 0

        page.locator("#pywebio-scope-menu").get_by_text("Updater").click()
        page.get_by_role("button", name="Check update", exact=True).wait_for(timeout=10000)
        assert page.locator("#pywebio-scope-content").get_by_text(
            "Automatic git update is not available yet."
        ).count() == 0


@pytest.mark.playwright
def test_home_navigation_interrupts_slow_card_load_and_discards_stale_result(
    tmp_path, monkeypatch
):
    get_calls = []
    with _running_palserver_process(tmp_path), _mock_metrics_server(
        delay=5, get_calls=get_calls
    ) as (rest_port, _):
        with _gui_page(tmp_path, monkeypatch, rest_port=rest_port) as (page, _):
            deadline = time.monotonic() + 15
            while "/v1/api/metrics" not in get_calls and time.monotonic() < deadline:
                time.sleep(0.05)
            assert "/v1/api/metrics" in get_calls

            started = time.monotonic()
            page.locator("#pywebio-scope-aside").get_by_text(
                "default", exact=True
            ).click()
            page.locator("#pywebio-scope-overview").wait_for(timeout=1000)
            assert time.monotonic() - started < 1.5

            page.locator("#pywebio-scope-aside").get_by_text(
                "Home", exact=True
            ).click()
            card = page.locator('.instance-card[data-instance-card="default"]')
            card.wait_for(timeout=5000)
            page.wait_for_timeout(1000)
            assert "instance-card-loading" in (card.get_attribute("class") or "")

            page.locator(
                '.instance-card[data-instance-card="default"]:not(.instance-card-loading)'
            ).wait_for(timeout=10000)


@pytest.mark.playwright
def test_empty_startup_does_not_create_default_instance(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch, seed_profile=False) as (page, _):
        aside = page.locator("#pywebio-scope-aside")
        aside.get_by_text("Home", exact=True).wait_for(timeout=5000)
        aside.get_by_text("Add", exact=True).wait_for(timeout=5000)
        assert aside.locator(".rail-button").all_inner_texts() == ["Home", "Add"]
        assert list_profiles() == []
        empty = page.locator("#pywebio-scope-home_empty")
        empty.get_by_text("No server instances yet", exact=True).wait_for(timeout=5000)
        empty.get_by_role("button", name="Add instance", exact=True).click()
        page.get_by_label("Profile name", exact=True).wait_for(timeout=5000)
        page.locator(".modal.show button.close").click()

        page.locator("#pywebio-scope-menu").get_by_text("Utils").click()
        page.locator("#pywebio-scope-content").get_by_text("Run all instances").wait_for(timeout=5000)


@pytest.mark.playwright
def test_updater_matches_state_tables_styles_and_git_behavior(tmp_path, monkeypatch):
    git_calls = tmp_path / "git-calls.txt"
    available = tmp_path / "update-available.flag"
    mock_git = tmp_path / "git-mock.cmd"
    mock_git.write_text(
        "\n".join(
            [
                f'@echo %* >> "{git_calls}"',
                '@if "%1"=="log" @echo abc1234---Tester---2026-07-09 12:00:00 +0800---test commit',
                '@if "%1"=="log" @exit /b 0',
                '@if "%1"=="fetch" @ping 127.0.0.1 -n 2 > nul',
                '@if "%1"=="fetch" @exit /b 0',
                f'@if "%1"=="rev-list" @if exist "{available}" (@echo 1) else (@echo 0)',
                '@if "%1"=="rev-list" @exit /b 0',
                '@exit /b 0',
            ]
        ),
        encoding="ascii",
    )

    with _gui_page(
        tmp_path,
        monkeypatch,
        extra_env={"PALSITTER_GIT": str(mock_git)},
    ) as (page, _):
        page.locator("#pywebio-scope-menu").get_by_text("Updater").click()
        page.get_by_text("Latest version", exact=True).wait_for(timeout=10000)
        page.get_by_role("button", name="Check update", exact=True).wait_for(timeout=5000)

        content = page.locator("#pywebio-scope-content")
        content.get_by_text("Local", exact=True).wait_for(timeout=5000)
        content.get_by_text("Upstream", exact=True).wait_for(timeout=5000)
        content.get_by_text("Detailed Commit History", exact=True).wait_for(timeout=5000)
        assert content.get_by_text("abc1234", exact=True).count() >= 2
        assert content.get_by_text("Commit time", exact=True).count() >= 1
        assert content.get_by_text("Commit message", exact=True).count() >= 1

        header_color = page.locator("#pywebio-scope-updater_info th").first.evaluate(
            "(element) => getComputedStyle(element).backgroundColor"
        )
        cell_color = page.locator("#pywebio-scope-updater_info td").first.evaluate(
            "(element) => getComputedStyle(element).backgroundColor"
        )
        assert header_color == "rgb(13, 17, 23)"
        assert cell_color == "rgb(22, 27, 34)"

        deadline = time.time() + 10
        while (
            "rev-list --count HEAD..origin/main"
            not in git_calls.read_text(encoding="ascii")
            and time.time() < deadline
        ):
            time.sleep(0.1)
        page.get_by_role("button", name="Check update", exact=True).wait_for(timeout=5000)
        available.write_text("", encoding="ascii")
        page.get_by_role("button", name="Check update", exact=True).click()
        page.get_by_text("Checking for updates", exact=True).wait_for(timeout=2000)
        page.get_by_text("New version available", exact=True).wait_for(timeout=10000)
        page.get_by_role("button", name="Click to update", exact=True).click()
        page.get_by_text("Update finished", exact=True).wait_for(timeout=10000)
        restart_modal = page.locator(".modal.show")
        restart_modal.get_by_text("Restart Palsitter?", exact=True).wait_for(timeout=5000)
        restart_modal.get_by_role("button", name="Cancel", exact=True).click()
        restart_modal.wait_for(state="hidden", timeout=5000)

        calls = git_calls.read_text(encoding="ascii")
        assert "remote set-url origin https://github.com/ken1882/palsitter.git" in calls
        assert "fetch origin main" in calls
        assert "rev-list --count HEAD..origin/main" in calls
        assert "pull --ff-only origin main" in calls


@pytest.mark.playwright
def test_utils_matches_actions_live_log_css_and_gated_code(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.locator("#pywebio-scope-menu").get_by_text("Utils").click()
        actions = page.locator("#pywebio-scope-util-buttons").get_by_role("button")
        page.get_by_role("button", name="Raise exception", exact=True).wait_for(timeout=5000)
        assert actions.all_inner_texts() == [
            "Raise exception",
            "Force restart",
            "Shutdown Palsitter",
            "Run all instances",
            "Stop all instances",
            "Kill all instances",
        ]
        assert page.get_by_role("button", name="Instances status", exact=True).count() == 0

        action_box = page.locator("#pywebio-scope-util-buttons").bounding_box()
        logs_box = page.locator("#pywebio-scope-logs").bounding_box()
        assert action_box is not None
        assert logs_box is not None
        assert action_box["x"] + action_box["width"] <= logs_box["x"]
        assert "monospace" in page.locator("#pywebio-scope-dev-log").evaluate(
            "(element) => getComputedStyle(element).fontFamily"
        ).lower()

        page.get_by_role("button", name="Raise exception", exact=True).click()
        page.locator("#pywebio-scope-dev-log").get_by_text(
            "RuntimeError: quq"
        ).wait_for(timeout=2500)
        page.evaluate(
            "window.__utilsLogNode = document.getElementById('utils-log-output')"
        )

        page.get_by_role("button", name="Force restart", exact=True).click()
        modal = page.locator(".modal.show")
        modal.get_by_text("Restart Palsitter?", exact=True).wait_for(timeout=2000)
        modal.get_by_text("every active managed server", exact=False).wait_for(timeout=2000)
        modal.get_by_role("button", name="Cancel", exact=True).click()
        assert page.evaluate(
            "document.getElementById('utils-log-output') === window.__utilsLogNode"
        )

        scroll = page.locator("#pywebio-scope-log_scroll_btn")
        scroll.get_by_role("button", name="Auto Scroll ON", exact=True).click()
        scroll.get_by_role("button", name="Auto Scroll OFF", exact=True).wait_for(timeout=2000)

        page.get_by_role("button", name="Run all instances", exact=True).click()
        modal = page.locator(".modal.show")
        modal.get_by_text("Select instances to run", exact=True).wait_for(timeout=5000)
        checkboxes = modal.locator("input.utils-instance-checkbox[type='checkbox']")
        assert checkboxes.count() == 1
        assert checkboxes.first.is_checked()
        assert modal.locator(".utils-instance-selection").evaluate(
            "element => getComputedStyle(element).overflowY"
        ) == "auto"
        select_toggle = modal.get_by_role("button", name="Select none", exact=True)
        select_toggle.click()
        assert not checkboxes.first.is_checked()
        modal.get_by_role("button", name="Select all", exact=True).click()
        assert checkboxes.first.is_checked()
        modal.locator("button.close").click()

        page.evaluate(
            "localStorage.setItem("
            "'DANGER_ENABLE_EVAL', "
            "'DO_NOT_PASTE_ANY_CODE_HERE_UNLESS_YOU_KNOW_WHAT_YOU_ARE_DOING')"
        )
        page.locator("#pywebio-scope-menu").get_by_text("Utils").click()
        page.get_by_role("button", name="Run Code", exact=True).click()
        editor = page.locator("#input-container .CodeMirror")
        editor.wait_for(timeout=5000)

        editor.evaluate(
            "(element, value) => element.CodeMirror.setValue(value)",
            '_append_util_log("Code executed")',
        )
        page.get_by_role("button", name="Submit", exact=True).click()
        page.locator("#pywebio-scope-dev-log").get_by_text(
            "Code executed"
        ).wait_for(timeout=2500)

        page.get_by_role("button", name="Shutdown Palsitter", exact=True).click()
        modal = page.locator(".modal.show")
        modal.get_by_text("Shutdown Palsitter?", exact=True).wait_for(timeout=2000)
        modal.get_by_role("button", name="Shutdown", exact=True).click()
        shutdown_overlay = page.locator("#pywebio-scope-shutdown_overlay")
        shutdown_overlay.locator(".shutdown-overlay-card").wait_for(timeout=5000)
        force_button = shutdown_overlay.get_by_role("button", name="Force Shutdown", exact=True)
        force_button.wait_for(timeout=10000)
        if force_button.is_enabled():
            force_button.click()
        shutdown_overlay.get_by_text(
            "Palsitter stopped, you can now safely close this window", exact=True
        ).wait_for(timeout=15000)


@pytest.mark.playwright
def test_utils_run_stop_and_kill_use_mocked_processes(tmp_path, monkeypatch):
    steam_calls = tmp_path / "utils-steamcmd-calls.txt"
    _prepare_fixed_palserver_python(tmp_path)
    _prepare_fixed_steamcmd(tmp_path)
    profile_overrides = {
        "executable_args": ["-c", "import time; time.sleep(60)"],
    }
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    create_instance("satisfactory", "satisfactory")

    with _gui_page(
        tmp_path,
        monkeypatch,
        profile_overrides=profile_overrides,
        extra_env={"PALSITTER_FAKE_STEAMCMD_CALLS": str(steam_calls)},
    ) as (page, _):
        page.locator("#pywebio-scope-menu").get_by_text("Utils").click()

        def run_bulk_action(action, result, *, deselect=None, expect_skipped=True):
            page.get_by_role("button", name=action, exact=True).click()
            modal = page.locator(".modal.show")
            modal.get_by_text(
                {
                    "Run all instances": "Select instances to run",
                    "Stop all instances": "Select instances to stop",
                    "Kill all instances": "Select instances to kill",
                }[action],
                exact=True,
            ).wait_for(timeout=5000)
            checkboxes = modal.locator("input.utils-instance-checkbox[type='checkbox']")
            assert checkboxes.count() == 2
            assert all(checkboxes.nth(index).is_checked() for index in range(checkboxes.count()))
            if action == "Run all instances":
                modal.get_by_role("button", name="Select none", exact=True).click()
                assert not any(checkboxes.nth(index).is_checked() for index in range(checkboxes.count()))
                modal.get_by_role("button", name="Select all", exact=True).click()
                assert all(checkboxes.nth(index).is_checked() for index in range(checkboxes.count()))
            if deselect:
                modal.get_by_role("checkbox", name=deselect, exact=True).uncheck()
            modal.get_by_role("button", name="Confirm", exact=True).click()
            modal = page.locator(".modal.show")
            modal.get_by_text(result, exact=True).wait_for(timeout=10000)
            if expect_skipped:
                modal.get_by_text("Skipped unsupported instances:", exact=False).wait_for(timeout=5000)
                modal.get_by_text("satisfactory", exact=False).wait_for(timeout=5000)
            else:
                assert modal.get_by_text("Skipped unsupported instances:", exact=False).count() == 0
            modal.locator("button.close").click()

        run_bulk_action("Run all instances", "Started")
        page.wait_for_timeout(1000)
        calls = steam_calls.read_text(encoding="ascii") if steam_calls.exists() else ""
        assert "+app_update 2394010" in calls

        run_bulk_action("Kill all instances", "Killed", deselect="satisfactory", expect_skipped=False)
        run_bulk_action("Run all instances", "Started")
        run_bulk_action("Stop all instances", "Stopped")


@pytest.mark.playwright
def test_side_add_server_is_overlay_modal(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        page.locator(".rail-button.rail-active").get_by_text(
            "default", exact=True
        ).wait_for(timeout=5000)
        page.locator("#pywebio-scope-aside").get_by_text("Add").click()
        page.get_by_label("Profile name").wait_for(timeout=5000)
        modal = page.locator(".modal.show")
        assert modal.count() == 1
        assert page.locator(".rail-button.rail-active").get_by_text(
            "default", exact=True
        ).count() == 1
        assert page.locator(".rail-button.rail-active").get_by_text(
            "Add", exact=True
        ).count() == 0
        assert page.locator(".modal-backdrop.show").evaluate(
            "(element) => getComputedStyle(element).opacity"
        ) == "0.5"
        assert modal.locator(".modal-dialog").evaluate(
            "(element) => getComputedStyle(element).width"
        ) == "500px"
        assert modal.locator(".modal-content").evaluate(
            "(element) => getComputedStyle(element).backgroundColor"
        ) == "rgb(25, 29, 33)"
        assert modal.locator(".modal-content").evaluate(
            "(element) => getComputedStyle(element).borderColor"
        ) == "rgba(255, 255, 255, 0.2)"
        assert modal.locator(".modal-body").evaluate(
            "(element) => getComputedStyle(element).backgroundColor"
        ) == "rgb(47, 49, 54)"
        assert modal.locator(".modal-title").evaluate(
            "(element) => getComputedStyle(element).color"
        ) == "rgb(211, 211, 211)"
        assert modal.locator(".modal-header .close").evaluate(
            "(element) => getComputedStyle(element).color"
        ) == "rgb(255, 255, 255)"
        assert modal.locator(".modal-header .close").evaluate(
            "(element) => getComputedStyle(element).opacity"
        ) == "0.5"

        profile_input = page.get_by_label("Profile name")
        source_select = page.get_by_label("Copy from")
        for control in (profile_input, source_select):
            assert control.evaluate(
                "(element) => getComputedStyle(element).height"
            ) == "26px"
            assert control.evaluate(
                "(element) => getComputedStyle(element).backgroundColor"
            ) == "rgba(0, 0, 0, 0)"
            assert control.evaluate(
                "(element) => getComputedStyle(element).borderBottomColor"
            ) == "rgb(122, 119, 187)"
            assert control.evaluate(
                "(element) => getComputedStyle(element).borderTopColor"
            ) == "rgb(108, 117, 125)"
        assert modal.locator(".form-group").first.evaluate(
            "(element) => getComputedStyle(element).marginBottom"
        ) == "0px"

        source_options = source_select.evaluate(
            "(element) => Array.from(element.options).map((option) => option.text)"
        )
        assert "template" in source_options
        assert "default" in source_options
        assert "server" not in source_options

        confirm = page.get_by_role("button", name="Confirm", exact=True)
        assert confirm.evaluate(
            "(element) => getComputedStyle(element).backgroundColor"
        ) == "rgb(55, 90, 127)"
        assert page.get_by_role("button", name="Cancel", exact=True).count() == 0

        page.get_by_label("Profile name").fill("server2")
        confirm.click()
        page.locator("#pywebio-scope-aside").get_by_text("server2").wait_for(timeout=5000)

        page.locator("#pywebio-scope-aside").get_by_text("server2", exact=True).click()
        page.locator("#pywebio-scope-menu").get_by_text("World Settings").click()
        page.locator("#pywebio-scope-world_settings_form").wait_for(timeout=5000)

        admin_password = page.locator('input[name="world_AdminPassword"]')
        server_password = page.locator('input[name="world_ServerPassword"]')
        assert admin_password.get_attribute("type") == "password"
        assert server_password.get_attribute("type") == "password"
        assert re.fullmatch(r"[a-z0-9]{8}", admin_password.input_value())
        page.locator("#pywebio-scope-world_toggle_RESTAPIEnabled").get_by_role(
            "button", name="On", exact=True
        ).wait_for(timeout=5000)
        page.locator("#pywebio-scope-world_toggle_EnableGameDataAPI").get_by_role(
            "button", name="On", exact=True
        ).wait_for(timeout=5000)

        eye = admin_password.locator("xpath=ancestor::div[contains(@style, 'grid-auto-flow')]").locator(
            "button.password-eye"
        )
        assert eye.get_attribute("aria-label") == "Show password"
        eye.click()
        assert admin_password.get_attribute("type") == "text"
        assert eye.get_attribute("aria-label") == "Hide password"
        eye.click()
        assert admin_password.get_attribute("type") == "password"

        page.locator("#pywebio-scope-menu").get_by_text(
            "Server Settings", exact=True
        ).click()
        page.locator("#pywebio-scope-settings_form").wait_for(timeout=5000)
        page.locator("#pywebio-scope-settings_toggle_launch_useperfthreads").get_by_role(
            "button", name="On", exact=True
        ).wait_for(timeout=5000)
        page.locator(
            "#pywebio-scope-settings_toggle_launch_no_async_loading_thread"
        ).get_by_role("button", name="Off", exact=True).wait_for(timeout=5000)
        page.locator(
            "#pywebio-scope-settings_toggle_launch_use_multithread_for_ds"
        ).get_by_role("button", name="On", exact=True).wait_for(timeout=5000)
        assert page.locator(
            'input[name="settings_launch_worker_threads_server"]'
        ).input_value() == str(max(1, (os.cpu_count() or 2) - 1))


@pytest.mark.playwright
def test_add_server_shows_locked_creation_progress(tmp_path, monkeypatch):
    world_id = "D" * 32
    world = tmp_path / "source" / world_id
    (world / "Players").mkdir(parents=True)
    (world / "Level.sav").write_text("level", encoding="utf-8")
    (world / "Players" / "one.sav").write_text("player", encoding="utf-8")
    for index in range(3000):
        (world / f"payload-{index}.dat").write_bytes(b"payload")

    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("Add", exact=True).click()
        page.get_by_label("Profile name", exact=True).fill("slow-import")
        page.get_by_label("Level.sav file", exact=True).fill(str(world / "Level.sav"))
        page.locator("#pywebio-scope-add_import_panel").get_by_role(
            "button", name="Browse", exact=True
        ).click()
        browser = page.locator(".modal.show")
        browser.get_by_text("Level.sav", exact=True).click()
        browser.locator("#pywebio-scope-browse_actions").get_by_role(
            "button", name="Open", exact=True
        ).click()
        page.get_by_role("button", name="Confirm", exact=True).click()

        creating = page.locator(".modal.show")
        creating.get_by_role(
            "heading", name="Creating profile #slow-import", exact=True
        ).wait_for(timeout=5000)
        assert creating.locator(".modal-header .close").count() == 0
        assert creating.get_by_role("button").count() == 0
        page.locator("#pywebio-scope-aside").get_by_text(
            "slow-import", exact=True
        ).wait_for(timeout=10000)


@pytest.mark.playwright
def test_satisfactory_picker_placeholder_and_delete(tmp_path, monkeypatch):
    steam_sentinel = tmp_path / "satisfactory-steamcmd-calls.txt"
    with _gui_page(
        tmp_path,
        monkeypatch,
        seed_profile=False,
        extra_env={"PALSITTER_FAKE_STEAMCMD_CALLS": str(steam_sentinel)},
    ) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("Add", exact=True).click()
        game_select = page.get_by_label("Game", exact=True)
        game_select.select_option(label="Satisfactory")
        page.wait_for_function(
            "() => document.querySelector('input[name=add_server_name]').value === 'satisfactory'"
        )
        source = page.get_by_label("Copy from", exact=True)
        assert source.locator("option").all_inner_texts() == ["template"]

        page.get_by_role("button", name="Confirm", exact=True).click()
        page.get_by_text(
            "Satisfactory server support is not implemented yet. This instance is a placeholder.",
            exact=True,
        ).wait_for(timeout=5000)
        assert load_instance("satisfactory").game_config == {}
        assert not (_profile_dir(tmp_path, "satisfactory") / "steamcmd").exists()
        assert not (_profile_dir(tmp_path, "satisfactory") / "backups").exists()
        page.locator("#pywebio-scope-menu .menu-button").get_by_text(
            "Overview", exact=True
        ).wait_for(timeout=5000)
        assert page.locator("#pywebio-scope-menu .menu-button").all_inner_texts() == ["Overview"]
        page.locator("#pywebio-scope-header_status").get_by_text("Unsupported", exact=True).wait_for()
        for action in ("Start", "Stop", "KILL", "Backup", "Server Settings", "Auto Restart", "World Settings"):
            assert page.get_by_text(action, exact=True).count() == 0

        page.locator("#pywebio-scope-aside").get_by_text("Home", exact=True).click()
        unsupported_card = page.locator(
            '.instance-card-unsupported[data-instance-card="satisfactory"]'
        )
        unsupported_card.get_by_text("Unsupported", exact=True).wait_for(timeout=5000)
        page.locator("#pywebio-scope-menu").get_by_text("Utils", exact=True).click()
        assert page.get_by_role("button", name="Instances status", exact=True).count() == 0
        page.locator("#pywebio-scope-aside").get_by_text("satisfactory", exact=True).click()

        page.get_by_role("button", name="Delete instance", exact=True).click()
        confirm = page.get_by_role("button", name="Yes, delete", exact=True)
        assert confirm.is_disabled()
        page.locator('input[name="delete_confirm_name"]').fill("satisfactory")
        page.wait_for_function(
            "() => Array.from(document.querySelectorAll('button')).some((b) => "
            "b.innerText.trim() === 'Yes, delete' && !b.disabled)"
        )
        confirm.click()
        page.locator("#pywebio-scope-menu").get_by_text("Home", exact=True).wait_for()
        assert page.locator("#pywebio-scope-aside").get_by_text("satisfactory", exact=True).count() == 0
    assert not steam_sentinel.exists()


@pytest.mark.playwright
def test_instance_overview_status_log_and_console(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()

        page.locator("#pywebio-scope-header_status").get_by_text("Inactive").wait_for(timeout=5000)
        instance_menu = page.locator("#pywebio-scope-menu")
        assert instance_menu.get_by_text("Saves & Backups", exact=True).count() == 1
        assert instance_menu.get_by_text("REST Actions", exact=True).count() == 0
        page.add_style_tag(content=".spinner-border { animation: none !important; transform: none !important; }")
        layout = page.wait_for_function(
            """
            () => {
                const spinner = document.querySelector('#pywebio-scope-header_status .spinner-border');
                const text = document.querySelector('#pywebio-scope-header_status p');
                const brand = document.querySelector('.brand-title');
                if (!spinner || !text || !brand) return null;
                const box = element => {
                    const rect = element.getBoundingClientRect();
                    return {x: rect.x, width: rect.width};
                };
                return {
                    spinner: box(spinner),
                    text: box(text),
                    brand: box(brand),
                    spinnerWidth: getComputedStyle(spinner).width,
                };
            }
            """,
            timeout=5000,
        ).json_value()
        spinner_box = layout["spinner"]
        text_box = layout["text"]
        brand_box = layout["brand"]
        assert layout["spinnerWidth"] == "24px"
        assert brand_box["x"] + brand_box["width"] < spinner_box["x"]
        assert spinner_box["x"] + spinner_box["width"] <= text_box["x"]
        page.locator("#pywebio-scope-content").get_by_text("Scheduler").wait_for(timeout=5000)
        page.locator("#pywebio-scope-content").get_by_text("Log", exact=True).wait_for(timeout=5000)
        page.locator(".log-box").wait_for(timeout=5000)
        scheduler = page.locator("#pywebio-scope-scheduler_panel")
        server_dir = _fixed_palserver_dir(tmp_path)
        scheduler.get_by_role("button", name="Open PalServer folder", exact=True).click()
        page.locator(".toastify").get_by_text(
            "Please start and install the server once", exact=True
        ).wait_for(timeout=5000)
        assert server_dir.is_dir()
        assert not _fixed_palserver_executable(tmp_path).exists()
        console = page.locator('input[name="console_command"]')
        console.focus()
        autocomplete = page.locator("#console-autocomplete")
        autocomplete.wait_for(state="visible", timeout=5000)
        assert autocomplete.get_by_role("option").all_inner_texts() == [
            "announce <message>\nSend a message to all players",
            "ban <user_id> [message]\nBan a player from the server",
            "backup\nCreate a save backup",
            "info\nShow server information",
            "kick <user_id> [message]\nKick a player from the server",
            "metrics\nShow server metrics",
            "players\nShow connected players",
            "restart\nRestart the server",
            "save\nSave the world data",
            "shutdown <waittime> [message]\nShut down the server after a delay",
            "start\nStart the server",
            "stop\nStop the server",
            "unban <user_id>\nUnban a player",
        ]
        assert autocomplete.locator(".console-autocomplete-hint").all_text_contents() == [
            "Send a message to all players",
            "Ban a player from the server",
            "Create a save backup",
            "Show server information",
            "Kick a player from the server",
            "Show server metrics",
            "Show connected players",
            "Restart the server",
            "Save the world data",
            "Shut down the server after a delay",
            "Start the server",
            "Stop the server",
            "Unban a player",
        ]
        console.press("ArrowDown")
        assert autocomplete.locator('[role="option"]').nth(0).get_attribute(
            "aria-selected"
        ) == "true"
        console.press("Tab")
        assert autocomplete.locator('[role="option"]').nth(1).get_attribute(
            "aria-selected"
        ) == "true"
        console.press("Shift+Tab")
        assert autocomplete.locator('[role="option"]').nth(0).get_attribute(
            "aria-selected"
        ) == "true"
        console.press("ArrowDown")
        assert autocomplete.locator('[role="option"]').nth(1).get_attribute(
            "aria-selected"
        ) == "true"
        console.press("ArrowUp")
        assert autocomplete.locator('[role="option"]').nth(0).get_attribute(
            "aria-selected"
        ) == "true"
        console.press("Enter")
        assert console.input_value() == "announce "
        assert autocomplete.is_hidden()
        assert "> announce" not in page.locator(".log-box").inner_text()
        console.fill("ann")
        visible_options = autocomplete.locator('[role="option"]:visible')
        assert visible_options.locator(".console-autocomplete-command").all_text_contents() == [
            "announce <message>"
        ]
        console.press("Escape")
        assert autocomplete.is_hidden()
        console.fill("ann")
        visible_options = autocomplete.locator('[role="option"]:visible')
        visible_options.first.click()
        assert console.input_value() == "announce "
        console.fill("nonsense")
        console.press("Enter")
        page.locator(".log-box").get_by_text("Unknown console command: nonsense").wait_for(timeout=5000)
        assert console.input_value() == ""
        assert autocomplete.is_hidden()


@pytest.mark.playwright
@pytest.mark.parametrize(
    ("command", "message"),
    [
        ("save", "Save requested"),
        ("shutdown 5", "Shutdown requested"),
    ],
)
def test_console_logs_request_before_delayed_rest_response(
    tmp_path, monkeypatch, command, message
):
    post_started = threading.Event()
    post_completed = threading.Event()
    with _running_palserver_process(tmp_path), _mock_metrics_server(
        post_delay=3,
        post_started=post_started,
        post_completed=post_completed,
    ) as (rest_port, _):
        with _gui_page(tmp_path, monkeypatch, rest_port=rest_port) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text("default").click()
            console = page.locator('input[name="console_command"]')
            console.fill(command)
            console.press("Enter")

            assert post_started.wait(timeout=5)
            page.locator(".log-box").get_by_text(message).wait_for(timeout=2000)
            assert not post_completed.is_set()


@pytest.mark.playwright
def test_scheduler_save_stays_disabled_until_rest_save_completes(tmp_path, monkeypatch):
    post_started = threading.Event()
    post_completed = threading.Event()
    with _running_palserver_process(tmp_path), _mock_metrics_server(
        post_delay=2,
        post_started=post_started,
        post_completed=post_completed,
    ) as (rest_port, _):
        with _gui_page(tmp_path, monkeypatch, rest_port=rest_port) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
            scheduler = page.locator("#pywebio-scope-scheduler_panel")
            save = scheduler.get_by_role("button", name="Save", exact=True)
            save.wait_for(timeout=5000)
            page.wait_for_function(
                "() => !document.querySelector('#pywebio-scope-scheduler_save button')?.disabled"
            )

            save.click()
            assert post_started.wait(timeout=5)
            assert save.is_disabled()
            assert not post_completed.is_set()
            post_completed.wait(timeout=5)
            page.wait_for_function(
                "() => !document.querySelector('#pywebio-scope-scheduler_save button')?.disabled"
            )


@pytest.mark.playwright
def test_open_rest_without_matching_process_stays_inactive_and_unpolled(tmp_path, monkeypatch):
    get_calls = []
    with _mock_metrics_server(get_calls=get_calls) as (rest_port, _):
        with _gui_page(tmp_path, monkeypatch, rest_port=rest_port) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text("default").click()
            page.locator("#pywebio-scope-header_status").get_by_text(
                "Inactive", exact=True
            ).wait_for(timeout=5000)
            scheduler = page.locator("#pywebio-scope-scheduler_panel")
            scheduler.locator('[data-endpoint="rest"]').get_by_text(
                f"Closed ({rest_port})", exact=True
            ).wait_for(timeout=5000)
            page.wait_for_timeout(3500)
            assert page.locator('[data-metric="fps"] .metric-value').inner_text() == "-"
            assert get_calls == []
            page.locator("#pywebio-scope-menu").get_by_text("Players", exact=True).click()
            detail = page.locator("#pywebio-scope-players_detail_panel")
            detail.locator("#pywebio-scope-players_detail_list").get_by_text(
                    "PalServer is not running or its REST API is unavailable.", exact=True
            ).wait_for(timeout=5000)
            assert detail.get_by_text("Loading players...", exact=True).count() == 0
            assert get_calls == []


@pytest.mark.playwright
def test_players_page_replaces_loading_when_running_rest_is_closed(tmp_path, monkeypatch):
    rest_port = _free_port()
    with _running_palserver_process(tmp_path):
        with _gui_page(tmp_path, monkeypatch, rest_port=rest_port) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text("default").click()
            page.locator("#pywebio-scope-menu").get_by_text("Players", exact=True).click()
            detail = page.locator("#pywebio-scope-players_detail_panel")
            detail.locator("#pywebio-scope-players_detail_list").get_by_text(
                    "PalServer is not running or its REST API is unavailable.", exact=True
            ).wait_for(timeout=5000)
            assert detail.get_by_text("Loading players...", exact=True).count() == 0


@pytest.mark.playwright
def test_scheduler_consolidates_instance_operations_without_persistent_strip(tmp_path, monkeypatch):
    steam_calls = tmp_path / "steamcmd-calls.txt"
    _prepare_fixed_palserver_python(tmp_path)
    _prepare_fixed_steamcmd(tmp_path)
    profile_overrides = {
        "executable_args": ["-c", "import time; time.sleep(60)"],
        "steam_validate": True,
    }

    with _mock_metrics_server(
        shutdown_executable=_fixed_palserver_executable(tmp_path)
    ) as (rest_port, rest_calls):
        with _gui_page(
            tmp_path,
            monkeypatch,
            rest_port=rest_port,
            profile_overrides={**profile_overrides, "shutdown_wait_seconds": 0},
            extra_env={"PALSITTER_FAKE_STEAMCMD_CALLS": str(steam_calls)},
        ) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text("default").click()
            scheduler = page.locator("#pywebio-scope-scheduler_panel")
            start = scheduler.get_by_role("button", name="Start", exact=True)
            stop = scheduler.get_by_role("button", name="Stop", exact=True)
            kill = scheduler.get_by_role("button", name="KILL", exact=True)
            save = scheduler.get_by_role("button", name="Save", exact=True)
            backup = scheduler.get_by_role("button", name="Backup", exact=True)

            start.wait_for(timeout=5000)
            save.wait_for(timeout=5000)
            backup.wait_for(timeout=5000)
            assert save.is_disabled()
            action_x = scheduler.evaluate(
                """
                element => Object.fromEntries(
                    [...element.querySelectorAll('button')]
                        .filter(button => ['Start', 'Save', 'Backup'].includes(button.textContent.trim()))
                        .map(button => [button.textContent.trim(), button.getBoundingClientRect().x])
                )
                """
            )
            assert set(action_x) == {"Start", "Save", "Backup"}
            assert action_x["Start"] < action_x["Save"] < action_x["Backup"]
            assert page.locator("#pywebio-scope-instance_actions").count() == 0
            for action in ("Check update", "Update server", "Validate / Repair", "Retry"):
                assert scheduler.get_by_role("button", name=action, exact=True).count() == 0
            assert scheduler.locator("#pywebio-scope-scheduler_operation_progress").count() == 0
            assert scheduler.locator(".scheduler-operation-progress").count() == 0
            assert stop.count() == 0
            assert scheduler.get_by_role("button", name="Restart", exact=True).count() == 0
            assert scheduler.get_by_role("button", name="Settings", exact=True).count() == 0
            assert scheduler.get_by_role("button", name="Logs", exact=True).count() == 0

            try:
                start.click()
                stop.wait_for(timeout=5000)
                stop.click()
                start.wait_for(timeout=10000)
                start.click()
                stop.wait_for(timeout=5000)
                for action in ("Stop", "Save", "Backup"):
                    scheduler.get_by_role("button", name=action, exact=True).wait_for(
                        timeout=10000
                    )
                page.wait_for_function(
                    "() => !document.querySelector('#pywebio-scope-scheduler_save button')?.disabled"
                )
                assert not scheduler.get_by_role(
                    "button", name="Save", exact=True
                ).is_disabled()
                assert scheduler.get_by_role(
                    "button", name="Restart", exact=True
                ).count() == 0
                calls = steam_calls.read_text(encoding="ascii") if steam_calls.exists() else ""
                assert "+app_update 2394010 validate" in calls

                stop.click()
                kill.wait_for(timeout=5000)
                scheduler.get_by_role("button", name="Save", exact=True).wait_for(
                    state="visible", timeout=5000
                )
                assert scheduler.get_by_role(
                    "button", name="Save", exact=True
                ).is_disabled()
                deadline = time.time() + 5
                while not any(
                    path == "/v1/api/shutdown" for path, _ in rest_calls
                ) and time.time() < deadline:
                    time.sleep(0.05)
                kill.click()
                start.wait_for(timeout=10000)
                shutdown_bodies = [
                    body for path, body in rest_calls if path == "/v1/api/shutdown"
                ]
                assert shutdown_bodies == [
                    {
                        "waittime": 5,
                        "message": "Server will shutdown immediately",
                    }
                ]
            finally:
                if stop.count() and stop.is_visible():
                    stop.click()
                    kill.wait_for(timeout=5000)
                if kill.count() and kill.is_visible():
                    kill.click()


@pytest.mark.playwright
def test_scheduler_stops_attached_external_server_and_refreshes_controls(tmp_path, monkeypatch):
    with _running_palserver_process(tmp_path) as external_process, _mock_metrics_server() as (rest_port, calls):
        with _gui_page(
            tmp_path,
            monkeypatch,
            rest_port=rest_port,
            profile_overrides={"shutdown_wait_seconds": 0},
        ) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text("default").click()
            scheduler = page.locator("#pywebio-scope-scheduler_panel")
            scheduler.get_by_role("button", name="Stop", exact=True).wait_for(timeout=5000)
            assert scheduler.get_by_role("button", name="Start", exact=True).count() == 0
            scheduler.get_by_role("button", name="Stop", exact=True).click()
            page.locator("#pywebio-scope-header_status").get_by_text(
                "Stopping", exact=True
            ).wait_for(timeout=5000)
            deadline = time.time() + 5
            while "/v1/api/stop" not in [path for path, _ in calls] and time.time() < deadline:
                time.sleep(0.05)
            external_process.terminate()
            external_process.wait(timeout=3)
            scheduler.get_by_role("button", name="Start", exact=True).wait_for(timeout=10000)

            deadline = time.time() + 5
            while "/v1/api/stop" not in [path for path, _ in calls] and time.time() < deadline:
                time.sleep(0.05)
            paths = [path for path, _ in calls]
            assert "/v1/api/shutdown" in paths
            assert "/v1/api/stop" in paths


@pytest.mark.playwright
def test_scheduler_endpoint_statuses_retry_during_startup(tmp_path, monkeypatch):
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    game_port = probe.getsockname()[1]
    probe.close()

    _prepare_fixed_palserver_python(tmp_path)
    _prepare_fixed_steamcmd(tmp_path)
    steam_calls = tmp_path / "endpoint-steamcmd.txt"
    with _mock_metrics_server() as (rest_port, _):
        with _gui_page(
            tmp_path,
            monkeypatch,
            rest_port=rest_port,
            profile_overrides={
                "game_port": game_port,
                "launch_enable_gamedata_api": False,
                "executable_args": ["-c", "import time; time.sleep(60)"],
            },
            extra_env={"PALSITTER_FAKE_STEAMCMD_CALLS": str(steam_calls)},
        ) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text("default").click()
            scheduler = page.locator("#pywebio-scope-scheduler_panel")
            scheduler.get_by_role("button", name="Start", exact=True).click()
            scheduler.get_by_role("button", name="Stop", exact=True).wait_for(timeout=10000)
            udp = scheduler.locator('[data-endpoint="udp"]')
            rest = scheduler.locator('[data-endpoint="rest"]')
            rcon = scheduler.locator('[data-endpoint="rcon"]')

            udp.get_by_text(f"Closed ({game_port})", exact=True).wait_for(timeout=5000)
            rest.get_by_text(f"Open ({rest_port})", exact=True).wait_for(timeout=15000)
            rcon.get_by_text("Disabled (25575)", exact=True).wait_for(timeout=5000)
            assert scheduler.locator("#pywebio-scope-scheduler_state").count() == 0
            metrics = page.locator("#pywebio-scope-metrics")
            metrics.locator('[data-metric="fps"]').get_by_text(
                "60.0 / 59.2", exact=False
            ).wait_for(timeout=10000)
            metrics.locator('[data-metric="days"]').get_by_text(
                "4", exact=True
            ).wait_for(timeout=5000)
            metrics.locator('[data-metric="uptime"]').get_by_text(
                "0d 00h 02m 00s", exact=False
            ).wait_for(timeout=5000)
            metrics.locator('[data-metric="game-version"]').get_by_text(
                "v1.2.3", exact=True
            ).wait_for(timeout=5000)
            metrics.locator('[data-metric="palbox"]').get_by_text(
                "3 / 128", exact=True
            ).wait_for(timeout=5000)
            assert metrics.locator('[data-metric="palbox"]').get_by_text(
                "Palbox", exact=True
            ).count() == 1
            assert scheduler.locator('[data-scheduler-metric]').count() == 0
            assert scheduler.locator("hr").count() == 0

            listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                listener.bind(("127.0.0.1", game_port))
                udp.get_by_text(f"Open ({game_port})", exact=True).wait_for(timeout=5000)
            finally:
                listener.close()


@pytest.mark.playwright
def test_scheduler_backup_skips_empty_save_source(tmp_path, monkeypatch):
    _fixed_backup_source(tmp_path).mkdir(parents=True)
    backup_dir = tmp_path / "backups"

    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        scheduler = page.locator("#pywebio-scope-scheduler_panel")
        backup = scheduler.get_by_role("button", name="Backup", exact=True)
        backup.click()

        page.get_by_text("Backup skipped: no save files found.", exact=True).wait_for(timeout=5000)
        backup = scheduler.get_by_role("button", name="Backup", exact=True)
        backup.wait_for(timeout=5000)
        assert not backup.is_disabled()
        assert scheduler.is_visible()
        assert not backup_dir.exists()


@pytest.mark.playwright
def test_scheduler_backup_disables_only_button_without_refreshing_overview(tmp_path, monkeypatch):
    save_dir = _fixed_backup_source(tmp_path) / "ABC123"
    save_dir.mkdir(parents=True)
    (save_dir / "large.sav").write_bytes(os.urandom(16 * 1024 * 1024))

    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        scheduler = page.locator("#pywebio-scope-scheduler_panel")
        scheduler.evaluate("element => window.__schedulerPanel = element")
        backup = scheduler.get_by_role("button", name="Backup", exact=True)

        backup.click()
        page.wait_for_function(
            "() => document.querySelector('#pywebio-scope-scheduler_backup button')?.disabled"
        )
        page.locator(".toastify").get_by_text(
            re.compile(r"Backup created:"), exact=False
        ).wait_for(timeout=15000)
        scheduler.get_by_role("button", name="Backup", exact=True).wait_for(timeout=5000)

        assert not scheduler.get_by_role("button", name="Backup", exact=True).is_disabled()
        assert scheduler.evaluate("element => element === window.__schedulerPanel")


@pytest.mark.playwright
def test_backups_tab_saves_browses_creates_deletes_and_rolls_back(tmp_path, monkeypatch):
    source = _fixed_backup_source(tmp_path)
    world_id = "A" * 32
    save = source / world_id
    save.mkdir(parents=True)
    level = save / "Level.sav"
    level.write_text("current", encoding="utf-8")
    builtin_local = save / "backup" / "local" / "2026.01.03-030405"
    builtin_world = save / "backup" / "world" / "2026.01.04-040506"
    builtin_local.mkdir(parents=True)
    builtin_world.mkdir(parents=True)
    (builtin_local / "LocalData.sav").write_text("local snapshot", encoding="utf-8")
    (builtin_world / "Level.sav").write_text("world snapshot", encoding="utf-8")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    rollback_file = backup_dir / "2026.01.01.000000.zip"
    delete_file = backup_dir / "2026.01.02.000000.zip"
    with zipfile.ZipFile(rollback_file, "w") as archive:
        archive.writestr(f"{world_id}/Level.sav", "restored")
    with zipfile.ZipFile(delete_file, "w") as archive:
        archive.writestr(f"{world_id}/Level.sav", "delete me")
    open_log = tmp_path / "opened-folder.txt"
    steam_calls = tmp_path / "rollback-steamcmd.txt"
    _prepare_fixed_palserver_python(tmp_path)
    _prepare_fixed_steamcmd(tmp_path)

    with _gui_page(
        tmp_path,
        monkeypatch,
        profile_overrides={
            "backup_dir": str(backup_dir),
            "backup_interval_minutes": 30,
            "backup_retention_count": 20,
            "dedicated_server_name": world_id,
            "executable_args": ["-c", "import time; time.sleep(60)"],
        },
        extra_env={
            "PALSITTER_FAKE_OPEN_FOLDER_LOG": str(open_log),
            "PALSITTER_FAKE_STEAMCMD_CALLS": str(steam_calls),
        },
    ) as (page, config_dir):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        page.locator("#pywebio-scope-menu").get_by_text("Server Settings").click()
        assert page.locator('input[name="settings_backup_dir"]').count() == 0
        assert page.locator('input[name="settings_backup_interval_minutes"]').count() == 0
        assert page.locator('input[name="settings_backup_retention_count"]').count() == 0

        page.locator("#pywebio-scope-menu").get_by_text("Saves & Backups").click()
        panel = page.locator("#pywebio-scope-backup_settings_panel")
        builtin_title = panel.get_by_text("Palworld built-in backups (1)", exact=True)
        builtin_title.wait_for(timeout=5000)
        panel.get_by_text("Managed backup files (2/20)", exact=True).wait_for(timeout=5000)
        builtin_table = page.locator("#pywebio-scope-builtin_backup_files table")
        assert builtin_table.locator(".backup-file-name").count() == 1
        assert builtin_table.evaluate("element => getComputedStyle(element).width") == "912px"
        assert builtin_table.locator("tbody td").first.evaluate(
            "element => getComputedStyle(element).backgroundColor"
        ) == "rgb(47, 49, 54)"
        assert builtin_table.get_by_text("2026.01.03-030405", exact=True).count() == 0
        assert builtin_table.get_by_text("2026.01.04-040506", exact=True).count() == 1
        assert builtin_table.get_by_role("columnheader", name="World", exact=True).count() == 0
        assert builtin_table.get_by_role("columnheader", name="Type", exact=True).count() == 0
        assert builtin_table.get_by_role("button", name="Rollback", exact=True).count() == 1
        builtin_box = builtin_title.bounding_box()
        builtin_folder = panel.get_by_role(
            "button", name="Open built-in backup folder", exact=True
        )
        builtin_folder_box = builtin_folder.bounding_box()
        assert builtin_box is not None and builtin_folder_box is not None
        assert 0 <= builtin_folder_box["x"] - (builtin_box["x"] + builtin_box["width"]) <= 16
        managed_box = panel.get_by_text(
            "Managed backup files (2/20)", exact=True
        ).bounding_box()
        assert builtin_box is not None and managed_box is not None
        assert builtin_box["y"] < managed_box["y"]
        title_box = panel.get_by_text(
            "Managed backup files (2/20)", exact=True
        ).bounding_box()
        folder_box = panel.get_by_role("button", name="Open backup folder", exact=True).bounding_box()
        assert title_box is not None and folder_box is not None
        assert 0 <= folder_box["x"] - (title_box["x"] + title_box["width"]) <= 16
        table = page.locator("#pywebio-scope-backup_files table")
        # The 58rem scope uses border-box sizing and 8px padding on both sides.
        assert table.evaluate("element => getComputedStyle(element).width") == "912px"
        assert table.locator("tbody td").first.evaluate(
            "element => getComputedStyle(element).backgroundColor"
        ) == "rgb(47, 49, 54)"
        assert table.locator(".backup-file-name").first.evaluate(
            "element => getComputedStyle(element).color"
        ) == "rgb(248, 249, 250)"
        assert page.locator('input[name="settings_backup_interval_minutes"]').input_value() == "30"
        skip_toggle = page.locator("#pywebio-scope-backup_skip_no_players_toggle")
        skip_toggle.get_by_role("button", name="On", exact=True).click()
        skip_toggle.get_by_role("button", name="Off", exact=True).wait_for(timeout=2000)
        page.locator('input[name="settings_backup_interval_minutes"]').fill("15")
        page.locator('input[name="settings_backup_retention_count"]').fill("20")
        page.locator("#pywebio-scope-backup_settings_actions").get_by_role(
            "button", name="Save", exact=True
        ).click()
        page.get_by_text("Settings saved", exact=True).last.wait_for(timeout=5000)
        monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(config_dir))
        saved = load_profile("default")
        assert saved.backup_interval_minutes == 15
        assert saved.backup_retention_count == 20
        assert saved.skip_backup_when_no_players is False

        builtin_folder.click()
        deadline = time.time() + 5
        while not open_log.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert open_log.read_text(encoding="utf-8") == str(builtin_world.parent)

        panel.get_by_role("button", name="Open backup folder", exact=True).click()
        deadline = time.time() + 5
        while open_log.read_text(encoding="utf-8") != str(backup_dir) and time.time() < deadline:
            time.sleep(0.05)
        assert open_log.read_text(encoding="utf-8") == str(backup_dir)

        delete_row = panel.locator("tr", has_text=delete_file.name)
        delete_row.get_by_role("button", name="Delete backup", exact=True).click()
        page.get_by_text(f"Delete {delete_file.name}? This cannot be undone.", exact=True).wait_for(timeout=5000)
        page.locator(".modal.show").get_by_role("button", name="Delete backup", exact=True).click()
        panel.get_by_text("Managed backup files (1/20)", exact=True).wait_for(timeout=5000)
        assert not delete_file.exists()

        builtin_row = builtin_table.locator("tr", has_text=builtin_world.name)
        builtin_row.get_by_role("button", name="Rollback", exact=True).click()
        modal = page.locator(".modal.show")
        modal.get_by_text(
            "Palsitter must create a safety backup before restoring.", exact=False
        ).wait_for(timeout=5000)
        modal.get_by_role("button", name="Rollback", exact=True).click()
        page.get_by_text(
            f"Rollback complete: {builtin_world.name}", exact=True
        ).wait_for(timeout=10000)
        deadline = time.time() + 5
        while level.read_text(encoding="utf-8") != "world snapshot" and time.time() < deadline:
            time.sleep(0.05)
        assert level.read_text(encoding="utf-8") == "world snapshot"

        rollback_row = panel.locator("tr", has_text=rollback_file.name)
        rollback_row.get_by_role("button", name="Rollback", exact=True).click()
        modal = page.locator(".modal.show")
        modal.get_by_text(
            "Palsitter must create a safety backup before restoring.", exact=False
        ).wait_for(timeout=5000)
        modal.get_by_role("button", name="Rollback", exact=True).click()
        page.get_by_text(f"Rollback complete: {rollback_file.name}", exact=True).wait_for(timeout=10000)
        deadline = time.time() + 5
        while level.read_text(encoding="utf-8") != "restored" and time.time() < deadline:
            time.sleep(0.05)
        assert level.read_text(encoding="utf-8") == "restored"
        assert len(list(backup_dir.glob("*.zip"))) >= 2
        page.locator("#pywebio-scope-menu").get_by_text("Overview", exact=True).click()
        page.locator("#pywebio-scope-scheduler_panel").get_by_role(
            "button", name="Start", exact=True
        ).wait_for(timeout=5000)
        calls = steam_calls.read_text(encoding="ascii") if steam_calls.exists() else ""
        assert "+app_update 2394010" not in calls


@pytest.mark.playwright
def test_delayed_player_refresh_does_not_render_after_leaving_overview(tmp_path, monkeypatch):
    with _running_palserver_process(tmp_path), _mock_metrics_server(players_delay=1) as (rest_port, _):
        with _gui_page(tmp_path, monkeypatch, rest_port=rest_port) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text("default").click()
            page.locator("#pywebio-scope-players_panel").get_by_text(
                "Alice (Lv: 17)", exact=True
            ).wait_for(timeout=15000)

            page.locator("#pywebio-scope-players_auto_refresh button").evaluate(
                "button => button.click()"
            )
            page.locator("#pywebio-scope-aside").get_by_text("Home", exact=True).click()
            page.locator("#pywebio-scope-scheduler_panel").wait_for(state="detached", timeout=5000)
            page.wait_for_timeout(1500)

            assert page.locator("#pywebio-scope-content").get_by_text(
                re.compile(r"Alice \(Lv:")
            ).count() == 0


@pytest.mark.playwright
def test_rapid_navigation_keeps_only_latest_page_widgets(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, _):
        aside = page.locator("#pywebio-scope-aside")
        menu = page.locator("#pywebio-scope-menu")

        aside.get_by_text("default", exact=True).click()
        menu.get_by_text("Players", exact=True).click()
        menu.get_by_text("Overview", exact=True).click()
        aside.get_by_text("Home", exact=True).click()
        menu.get_by_text("Utils", exact=True).click()
        aside.get_by_text("default", exact=True).click()
        menu.get_by_text("Game Map", exact=True).click()
        menu.get_by_text("Overview", exact=True).click()

        page.locator("#pywebio-scope-overview").wait_for(timeout=10000)
        page.wait_for_timeout(1500)

        assert page.locator("#pywebio-scope-overview").count() == 1
        assert page.locator("#pywebio-scope-players_detail_panel").count() == 0
        assert page.locator("#palworld-map-viewport").count() == 0
        assert page.locator("#pywebio-scope-home_page").count() == 0
        assert page.locator("#pywebio-scope-util-buttons").count() == 0
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )


@pytest.mark.playwright
def test_audit_page_supports_monthly_rows_search_tags_time_and_pagination(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_PROFILE_DIR", str(tmp_path / "profile"))
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    store = AuditStore("default")
    for index in range(80):
        store.append(
            AuditEvent(
                now - dt.timedelta(minutes=index),
                "palsitter_command" if index % 2 == 0 else "server_start",
                f"audit-row-{index}",
            )
        )
    store.append(
        AuditEvent(now - dt.timedelta(days=2), "game_command", "older-game-command")
    )
    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        page.locator("#pywebio-scope-menu").get_by_text("Game Map", exact=True).click()
        page.locator("#palworld-map-viewport").wait_for(timeout=5000)
        page.locator("#pywebio-scope-menu").get_by_text("Audit", exact=True).click()
        audit = page.locator("#palworld-audit-table")
        audit.wait_for(timeout=5000)
        content = page.locator("#pywebio-scope-content")
        search_input_rect = audit.locator(".pagination-table-search input").bounding_box()
        tags_button_rect = audit.locator(".pagination-table-tags-button").bounding_box()
        time_button_rect = audit.locator(".pagination-table-time-button").bounding_box()
        page_size_rect = audit.locator(".pagination-table-page-size select").bounding_box()
        assert audit.locator(".pagination-table-search > span").count() == 0
        assert audit.locator(".pagination-table-search-icon").count() == 0
        assert audit.locator(".pagination-table-chip-chevron").count() == 0
        assert "btn-secondary" in audit.locator(".pagination-table-tags-button").get_attribute("class")
        assert "btn-secondary" in audit.locator(".pagination-table-time-button").get_attribute("class")
        assert audit.locator("table").evaluate(
            "element => getComputedStyle(element).tableLayout"
        ) == "auto"
        assert audit.locator("tbody td").first.evaluate(
            "element => getComputedStyle(element).verticalAlign"
        ) == "middle"
        navigation_rect = audit.locator(".pagination-table-navigation").bounding_box()
        audit_rect = audit.bounding_box()
        assert navigation_rect is not None and audit_rect is not None
        assert abs(
            navigation_rect["x"] + navigation_rect["width"] / 2
            - audit_rect["x"] - audit_rect["width"] / 2
        ) < 1
        table_shell = audit.locator(".pagination-table-shell")
        footer = audit.locator(".pagination-table-footer")
        assert table_shell.evaluate("element => getComputedStyle(element).overflowY") == "auto"
        footer_box = footer.bounding_box()
        viewport_size = page.evaluate(
            "() => ({width: document.documentElement.clientWidth, height: document.documentElement.clientHeight})"
        )
        assert footer_box is not None
        assert abs(footer_box["y"] + footer_box["height"] - viewport_size["height"]) < 16
        assert table_shell.evaluate("element => element.scrollHeight > element.clientHeight")
        assert abs(time_button_rect["y"] - page_size_rect["y"]) < 1
        assert abs(time_button_rect["y"] + time_button_rect["height"] - page_size_rect["y"] - page_size_rect["height"]) < 1
        for control_rect in (search_input_rect, tags_button_rect, time_button_rect):
            assert abs(
                control_rect["y"] + control_rect["height"]
                - page_size_rect["y"] - page_size_rect["height"]
            ) < 1
        assert audit.evaluate(
            "element => Boolean(element.closest('#pywebio-scope-content'))"
        )
        assert content.evaluate("element => element.scrollTop") == 0
        header = content.locator(".audit-page-header")
        assert header.get_by_role("heading", name="Audit logs", exact=True).count() == 1
        assert audit.bounding_box()["y"] > header.bounding_box()["y"]
        assert audit.get_by_role("columnheader").all_text_contents() == [
            "Timestamp", "Type", "Message"
        ]
        assert audit.locator("thead th").nth(1).bounding_box()["width"] >= 140
        first_header = audit.locator("thead th").nth(0)
        handle = first_header.locator(".pagination-table-resize-handle")
        before_width = first_header.bounding_box()["width"]
        handle_box = handle.bounding_box()
        page.mouse.move(handle_box["x"] + handle_box["width"] / 2, handle_box["y"] + 8)
        page.mouse.down()
        page.mouse.move(handle_box["x"] + handle_box["width"] / 2 + 40, handle_box["y"] + 8)
        page.mouse.up()
        assert first_header.bounding_box()["width"] > before_width + 20
        header_rects = audit.locator("thead th").evaluate_all(
            "cells => cells.map(cell => ({x: cell.getBoundingClientRect().x, width: cell.getBoundingClientRect().width}))"
        )
        body_rects = audit.locator("tbody tr").first.locator("td").evaluate_all(
            "cells => cells.map(cell => ({x: cell.getBoundingClientRect().x, width: cell.getBoundingClientRect().width}))"
        )
        for header_rect, body_rect in zip(header_rects, body_rects):
            assert abs(header_rect["x"] - body_rect["x"]) < 1
            assert abs(header_rect["width"] - body_rect["width"]) < 1
        assert audit.locator("tbody tr").count() == 25
        audit.locator("select[id$='-page-size']").select_option("10")
        assert audit.locator("tbody tr").count() == 10
        audit.get_by_role("button", name="Next", exact=True).click()
        assert content.evaluate("element => element.scrollTop") == 0
        assert audit.locator(".pagination-table-page-number").input_value() == "2"
        assert audit.get_by_role("button", name="First page", exact=True).count() == 1
        assert audit.get_by_role("button", name="Last page", exact=True).count() == 1
        assert audit.locator(".pagination-table-ellipsis").count() == 1
        audit.locator("input[type='search']").fill("older-game-command")
        assert audit.locator("tbody tr").count() == 1
        audit.locator(".pagination-table-time-button").click()
        time_popup = audit.locator(".pagination-table-time-popup")
        time_popup.wait_for()
        assert time_popup.evaluate(
            "element => getComputedStyle(element).position"
        ) == "fixed"
        time_button_box = audit.locator(".pagination-table-time-button").bounding_box()
        time_popup_box = time_popup.bounding_box()
        assert time_button_box is not None and time_popup_box is not None
        assert time_popup_box["y"] >= time_button_box["y"] + time_button_box["height"]
        viewport = page.evaluate(
            "() => ({width: document.documentElement.clientWidth, height: document.documentElement.clientHeight})"
        )
        assert time_popup_box["x"] >= 0
        assert time_popup_box["x"] + time_popup_box["width"] <= viewport["width"]
        assert time_popup_box["y"] + time_popup_box["height"] <= viewport["height"]
        time_popup.get_by_role(
            "button", name="Last 3 days", exact=True
        ).click()
        assert audit.locator("tbody tr").count() == 1
        audit.locator(".pagination-table-tags-button").click()
        popup = audit.locator(".pagination-table-tags-popup")
        popup.wait_for()
        assert popup.evaluate("element => getComputedStyle(element).position") == "fixed"
        popup_box = popup.bounding_box()
        assert popup_box is not None
        assert popup_box["x"] >= 0
        assert popup_box["x"] + popup_box["width"] <= viewport["width"]
        popup.get_by_text("Palsitter command", exact=True).wait_for(timeout=5000)
        popup.get_by_role("button", name="Select none", exact=True).click()
        assert popup.locator("input[data-tag-value]").evaluate_all(
            "inputs => inputs.every(input => !input.checked)"
        )
        popup.get_by_role("button", name="Select all", exact=True).click()
        assert popup.locator("input[data-tag-value]").evaluate_all(
            "inputs => inputs.every(input => input.checked)"
        )
        popup.locator("input[data-tag-value='game_command']").uncheck()
        assert audit.locator("tbody tr").count() == 0


@pytest.mark.playwright
def test_palworld_tools_checks_and_repairs_windows_firewall(tmp_path, monkeypatch):
    firewall_state = tmp_path / "firewall-state.txt"
    firewall_state.write_text("blocked", encoding="utf-8")
    with _gui_page(
        tmp_path,
        monkeypatch,
        extra_env={"PALSITTER_TEST_FIREWALL_STATE": str(firewall_state)},
    ) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        page.locator("#pywebio-scope-menu").get_by_text("Tools", exact=True).click()

        tools_panel = page.locator("#pywebio-scope-tools_panel")
        tools_panel.wait_for(timeout=5000)
        tools_panel.get_by_text("PalServer.exe", exact=False).wait_for(timeout=5000)
        tools_panel.get_by_text("8211", exact=True).wait_for(timeout=5000)
        page.locator("#pywebio-scope-tools_status").get_by_text(
            "Not checked", exact=True
        ).wait_for(timeout=5000)

        tools_panel.get_by_role("button", name="Check", exact=True).click()
        modal = page.locator(".modal.show")
        modal.get_by_text("Fix Firewall", exact=True).wait_for(timeout=5000)
        modal.get_by_text("Administrator approval is required", exact=False).wait_for(
            timeout=5000
        )
        modal.get_by_role("button", name="Fix", exact=True).click()

        page.locator("#pywebio-scope-tools_status").get_by_text(
            "Open", exact=True
        ).wait_for(timeout=5000)
        assert firewall_state.read_text(encoding="utf-8") == "open"
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )


@pytest.mark.playwright
def test_palworld_tools_requests_root_password_after_permission_denied(tmp_path, monkeypatch):
    firewall_state = tmp_path / "firewall-state.txt"
    firewall_state.write_text("blocked", encoding="utf-8")
    with _gui_page(
        tmp_path,
        monkeypatch,
        extra_env={
            "PALSITTER_TEST_FIREWALL_STATE": str(firewall_state),
            "PALSITTER_TEST_FIREWALL_REQUIRE_PASSWORD": "1",
        },
    ) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        page.locator("#pywebio-scope-menu").get_by_text("Tools", exact=True).click()
        tools_panel = page.locator("#pywebio-scope-tools_panel")
        tools_panel.get_by_role("button", name="Check", exact=True).click()

        fix_modal = page.locator(".modal.show")
        fix_modal.get_by_role("button", name="Fix", exact=True).click()
        password_modal = page.locator(".modal.show")
        password_modal.get_by_text("Administrator authentication", exact=True).wait_for(
            timeout=5000
        )
        password_modal.get_by_label("Root password", exact=True).fill("root-secret")
        password_modal.get_by_role("button", name="Fix", exact=True).click()

        page.locator("#pywebio-scope-tools_status").get_by_text(
            "Open", exact=True
        ).wait_for(timeout=5000)
        assert firewall_state.read_text(encoding="utf-8") == "open"


@pytest.mark.playwright
def test_palworld_tools_requests_root_password_when_check_is_denied(tmp_path, monkeypatch):
    firewall_state = tmp_path / "firewall-state.txt"
    firewall_state.write_text("open", encoding="utf-8")
    with _gui_page(
        tmp_path,
        monkeypatch,
        extra_env={
            "PALSITTER_TEST_FIREWALL_STATE": str(firewall_state),
            "PALSITTER_TEST_FIREWALL_CHECK_REQUIRE_PASSWORD": "1",
        },
    ) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        page.locator("#pywebio-scope-menu").get_by_text("Tools", exact=True).click()
        tools_panel = page.locator("#pywebio-scope-tools_panel")
        tools_panel.get_by_role("button", name="Check", exact=True).click()

        password_modal = page.locator(".modal.show")
        password_modal.get_by_text("Administrator authentication", exact=True).wait_for(
            timeout=5000
        )
        password_modal.get_by_text(
            "sudo firewall-cmd --get-active-zones", exact=False
        ).wait_for(timeout=5000)
        password_modal.get_by_label("Root password", exact=True).fill("root-secret")
        password_modal.get_by_role("button", name="Check", exact=True).click()

        page.locator("#pywebio-scope-tools_status").get_by_text(
            "Open", exact=True
        ).wait_for(timeout=5000)


@pytest.mark.playwright
def test_palworld_tools_check_does_not_update_after_navigation(tmp_path, monkeypatch):
    firewall_state = tmp_path / "firewall-state.txt"
    firewall_state.write_text("open", encoding="utf-8")
    with _gui_page(
        tmp_path,
        monkeypatch,
        extra_env={
            "PALSITTER_TEST_FIREWALL_STATE": str(firewall_state),
            "PALSITTER_TEST_FIREWALL_DELAY": "1.0",
        },
    ) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        page.locator("#pywebio-scope-menu").get_by_text("Tools", exact=True).click()
        tools_panel = page.locator("#pywebio-scope-tools_panel")
        tools_panel.get_by_role("button", name="Check", exact=True).click()
        page.wait_for_timeout(100)
        page.locator("#pywebio-scope-menu").get_by_text("Overview", exact=True).click()
        page.locator("#pywebio-scope-overview").wait_for(timeout=5000)
        page.wait_for_timeout(1500)

        assert page.get_by_text("Executable rule", exact=True).count() == 0
        assert page.get_by_text("UDP port rule", exact=True).count() == 0


@pytest.mark.playwright
def test_palworld_tools_player_migration_uses_real_selection_and_confirmation(
    tmp_path, monkeypatch
):
    world_id = "D" * 32
    source = _fixed_backup_source(tmp_path)
    world = source / world_id
    players = world / "Players"
    players.mkdir(parents=True)
    (world / "Level.sav").write_bytes(b"fake level")
    old_player = "00000000000000000000000000000001.sav"
    new_player = "8E910AC2000000000000000000000000.sav"
    (players / old_player).write_bytes(b"fake old player")
    (players / new_player).write_bytes(b"fake new player")
    (world / ".palsitter-player-names.json").write_text(
        json.dumps({old_player[:-4]: "Original", new_player[:-4]: "New"}),
        encoding="utf-8",
    )

    with _gui_page(
        tmp_path,
        monkeypatch,
        profile_overrides={
            "dedicated_server_name": world_id,
        },
    ) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        page.locator("#pywebio-scope-menu").get_by_text("Tools", exact=True).click()
        migration = page.locator("#pywebio-scope-tools_migration")
        migration.get_by_text("Palworld player ID migration", exact=True).wait_for(
            timeout=5000
        )
        assert migration.get_by_label(
            "Original player save", exact=True
        ).locator("option").all_text_contents() == [
            f"Original — {old_player}",
            f"New — {new_player}",
        ]
        page.get_by_label("Original player save", exact=True).select_option(
            label=f"Original — {old_player}"
        )
        page.get_by_label("New server player save", exact=True).select_option(
            label=f"New — {new_player}"
        )
        migration.get_by_role("button", name="Migrate player ID", exact=True).click()

        modal = page.locator(".modal.show")
        modal.get_by_text(old_player, exact=False).wait_for(timeout=5000)
        modal.get_by_text(new_player, exact=False).wait_for(timeout=5000)
        modal.get_by_role("button", name="Cancel", exact=True).click()
        modal.wait_for(state="hidden", timeout=5000)

        migration.get_by_role("button", name="Migrate player ID", exact=True).click()
        page.locator(".modal.show").get_by_role(
            "button", name="Migrate player ID", exact=True
        ).click()
        migration.get_by_text(
            re.compile("(Player migration is unavailable|Could not migrate the player ID)"),
            exact=False,
        ).wait_for(timeout=10000)


@pytest.mark.playwright
def test_palworld_tools_build_player_name_cache_uses_confirmation_flow(
    tmp_path, monkeypatch
):
    world_id = "D" * 32
    source = _fixed_backup_source(tmp_path)
    world = source / world_id
    players = world / "Players"
    players.mkdir(parents=True)
    (world / "Level.sav").write_bytes(b"fake level")
    (players / "00000000000000000000000000000001.sav").write_bytes(b"old")

    with _gui_page(
        tmp_path,
        monkeypatch,
        profile_overrides={"dedicated_server_name": world_id},
    ) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text(
            "default", exact=True
        ).click()
        page.locator("#pywebio-scope-menu").get_by_text(
            "Tools", exact=True
        ).click()
        migration = page.locator("#pywebio-scope-tools_migration")
        migration.get_by_role(
            "button", name="Build player name cache", exact=True
        ).click()
        modal = page.locator(".modal.show")
        modal.get_by_text(
            "This reads Level.sav and creates an ID-to-name cache", exact=False
        ).wait_for(timeout=5000)
        modal.get_by_role(
            "button", name="Build player name cache", exact=True
        ).click()
        migration.get_by_text(
            re.compile("(Could not build the player name cache|Player name cache is unavailable)"),
            exact=False,
        ).wait_for(timeout=10000)


@pytest.mark.playwright
def test_palworld_tools_disables_player_migration_while_server_runs(
    tmp_path, monkeypatch
):
    world_id = "D" * 32
    source = _fixed_backup_source(tmp_path)
    world = source / world_id
    players = world / "Players"
    players.mkdir(parents=True)
    (world / "Level.sav").write_bytes(b"fake level")
    (players / "00000000000000000000000000000001.sav").write_bytes(b"old")
    (players / "8E910AC2000000000000000000000000.sav").write_bytes(b"new")

    with _running_palserver_process(tmp_path):
        with _gui_page(
            tmp_path,
            monkeypatch,
            profile_overrides={"dedicated_server_name": world_id},
        ) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text(
                "default", exact=True
            ).click()
            page.locator("#pywebio-scope-menu").get_by_text(
                "Tools", exact=True
            ).click()
            migration = page.locator("#pywebio-scope-tools_migration")
            migration.get_by_text(
                "Palworld player ID migration", exact=True
            ).wait_for(timeout=5000)
            assert migration.get_by_role(
                "button", name="Migrate player ID", exact=True
            ).is_disabled()


@pytest.mark.playwright
def test_game_map_places_overlays_and_centers_from_cached_players(tmp_path, monkeypatch):
    get_calls = []
    players = [
        {
            "name": "Alice",
            "userId": "steam_1",
            "level": 10,
            "location_x": -358799.53125,
            "location_y": 267952.1875,
        },
        {
            "name": "Bob",
            "userId": "steam_2",
            "level": 20,
            "location_x": 117637.140625,
            "location_y": 400116.03125,
        },
        {
            "name": "Tree Walker",
            "userId": "steam_3",
            "level": 30,
            "location_x": 621792.625,
            "location_y": -757916.125,
        },
    ]
    with _running_palserver_process(tmp_path), _mock_metrics_server(
        players_override=players,
        game_data_override={
            "ActorData": [
                {
                    "Type": "Palbox",
                    "GuildName": "Moon Guild",
                    "LocationX": -100000,
                    "LocationY": 0,
                },
                {
                    "Type": "Character",
                    "UnitType": "Player",
                    "userid": "steam_1",
                    "GuildID": "guild-a",
                },
                {
                    "Type": "Character",
                    "UnitType": "Player",
                    "userid": "steam_2",
                    "GuildID": "guild-a",
                },
                {
                    "Type": "Character",
                    "UnitType": "Player",
                    "userid": "steam_3",
                    "GuildID": "guild-b",
                },
            ]
        },
        get_calls=get_calls,
    ) as (rest_port, _):
        with _gui_page(tmp_path, monkeypatch, rest_port=rest_port) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
            page.locator("#pywebio-scope-menu").get_by_text("Game Map", exact=True).click()
            map_page = page.locator("#pywebio-scope-map_page")
            viewport = page.locator("#palworld-map-viewport")
            viewport.wait_for(timeout=5000)
            assert page.locator(".palworld-map-tile").count() == 414
            assert page.locator(".palworld-map-fallback-tile").count() == 4
            assert page.locator(".palworld-map-layer[data-map-name='palpagos'] .palworld-map-poi[data-poi-type='Fast Travel']").count() == 137
            assert page.locator(".palworld-map-layer[data-map-name='palpagos'] .palworld-map-poi[data-poi-type='Watchtower']").count() == 20
            assert page.locator(".palworld-map-layer[data-map-name='world-tree'] .palworld-map-poi[data-poi-type='Fast Travel']").count() == 15
            assert page.locator(".palworld-map-layer[data-map-name='world-tree'] .palworld-map-poi[data-poi-type='Watchtower']").count() == 2
            assert page.locator(".palworld-map-layer[data-map-name='world-tree'] .palworld-map-tile").count() == 158
            assert page.locator(".palworld-map-layer[data-map-name='world-tree'] .palworld-map-fallback-tile").count() == 4
            palbox = page.locator(".palworld-map-palbox").first
            palbox.wait_for(timeout=10000)
            assert palbox.get_attribute("src").endswith("/static/gui/map/home.webp")
            assert "/v1/api/game-data" in get_calls
            palbox.hover()
            assert palbox.locator("xpath=..").locator(
                ".palworld-map-tooltip"
            ).inner_text() == "Palbox: Moon Guild"
            guild_color = page.locator('.palworld-map-player-dot[data-player-id="steam_1"]').get_attribute(
                "data-guild-color"
            )
            assert guild_color in {"red", "blue", "green", "yellow", "purple", "teal", "gray", "orange"}
            assert page.locator('.palworld-map-player-dot[data-player-id="steam_2"]').get_attribute(
                "data-guild-color"
            ) == guild_color
            assert page.locator(".palworld-map-layer[data-map-name='world-tree'] img[src$='z4x8y0.webp']").count() == 0
            assert not page.locator("#palworld-map-select option[value='world-tree']").is_disabled()

            page.locator("#palworld-map-player-button").get_by_text("Players (2)", exact=True).wait_for(
                timeout=10000
            )
            player_button = page.locator("#palworld-map-player-button")
            player_overlay = page.locator("#palworld-map-top-right")
            assert player_button.evaluate(
                "element => getComputedStyle(element).display"
            ) == "block"
            button_box = player_button.bounding_box()
            overlay_box = player_overlay.bounding_box()
            assert button_box is not None and overlay_box is not None
            assert button_box["y"] + button_box["height"] == pytest.approx(
                overlay_box["y"] + overlay_box["height"] - 1, abs=1
            )
            page.locator("#palworld-map-player-button").click()
            dropdown = page.locator("#palworld-map-player-dropdown")
            dropdown.get_by_text("Alice (Lv. 10)", exact=True).wait_for(timeout=10000)
            dropdown.get_by_text("Bob (Lv. 20)", exact=True).wait_for(timeout=10000)
            assert dropdown.get_by_text("Tree Walker (Lv. 30)", exact=True).count() == 0
            page.wait_for_timeout(5000)
            assert dropdown.is_visible()
            assert page.locator(".palworld-map-bottom-left").count() == 0
            assert dropdown.get_by_text("267952", exact=False).count() == 0
            assert page.locator(".palworld-map-player-dot").count() == 2
            assert page.locator(".palworld-map-player-dot").first.evaluate(
                "element => [element.offsetWidth, element.offsetHeight]"
            ) == [16, 16]
            assert page.locator(".palworld-map-poi").first.evaluate(
                "element => element.getBoundingClientRect().width"
            ) == pytest.approx(48)
            assert page.locator(".palworld-map-poi").first.evaluate(
                "element => getComputedStyle(element).backgroundColor"
            ) in ("rgba(0, 0, 0, 0)", "transparent")
            marker_name = page.locator(".palworld-map-poi-wrap").first.get_attribute(
                "data-location-name"
            )
            assert marker_name and not re.search(r"\s[-+]?\d+\s*,\s*[-+]?\d+\s*$", marker_name)
            visible_marker_index = page.locator(".palworld-map-poi-wrap").evaluate_all(
                """
                elements => {
                    const viewport = document.querySelector('#palworld-map-viewport').getBoundingClientRect();
                    return elements.findIndex(element => {
                        const box = element.getBoundingClientRect();
                        if (!(box.right > viewport.left && box.left < viewport.right
                            && box.bottom > viewport.top && box.top < viewport.bottom)) return false;
                        const target = document.elementFromPoint(
                            (box.left + box.right) / 2, (box.top + box.bottom) / 2
                        );
                        return target === element || element.contains(target);
                    });
                }
                """
            )
            assert visible_marker_index >= 0
            visible_marker = page.locator(".palworld-map-poi-wrap").nth(visible_marker_index)
            visible_marker.hover()
            assert visible_marker.locator(".palworld-map-tooltip").evaluate(
                "element => getComputedStyle(element).visibility"
            ) == "visible"
            tooltip_text = visible_marker.locator(".palworld-map-tooltip").inner_text()
            assert tooltip_text == visible_marker.get_attribute("data-location-name")
            assert not re.search(r"\s[-+]?\d+\s*,\s*[-+]?\d+\s*$", tooltip_text)
            assert page.locator(".palworld-map-player-dot").first.get_attribute(
                "data-player-name"
            ) == "Alice"
            page.locator(".palworld-map-player-dot").first.hover()
            assert page.locator(".palworld-map-player-dot").first.evaluate(
                "element => getComputedStyle(element, '::after').content"
            ) == '"Alice"'

            player_list = page.locator("#palworld-map-player-list")
            page.evaluate(
                "players => window.Palsitter.palworld.map.pushPlayers({players, state: 'live'})",
                [
                    {
                        "userId": "steam_1",
                        "name": "Alice",
                        "level": 10,
                        "x": 5611,
                        "y": 4004,
                        "valid": True,
                        "map": "palpagos",
                    },
                    {
                        "userId": "steam_2",
                        "name": "Bob",
                        "level": 20,
                        "x": 6358,
                        "y": 1310,
                        "valid": True,
                        "map": "palpagos",
                    },
                    {
                        "userId": "steam_3",
                        "name": "Tree Walker",
                        "level": 30,
                        "x": 1445,
                        "y": 1614,
                        "valid": True,
                        "map": "world-tree",
                    },
                ],
            )
            assert dropdown.is_visible()
            assert page.locator("#palworld-map-player-button").get_attribute("aria-expanded") == "true"

            before_player_click = get_calls.count("/v1/api/players")
            dropdown.get_by_text("Bob (Lv. 20)", exact=True).click()
            assert page.locator("#palworld-map-player-dropdown").is_hidden()
            assert get_calls.count("/v1/api/players") == before_player_click
            assert float(viewport.get_attribute("data-camera-center-x")) == pytest.approx(
                6358, abs=2
            )
            assert float(viewport.get_attribute("data-camera-center-y")) == pytest.approx(
                1310, abs=2
            )

            page.locator("#palworld-map-select").select_option("world-tree")
            assert page.locator("#palworld-map-viewport").get_attribute("data-map-name") == "world-tree"
            page.locator("#palworld-map-player-button").get_by_text("Players (1)", exact=True).wait_for(
                timeout=5000
            )
            page.locator("#palworld-map-player-button").click()
            dropdown.get_by_text("Tree Walker (Lv. 30)", exact=True).wait_for(timeout=5000)
            assert dropdown.get_by_text("Alice (Lv. 10)", exact=True).count() == 0
            assert page.locator(".palworld-map-layer[data-map-name='world-tree']").is_visible()
            assert page.locator(".palworld-map-layer[data-map-name='palpagos']").is_hidden()

            before_zoom = float(viewport.get_attribute("data-zoom"))
            viewport.hover()
            page.mouse.wheel(0, -500)
            assert float(viewport.get_attribute("data-zoom")) > before_zoom
            assert page.locator(".palworld-map-layer[data-map-name='world-tree'] .palworld-map-poi").first.evaluate(
                "element => element.getBoundingClientRect().width"
            ) == pytest.approx(48, abs=0.1)
            assert page.locator(".palworld-map-player-dot").first.evaluate(
                "element => element.getBoundingClientRect().width"
            ) == pytest.approx(16, abs=0.1)

            before_drag = float(viewport.get_attribute("data-camera-center-x"))
            box = viewport.bounding_box()
            assert box is not None
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] / 2 + 80, box["y"] + box["height"] / 2)
            page.mouse.up()
            assert float(viewport.get_attribute("data-camera-center-x")) != pytest.approx(
                before_drag
            )

            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
            )
            assert map_page.bounding_box() is not None
            page.locator("#palworld-map-player-button").click()
            page.keyboard.press("Escape")
            assert page.locator("#palworld-map-player-dropdown").is_hidden()
            page.locator("#pywebio-scope-menu").get_by_text("Overview", exact=True).click()
            page.locator("#pywebio-scope-scheduler_panel").wait_for(timeout=5000)
            assert "map-content" not in (page.locator("#pywebio-scope-content").get_attribute("class") or "")


@pytest.mark.playwright
@pytest.mark.parametrize(
    ("language", "map_label", "map_name", "expected_marker"),
    [
        ("zh-TW", "遊戲地圖", "palpagos", "忘卻孤島"),
        ("ja-JP", "ゲームマップ", "palpagos", "忘れられた孤島"),
        ("zh-TW", "遊戲地圖", "world-tree", "腐蝕霧的源頭"),
        ("ja-JP", "ゲームマップ", "world-tree", "腐蝕霧の根源"),
    ],
)
def test_game_map_uses_paldb_localized_marker_names(
    tmp_path, monkeypatch, language, map_label, map_name, expected_marker
):
    with _running_palserver_process(tmp_path), _mock_metrics_server() as (rest_port, _):
        with _gui_page(
            tmp_path,
            monkeypatch,
            rest_port=rest_port,
            preferred_language=language,
        ) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text(
                "default", exact=True
            ).click()
            page.locator("#pywebio-scope-menu").get_by_text(
                map_label, exact=True
            ).click()
            viewport = page.locator("#palworld-map-viewport")
            viewport.wait_for(timeout=5000)
            if map_name == "world-tree":
                page.locator("#palworld-map-select").select_option("world-tree")
                assert viewport.get_attribute("data-map-name") == "world-tree"
            marker = page.locator(
                f".palworld-map-layer[data-map-name='{map_name}'] .palworld-map-poi-wrap"
            ).first
            assert marker.get_attribute("data-location-name") == expected_marker
            visible_marker_index = page.locator(".palworld-map-poi-wrap").evaluate_all(
                """
                elements => {
                    const viewport = document.querySelector('#palworld-map-viewport').getBoundingClientRect();
                    return elements.findIndex(element => {
                        const box = element.getBoundingClientRect();
                        if (!(box.right > viewport.left && box.left < viewport.right
                            && box.bottom > viewport.top && box.top < viewport.bottom)) return false;
                        const target = document.elementFromPoint(
                            (box.left + box.right) / 2, (box.top + box.bottom) / 2
                        );
                        return target === element || element.contains(target);
                    });
                }
                """
            )
            assert visible_marker_index >= 0
            visible_marker = page.locator(".palworld-map-poi-wrap").nth(visible_marker_index)
            visible_marker.hover()
            assert visible_marker.locator(".palworld-map-tooltip").inner_text() == (
                visible_marker.get_attribute("data-location-name")
            )


@pytest.mark.playwright
def test_overview_check_update_logs_result_and_shows_update_marker(tmp_path, monkeypatch):
    _prepare_fixed_palserver_python(tmp_path)
    _prepare_fixed_steamcmd(tmp_path)
    manifest = _fixed_palserver_dir(tmp_path) / "steamapps" / "appmanifest_2394010.acf"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('"buildid" "12345"', encoding="utf-8")
    steam_calls = tmp_path / "overview-check-steamcmd.txt"

    with _gui_page(
        tmp_path,
        monkeypatch,
        extra_env={"PALSITTER_FAKE_STEAMCMD_CALLS": str(steam_calls)},
    ) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        page.locator("#pywebio-scope-scheduler_panel").wait_for(timeout=5000)
        check_update = page.get_by_role("button", name="Check update", exact=True)
        filter_button = page.get_by_role("button", name="Filter", exact=True)
        assert check_update.bounding_box()["x"] < filter_button.bounding_box()["x"]

        check_update.click()
        page.locator(".log-box").get_by_text(
            "Palworld is up to date at build 12345", exact=False
        ).wait_for(timeout=10000)
        page.evaluate(
            "() => window.Palsitter.palworld.overview.updateMetrics({"
            "values: {'game-version': 'v1.2.3'}, updateAvailable: true, "
            "updateTooltip: 'update available: 12345 → 67890'})"
        )
        marker = page.locator('[data-metric="game-version"] .metric-update-available')
        marker.wait_for(timeout=5000)
        assert marker.get_attribute("data-tooltip") == "update available: 12345 → 67890"
        marker.hover()
        assert marker.evaluate(
            "element => getComputedStyle(element, '::after').content"
        ) == '"update available: 12345 → 67890"'


@pytest.mark.playwright
def test_overview_uses_persisted_version_and_build_when_server_is_down(
    tmp_path, monkeypatch
):
    _prepare_fixed_palserver_python(tmp_path)
    manifest = _fixed_palserver_dir(tmp_path) / "steamapps" / "appmanifest_2394010.acf"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('"buildid" "12345"', encoding="utf-8")
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    update_version_cache(
        "default",
        game_version="v9.9.9",
        installed_build_id="12345",
        status="up_to_date",
    )

    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.get_by_text("Game Version: v9.9.9", exact=False).wait_for(timeout=10000)
        page.get_by_text("Build: 12345", exact=False).wait_for(timeout=5000)
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        page.locator('[data-metric="game-version"]').get_by_text(
            "v9.9.9", exact=True
        ).wait_for(timeout=5000)


@pytest.mark.playwright
def test_overview_log_append_preserves_text_selection(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        log_box = page.locator(".log-box")
        log_box.get_by_text("No log output yet.").wait_for(timeout=5000)

        page.locator('input[name="console_command"]').fill("selected-entry")
        page.get_by_role("button", name="Run", exact=True).click()
        selected_text = "Unknown console command: selected-entry"
        log_box.get_by_text(selected_text).wait_for(timeout=5000)

        page.locator('input[name="console_command"]').fill("new-entry")
        page.get_by_role("button", name="Run", exact=True).click()
        assert log_box.evaluate(
            """
            (node, text) => {
                const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
                let textNode;
                while ((textNode = walker.nextNode())) {
                    const start = textNode.data.indexOf(text);
                    if (start !== -1) {
                        const range = document.createRange();
                        range.setStart(textNode, start);
                        range.setEnd(textNode, start + text.length);
                        const selection = window.getSelection();
                        selection.removeAllRanges();
                        selection.addRange(range);
                        return selection.toString();
                    }
                }
                return "";
            }
            """,
            selected_text,
        ) == selected_text

        log_box.get_by_text("Unknown console command: new-entry").wait_for(timeout=5000)
        assert page.evaluate("() => window.getSelection().toString()") == selected_text


@pytest.mark.playwright
def test_overview_replays_persisted_server_logs_after_gui_start(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("PALSITTER_PROFILE_DIR", str(tmp_path / "profile"))
    log_path = profile_log_path("default")
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "12:00:00 [default] PalServer: Running Palworld dedicated server on :8211\n",
        encoding="utf-8",
    )

    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()

        page.locator("#overview-log-box").get_by_text(
            "Running Palworld dedicated server on :8211",
        ).wait_for(timeout=5000)


@pytest.mark.playwright
def test_running_header_receives_new_palserver_output(tmp_path, monkeypatch):
    _prepare_fixed_palserver_python(tmp_path)
    _prepare_fixed_steamcmd(tmp_path)
    steam_calls = tmp_path / "live-log-steamcmd.txt"
    launch_script = (
        "import time; "
        "print('initial server output', flush=True); "
        "time.sleep(4); "
        "print('[2026-07-21 12:00:00] [LOG] REST accessed endpoint "
        "/v1/api/players OK', flush=True); "
        "time.sleep(60)"
    )

    with _mock_metrics_server(
        shutdown_executable=_fixed_palserver_executable(tmp_path)
    ) as (rest_port, _):
        with _gui_page(
            tmp_path,
            monkeypatch,
            rest_port=rest_port,
            profile_overrides={
                "executable_args": ["-c", launch_script],
                "launch_enable_gamedata_api": False,
                "shutdown_wait_seconds": 0,
            },
            extra_env={"PALSITTER_FAKE_STEAMCMD_CALLS": str(steam_calls)},
        ) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text("default").click()
            scheduler = page.locator("#pywebio-scope-scheduler_panel")
            scheduler.get_by_role("button", name="Start", exact=True).click()

            page.locator("#pywebio-scope-header_status").get_by_text(
                "Running", exact=True
            ).wait_for(timeout=10000)
            log = page.locator("#overview-log-box")
            log.get_by_text("PalServer: initial server output", exact=False).wait_for(
                timeout=5000
            )
            previous_players = log.get_by_text(
                "REST accessed endpoint /v1/api/players OK", exact=False
            ).count()
            assert previous_players == 0
            log.get_by_text(
                "REST accessed endpoint /v1/api/players OK", exact=False
            ).nth(previous_players).wait_for(timeout=5000)

            stop = scheduler.get_by_role("button", name="Stop", exact=True)
            if stop.count() and stop.is_visible():
                stop.click()
                kill = scheduler.get_by_role("button", name="KILL", exact=True)
                kill.wait_for(timeout=5000)
                kill.click()


@pytest.mark.playwright
def test_overview_log_type_filter_hides_stable_rows_and_applies_to_appends(
    tmp_path, monkeypatch
):
    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        log_box = page.locator("#overview-log-box")
        log_box.get_by_text("No log output yet.").wait_for(timeout=5000)

        console = page.locator('input[name="console_command"]')
        console.fill("filter-existing")
        page.get_by_role("button", name="Run", exact=True).click()
        existing = log_box.locator(
            '.overview-log-line[data-log-type="palsitter"]',
            has_text="Unknown console command: filter-existing",
        )
        existing.wait_for(timeout=5000)
        initial_count = log_box.locator(".overview-log-line").count()
        page.evaluate(
            """
            () => {
                window.__filterLogBox = document.querySelector('#overview-log-box');
                window.__filterExistingRow = Array.from(
                    document.querySelectorAll('.overview-log-line')
                ).find(row => row.textContent.includes('filter-existing'));
            }
            """
        )

        page.locator("#pywebio-scope-log_bar").get_by_role(
            "button", name="Filter", exact=True
        ).click()
        modal = page.locator(".modal.show")
        modal.get_by_text("Filter log types", exact=True).wait_for(timeout=5000)
        checkboxes = modal.locator('input.overview-log-filter-checkbox[type="checkbox"]')
        assert checkboxes.count() == 4
        assert modal.locator(".overview-log-filter-option").all_inner_texts() == [
            "Palsitter",
            "PalServer",
            "SteamCMD",
            "UE4SS",
        ]
        assert modal.get_by_text("All", exact=True).count() == 0
        assert all(checkboxes.nth(index).is_checked() for index in range(4))
        modal.get_by_role("button", name="Select none", exact=True).click()
        assert not any(checkboxes.nth(index).is_checked() for index in range(4))
        modal.get_by_role("button", name="Select all", exact=True).click()
        assert all(checkboxes.nth(index).is_checked() for index in range(4))

        modal.get_by_label("Palsitter", exact=True).uncheck()
        assert page.evaluate("() => !window.__filterExistingRow.isConnected")
        assert existing.count() == 0
        assert log_box.locator(".overview-log-line").count() < initial_count
        assert page.evaluate(
            """
            () => document.querySelector('#overview-log-box') === window.__filterLogBox
                && !window.__filterExistingRow.isConnected
            """
        )
        modal.get_by_text("Close", exact=True).click()

        console.fill("filter-appended")
        page.get_by_role("button", name="Run", exact=True).click()
        appended = log_box.locator(
            '.overview-log-line[data-log-type="palsitter"]',
            has_text="Unknown console command: filter-appended",
        )
        page.wait_for_timeout(1500)
        assert appended.count() == 0
        assert page.evaluate(
            "() => window.Palsitter.palworld.overview.logContains({text: 'filter-appended'})"
        )
        assert page.evaluate(
            "() => document.querySelector('#overview-log-box') === window.__filterLogBox"
        )

        page.locator("#pywebio-scope-log_bar").get_by_role(
            "button", name="Filter", exact=True
        ).click()
        modal = page.locator(".modal.show")
        palsitter_filter = modal.get_by_label("Palsitter", exact=True)
        assert not palsitter_filter.is_checked()
        palsitter_filter.check()
        existing.wait_for(state="attached", timeout=5000)
        appended.wait_for(state="attached", timeout=5000)
        assert existing.evaluate("row => getComputedStyle(row).display") == "block"
        assert appended.evaluate("row => getComputedStyle(row).display") == "block"
        assert page.evaluate(
            "() => window.__filterExistingRow === Array.from(document.querySelectorAll('.overview-log-line')).find(row => row.textContent.includes('filter-existing'))"
        )
        modal.get_by_text("Close", exact=True).click()

        page.locator("#pywebio-scope-menu").get_by_text(
            "Server Settings", exact=True
        ).click()
        page.locator("#pywebio-scope-menu").get_by_text("Overview", exact=True).click()
        page.locator("#overview-log-box").wait_for(timeout=5000)
        page.locator("#pywebio-scope-log_bar").get_by_role(
            "button", name="Filter", exact=True
        ).click()
        reset_modal = page.locator(".modal.show")
        reset_modal.get_by_text("Filter log types", exact=True).wait_for(timeout=5000)
        reset_checkboxes = reset_modal.locator(
            'input.overview-log-filter-checkbox[type="checkbox"]'
        )
        assert reset_checkboxes.count() == 4
        assert all(reset_checkboxes.nth(index).is_checked() for index in range(4))


@pytest.mark.playwright
def test_overview_log_auto_scroll_off_preserves_manual_scroll(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        log_box = page.locator(".log-box")
        log_box.get_by_text("No log output yet.").wait_for(timeout=5000)

        seeded = log_box.evaluate(
            """
            (node) => {
                node.replaceChildren();
                node.dataset.empty = 'false';
                for (let index = 0; index < 120; index += 1) {
                    const row = document.createElement('span');
                    row.className = 'overview-log-line';
                    row.dataset.logType = 'palsitter';
                    row.textContent = `seed line ${index}`;
                    node.appendChild(row);
                }
                node.scrollTop = 24;
                return {
                    top: node.scrollTop,
                    max: node.scrollHeight - node.clientHeight,
                };
            }
            """
        )
        assert seeded["max"] > seeded["top"]

        page.locator("#pywebio-scope-log_bar").get_by_role(
            "button", name="Auto Scroll ON", exact=True
        ).click()
        page.locator("#pywebio-scope-log_bar").get_by_role(
            "button", name="Auto Scroll OFF", exact=True
        ).wait_for(timeout=2000)
        before = log_box.evaluate("(node) => node.scrollTop")

        page.locator('input[name="console_command"]').fill("autoscroll-off-test")
        page.get_by_role("button", name="Run", exact=True).click()
        log_box.get_by_text(
            "Unknown console command: autoscroll-off-test"
        ).wait_for(timeout=5000)

        after = log_box.evaluate(
            "(node) => ({ top: node.scrollTop, max: node.scrollHeight - node.clientHeight })"
        )
        assert after["top"] == before
        assert after["top"] < after["max"]


@pytest.mark.playwright
def test_instance_panel_titles_do_not_repeat_instance_name(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()

        for menu_label, panel_scope, expected_title in (
            ("Server Settings", "settings_panel", "Settings"),
            ("Auto Restart", "auto_restart_panel", "Auto Restart"),
            ("World Settings", "world_settings_panel", "World Settings"),
            ("Saves & Backups", "backup_settings_panel", "Saves & Backups"),
        ):
            page.locator("#pywebio-scope-menu").get_by_text(
                menu_label, exact=True
            ).click()
            title = page.locator(f"#pywebio-scope-{panel_scope} .panel-title").first
            title.wait_for(timeout=5000)
            assert title.inner_text() == expected_title
            assert "default" not in title.inner_text()


@pytest.mark.playwright
def test_server_settings_are_embedded_and_save(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    profile_overrides = {
        "backup_dir": str(backup_dir),
    }

    with _gui_page(tmp_path, monkeypatch, profile_overrides=profile_overrides) as (page, config_dir):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        page.locator("#pywebio-scope-menu").get_by_text("Server Settings").click()

        page.locator("#pywebio-scope-settings_form").wait_for(timeout=5000)
        assert page.locator(".modal.show").count() == 0
        query_label_box = page.locator("#pywebio-scope-settings_form").locator("label").filter(
            has_text="Query port"
        ).first.bounding_box()
        query_input_box = page.locator('input[name="settings_query_port"]').bounding_box()
        assert query_label_box is not None and query_input_box is not None
        assert abs(query_label_box["y"] - query_input_box["y"]) < 8
        assert query_input_box["x"] > query_label_box["x"] + query_label_box["width"]
        assert page.locator('input[name="settings_game_port"]').count() == 0
        assert page.locator('input[name="settings_rest_host"]').count() == 0
        assert page.locator('input[name="settings_rest_port"]').count() == 0
        assert page.locator('input[name="settings_rest_username"]').count() == 0
        assert page.locator('input[name="settings_rest_password"]').count() == 0
        assert page.locator('input[name="settings_workdir"]').count() == 0
        assert page.locator('input[name="settings_server_name"]').count() == 0
        assert page.locator('input[name="settings_executable"]').count() == 0
        assert page.locator('input[name="settings_steamcmd"]').count() == 0
        assert page.locator('input[name="settings_backup_source"]').count() == 0
        page.get_by_role("button", name="Download", exact=True).wait_for(timeout=5000)
        page.locator('input[name="settings_query_port"]').fill("28000")
        dedicated_name = "A1" * 16
        page.locator('input[name="settings_dedicated_server_name"]').fill(dedicated_name)

        validate_toggle = page.locator("#pywebio-scope-settings_toggle_steam_validate")
        validate_toggle.get_by_role("button", name="Off", exact=True).click()
        validate_toggle.get_by_role("button", name="On", exact=True).wait_for(timeout=2000)
        assert page.locator('input[name="settings_memory_restart_mb"]').count() == 0
        assert page.locator("#pywebio-scope-settings_toggle_restart_on_crash").count() == 0

        page.locator("#pywebio-scope-settings_actions").get_by_role(
            "button", name="Save", exact=True
        ).click()
        page.get_by_text("Settings saved").wait_for(timeout=5000)

        monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(config_dir))
        saved = load_profile("default")
        assert saved.server_name == "default"
        assert saved.game_port == 8211
        assert saved.query_port == 28000
        assert saved.dedicated_server_name == dedicated_name
        assert saved.restart_on_crash is True
        assert saved.self_heal_enabled is True
        assert saved.steam_validate is True
        assert f"DedicatedServerName={dedicated_name}" in game_user_settings_path(
            "default"
        ).read_text(encoding="utf-8")


@pytest.mark.playwright
def test_server_settings_auto_update_dependencies_and_persistence(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, config_dir):
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        page.locator("#pywebio-scope-menu").get_by_text("Server Settings", exact=True).click()
        page.locator("#pywebio-scope-settings_form").wait_for(timeout=5000)

        update_toggle = page.locator("#pywebio-scope-settings_toggle_update_on_start")
        auto_toggle = page.locator("#pywebio-scope-settings_toggle_auto_update")
        idle = page.locator('input[name="settings_auto_update_idle_minutes"]')

        assert update_toggle.get_by_role("button", name="On", exact=True).count() == 1
        assert auto_toggle.get_by_role("button", name="On", exact=True).count() == 1
        assert idle.input_value() == "30"
        assert idle.is_disabled() is False

        update_toggle.get_by_role("button", name="On", exact=True).click()
        auto_off = auto_toggle.get_by_role("button", name="Off", exact=True)
        auto_off.wait_for(timeout=2000)
        assert auto_off.is_disabled() is True
        page.wait_for_function(
            "() => document.querySelector('input[name=settings_auto_update_idle_minutes]').disabled"
        )
        assert idle.evaluate("element => getComputedStyle(element).backgroundColor") == "rgb(37, 40, 45)"
        assert idle.evaluate("element => getComputedStyle(element).color") == "rgb(173, 181, 189)"

        update_toggle.get_by_role("button", name="Off", exact=True).click()
        auto_toggle.get_by_role("button", name="On", exact=True).wait_for(timeout=2000)
        idle.fill("45")
        auto_toggle.get_by_role("button", name="On", exact=True).click()
        page.wait_for_function(
            "() => document.querySelector('input[name=settings_auto_update_idle_minutes]').disabled"
        )
        page.locator("#pywebio-scope-settings_actions").get_by_role(
            "button", name="Save", exact=True
        ).click()
        page.get_by_text("Settings saved", exact=True).wait_for(timeout=5000)

        monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(config_dir))
        saved = load_profile("default")
        assert saved.update_on_start is True
        assert saved.auto_update is False
        assert saved.auto_update_idle_minutes == 45


@pytest.mark.playwright
def test_server_settings_help_icon_shows_real_tooltip_text(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    profile_overrides = {
        "backup_dir": str(backup_dir),
    }

    with _gui_page(tmp_path, monkeypatch, profile_overrides=profile_overrides) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        page.locator("#pywebio-scope-menu").get_by_text("Server Settings").click()
        page.locator("#pywebio-scope-settings_form").wait_for(timeout=5000)

        page.wait_for_function(
            "() => document.querySelectorAll('#pywebio-scope-settings_form .field-help').length >= 10",
            timeout=5000,
        )
        assert page.locator("#pywebio-scope-settings_form .field-help").count() >= 10
        label = page.locator("#pywebio-scope-settings_form").locator("label").filter(
            has_text="SteamCMD"
        ).first
        help_icon = label.locator(".field-help")
        help_icon.wait_for(timeout=5000)
        expected = (
            "Fixed under this profile's steamcmd folder. If it exists, Show opens that folder; "
            "otherwise Download fetches and extracts Valve's SteamCMD installer."
        )

        rendered_icon = help_icon.evaluate("(el) => getComputedStyle(el, '::before').content")
        assert rendered_icon == '"i"'
        assert help_icon.get_attribute("data-tooltip") == expected

        help_icon.hover()
        rendered_content = help_icon.evaluate("(el) => getComputedStyle(el, '::after').content")
        assert expected in rendered_content

        launch_tooltips = {
            "Use performance threads":
                "Adds -useperfthreads. On by default for new profiles.",
            "Disable async loading thread":
                "Adds -NoAsyncLoadingThread. Off by default for new profiles.",
            "Use dedicated-server multithreading":
                "Adds -UseMultithreadForDS. On by default for new profiles.",
            "Worker threads":
                "Adds -NumberOfWorkerThreadsServer=<value>. New profiles default to the detected logical CPU count minus one, with a minimum of 1.",
        }
        for label_text, tooltip in launch_tooltips.items():
            launch_help = page.locator("#pywebio-scope-settings_form").locator(
                "label"
            ).filter(has_text=label_text).first.locator(".field-help")
            assert launch_help.get_attribute("data-tooltip") == tooltip


@pytest.mark.playwright
def test_auto_restart_self_heal_toggle_independent_and_reenables(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    profile_overrides = {
        "backup_dir": str(backup_dir),
    }

    with _gui_page(tmp_path, monkeypatch, profile_overrides=profile_overrides) as (page, config_dir):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        page.locator("#pywebio-scope-menu").get_by_text("Auto Restart").click()
        page.locator("#pywebio-scope-auto_restart_form").wait_for(timeout=5000)

        restart_toggle = page.locator("#pywebio-scope-settings_toggle_restart_on_crash")
        self_heal_toggle = page.locator("#pywebio-scope-settings_toggle_self_heal_enabled")
        trigger_frame = page.locator(
            'input[name="settings_self_heal_trigger_frame_minutes"]'
        )
        trigger_crashes = page.locator(
            'input[name="settings_self_heal_trigger_crash_times"]'
        )
        restart_toggle.get_by_role("button", name="On", exact=True).wait_for(timeout=5000)
        assert trigger_frame.input_value() == "30"
        assert trigger_crashes.input_value() == "2"

        # Self-heal can be turned off independently while Restart on crash stays on.
        self_heal_toggle.get_by_role("button", name="On", exact=True).click()
        enabled_off = self_heal_toggle.get_by_role("button", name="Off", exact=True)
        enabled_off.wait_for(timeout=2000)
        assert not enabled_off.is_disabled()

        # Turning Restart on crash off then back on re-enables the Self-heal toggle
        # without resurrecting its previous On value.
        restart_toggle.get_by_role("button", name="On", exact=True).click()
        restart_toggle.get_by_role("button", name="Off", exact=True).wait_for(timeout=2000)
        restart_toggle.get_by_role("button", name="Off", exact=True).click()
        restart_toggle.get_by_role("button", name="On", exact=True).wait_for(timeout=2000)
        reenabled_off = self_heal_toggle.get_by_role("button", name="Off", exact=True)
        reenabled_off.wait_for(timeout=2000)
        assert not reenabled_off.is_disabled()
        trigger_frame.fill("45")
        trigger_crashes.fill("3")

        page.locator("#pywebio-scope-auto_restart_actions").get_by_role(
            "button", name="Save", exact=True
        ).click()
        page.get_by_text("Settings saved").wait_for(timeout=5000)

        monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(config_dir))
        saved = load_profile("default")
        assert saved.restart_on_crash is True
        assert saved.self_heal_enabled is False
        assert saved.self_heal_trigger_frame_minutes == 45
        assert saved.self_heal_trigger_crash_times == 3


@pytest.mark.playwright
def test_auto_restart_history_persists_crash_cause_and_escaped_output(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, config_dir):
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        page.locator("#pywebio-scope-menu").get_by_text("Auto Restart", exact=True).click()
        history = page.locator("#pywebio-scope-restart_history")
        history.get_by_text(
            "No automatic restart decisions have been recorded.", exact=True
        ).wait_for(timeout=5000)

        monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(config_dir))
        RestartHistoryStore("default").append(
            LifecycleEvent(
                dt.datetime(2026, 7, 16, 12, 29),
                "crash",
                "restarted",
                termination=classify_process_exit(0, platform="nt"),
            )
        )
        RestartHistoryStore("default").append(
            LifecycleEvent(
                dt.datetime(2026, 7, 16, 12, 30),
                "crash",
                "restarted",
                termination=classify_process_exit(
                    -1073741819,
                    platform="nt",
                    output=["fatal native crash", "<script>window.__unsafe = true</script>"],
                ),
            )
        )

        history.get_by_text("Access violation", exact=False).wait_for(timeout=5000)
        assert "STATUS_ACCESS_VIOLATION" in history.inner_text()
        assert "0xC0000005" in history.inner_text()
        assert "0x0" not in history.inner_text()
        history.get_by_text("Final server output", exact=True).click()
        history.locator("pre.restart-output").get_by_text(
            "fatal native crash", exact=False
        ).wait_for(timeout=2000)
        assert page.evaluate("() => window.__unsafe") is None

        page.locator("#pywebio-scope-menu").get_by_text("Overview", exact=True).click()
        assert page.locator("#pywebio-scope-reliability_card").count() == 0


@pytest.mark.playwright
def test_server_settings_validation_and_unsaved_leave_guard(tmp_path, monkeypatch):
    valid_backup_dir = tmp_path / "backups"
    valid_backup_dir.mkdir()
    profile_overrides = {
        "backup_dir": str(valid_backup_dir),
    }

    with _gui_page(tmp_path, monkeypatch, profile_overrides=profile_overrides) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        page.locator("#pywebio-scope-menu").get_by_text("Server Settings").click()
        page.locator("#pywebio-scope-settings_form").wait_for(timeout=5000)

        page.locator('input[name="settings_query_port"]').fill("")
        page.locator('input[name="settings_dedicated_server_name"]').fill("invalid")
        page.get_by_role("button", name="Save").click()
        page.get_by_text("Fix the highlighted fields first.", exact=True).wait_for(timeout=5000)
        page.get_by_text("Enter a valid number.", exact=True).wait_for(timeout=5000)
        page.get_by_text(
            "Enter exactly 32 uppercase letters or digits.", exact=True
        ).wait_for(timeout=5000)
        assert "field-invalid" in page.locator('input[name="settings_query_port"]').get_attribute("class")

        page.locator('input[name="settings_query_port"]').fill("28001")
        page.locator('input[name="settings_dedicated_server_name"]').fill("B2" * 16)
        page.get_by_role("button", name="Save").click()
        page.get_by_text("Settings saved").wait_for(timeout=5000)
        assert "field-invalid" not in (page.locator('input[name="settings_query_port"]').get_attribute("class") or "")

        page.locator('input[name="settings_query_port"]').fill("28002")
        page.locator("#pywebio-scope-menu").get_by_text("Overview").click()
        page.get_by_text("Unsaved changes", exact=True).wait_for(timeout=5000)
        page.get_by_role("button", name="Cancel", exact=True).click()
        page.locator(".modal.show").wait_for(state="detached", timeout=5000)
        page.locator("#pywebio-scope-settings_form").wait_for(timeout=5000)
        assert page.locator('input[name="settings_query_port"]').input_value() == "28002"

        page.locator("#pywebio-scope-menu").get_by_text("Overview").click()
        page.get_by_text("Unsaved changes", exact=True).wait_for(timeout=5000)
        page.get_by_role("button", name="Discard changes", exact=True).click()
        page.locator(".modal.show").wait_for(state="detached", timeout=5000)
        page.locator("#pywebio-scope-scheduler_panel").wait_for(timeout=5000)


def _browse_button_for(page, pin_name):
    return page.locator(f'input[name="{pin_name}"]').locator(
        "xpath=ancestor::div[contains(@style,'grid-auto-flow')][1]"
    ).get_by_role("button", name="Browse", exact=True)


def _dir_suggestion(path):
    text = str(path)
    return text if text.endswith(("/", "\\")) else f"{text}{os.sep}"


def _browse_datalist_id(page):
    list_id = page.locator('input[name="browse_address_value"]').get_attribute("list")
    assert list_id
    return list_id


def _browse_datalist_values(page):
    return page.locator(f"#{_browse_datalist_id(page)} option").evaluate_all(
        "options => options.map(option => option.value)"
    )


def _wait_for_datalist_value(page, expected):
    page.wait_for_function(
        """
        expected => {
            const input = document.querySelector('input[name="browse_address_value"]');
            const list = input && document.getElementById(input.getAttribute('list'));
            return !!list && Array.from(list.options).some(option => option.value === expected);
        }
        """,
        arg=expected,
        timeout=5000,
    )


def _autocomplete_snapshot(page):
    return page.evaluate(
        """
        () => {
            const input = document.querySelector('input[name="browse_address_value"]');
            const entry = input && window.Palsitter?.fileBrowser?.entries[input.id];
            return entry ? {
                inputId: input.id,
                datalistId: input.getAttribute('list'),
                suggestions: entry.suggestions,
                filtered: entry.filtered,
                registrySize: Object.keys(window.Palsitter.fileBrowser.entries).length,
            } : null;
        }
        """
    )


@pytest.mark.playwright
def test_backup_settings_browse_navigates_and_selects_folder(tmp_path, monkeypatch):
    workroot = tmp_path / "workroot"
    subdir = workroot / "SaveData"
    subdir.mkdir(parents=True)
    (workroot / "readme.txt").write_text("hi", encoding="utf-8")

    with _gui_page(tmp_path, monkeypatch, profile_overrides={"backup_dir": str(workroot)}) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        page.locator("#pywebio-scope-menu").get_by_text("Saves & Backups").click()
        page.locator("#pywebio-scope-backup_settings_form").wait_for(timeout=5000)

        _browse_button_for(page, "settings_backup_dir").click()
        modal = page.locator(".modal.show")
        modal.wait_for(timeout=5000)
        address = page.locator('input[name="browse_address_value"]')
        list_scope = page.locator("#pywebio-scope-browse_list")
        page.get_by_text("2 entries", exact=True).wait_for(timeout=5000)
        assert address.input_value() == str(workroot)
        assert modal.get_by_label("Volume").count() == 1
        assert modal.locator("label").filter(has_text="Path").count() == 1
        datalist_id = _browse_datalist_id(page)
        assert datalist_id == "browse-address-autocomplete-list"
        assert page.locator(f"#{datalist_id}").count() == 1
        snapshot = _autocomplete_snapshot(page)
        assert snapshot["inputId"] == "browse-address-autocomplete"
        assert snapshot["registrySize"] == 1
        assert workroot.anchor in snapshot["suggestions"]
        assert _dir_suggestion(workroot) in snapshot["suggestions"]
        assert _dir_suggestion(subdir) in snapshot["suggestions"]
        assert str(workroot / "readme.txt") not in snapshot["suggestions"]
        assert modal.get_by_role("button", name="Go", exact=True).locator("svg").count() == 1
        assert modal.get_by_role("button", name="Up", exact=True).locator("svg").count() == 1
        list_scope.get_by_text(".", exact=True).wait_for(timeout=5000)
        list_scope.get_by_text("Folder", exact=True).wait_for(timeout=5000)
        list_scope.get_by_text("SaveData", exact=True).wait_for(timeout=5000)
        list_scope.get_by_text("readme.txt", exact=True).wait_for(timeout=5000)

        # Relative paths resolve from the directory where gui.py was launched.
        relative_target = Path("assets")
        address.fill(str(relative_target))
        modal.get_by_role("button", name="Go", exact=True).click()
        page.wait_for_function(
            "expected => document.querySelector('input[name=\"browse_address_value\"]')?.value === expected",
            arg=str(relative_target.resolve()),
            timeout=5000,
        )
        assert address.input_value() == str(relative_target.resolve())
        list_scope.get_by_text("gui", exact=True).wait_for(timeout=5000)
        address.fill(str(workroot))
        modal.get_by_role("button", name="Go", exact=True).click()
        page.get_by_text("2 entries", exact=True).wait_for(timeout=5000)
        assert address.input_value() == str(workroot)

        # The datalist filters by basename and parent+partial prefixes, but does
        # not navigate until Go is pressed.
        address.fill("Save")
        _wait_for_datalist_value(page, _dir_suggestion(subdir))
        assert _browse_datalist_values(page) == [_dir_suggestion(subdir)]
        address.fill(str(workroot / "Sa"))
        _wait_for_datalist_value(page, _dir_suggestion(subdir))
        address.fill(_dir_suggestion(subdir))
        page.get_by_text("2 entries", exact=True).wait_for(timeout=5000)
        modal.get_by_role("button", name="Go", exact=True).click()
        page.get_by_text("No entries", exact=True).wait_for(timeout=5000)
        assert address.input_value() == str(subdir)
        modal.get_by_role("button", name="Up", exact=True).click()
        page.get_by_text("2 entries", exact=True).wait_for(timeout=5000)
        assert address.input_value() == str(workroot)

        # A single click selects without navigating or changing the original input.
        list_scope.get_by_text("SaveData", exact=True).click()
        page.get_by_text("Selected: SaveData", exact=True).wait_for(timeout=5000)
        assert address.input_value() == str(workroot)
        assert page.locator('input[name="settings_backup_dir"]').input_value() == str(workroot)

        # A double-click opens a folder.
        list_scope.get_by_text("SaveData", exact=True).dblclick()
        page.get_by_text("No entries", exact=True).wait_for(timeout=5000)
        assert address.input_value() == str(subdir)
        assert page.evaluate("window.getSelection().toString()") == ""

        # Up returns to the parent directory.
        modal.get_by_role("button", name="Up", exact=True).click()
        page.get_by_text("2 entries", exact=True).wait_for(timeout=5000)
        assert address.input_value() == str(workroot)

        # "." makes the current folder selectable again after another row was highlighted.
        list_scope.get_by_text("SaveData", exact=True).click()
        list_scope.get_by_text(".", exact=True).click()
        page.get_by_text("Selected: .", exact=True).wait_for(timeout=5000)
        list_scope.get_by_text("SaveData", exact=True).click()
        page.locator("#pywebio-scope-browse_actions").get_by_role(
            "button", name="Select", exact=True
        ).click()

        page.locator(".modal.show").wait_for(state="detached", timeout=5000)
        assert page.locator('input[name="settings_backup_dir"]').input_value() == str(subdir)


@pytest.mark.playwright
def test_players_panel_lists_roster_and_manages_file_backed_bans(tmp_path, monkeypatch):
    get_calls = []
    banlist_path = (
        _fixed_palserver_dir(tmp_path) / "Pal" / "Saved" / "SaveGames" / "banlist.txt"
    )
    with _running_palserver_process(tmp_path), _mock_metrics_server(
        banlist_path=banlist_path,
        get_calls=get_calls,
    ) as (rest_port, calls):
        with _gui_page(tmp_path, monkeypatch, rest_port=rest_port) as (page, config_dir):
            page.locator("#pywebio-scope-aside").get_by_text("default").click()

            players_panel = page.locator("#pywebio-scope-players_panel")
            players_panel.get_by_text("Players (3/32)", exact=True).wait_for(timeout=15000)
            players_panel.get_by_text("Alice (Lv: 17)", exact=True).wait_for(timeout=15000)
            fps_metric = page.locator('[data-metric="fps"] .metric-value')
            fps_metric.evaluate("node => window.__stableFpsMetricNode = node")
            compact_row = page.locator("#pywebio-scope-players_list > div").first
            compact_row.evaluate("node => window.__compactPlayerRow = node")
            console = page.locator('input[name="console_command"]')
            console.fill("info")
            console.press("Enter")
            page.locator(".log-box").get_by_text(
                '{"version": "v1.2.3"}', exact=False
            ).wait_for(timeout=5000)
            players_panel.get_by_text("uid: steam_1", exact=True).wait_for(timeout=15000)
            assert players_panel.get_by_role("button", name="Refresh", exact=True).count() == 0
            assert page.locator("#pywebio-scope-players_list").evaluate(
                "element => getComputedStyle(element).overflowY"
            ) == "auto"
            left_box = page.locator("#pywebio-scope-scheduler").bounding_box()
            panel_box = players_panel.bounding_box()
            assert left_box is not None and panel_box is not None
            assert abs((panel_box["y"] + panel_box["height"]) - (left_box["y"] + left_box["height"])) < 3
            assert players_panel.get_by_role("button", name="Unban", exact=True).count() == 0
            assert page.locator('input[name="players_unban_userid"]').count() == 0
            players_panel.get_by_text("Alice (Lv: 18)", exact=True).wait_for(timeout=12000)
            assert compact_row.evaluate("node => node === window.__compactPlayerRow")
            assert fps_metric.evaluate("node => node === window.__stableFpsMetricNode")

            kick_button = players_panel.get_by_role("button", name="Kick", exact=True)
            ban_button = players_panel.get_by_role("button", name="Ban", exact=True)
            assert kick_button.locator('svg[data-player-action-icon="kick"]').count() == 1
            assert ban_button.locator('svg[data-player-action-icon="ban"]').count() == 1
            kick_angle = kick_button.locator(
                'svg[data-player-action-icon="kick"]'
            ).evaluate(
                """
                icon => {
                    const matrix = new DOMMatrix(getComputedStyle(icon).transform);
                    return Math.round(Math.atan2(matrix.b, matrix.a) * 180 / Math.PI);
                }
                """
            )
            assert kick_angle == -30
            kick_button.hover()
            kick_button.locator('[role="tooltip"]').get_by_text(
                "Kick", exact=True
            ).wait_for(state="visible", timeout=5000)
            alignment = kick_button.evaluate(
                """
                button => {
                    let row = button.parentElement;
                    while (row && getComputedStyle(row).display !== 'grid') row = row.parentElement;
                    if (!row) return null;
                    const buttonBox = button.getBoundingClientRect();
                    const rowBox = row.getBoundingClientRect();
                    return {
                        alignItems: getComputedStyle(row).alignItems,
                        centerDelta: Math.abs(
                            buttonBox.top + buttonBox.height / 2 -
                            (rowBox.top + rowBox.height / 2)
                        ),
                    };
                }
                """
            )
            assert alignment["alignItems"] == "center"
            assert alignment["centerDelta"] < 2

            kick_button.click()
            page.get_by_text("Confirm kick", exact=True).wait_for(timeout=5000)
            assert calls == []
            page.get_by_role("button", name="Cancel", exact=True).click()
            kick_button.click()
            page.get_by_role("button", name="Yes, kick", exact=True).click()
            page.get_by_text("Kick sent", exact=True).wait_for(timeout=5000)

            players_panel.get_by_role("button", name="Ban", exact=True).click()
            page.get_by_text("Confirm ban", exact=True).wait_for(timeout=5000)
            assert [path for path, _ in calls] == ["/v1/api/kick"]
            page.get_by_role("button", name="Yes, ban", exact=True).click()
            page.get_by_text("Ban sent", exact=True).wait_for(timeout=5000)

            assert [path for path, _ in calls] == [
                "/v1/api/kick",
                "/v1/api/ban",
            ]
            assert calls[0][1] == {"userid": "steam_1", "message": ""}
            assert calls[1][1] == {"userid": "steam_1", "message": ""}
            audited_actions = [
                event.message
                for event in AuditStore("default").load()
                if event.type == "palsitter_command"
            ]
            assert "Executed: kick Alice (steam_1) (result: success)" in audited_actions
            assert "Executed: ban Alice (steam_1) (result: success)" in audited_actions

            cache = json.loads(
                (config_dir.parent / "profile" / "default" / "players.json").read_text(
                    encoding="utf-8"
                )
            )
            cached_alice = next(
                player for player in cache["players"] if player["userId"] == "steam_1"
            )
            assert cached_alice["name"] == "Alice"
            assert cached_alice["updated_at"].endswith("Z")
            assert "banned_userids" not in cache
            assert banlist_path.read_text(encoding="utf-8") == "steam_1\n"

            page.locator("#pywebio-scope-menu").get_by_text("Players", exact=True).click()
            detail = page.locator("#pywebio-scope-players_detail_panel")
            detail_row = detail.locator("#pywebio-scope-players_detail_list > div").first
            detail_row.get_by_text("Alice", exact=False).wait_for(timeout=15000)
            detail_kick = detail_row.get_by_role("button", name="Kick", exact=True)
            detail_ban = detail_row.get_by_role("button", name="Ban", exact=True)
            assert detail_kick.locator('svg[data-player-action-icon="kick"]').count() == 1
            assert detail_ban.locator('svg[data-player-action-icon="ban"]').count() == 1
            detail_ban.hover()
            detail_ban.locator('[role="tooltip"]').get_by_text(
                "Ban", exact=True
            ).wait_for(state="visible", timeout=5000)
            detail_alignment = detail_ban.evaluate(
                """
                button => {
                    let row = button.parentElement;
                    while (row && getComputedStyle(row).display !== 'grid') row = row.parentElement;
                    if (!row) return null;
                    const buttonBox = button.getBoundingClientRect();
                    const rowBox = row.getBoundingClientRect();
                    return Math.abs(
                        buttonBox.top + buttonBox.height / 2 -
                        (rowBox.top + rowBox.height / 2)
                    );
                }
                """
            )
            assert detail_alignment < 2
            banned = page.locator("#pywebio-scope-players_banned_list")
            banned.get_by_text("steam_1", exact=True).wait_for(timeout=15000)
            banned.get_by_text("Alice", exact=True).wait_for(timeout=5000)
            assert page.locator('input[name="players_broadcast_message"]').count() == 0
            assert page.locator('input[name="players_detail_unban_userid"]').count() == 0
            banned.get_by_role("button", name="Unban", exact=True).click()
            page.get_by_text("Unban sent", exact=True).wait_for(timeout=5000)

            assert [path for path, _ in calls] == [
                "/v1/api/kick",
                "/v1/api/ban",
                "/v1/api/unban",
            ]
            assert calls[2][1] == {"userid": "steam_1"}
            banned.get_by_text("No banned players.", exact=True).wait_for(
                timeout=5000
            )
            assert banlist_path.read_text(encoding="utf-8") == ""
            assert get_calls.count("/v1/api/info") == 1
            assert get_calls.count("/v1/api/players") >= 2
            assert get_calls.count("/v1/api/metrics") >= 2
            assert abs(
                get_calls.count("/v1/api/players")
                - get_calls.count("/v1/api/metrics")
            ) <= 1


@pytest.mark.playwright
def test_players_page_splits_cached_offline_players_and_shows_activity(
    tmp_path, monkeypatch
):
    alice = {
        "name": "Alice",
        "userId": "steam_1",
        "level": 17,
        "ping": 23.9,
        "location_x": 192205.78125,
        "location_y": -226869.515625,
        "building_count": 7,
    }
    tree_walker = {
        "name": "Tree Walker",
        "userId": "steam_3",
        "level": 30,
        "location_x": 621794,
        "location_y": -757915,
    }
    bob = {**alice, "name": "Bob", "userId": "steam_2"}
    with _running_palserver_process(tmp_path), _mock_metrics_server(
        players_sequence=[[alice, tree_walker], [bob, tree_walker], [alice, tree_walker]],
    ) as (rest_port, _):
        with _gui_page(tmp_path, monkeypatch, rest_port=rest_port) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
            page.locator("#pywebio-scope-players_panel").get_by_text(
                "Alice (Lv: 17)", exact=True
            ).wait_for(timeout=15000)
            page.locator("#pywebio-scope-menu").get_by_text("Players", exact=True).click()

            online = page.locator("#pywebio-scope-players_detail_list")
            offline = page.locator("#pywebio-scope-players_offline_list")
            online.get_by_text("Bob", exact=False).wait_for(timeout=15000)
            offline.get_by_text("Alice", exact=False).wait_for(timeout=5000)
            online.get_by_text("Location -838, 689", exact=True).wait_for(timeout=5000)
            online.get_by_text("Location -83, 854", exact=True).wait_for(timeout=5000)
            offline.get_by_text("Last location -838, 689", exact=True).wait_for(timeout=5000)
            assert offline.locator('[data-player-field="ping"]').count() == 0
            assert online.get_by_role("button", name="Kick", exact=True).count() == 2
            assert offline.get_by_role("button", name="Kick", exact=True).count() == 0
            assert online.get_by_text("Last login: ", exact=False).count() == 2
            assert online.get_by_text("Play time: 0.0hours", exact=True).count() == 2
            online_row = online.locator("> div").first
            activity = online_row.locator(".player-activity")
            kick = online_row.get_by_role("button", name="Kick", exact=True)
            activity_box = activity.bounding_box()
            kick_box = kick.bounding_box()
            assert activity_box is not None and kick_box is not None
            assert activity_box["x"] + activity_box["width"] <= kick_box["x"]
            assert activity.evaluate("node => getComputedStyle(node).textAlign") == "right"
            online.get_by_text("Alice", exact=False).wait_for(timeout=15000)
            offline.get_by_text("Bob", exact=False).wait_for(timeout=5000)


@pytest.mark.playwright
def test_players_page_shows_cached_players_when_server_is_unavailable(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, _):
        PlayerCache("default").upsert(
            [{"userId": "steam_cached", "name": "Cached Player", "level": 22}],
            updated_at="2026-07-22T13:00:00Z",
            poll_interval_seconds=3,
        )
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        page.locator("#pywebio-scope-menu").get_by_text("Players", exact=True).click()

        offline = page.locator("#pywebio-scope-players_offline_list")
        offline.get_by_text("Cached Player", exact=False).wait_for(timeout=10000)
        page.get_by_text("PalServer is not running or its REST API is unavailable.", exact=False).wait_for(
            timeout=5000
        )


@pytest.mark.playwright
def test_server_settings_reset_next_to_save_and_delete_instance(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, config_dir):
        # Create a second instance through the Add modal (real UI path).
        page.locator("#pywebio-scope-aside").get_by_text("Add").click()
        page.get_by_label("Profile name").fill("server2")
        page.get_by_role("button", name="Confirm", exact=True).click()
        page.locator("#pywebio-scope-aside").get_by_text("server2", exact=True).wait_for(timeout=5000)

        page.locator("#pywebio-scope-aside").get_by_text("server2", exact=True).click()
        page.locator("#pywebio-scope-menu").get_by_text("Server Settings").click()
        page.locator("#pywebio-scope-settings_form").wait_for(timeout=5000)

        actions = page.locator("#pywebio-scope-settings_actions")
        save = actions.get_by_role("button", name="Save", exact=True)
        reset = actions.get_by_role("button", name="Reset", exact=True)
        assert not actions.is_visible()
        assert actions.get_by_role("button", name="Back", exact=True).count() == 0

        page.locator('input[name="settings_query_port"]').fill("29000")
        actions.get_by_text("Careful — you have unsaved changes!", exact=True).wait_for(timeout=5000)
        assert actions.evaluate("(element) => getComputedStyle(element).position") == "fixed"
        viewport_height = page.evaluate("window.innerHeight")
        actions_box = actions.bounding_box()
        assert actions_box is not None and actions_box["y"] + actions_box["height"] < viewport_height

        # Reset and Save share the floating bar, with Save on the right.
        save_box = save.bounding_box()
        reset_box = reset.bounding_box()
        assert save_box is not None and reset_box is not None
        assert abs(save_box["y"] - reset_box["y"]) < 5
        assert save_box["x"] >= reset_box["x"] + reset_box["width"]

        # Reset reloads the saved values, discarding unsaved edits.
        page.evaluate(
            "window.__queryBeforeReset = document.querySelector('input[name=settings_query_port]')"
        )
        reset.click()
        page.wait_for_function(
            "document.querySelector('input[name=settings_query_port]') !== window.__queryBeforeReset",
            timeout=5000,
        )
        assert page.locator('input[name="settings_query_port"]').input_value() != "29000"

        # Delete instance: red button beneath a horizontal rule.
        delete_scope = page.locator("#pywebio-scope-settings_delete")
        delete_btn = delete_scope.get_by_role("button", name="Delete instance", exact=True)
        delete_btn.wait_for(timeout=5000)
        assert delete_btn.evaluate(
            "(element) => getComputedStyle(element).backgroundColor"
        ) == "rgb(231, 76, 60)"
        hr_box = delete_scope.locator("hr").first.bounding_box()
        del_box = delete_btn.bounding_box()
        assert hr_box is not None and del_box is not None
        assert hr_box["y"] < del_box["y"]

        # Confirmation modal: Yes stays disabled until the exact instance name is typed.
        data_file = profile_dir("server2") / "server-data" / "save.sav"
        data_file.parent.mkdir()
        data_file.write_text("save-data", encoding="utf-8")
        delete_btn.click()
        confirm = page.get_by_role("button", name="Yes, delete", exact=True)
        confirm.wait_for(timeout=5000)
        assert confirm.is_disabled()
        wipe = page.get_by_label("Wipe data", exact=True)
        assert not wipe.is_checked()
        page.locator('input[name="delete_confirm_name"]').fill("wrong")
        assert confirm.is_disabled()
        page.locator('input[name="delete_confirm_name"]').fill("server2")
        page.wait_for_function(
            "() => { const b = Array.from(document.querySelectorAll('button'))"
            ".find((x) => x.innerText.trim() === 'Yes, delete'); return b && !b.disabled; }",
            timeout=5000,
        )
        wipe.check()
        confirm.click()
        wipe_confirm = page.get_by_role("button", name="Yes, wipe data", exact=True)
        wipe_confirm.wait_for(timeout=5000)
        page.get_by_role("button", name="Cancel", exact=True).click()
        assert page.locator("#pywebio-scope-aside").get_by_text("server2", exact=True).count() >= 1
        assert data_file.exists()

        delete_btn = page.locator("#pywebio-scope-settings_delete").get_by_role(
            "button", name="Delete instance", exact=True
        )
        delete_btn.click()
        page.locator('input[name="delete_confirm_name"]').fill("server2")
        page.get_by_label("Wipe data", exact=True).check()
        page.get_by_role("button", name="Yes, delete", exact=True).click()
        page.get_by_role("button", name="Yes, wipe data", exact=True).click()

        # Instance removed from the sidebar; the default instance remains.
        page.locator("#pywebio-scope-aside").get_by_text(
            "server2", exact=True
        ).wait_for(state="detached", timeout=5000)
        assert page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).count() >= 1

        monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(config_dir))
        assert "server2" not in list_profiles()
        assert not data_file.exists()


@pytest.mark.playwright
def test_i18n_detects_switches_and_persists_language(tmp_path, monkeypatch):
    with _gui_page(
        tmp_path,
        monkeypatch,
        preferred_language=None,
        browser_locale="zh-TW",
    ) as (page, config_dir):
        page.locator("#pywebio-scope-menu").get_by_text("首頁").wait_for(timeout=5000)
        page.locator("#pywebio-scope-aside").get_by_text("新增").wait_for(timeout=5000)

        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        page.locator("#pywebio-scope-header_status").get_by_text("未執行").wait_for(timeout=5000)
        page.locator("#pywebio-scope-content").get_by_text("排程與操作").wait_for(timeout=5000)
        page.locator("#pywebio-scope-content").get_by_text("記錄", exact=True).wait_for(timeout=5000)
        console = page.locator('input[name="console_command"]')
        console.focus()
        autocomplete = page.locator("#console-autocomplete")
        autocomplete.wait_for(state="visible", timeout=5000)
        assert autocomplete.locator(".console-autocomplete-hint").first.inner_text() == "向所有玩家傳送訊息"

        page.locator("#pywebio-scope-menu").get_by_text("伺服器設定").click()
        page.get_by_text("查詢連接埠", exact=True).wait_for(timeout=5000)

        page.locator("#pywebio-scope-aside").get_by_text("首頁").click()
        page.locator("#pywebio-scope-aside").get_by_text("新增").click()
        page.get_by_label("設定檔名稱").fill("")
        page.get_by_role("button", name="確認").click()
        page.get_by_text("名稱只能使用英文字母、數字、連字號或底線").wait_for(timeout=5000)
        page.locator(".modal.show button.close").click()

        page.get_by_role("button", name="English").click()
        page.locator("#pywebio-scope-menu").get_by_text("Home").wait_for(timeout=5000)
        saved = json.loads((config_dir / "webui" / "settings.json").read_text(encoding="utf-8"))
        assert saved["language"] == "en-US"

    with _gui_page(
        tmp_path,
        monkeypatch,
        preferred_language=None,
        browser_locale="zh-TW",
    ) as (page, _):
        page.locator("#pywebio-scope-menu").get_by_text("Home").wait_for(timeout=5000)
        assert page.locator("#pywebio-scope-menu").get_by_text("首頁").count() == 0


@pytest.mark.playwright
def test_theme_switches_to_light_and_persists(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, config_dir):
        page.get_by_role("button", name="Light", exact=True).click()
        page.locator("body.light-palsitter").wait_for(timeout=5000)
        assert page.locator("#pywebio-scope-content").evaluate(
            "element => getComputedStyle(element).backgroundColor"
        ) == "rgb(249, 249, 249)"
        saved = json.loads((config_dir / "webui" / "settings.json").read_text(encoding="utf-8"))
        assert saved["theme"] == "light"
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        page.locator("#pywebio-scope-menu").get_by_text("Server Settings", exact=True).click()
        page.locator("#pywebio-scope-settings_form").wait_for(timeout=5000)
        page.locator("#pywebio-scope-settings_toggle_auto_update").get_by_role(
            "button", name="On", exact=True
        ).click()
        disabled_idle = page.locator('input[name="settings_auto_update_idle_minutes"]')
        disabled_idle.wait_for()
        page.wait_for_function(
            "() => document.querySelector('input[name=settings_auto_update_idle_minutes]').disabled"
        )
        assert disabled_idle.evaluate("element => getComputedStyle(element).backgroundColor") == "rgb(241, 243, 245)"
        assert disabled_idle.evaluate("element => getComputedStyle(element).color") == "rgb(108, 117, 125)"

    with _gui_page(tmp_path, monkeypatch, preferred_language=None) as (page, _):
        page.locator("body.light-palsitter").wait_for(timeout=5000)
        page.get_by_role("button", name="Dark", exact=True).click()
        page.locator("body.dark-palsitter").wait_for(timeout=5000)


@pytest.mark.playwright
def test_i18n_switches_to_japanese_via_home(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, config_dir):
        page.get_by_role("button", name="日本語", exact=True).click()
        page.locator("#pywebio-scope-menu").get_by_text("ホーム", exact=True).wait_for(timeout=5000)
        page.locator("#pywebio-scope-aside").get_by_text("追加", exact=True).wait_for(timeout=5000)
        saved = json.loads((config_dir / "webui" / "settings.json").read_text(encoding="utf-8"))
        assert saved["language"] == "ja-JP"


@pytest.mark.playwright
def test_world_settings_numeric_fields_have_working_spinner_step(tmp_path, monkeypatch):
    workdir = _fixed_palserver_dir(tmp_path)

    with _gui_page(tmp_path, monkeypatch) as (page, config_dir):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        page.locator("#pywebio-scope-menu").get_by_text("World Settings").click()
        page.locator("#pywebio-scope-world_settings_form").wait_for(timeout=5000)

        # Float fields render internally as type="text" (pywebio's float pseudo-type),
        # which drops the native spinner; gui.py flips them to type="number" via JS so
        # the up/down arrows work, without breaking decimal parsing on save.
        day_rate = page.locator('input[name="world_DayTimeSpeedRate"]')
        assert day_rate.get_attribute("type") == "number"
        assert day_rate.get_attribute("step") == "0.1"
        day_rate.click()
        day_rate.press("ArrowUp")
        assert day_rate.input_value() == "1.1"

        base_camp = page.locator('input[name="world_BaseCampMaxNum"]')
        assert base_camp.get_attribute("type") == "number"
        assert base_camp.get_attribute("step") == "1"
        base_camp.click()
        base_camp.press("ArrowUp")
        assert base_camp.input_value() == "129"

        page.locator('input[name="world_RESTAPIPort"]').fill("9124")
        page.locator('input[name="world_AdminPassword"]').fill("new-secret")

        # Free-text fields (not numeric) must be left untouched.
        seed = page.locator('input[name="world_RandomizerSeed"]')
        assert seed.get_attribute("type") == "text"
        assert seed.get_attribute("step") is None

        world_actions = page.locator("#pywebio-scope-world_settings_actions")
        world_actions.get_by_text("Careful — you have unsaved changes!", exact=True).wait_for(timeout=5000)
        assert world_actions.evaluate("element => getComputedStyle(element).position") == "fixed"
        world_actions.get_by_role(
            "button", name="Save", exact=True
        ).click()
        page.get_by_text("World settings saved", exact=True).wait_for(timeout=5000)

        profile = Profile(name="default", workdir=str(workdir))
        saved = read_ini_option_settings(resolve_ini_path(profile))
        assert saved["DayTimeSpeedRate"] == 1.1
        assert saved["BaseCampMaxNum"] == 129
        monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(config_dir))
        saved_profile = load_profile("default")
        assert saved_profile.rest_host == "localhost"
        assert saved_profile.rest_port == 9124
        assert saved_profile.rest_username == "admin"
        assert saved_profile.rest_password == "new-secret"


@pytest.mark.playwright
def test_world_settings_accept_decimal_format_for_integer_ini_fields(tmp_path, monkeypatch):
    workdir = _fixed_palserver_dir(tmp_path)

    with _gui_page(tmp_path, monkeypatch) as (page, _):
        ini_path = resolve_ini_path(Profile(name="default", workdir=str(workdir)))
        ini_path.parent.mkdir(parents=True, exist_ok=True)
        ini_path.write_text(
            "[/Script/Pal.PalGameWorldSettings]\n"
            "OptionSettings=(BaseCampMaxNum=600.000000)\n",
            encoding="utf-8",
        )

        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        page.locator("#pywebio-scope-menu").get_by_text("World Settings").click()
        page.locator("#pywebio-scope-world_settings_form").wait_for(timeout=5000)

        assert page.locator('input[name="world_BaseCampMaxNum"]').input_value() == "600"
        assert page.locator("#pywebio-scope-world_settings_warning").count() == 0


@pytest.mark.playwright
def test_world_settings_validation_and_unsaved_save_leave(tmp_path, monkeypatch):
    workdir = _fixed_palserver_dir(tmp_path)

    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        page.locator("#pywebio-scope-menu").get_by_text("World Settings").click()
        page.locator("#pywebio-scope-world_settings_form").wait_for(timeout=5000)

        day_rate = page.locator('input[name="world_DayTimeSpeedRate"]')
        day_rate.fill("")
        page.locator("#pywebio-scope-world_settings_actions").get_by_role(
            "button", name="Save", exact=True
        ).click()
        page.get_by_text("Fix the highlighted fields first.", exact=True).wait_for(timeout=5000)
        page.get_by_text("Enter a valid number.", exact=True).wait_for(timeout=5000)
        assert "field-invalid" in day_rate.get_attribute("class")

        day_rate.fill("1.7")
        page.locator("#pywebio-scope-menu").get_by_text("Overview").click()
        page.get_by_text("Unsaved changes", exact=True).wait_for(timeout=5000)
        page.get_by_role("button", name="Save and leave", exact=True).click()
        page.locator(".modal.show").wait_for(state="detached", timeout=5000)
        page.locator("#pywebio-scope-scheduler_panel").wait_for(timeout=5000)

        profile = Profile(name="default", workdir=str(workdir), backup_source=str(_fixed_backup_source(tmp_path)))
        saved = read_ini_option_settings(resolve_ini_path(profile))
        assert saved["DayTimeSpeedRate"] == 1.7


@pytest.mark.playwright
def test_world_settings_help_icon_shows_real_tooltip_text(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        page.locator("#pywebio-scope-menu").get_by_text("World Settings").click()
        page.locator("#pywebio-scope-world_settings_form").wait_for(timeout=5000)

        label = page.locator("label").filter(has_text="Enable PvP").first
        help_icon = label.locator(".field-help")
        help_icon.wait_for(timeout=5000)

        expected = (
            "Master switch for player-vs-player combat. When Off (default), players "
            "generally cannot fight each other; when On, PvP damage becomes possible "
            "(still gated by bEnablePlayerToPlayerDamage)."
        )
        # The tooltip text is a real i18n-backed description (not the raw i18n key,
        # and not a guessed placeholder), attached as a real DOM attribute.
        assert help_icon.get_attribute("data-tooltip") == expected

        # Hover the icon for real and read the CSS-generated tooltip content, the same
        # way a user would actually see it - not just asserting the attribute exists.
        help_icon.hover()
        rendered_content = help_icon.evaluate("(el) => getComputedStyle(el, '::after').content")
        assert expected in rendered_content


@pytest.mark.playwright
def test_world_settings_menu_position_and_field_types_save_to_ini(tmp_path, monkeypatch):
    workdir = _fixed_palserver_dir(tmp_path)

    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        page.locator("#pywebio-scope-menu").get_by_text("World Settings").click()
        page.locator("#pywebio-scope-world_settings_form").wait_for(timeout=5000)

        assert page.locator(".modal.show").count() == 0
        assert page.locator("#pywebio-scope-menu .menu-button").all_inner_texts() == [
            "Overview",
            "Players",
            "Server Settings",
            "Auto Restart",
            "World Settings",
            "Mods",
            "Saves & Backups",
            "Game Map",
            "Audit",
            "Tools",
        ]
        assert page.locator('select[name="world_settings_format"]').count() == 0
        assert page.locator("#pywebio-scope-world_settings_warning").count() == 0

        # One field of each editable type.
        bool_toggle = page.locator("#pywebio-scope-world_toggle_bIsPvP")
        bool_toggle.get_by_role("button", name="Off", exact=True).wait_for(timeout=5000)
        bool_toggle.get_by_role("button", name="Off", exact=True).click()
        bool_toggle.get_by_role("button", name="On", exact=True).wait_for(timeout=2000)

        page.locator('input[name="world_BaseCampMaxNum"]').fill("77")
        page.locator('input[name="world_PhysicsActiveDropItemMaxNum"]').fill("42")
        page.locator('input[name="world_DayTimeSpeedRate"]').fill("2.5")
        page.locator('select[name="world_DeathPenalty"]').select_option(label="Item")
        page.locator('input[name="world_ServerDescription"]').fill("Hello World")
        xbox = page.get_by_label("Xbox", exact=True)
        xbox.uncheck()
        game_data_api = page.locator("#pywebio-scope-world_toggle_EnableGameDataAPI")
        game_data_api.get_by_role("button", name="On", exact=True).click()
        game_data_api.get_by_role("button", name="Off", exact=True).wait_for(timeout=2000)

        page.locator("#pywebio-scope-world_settings_actions").get_by_role(
            "button", name="Save", exact=True
        ).click()
        page.get_by_text("World settings saved", exact=True).wait_for(timeout=5000)

        profile = Profile(name="default", workdir=str(workdir))
        saved = read_ini_option_settings(resolve_ini_path(profile))
        assert saved["bIsPvP"] is True
        assert saved["BaseCampMaxNum"] == 77
        assert saved["PhysicsActiveDropItemMaxNum"] == 42
        assert saved["DayTimeSpeedRate"] == 2.5
        assert saved["DeathPenalty"] == "Item"
        assert saved["ServerDescription"] == "Hello World"
        assert saved["CrossplayPlatforms"] == ["Steam", "PS5", "Mac"]
        assert "EnableGameDataAPI" not in saved
        profile_copy = load_profile("default")
        assert profile_copy.world_settings["CrossplayPlatforms"] == ["Steam", "PS5", "Mac"]
        assert profile_copy.launch_enable_gamedata_api is False
        assert "-enable-gamedata-api" not in profile_copy.build_executable_args()


@pytest.mark.playwright
def test_world_settings_game_data_api_toggle_controls_launch_argument(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, config_dir):
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        page.locator("#pywebio-scope-menu").get_by_text("World Settings", exact=True).click()
        page.locator("#pywebio-scope-world_settings_form").wait_for(timeout=5000)

        toggle = page.locator("#pywebio-scope-world_toggle_EnableGameDataAPI")
        toggle.get_by_role("button", name="On", exact=True).click()
        toggle.get_by_role("button", name="Off", exact=True).wait_for(timeout=2000)
        page.locator("#pywebio-scope-world_settings_actions").get_by_role(
            "button", name="Save", exact=True
        ).click()
        page.get_by_text("World settings saved", exact=True).wait_for(timeout=5000)

        monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(config_dir))
        saved = load_profile("default")
        assert saved.launch_enable_gamedata_api is False
        assert "-enable-gamedata-api" not in saved.build_executable_args()
        assert "EnableGameDataAPI" not in read_ini_option_settings(
            resolve_ini_path(saved)
        )


@pytest.mark.playwright
def test_running_home_card_opens_overview_without_redundant_action_strip(tmp_path, monkeypatch):
    with _running_palserver_process(tmp_path) as external_process, _mock_metrics_server() as (rest_port, _):
        with _gui_page(
            tmp_path,
            monkeypatch,
            rest_port=rest_port,
        ) as (page, _):
            card = page.locator(
                '.instance-card[data-instance-card="default"]:not(.instance-card-loading)'
            )
            card.wait_for(timeout=10000)
            assert "Palworld" in card.inner_text()
            assert "Running" in card.inner_text()
            assert "3/32" in card.inner_text()
            assert "v1.2.3" in card.inner_text()
            card.evaluate("node => window.__stableHomeCardNode = node")
            page.wait_for_timeout(5500)
            assert card.evaluate("node => node === window.__stableHomeCardNode")

            card.click()
            page.locator("#pywebio-scope-header_status").get_by_text(
                "Running", exact=True
            ).wait_for(timeout=5000)
            scheduler = page.locator("#pywebio-scope-scheduler_panel")
            scheduler.get_by_role(
                "button", name="Detach", exact=True
            ).wait_for(timeout=10000)
            assert scheduler.get_by_role("button", name="Start", exact=True).count() == 0
            page.locator("#overview-log-box").wait_for(timeout=5000)
            assert page.locator("#pywebio-scope-instance_actions").count() == 0
            memory = page.locator('[data-metric="memory"] .metric-value')
            cpu = page.locator('[data-metric="cpu"] .metric-value')
            page.wait_for_function(
                "document.querySelector('[data-metric=memory] .metric-value')?.textContent !== '-'",
                timeout=10000,
            )
            page.wait_for_function(
                "document.querySelector('[data-metric=cpu] .metric-value')?.textContent !== '-'",
                timeout=10000,
            )
            assert "MiB" in memory.inner_text()
            assert cpu.inner_text().endswith("%")
            scheduler.get_by_role("button", name="Detach", exact=True).click()
            scheduler.get_by_role("button", name="Start", exact=True).wait_for(
                timeout=5000
            )
            external_process.terminate()
            external_process.wait(timeout=3)
            page.locator("#pywebio-scope-header_status").get_by_text(
                "Inactive", exact=True
            ).wait_for(timeout=5000)


@pytest.mark.playwright
def test_home_card_does_not_run_background_server_update_checks(tmp_path, monkeypatch):
    calls_file = tmp_path / "home-update-check.txt"
    _prepare_fixed_palserver_python(tmp_path)
    _prepare_fixed_steamcmd(tmp_path)
    manifest = _fixed_palserver_dir(tmp_path) / "steamapps" / "appmanifest_2394010.acf"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('"buildid" "12345"', encoding="utf-8")
    with _gui_page(
        tmp_path,
        monkeypatch,
        extra_env={"PALSITTER_FAKE_STEAMCMD_CALLS": str(calls_file)},
    ) as (page, _):
        page.wait_for_timeout(6000)
        calls = calls_file.read_text(encoding="ascii") if calls_file.exists() else ""
        assert "+app_info_print 2394010" not in calls
        assert "+app_update 2394010" not in calls
        card = page.locator('.instance-card[data-instance-card="default"]')
        card.wait_for(timeout=10000)


@pytest.mark.playwright
@pytest.mark.serial_playwright
def test_install_failure_returns_to_start_without_scheduler_retry_or_progress(tmp_path, monkeypatch):
    steam_calls = tmp_path / "install-steamcmd.txt"
    _prepare_fixed_steamcmd(tmp_path)
    with _gui_page(
        tmp_path,
        monkeypatch,
        extra_env={"PALSITTER_FAKE_STEAMCMD_CALLS": str(steam_calls)},
    ) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        scheduler = page.locator("#pywebio-scope-scheduler_panel")
        scheduler.get_by_role("button", name="Start", exact=True).wait_for(timeout=5000)
        scheduler.get_by_role("button", name="Start", exact=True).click()
        page.locator("#pywebio-scope-header_status").get_by_text(
            "Updating", exact=True
        ).wait_for(timeout=5000)
        scheduler.get_by_role("button", name="Start", exact=True).wait_for(timeout=10000)
        assert scheduler.get_by_role("button", name="Retry", exact=True).count() == 0
        assert scheduler.locator(".scheduler-operation-progress").count() == 0
        assert scheduler.locator("#pywebio-scope-scheduler_operation_progress").count() == 0
        assert "+app_update 2394010" in steam_calls.read_text(encoding="ascii")


@pytest.mark.playwright
def test_external_overview_operations_and_players_page(tmp_path, monkeypatch):
    with _running_palserver_process(tmp_path), _mock_metrics_server() as (rest_port, calls):
        with _gui_page(tmp_path, monkeypatch, rest_port=rest_port) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
            operations = page.locator("#pywebio-scope-scheduler_panel")
            operations.get_by_role("button", name="Detach", exact=True).wait_for(timeout=10000)
            for forbidden in ("KILL", "Restart", "Update server", "Validate / Repair"):
                assert operations.get_by_role("button", name=forbidden, exact=True).count() == 0
            for allowed in ("Stop", "Save", "Backup", "Detach"):
                assert operations.get_by_role("button", name=allowed, exact=True).count() == 1
            assert operations.get_by_role("button", name="Logs", exact=True).count() == 0
            assert page.locator("#pywebio-scope-instance_actions").count() == 0

            page.locator("#pywebio-scope-menu").get_by_text("Players", exact=True).click()
            detail = page.locator("#pywebio-scope-players_detail_panel")
            assert page.locator("#pywebio-scope-players_detail_summary").count() == 0
            detail.get_by_text("Alice", exact=False).first.wait_for(timeout=15000)
            detail.get_by_text("Ping 23ms", exact=False).wait_for(timeout=5000)
            detail.get_by_text("Location -344, 270", exact=False).wait_for(timeout=5000)
            detail.get_by_text("Buildings 7", exact=False).wait_for(timeout=5000)
            assert detail.get_by_text("203.0.113.5", exact=False).count() == 0
            masked = detail.locator(".player-userid-value")
            assert masked.inner_text() == "••••"
            reveal = detail.get_by_role("button", name="Reveal ID", exact=True)
            copy = detail.get_by_role("button", name="Copy ID", exact=True)
            reveal_before = reveal.bounding_box()
            copy_before = copy.bounding_box()
            detail.get_by_role("button", name="Reveal ID", exact=True).click()
            assert masked.inner_text() == "steam_1"
            reveal_after = detail.get_by_role("button", name="Hide ID", exact=True).bounding_box()
            copy_after = copy.bounding_box()
            assert reveal_before is not None and reveal_after is not None
            assert copy_before is not None and copy_after is not None
            assert abs(reveal_before["x"] - reveal_after["x"]) < 1
            assert abs(copy_before["x"] - copy_after["x"]) < 1
            assert detail.get_by_role("button", name="Copy ID", exact=True).count() == 1
            assert detail.get_by_role("button", name="Broadcast", exact=True).count() == 0
            assert detail.locator('input[name="players_broadcast_message"]').count() == 0
            assert detail.locator('input[name="players_detail_unban_userid"]').count() == 0
            detail.get_by_text("No banned players.", exact=True).wait_for(
                timeout=5000
            )

            row = detail.locator("#pywebio-scope-players_detail_list > div").first
            row.get_by_role("button", name="Kick", exact=True).click()
            page.get_by_label("Reason (optional)", exact=True).fill("Please reconnect")
            page.locator(".modal.show").get_by_role(
                "button", name="Yes, kick", exact=True
            ).click()
            deadline = time.time() + 5
            while len(calls) < 1 and time.time() < deadline:
                time.sleep(0.05)
            assert calls[:1] == [
                (
                    "/v1/api/kick",
                    {"userid": "steam_1", "message": "Please reconnect"},
                ),
            ]
            assert any(
                event.type == "palsitter_command"
                and event.message == "Executed: kick Alice (steam_1) (result: success)"
                for event in AuditStore("default").load()
            )

            page.locator("#pywebio-scope-menu").get_by_text("Overview", exact=True).click()
            operations = page.locator("#pywebio-scope-scheduler_panel")
            operations.get_by_role("button", name="Detach", exact=True).click()
            operations.get_by_role("button", name="Start", exact=True).wait_for(timeout=5000)


@pytest.mark.playwright
def test_players_poll_retains_stale_roster_on_api_failure(tmp_path, monkeypatch):
    with _running_palserver_process(tmp_path), _mock_metrics_server(players_fail_after=2) as (rest_port, _):
        with _gui_page(tmp_path, monkeypatch, rest_port=rest_port) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
            page.locator("#pywebio-scope-menu").get_by_text("Players", exact=True).click()
            detail = page.locator("#pywebio-scope-players_detail_panel")
            detail.get_by_text("Alice", exact=False).first.wait_for(timeout=15000)
            player_id = detail.locator(".player-userid-value")
            detail.get_by_role("button", name="Reveal ID", exact=True).click()
            assert player_id.inner_text() == "steam_1"
            player_id.evaluate("node => window.__revealedPlayerIdNode = node")
            detail.get_by_text("Alice (Lv: 18)", exact=True).wait_for(timeout=15000)
            assert player_id.inner_text() == "steam_1"
            detail.get_by_text("Could not refresh all player data", exact=False).wait_for(
                timeout=15000
            )
            assert detail.get_by_text("Alice", exact=False).count() >= 1
            assert player_id.inner_text() == "steam_1"
            assert player_id.evaluate("node => node === window.__revealedPlayerIdNode")
            page.locator("#pywebio-scope-aside").get_by_text("Home", exact=True).click()
            detail.wait_for(state="detached", timeout=5000)


@pytest.mark.playwright
def test_world_filters_structured_launch_and_auto_restart_settings(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, config_dir):
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        page.locator("#pywebio-scope-menu").get_by_text("World Settings", exact=True).click()
        page.locator("#pywebio-scope-world_settings_form").wait_for(timeout=5000)
        search = page.locator("#world-settings-search")
        page.evaluate(
            "window.__worldFieldNode = document.querySelector('input[name=world_ServerDescription]')"
        )
        search.fill("ServerDescription")
        description = page.locator('input[name="world_ServerDescription"]')
        assert description.is_visible()
        assert not page.locator('input[name="world_DayTimeSpeedRate"]').is_visible()
        description.fill("Filtered but preserved")
        page.get_by_text("Changed: 1", exact=True).wait_for(timeout=3000)
        page.locator("#world-changed-only").click()
        assert page.evaluate(
            "document.querySelector('input[name=world_ServerDescription]') === window.__worldFieldNode"
        )
        search.fill("")
        page.locator('button[data-world-category="server_admin_network"]').click()
        assert description.is_visible()
        assert not page.locator('input[name="world_DayTimeSpeedRate"]').is_visible()

        page.locator("#pywebio-scope-world_settings_actions").get_by_role(
            "button", name="Reset", exact=True
        ).click()
        page.locator("#pywebio-scope-menu").get_by_text("Server Settings", exact=True).click()
        settings = page.locator("#pywebio-scope-settings_panel")
        settings.wait_for(timeout=5000)
        worker = page.locator('input[name="settings_launch_worker_threads_server"]')
        page.evaluate(
            "window.__settingsWorkerNode = document.querySelector('input[name=settings_launch_worker_threads_server]')"
        )
        page.locator("#server-settings-search").fill("launch_worker_threads_server")
        assert worker.is_visible()
        assert page.locator('input[name="settings_memory_restart_mb"]').count() == 0
        page.locator("#server-settings-search").fill("")
        assert page.evaluate(
            "document.querySelector('input[name=settings_launch_worker_threads_server]') === window.__settingsWorkerNode"
        )
        page.locator("#pywebio-scope-settings_toggle_launch_useperfthreads").get_by_role(
            "button", name="Off", exact=True
        ).click()
        worker.fill("4")
        page.locator('textarea[name="settings_extra_args"]').fill("-UsePerfThreads")
        assert page.locator("#pywebio-scope-instance_actions").count() == 0
        assert page.locator("#pywebio-scope-settings_actions").is_visible()
        page.locator("#pywebio-scope-settings_actions").get_by_role(
            "button", name="Save", exact=True
        ).click()
        page.get_by_text(
            "Advanced arguments cannot duplicate structured launch option", exact=False
        ).wait_for(timeout=5000)

        page.locator('textarea[name="settings_extra_args"]').fill("-custom=value")
        page.locator("#pywebio-scope-settings_actions").get_by_role(
            "button", name="Save", exact=True
        ).click()
        page.get_by_text("Settings saved", exact=True).wait_for(timeout=5000)

        page.locator("#pywebio-scope-menu").get_by_text("Auto Restart", exact=True).click()
        page.locator("#pywebio-scope-auto_restart_form").wait_for(timeout=5000)
        page.locator('input[name="settings_memory_restart_mb"]').fill("2048")
        page.locator('input[name="settings_crash_restart_limit_per_hour"]').fill("3")
        page.locator('select[name="settings_planned_restart_mode"]').select_option(
            label="Daily"
        )
        page.locator('input[name="settings_planned_restart_daily_time"]').fill("28:00")
        page.locator("#pywebio-scope-auto_restart_actions").get_by_role(
            "button", name="Save", exact=True
        ).click()
        page.get_by_text("Enter a 24-hour time in HH:MM format.", exact=True).wait_for(
            timeout=5000
        )
        page.locator('input[name="settings_planned_restart_daily_time"]').fill("04:30")
        page.evaluate(
            "window.__autoRestartField = document.querySelector('input[name=settings_memory_restart_mb]')"
        )
        page.locator("#pywebio-scope-auto_restart_actions").get_by_role(
            "button", name="Save", exact=True
        ).click()
        page.wait_for_function(
            "document.querySelector('input[name=settings_memory_restart_mb]') !== window.__autoRestartField",
            timeout=5000,
        )
        page.get_by_text("Settings saved", exact=True).last.wait_for(timeout=5000)
        monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(config_dir))
        saved = load_profile("default")
        assert saved.launch_useperfthreads is True
        assert saved.launch_worker_threads_server == 4
        assert saved.extra_args == ["-custom=value"]
        assert saved.memory_restart_mb == 2048
        assert saved.crash_restart_limit_per_hour == 3
        assert saved.planned_restart_mode == "daily"
        assert saved.planned_restart_daily_time == "04:30"

        page.locator("#pywebio-scope-menu").get_by_text("Overview", exact=True).click()
        assert page.locator("#pywebio-scope-reliability_card").count() == 0


@pytest.mark.playwright
def test_dedicated_save_import_preview_and_managed_world_switch(tmp_path, monkeypatch):
    first_id = "A" * 32
    second_id = "B" * 32
    source_root = tmp_path / "source-server"
    first = source_root / "Pal" / "Saved" / "SaveGames" / "0" / first_id
    (first / "Players").mkdir(parents=True)
    (first / "Level.sav").write_text("original", encoding="utf-8")
    (first / "Players" / "one.sav").write_text("player", encoding="utf-8")
    (first / "Level.sav.bak").write_text("backup", encoding="utf-8")
    (first / "notes.txt").write_text("notes", encoding="utf-8")
    config_directory = "WindowsServer" if os.name == "nt" else "LinuxServer"
    source_ini = source_root / "Pal" / "Saved" / "Config" / config_directory / "PalWorldSettings.ini"
    source_ini.parent.mkdir(parents=True, exist_ok=True)
    source_ini.write_text("source world settings", encoding="utf-8")

    with _gui_page(tmp_path, monkeypatch) as (page, config_dir):
        page.locator("#pywebio-scope-aside").get_by_text("Add", exact=True).click()
        page.get_by_label("Profile name", exact=True).fill("imported")
        import_path = first / "Level.sav"
        page.get_by_label("Level.sav file", exact=True).fill(str(import_path))
        page.locator("#pywebio-scope-add_import_panel").get_by_role(
            "button", name="Browse", exact=True
        ).click()
        browser = page.locator(".modal.show")
        browser.get_by_text("Players", exact=True).wait_for(timeout=5000)
        browser.get_by_text("Level.sav", exact=True).wait_for(timeout=5000)
        assert browser.get_by_text("WorldOption.sav", exact=True).count() == 0
        assert browser.get_by_text("level.sav.bak", exact=True).count() == 0
        assert browser.get_by_text("notes.txt", exact=True).count() == 0
        browser.get_by_text("Level.sav", exact=True).click()
        browser.locator("#pywebio-scope-browse_actions").get_by_role(
            "button", name="Open", exact=True
        ).click()
        page.locator('input[name="add_import_path"]').wait_for(timeout=5000)
        assert page.locator('input[name="add_import_path"]').input_value() == str(import_path)
        page.get_by_role("button", name="Confirm", exact=True).click()
        page.locator("#pywebio-scope-menu .menu-active").get_by_text(
            "Overview", exact=True
        ).wait_for(timeout=5000)
        monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(config_dir))
        imported = load_profile("imported")
        assert imported.dedicated_server_name == first_id
        managed_first = Path(imported.backup_source) / first_id
        assert (managed_first / "Level.sav").read_text(encoding="utf-8") == "original"
        imported_ini = (
            Path(imported.workdir)
            / "Pal"
            / "Saved"
            / "Config"
            / config_directory
            / "PalWorldSettings.ini"
        )
        assert imported_ini.read_text(encoding="utf-8") == "source world settings"
        assert (first / "Level.sav").read_text(encoding="utf-8") == "original"

        second = Path(imported.backup_source) / second_id
        (second / "Players").mkdir(parents=True)
        (second / "Level.sav").write_text("second", encoding="utf-8")
        (second / "Players" / "two.sav").write_text("player", encoding="utf-8")
        page.locator("#pywebio-scope-menu").get_by_text(
            "Saves & Backups", exact=True
        ).click()
        worlds = page.locator("#pywebio-scope-managed_worlds")
        worlds.locator("tr", has_text=first_id).get_by_text("Active", exact=True).wait_for(
            timeout=5000
        )
        second_row = worlds.locator("tr", has_text=second_id)
        second_row.get_by_role("button", name="Switch", exact=True).click()
        page.locator(".modal.show").get_by_role("button", name="Switch", exact=True).click()
        page.get_by_text(f"Active world changed to {second_id}", exact=False).wait_for(
            timeout=10000
        )
        assert load_profile("imported").dedicated_server_name == second_id
        assert list(Path(imported.backup_dir).glob("*.zip"))
        assert (first / "Level.sav").exists()


@pytest.mark.playwright
def test_single_player_save_import_starts_migration_workflow(tmp_path, monkeypatch):
    world_id = "C" * 32
    world = tmp_path / "Pal" / "Saved" / "SaveGames" / "76561198170852193" / world_id
    (world / "Players").mkdir(parents=True)
    (world / "Level.sav").write_text("level", encoding="utf-8")
    (world / "Players" / "00000000000000000000000000000001.sav").write_text(
        "player", encoding="utf-8"
    )
    codec = WorldOptionSavCodec()
    codec.write(
        world / "WorldOption.sav",
        merge_option_values(codec.load_template(), {"ServerName": "Imported single-player world"}),
    )

    with _gui_page(tmp_path, monkeypatch) as (page, config_dir):
        page.locator("#pywebio-scope-aside").get_by_text("Add", exact=True).click()
        page.get_by_label("Profile name", exact=True).fill("singleplayer-import")
        level_path = world / "Level.sav"
        page.get_by_label("Level.sav file", exact=True).fill(str(level_path))
        page.locator("#pywebio-scope-add_import_panel").get_by_role(
            "button", name="Browse", exact=True
        ).click()
        browser = page.locator(".modal.show")
        browser.get_by_text("Level.sav", exact=True).click()
        browser.locator("#pywebio-scope-browse_actions").get_by_role(
            "button", name="Open", exact=True
        ).click()
        page.get_by_role("button", name="Confirm", exact=True).click()

        page.locator("#pywebio-scope-menu .menu-active").get_by_text(
            "Overview", exact=True
        ).wait_for(timeout=5000)
        page.get_by_text("Single-player world imported", exact=False).wait_for(timeout=5000)
        monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(config_dir))
        imported = load_profile("singleplayer-import")
        assert (Path(imported.backup_source) / world_id / "Level.sav").read_text(
            encoding="utf-8"
        ) == "level"
        imported_world = Path(imported.backup_source) / world_id
        assert not (imported_world / "WorldOption.sav").exists()
        imported_ini = resolve_ini_path(imported)
        imported_values = read_ini_option_settings(imported_ini)
        assert imported_values["ServerName"] == "Imported single-player world"
        assert imported_values["PublicPort"] == imported.game_port
        assert imported_values["RESTAPIPort"] == imported.rest_port
        assert (world / "WorldOption.sav").exists()


@pytest.mark.playwright
def test_saves_removes_test_schedule_and_keeps_manual_flush(tmp_path, monkeypatch):
    source = _fixed_backup_source(tmp_path)
    world = source / ("C" * 32)
    world.mkdir(parents=True)
    (world / "Level.sav").write_text("save", encoding="utf-8")
    with _running_palserver_process(tmp_path), _mock_metrics_server() as (rest_port, calls):
        with _gui_page(tmp_path, monkeypatch, rest_port=rest_port) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
            page.locator("#pywebio-scope-menu").get_by_text(
                "Saves & Backups", exact=True
            ).click()
            panel = page.locator("#pywebio-scope-backup_settings_panel")
            assert panel.get_by_role("button", name="Test schedule", exact=True).count() == 0
            panel.get_by_role("button", name="Backup now", exact=True).click()
            page.get_by_text("Backup created:", exact=False).wait_for(timeout=5000)
            assert ("/v1/api/save", {}) in calls
            assert len(list((tmp_path / "backups").glob("*.zip"))) >= 1


@pytest.mark.playwright
def test_guided_ini_recovery_click_path(tmp_path, monkeypatch):
    config_dir = "WindowsServer" if os.name == "nt" else "LinuxServer"
    ini_path = (
        _fixed_palserver_dir(tmp_path)
        / "Pal"
        / "Saved"
        / "Config"
        / config_dir
        / "PalWorldSettings.ini"
    )
    ini_path.parent.mkdir(parents=True)
    ini_path.write_text("[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(broken", encoding="utf-8")

    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        page.locator("#pywebio-scope-menu").get_by_text("World Settings", exact=True).click()
        recovery = page.locator("#pywebio-scope-world_recovery_panel")
        recovery.get_by_text("PalWorldSettings.ini needs recovery", exact=True).wait_for(
            timeout=5000
        )
        recovery.get_by_role(
            "button", name="Preserve copy & regenerate INI", exact=True
        ).click()
        page.locator(".modal.show").get_by_role(
            "button", name="Preserve copy & regenerate INI", exact=True
        ).click()
        page.locator("#pywebio-scope-world_settings_form").wait_for(timeout=5000)
        assert list(ini_path.parent.glob("PalWorldSettings.ini.malformed-*.bak"))

@pytest.mark.playwright
def test_390px_responsive_overview_navigation_without_action_strip(tmp_path, monkeypatch):
    with _gui_page(tmp_path, monkeypatch) as (page, _):
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        menu = page.locator("#pywebio-scope-menu")
        menu.get_by_text("Saves & Backups", exact=True).wait_for(timeout=5000)
        menu_boxes = [item.bounding_box() for item in menu.locator(".menu-button").all()]
        assert all(box is not None for box in menu_boxes)
        assert max(box["y"] for box in menu_boxes) - min(box["y"] for box in menu_boxes) < 3
        assert menu.evaluate("element => getComputedStyle(element).overflowX") == "auto"

        scheduler_box = page.locator("#pywebio-scope-scheduler").bounding_box()
        log_box = page.locator("#pywebio-scope-scheduler_log").bounding_box()
        assert scheduler_box is not None and log_box is not None
        assert abs(scheduler_box["x"] - log_box["x"]) < 3
        assert log_box["y"] >= scheduler_box["y"] + scheduler_box["height"]
        assert page.locator("#pywebio-scope-instance_actions").count() == 0
        operations = page.locator("#pywebio-scope-scheduler_panel")
        for action in ("Start", "Backup"):
            box = operations.get_by_role("button", name=action, exact=True).bounding_box()
            assert box is not None
            assert box["x"] >= 0 and box["x"] + box["width"] <= 390
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
        )


@pytest.mark.playwright
def test_world_settings_uses_ini_only_when_legacy_sav_exists(tmp_path, monkeypatch):
    backup_source = _fixed_backup_source(tmp_path)
    dedicated_name = "C3" * 16
    save_dir = backup_source / dedicated_name
    save_dir.mkdir(parents=True)

    codec = WorldOptionSavCodec()
    seeded = merge_option_values(codec.load_template(), {"RandomizerType": "Region"})
    sav_path = save_dir / "WorldOption.sav"
    codec.write(sav_path, seeded)

    with _gui_page(
        tmp_path,
        monkeypatch,
        profile_overrides={"dedicated_server_name": dedicated_name},
    ) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default").click()
        page.locator("#pywebio-scope-menu").get_by_text("World Settings").click()
        page.locator("#pywebio-scope-world_settings_form").wait_for(timeout=5000)

        assert page.locator('select[name="world_settings_format"]').count() == 0
        assert page.locator("#pywebio-scope-world_settings_warning").count() == 0
        randomizer_select = page.locator('select[name="world_RandomizerType"]')
        assert randomizer_select.locator("option:checked").inner_text() == "None"

        page.locator('input[name="world_ServerDescription"]').fill("Changed via ini")
        page.locator("#pywebio-scope-world_settings_actions").get_by_role(
            "button", name="Save", exact=True
        ).click()
        page.get_by_text("World settings saved", exact=True).wait_for(timeout=5000)

        assert page.locator('select[name="world_settings_format"]').count() == 0

    reread = extract_option_values(codec.read(sav_path))
    assert reread["RandomizerType"] == "Region"


@pytest.mark.playwright
def test_mods_page_hides_ue4ss_components_on_linux(tmp_path, monkeypatch):
    _prepare_fixed_palserver_python(tmp_path)
    root = _fixed_palserver_dir(tmp_path)
    pak_dir = root / "Pal" / "Content" / "Paks"
    pak_dir.mkdir(parents=True)
    (pak_dir / "LinuxPak.pak").write_bytes(b"pak")

    with _gui_page(
        tmp_path,
        monkeypatch,
        extra_env={"PALSITTER_TEST_UE4SS_PLATFORM_SUPPORTED": "0"},
    ) as (page, _):
        page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
        page.locator("#pywebio-scope-menu").get_by_text("Mods", exact=True).click()
        panel = page.locator("#pywebio-scope-mods_panel")

        panel.get_by_text("UE4SS mod loader", exact=True).wait_for(timeout=5000)
        panel.get_by_text(
            "Unavailable: UE4SS Lua/C++ management is not supported for native Linux "
            "Palworld servers.",
            exact=True,
        ).wait_for(timeout=5000)
        assert panel.locator('select[name="ue4ss_release"]').count() == 0
        assert panel.get_by_text("Lua mods (UE4SS)", exact=True).count() == 0
        panel.get_by_text("Pak mods", exact=True).wait_for(timeout=5000)
        panel.get_by_text("LinuxPak.pak", exact=True).wait_for(timeout=5000)


@pytest.mark.playwright
def test_mods_page_installs_lists_opens_folders_and_removes_ue4ss(tmp_path, monkeypatch):
    _prepare_fixed_palserver_python(tmp_path)
    root = _fixed_palserver_dir(tmp_path)
    pak_dir = root / "Pal" / "Content" / "Paks"
    logic_dir = pak_dir / "LogicMods"
    tilde_mods_dir = pak_dir / "~mods"
    logic_dir.mkdir(parents=True)
    tilde_mods_dir.mkdir()
    pak_file = pak_dir / "ExamplePak.pak"
    pak_file.write_bytes(b"pak")
    initially_disabled = pak_dir / "InitiallyDisabled.pak.disabled"
    initially_disabled.write_bytes(b"disabled")
    (pak_dir / "Pal-WindowsServer.pak").write_bytes(b"game")
    (logic_dir / "ExampleBlueprint.pak").write_bytes(b"logic")
    (tilde_mods_dir / "CreativeMenu_P.pak").write_bytes(b"tilde")
    open_log = tmp_path / "opened-mod-folder.txt"

    with _mock_ue4ss_server() as (github_api, calls):
        with _gui_page(
            tmp_path,
            monkeypatch,
            extra_env={
                "PALSITTER_TEST_UE4SS_DOWNLOAD_URL": (
                    f"{github_api}/download/UE4SS-Palworld.zip"
                ),
                "PALSITTER_TEST_UE4SS_PLATFORM_SUPPORTED": "1",
                "PALSITTER_FAKE_OPEN_FOLDER_LOG": str(open_log),
            },
        ) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
            menu = page.locator("#pywebio-scope-menu")
            menu.get_by_text("Mods", exact=True).click()
            panel = page.locator("#pywebio-scope-mods_panel")
            release_select = panel.locator('select[name="ue4ss_release"]')
            release_select.wait_for(timeout=5000)

            assert release_select.locator("option").count() == 1
            assert release_select.locator("option:checked").inner_text().startswith(
                "experimental-palworld"
            )
            assert panel.get_by_text("PalDefender", exact=False).count() == 0
            assert panel.get_by_text(re.compile("upload", re.IGNORECASE)).count() == 0
            panel.get_by_text("Not installed", exact=True).wait_for(timeout=5000)
            assert panel.get_by_text("ExamplePak.pak", exact=True).count() == 1
            assert panel.get_by_text("LogicMods/ExampleBlueprint.pak", exact=True).count() == 1
            assert panel.get_by_text("~mods/CreativeMenu_P.pak", exact=True).count() == 1
            assert panel.get_by_text("Pal-WindowsServer.pak", exact=True).count() == 0
            pak_table = page.locator("#pywebio-scope-pak_mods table")
            assert pak_table.locator("th").all_inner_texts() == [
                "Mod",
                "Enabled",
                "Delete",
            ]
            column_widths = pak_table.locator("th").evaluate_all(
                "elements => elements.map(element => getComputedStyle(element).width)"
            )
            creative_checkbox = panel.get_by_role(
                "checkbox", name="Enabled ~mods/CreativeMenu_P.pak", exact=True
            )
            assert creative_checkbox.is_checked()
            creative_checkbox.click()
            panel.get_by_text(
                "~mods/CreativeMenu_P.pak.disabled", exact=True
            ).wait_for(timeout=5000)
            assert (tilde_mods_dir / "CreativeMenu_P.pak.disabled").exists()
            assert pak_table.locator("th").evaluate_all(
                "elements => elements.map(element => getComputedStyle(element).width)"
            ) == column_widths
            disabled_creative_checkbox = panel.get_by_role(
                "checkbox",
                name="Enabled ~mods/CreativeMenu_P.pak.disabled",
                exact=True,
            )
            assert not disabled_creative_checkbox.is_checked()
            disabled_creative_checkbox.click()
            panel.get_by_text("~mods/CreativeMenu_P.pak", exact=True).wait_for(timeout=5000)
            assert (tilde_mods_dir / "CreativeMenu_P.pak").exists()

            initially_disabled_checkbox = panel.get_by_role(
                "checkbox", name="Enabled InitiallyDisabled.pak.disabled", exact=True
            )
            assert not initially_disabled_checkbox.is_checked()
            initially_disabled_checkbox.click()
            panel.get_by_text("InitiallyDisabled.pak", exact=True).wait_for(timeout=5000)
            assert (pak_dir / "InitiallyDisabled.pak").exists()

            panel.get_by_role(
                "button", name="Delete LogicMods/ExampleBlueprint.pak", exact=True
            ).click()
            delete_modal = page.locator(".modal.show")
            delete_modal.get_by_text(
                "Delete LogicMods/ExampleBlueprint.pak? This cannot be undone.", exact=True
            ).wait_for(timeout=5000)
            delete_modal.get_by_role("button", name="Delete", exact=True).click()
            panel.get_by_text("LogicMods/ExampleBlueprint.pak", exact=True).wait_for(
                state="detached", timeout=5000
            )
            assert not (logic_dir / "ExampleBlueprint.pak").exists()

            panel.get_by_role("button", name="Install", exact=True).click()
            page.locator(".toastify").get_by_text(
                "UE4SS experimental-palworld installed", exact=True
            ).wait_for(timeout=10000)
            panel.get_by_text("Installed: experimental-palworld", exact=True).wait_for(timeout=5000)
            panel.get_by_text("ExampleLua", exact=True).wait_for(timeout=5000)

            win64 = root / "Pal" / "Binaries" / "Win64"
            settings = win64 / "ue4ss" / "UE4SS-settings.ini"
            deadline = time.time() + 5
            while not settings.exists() and time.time() < deadline:
                time.sleep(0.05)
            assert "bUseUObjectArrayCache = false" in settings.read_text(encoding="utf-8")

            lua_dir = win64 / "ue4ss" / "Mods"
            shutil.rmtree(lua_dir)
            panel.get_by_role("button", name="Open Lua mods folder", exact=True).click()
            deadline = time.time() + 5
            while not open_log.exists() and time.time() < deadline:
                time.sleep(0.05)
            assert open_log.read_text(encoding="utf-8") == str(win64 / "ue4ss" / "Mods")
            assert lua_dir.is_dir()
            preserved_mod = lua_dir / "PreservedMod"
            preserved_mod.mkdir()

            shutil.rmtree(logic_dir)
            shutil.rmtree(tilde_mods_dir)
            panel.get_by_role("button", name="Open Paks folder", exact=True).click()
            deadline = time.time() + 5
            while open_log.read_text(encoding="utf-8") != str(pak_dir) and time.time() < deadline:
                time.sleep(0.05)
            assert open_log.read_text(encoding="utf-8") == str(pak_dir)
            assert logic_dir.is_dir()
            assert tilde_mods_dir.is_dir()

            panel.get_by_role("button", name="Remove", exact=True).click()
            modal = page.locator(".modal.show")
            modal.get_by_text("Lua mods are preserved", exact=False).wait_for(timeout=5000)
            modal.get_by_role("button", name="Remove", exact=True).click()
            page.locator(".toastify").get_by_text("UE4SS removed", exact=True).wait_for(timeout=10000)
            panel.get_by_text("Not installed", exact=True).wait_for(timeout=5000)
            assert preserved_mod.is_dir()
            assert (win64 / "ue4ss").exists()
            assert not (win64 / "ue4ss" / "UE4SS.dll").exists()
            assert not (win64 / "dwmapi.dll").exists()
            assert pak_file.exists()

    assert calls == ["/download/UE4SS-Palworld.zip"]


@pytest.mark.playwright
def test_overview_streams_ue4ss_log_for_attached_server(tmp_path, monkeypatch):
    _prepare_fixed_palserver_python(tmp_path)
    root = _fixed_palserver_dir(tmp_path)

    with _mock_ue4ss_server() as (github_api, _):
        with _gui_page(
            tmp_path,
            monkeypatch,
            extra_env={
                "PALSITTER_TEST_UE4SS_DOWNLOAD_URL": (
                    f"{github_api}/download/UE4SS-Palworld.zip"
                ),
                "PALSITTER_TEST_UE4SS_PLATFORM_SUPPORTED": "1",
            },
        ) as (page, _):
            page.locator("#pywebio-scope-aside").get_by_text("default", exact=True).click()
            menu = page.locator("#pywebio-scope-menu")
            menu.get_by_text("Mods", exact=True).click()
            panel = page.locator("#pywebio-scope-mods_panel")
            panel.locator('select[name="ue4ss_release"]').wait_for(timeout=5000)
            panel.get_by_role("button", name="Install", exact=True).click()
            page.locator(".toastify").get_by_text(
                "UE4SS experimental-palworld installed", exact=True
            ).wait_for(timeout=10000)

            ue4ss_log = root / "Pal" / "Binaries" / "Win64" / "ue4ss" / "UE4SS.log"
            ue4ss_log.write_text("existing UE4SS line\n", encoding="utf-8")
            with _running_palserver_process(tmp_path):
                menu.get_by_text("Overview", exact=True).click()
                log_box = page.locator(".log-box")
                log_box.get_by_text("UE4SS: existing UE4SS line").wait_for(
                    timeout=10000
                )
                with ue4ss_log.open("a", encoding="utf-8") as handle:
                    handle.write("new UE4SS line\n")
                log_box.get_by_text("UE4SS: new UE4SS line").wait_for(
                    timeout=10000
                )

            overview_log = profile_log_path("default")
            assert "UE4SS: existing UE4SS line" in overview_log.read_text(encoding="utf-8")
            assert "UE4SS: new UE4SS line" in overview_log.read_text(encoding="utf-8")
