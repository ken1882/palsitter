from __future__ import annotations

import threading
from collections.abc import Callable

from pywebio.session import local


def initialize_page_lifecycle() -> list[tuple[Callable[[], None], bool]]:
    cleanups: list[tuple[Callable[[], None], bool]] = []
    local.page_cleanups = cleanups
    return cleanups


def register_page_cleanup(cleanup: Callable[[], None], *, session_safe: bool = False) -> None:
    cleanups = getattr(local, "page_cleanups", None)
    if cleanups is None:
        cleanups = []
        local.page_cleanups = cleanups
    cleanups.append((cleanup, session_safe))


def register_stop_event(stop_event: threading.Event) -> None:
    register_page_cleanup(stop_event.set, session_safe=True)


def _run_cleanups(cleanups: list[tuple[Callable[[], None], bool]], *, session_only: bool) -> None:
    pending = list(cleanups)
    cleanups.clear()
    for cleanup, session_safe in reversed(pending):
        if session_only and not session_safe:
            continue
        try:
            cleanup()
        except Exception:
            # Navigation cleanup must not strand the user on the previous page.
            continue


def cleanup_page() -> None:
    _run_cleanups(getattr(local, "page_cleanups", []), session_only=False)


def cleanup_session(cleanups: list[tuple[Callable[[], None], bool]]) -> None:
    _run_cleanups(cleanups, session_only=True)


__all__ = [
    "cleanup_page",
    "cleanup_session",
    "initialize_page_lifecycle",
    "register_page_cleanup",
    "register_stop_event",
]
