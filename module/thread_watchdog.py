from __future__ import annotations

import threading
from collections.abc import Callable


class ThreadWatchdog:
    """Run a long-lived target and restart it until explicitly stopped."""

    def __init__(
        self,
        target: Callable[[], None],
        *,
        name: str,
        should_run: Callable[[], bool] | None = None,
        stop_event: threading.Event | None = None,
        restart_delay: float = 1.0,
        restart_on_return: bool = True,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self._target = target
        self._name = name
        self._should_run = should_run or (lambda: True)
        self.stop_event = stop_event or threading.Event()
        self._restart_delay = max(0.0, float(restart_delay))
        self._restart_on_return = restart_on_return
        self._logger = logger
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def thread(self) -> threading.Thread | None:
        with self._lock:
            return self._thread

    @property
    def alive(self) -> bool:
        thread = self.thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self.stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name=self._name,
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float | None = None) -> bool:
        self.stop_event.set()
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        return thread is None or not thread.is_alive()

    def _report(self, message: str) -> None:
        if self._logger is None:
            return
        try:
            self._logger(message)
        except Exception:
            pass

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                active = self._should_run()
            except Exception as exc:
                self._report(f"{self._name} health check failed; retrying: {exc}")
                active = True
            if not active:
                return

            failed = False
            try:
                self._target()
            except Exception as exc:
                if self.stop_event.is_set():
                    return
                failed = True
                self._report(f"{self._name} failed; restarting: {exc}")
            if self.stop_event.is_set():
                return
            if not self._restart_on_return and not failed:
                return

            try:
                active = self._should_run()
            except Exception as exc:
                self._report(f"{self._name} health check failed; retrying: {exc}")
                active = True
            if not active:
                return
            if not failed:
                self._report(f"{self._name} exited; restarting")
            self.stop_event.wait(self._restart_delay)
