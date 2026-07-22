from __future__ import annotations

from dataclasses import dataclass, field
import threading
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from pywebio.session import local


@dataclass
class PageContext:
    """Identity and cancellation state for one rendered page."""

    generation: int
    stop_event: threading.Event = field(default_factory=threading.Event)
    active: bool = True

    def invalidate(self) -> None:
        self.active = False
        self.stop_event.set()


def initialize_page_lifecycle() -> list[tuple[Callable[[], None], bool]]:
    cleanups: list[tuple[Callable[[], None], bool]] = []
    local.page_cleanups = cleanups
    local.page_context = None
    local.page_generation = 0
    local.navigation_request = 0
    local.navigation_request_lock = threading.RLock()
    local.navigation_render_lock = threading.RLock()
    local.page_lifecycle_lock = threading.RLock()
    return cleanups


def register_page_cleanup(cleanup: Callable[[], None], *, session_safe: bool = False) -> None:
    cleanups = getattr(local, "page_cleanups", None)
    if cleanups is None:
        cleanups = []
        local.page_cleanups = cleanups
    cleanups.append((cleanup, session_safe))


def register_stop_event(stop_event: threading.Event) -> None:
    """Register a session-wide worker stop event."""

    register_page_cleanup(stop_event.set, session_safe=True)


def register_page_stop_event(stop_event: threading.Event) -> None:
    """Stop a page-owned worker on navigation and at session shutdown."""

    register_page_cleanup(stop_event.set)


def page_context() -> PageContext | None:
    return getattr(local, "page_context", None)


def request_navigation() -> int:
    """Reserve the newest navigation request for this session."""

    lock = getattr(local, "navigation_request_lock", None)
    if lock is None:
        local.navigation_request_lock = lock = threading.RLock()
    with lock:
        request = int(getattr(local, "navigation_request", 0)) + 1
        local.navigation_request = request
        return request


def is_navigation_current(request: int) -> bool:
    lock = getattr(local, "navigation_request_lock", None)
    if lock is None:
        return int(getattr(local, "navigation_request", 0)) == request
    with lock:
        return int(getattr(local, "navigation_request", 0)) == request


@contextmanager
def navigation_transaction():
    """Serialize page rendering while allowing newer requests to supersede waiters."""

    request = request_navigation()
    lock = getattr(local, "navigation_render_lock", None)
    if lock is None:
        local.navigation_render_lock = lock = threading.RLock()
    lock.acquire()
    try:
        yield request if is_navigation_current(request) else None
    finally:
        lock.release()


def begin_page_navigation(request: int | None = None) -> PageContext | None:
    """Invalidate the old page and create a context for the newest request."""

    lock = getattr(local, "page_lifecycle_lock", None)
    if lock is None:
        local.page_lifecycle_lock = lock = threading.RLock()
    with lock:
        if request is not None and not is_navigation_current(request):
            return None
        previous = getattr(local, "page_context", None)
        if previous is not None:
            previous.invalidate()
        cleanup_page()
        generation = int(getattr(local, "page_generation", 0)) + 1
        context = PageContext(generation=generation)
        local.page_generation = generation
        local.page_context = context
        return context


def is_current(context: PageContext | None) -> bool:
    # PageContext is captured by worker callbacks and is shared across the
    # session. Checking its invalidation flag avoids relying on PyWebIO's
    # thread-local view of ``local`` inside registered worker threads.
    return context is not None and context.active


def run_if_current(context: PageContext | None, callback: Callable[[], Any]) -> Any:
    """Run a page UI update atomically only while its page is current."""

    if context is None:
        return callback()
    lock = getattr(local, "page_lifecycle_lock", None)
    if lock is None:
        local.page_lifecycle_lock = lock = threading.RLock()
    with lock:
        if not is_current(context):
            return None
        return callback()


def _run_cleanups(cleanups: list[tuple[Callable[[], None], bool]], *, session_only: bool) -> None:
    pending = list(cleanups)
    cleanups.clear()
    remaining: list[tuple[Callable[[], None], bool]] = []
    for cleanup, session_safe in reversed(pending):
        if not session_only and session_safe:
            remaining.append((cleanup, session_safe))
            continue
        if session_only and not session_safe:
            continue
        try:
            cleanup()
        except Exception:
            # Navigation cleanup must not strand the user on the previous page.
            continue
    cleanups.extend(reversed(remaining))


def cleanup_page() -> None:
    _run_cleanups(getattr(local, "page_cleanups", []), session_only=False)


def cleanup_session(cleanups: list[tuple[Callable[[], None], bool]]) -> None:
    _run_cleanups(cleanups, session_only=True)


__all__ = [
    "cleanup_page",
    "cleanup_session",
    "PageContext",
    "begin_page_navigation",
    "initialize_page_lifecycle",
    "is_current",
    "is_navigation_current",
    "navigation_transaction",
    "page_context",
    "register_page_cleanup",
    "register_page_stop_event",
    "register_stop_event",
    "request_navigation",
    "run_if_current",
]
