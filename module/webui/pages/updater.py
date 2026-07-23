from __future__ import annotations
import os
import subprocess
import threading
from pathlib import Path
from pywebio.exceptions import SessionException
from pywebio.output import clear, put_button, put_loading, put_row, put_scope, put_table, put_text, use_scope
from pywebio.session import local, register_thread
from module.webui.i18n import t
from module.webui.session import page_context, register_page_stop_event, run_if_current
from module.webui.assets import put_asset_widget

def _home(*args, **kwargs):
    from module.webui.pages.home import _home as implementation
    return implementation(*args, **kwargs)

def _menu_button(*args, **kwargs):
    from module.webui.instance import _menu_button as implementation
    return implementation(*args, **kwargs)

def _set_frame(*args, **kwargs):
    from module.webui.instance import _set_frame as implementation
    return implementation(*args, **kwargs)

def _run_navigation(*args, **kwargs):
    from module.webui.instance import _run_navigation as implementation
    return implementation(*args, **kwargs)

def _utils(*args, **kwargs):
    from module.webui.pages.utils import _utils as implementation
    return implementation(*args, **kwargs)

def _force_restart(*args, **kwargs):
    from module.webui.pages.utils import _force_restart as implementation
    return implementation(*args, **kwargs)

UPDATER_REMOTE = "https://github.com/ken1882/palsitter.git"

UPDATER_BRANCH = "main"


def _put_updater_loading(shape: str, color: str, *, fill: bool = False) -> None:
    put_asset_widget(
        "shared.loading_indicator",
        {"shape": shape, "fill": fill, "indicator": put_loading(shape, color)},
        scope="updater_loading",
    )

def _git_commit(ref: str = "HEAD", count: int = 1) -> list[list[str]]:
    fmt = "%h---%an---%ai---%s"
    try:
        result = _run_git(
            "log",
            ref,
            f"-n{count}",
            f"--pretty=format:{fmt}",
        )
        if result.returncode != 0 or not result.stdout.strip():
            return [["", "", "", t("updater.unavailable")]]
        return [line.split("---", 3) for line in result.stdout.splitlines()]
    except (OSError, subprocess.SubprocessError):
        return [["", "", "", t("updater.unavailable")]]

def _run_git(*args: str, timeout: float = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
            [os.getenv("PALSITTER_GIT", "git"), *args],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

def _render_updater_tables() -> None:
    with use_scope("updater_info", clear=True):
        put_table(
            [
                [t("updater.local"), *_git_commit("HEAD", 1)[0]],
                [
                    t("updater.upstream"),
                    *_git_commit(f"origin/{UPDATER_BRANCH}", 1)[0],
                ],
            ],
            header=[
                "",
                "SHA1",
                t("updater.author"),
                t("updater.commit_time"),
                t("updater.commit_message"),
            ],
        )
    with use_scope("updater_detail", clear=True):
        put_text(t("updater.history"))
        put_table(
            _git_commit(f"origin/{UPDATER_BRANCH}", 20),
            header=[
                "SHA1",
                t("updater.author"),
                t("updater.commit_time"),
                t("updater.commit_message"),
            ],
        )

def _render_updater_state(state) -> None:
    clear("updater_loading")
    clear("updater_state")
    clear("updater_btn")
    if state == 0:
        _put_updater_loading("border", "secondary", fill=True)
        put_text(t("updater.latest_version"), scope="updater_state")
        put_button(
            t("updater.check_update"),
            onclick=_check_updater,
            color="info",
            scope="updater_btn",
        )
    elif state == 1:
        _put_updater_loading("grow", "success")
        put_text(t("updater.update_available"), scope="updater_state")
        put_button(
            t("updater.click_update"),
            onclick=_run_updater,
            color="success",
            scope="updater_btn",
        )
    elif state == "checking":
        _put_updater_loading("border", "primary")
        put_text(t("updater.checking"), scope="updater_state")
    elif state == "updating":
        _put_updater_loading("border", "primary")
        put_text(t("updater.updating"), scope="updater_state")
    elif state == "failed":
        _put_updater_loading("grow", "danger")
        put_text(t("updater.failed"), scope="updater_state")
        put_button(
            t("updater.retry"),
            onclick=_check_updater,
            color="primary",
            scope="updater_btn",
        )
    elif state == "finish":
        _put_updater_loading("grow", "success")
        put_text(t("updater.finished"), scope="updater_state")
        _render_updater_tables()

def _check_updater() -> None:
    from module.webui.shutdown import is_shutting_down

    if is_shutting_down():
        return
    _render_updater_state("checking")
    stop_event = threading.Event()
    register_page_stop_event(stop_event)
    context = page_context()

    def check() -> None:
        try:
            _run_git("remote", "set-url", "origin", UPDATER_REMOTE)
            fetched = _run_git("fetch", "origin", UPDATER_BRANCH, timeout=30)
            result = _run_git(
                "rev-list",
                "--count",
                f"HEAD..origin/{UPDATER_BRANCH}",
            )
            available = (
                fetched.returncode == 0
                and result.returncode == 0
                and int((result.stdout or "0").strip() or "0") > 0
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            available = False
        if stop_event.is_set():
            return
        try:
            run_if_current(
                context,
                lambda: (
                    _render_updater_state(1 if available else 0),
                    _render_updater_tables(),
                ),
            )
        except SessionException:
            return

    thread = threading.Thread(target=check, daemon=True)
    register_thread(thread)
    thread.start()


def _pull_update() -> bool:
    try:
        result = _run_git(
            "pull",
            "--ff-only",
            "origin",
            UPDATER_BRANCH,
            timeout=120,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False

def _run_updater() -> None:
    from module.webui.shutdown import is_shutting_down

    if is_shutting_down():
        return
    _render_updater_state("updating")
    stop_event = threading.Event()
    register_page_stop_event(stop_event)
    context = page_context()

    def update() -> None:
        succeeded = _pull_update()
        if not stop_event.is_set():
            try:
                if not succeeded:
                    run_if_current(context, lambda: _render_updater_state("failed"))
                    return
                def finish_update() -> None:
                    _render_updater_state("finish")
                    _force_restart()

                run_if_current(context, finish_update)
            except SessionException:
                return

    thread = threading.Thread(target=update, daemon=True)
    register_thread(thread)
    thread.start()

def _updater() -> None:
    return _run_navigation(_render_updater)

def _render_updater() -> None:
    if _set_frame(t("nav.updater"), "Home") is None:
        return
    clear("menu")
    with use_scope("menu"):
        _menu_button(t("nav.home"), _home)
        _menu_button(t("nav.updater"), _updater, True)
        _menu_button(t("nav.utils"), _utils)
    clear("content")
    with use_scope("content"):
        put_scope(
            "updater-state-row",
            [
                put_row(
                    [put_scope("updater_loading"), None, put_scope("updater_state")],
                    size="auto .25rem 1fr",
                )
            ],
        )
        put_scope("updater_btn")
        put_scope("updater_info")
        put_scope("updater_detail")
    _render_updater_state(0)
    _render_updater_tables()
    _check_updater()
