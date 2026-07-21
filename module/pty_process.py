from __future__ import annotations

import codecs
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from typing import Protocol


class PtyStdoutLike(Protocol):
    def read(self, size: int = 1) -> str:
        ...


class PtyProcessLike(Protocol):
    pid: int
    stdout: PtyStdoutLike

    def poll(self) -> int | None:
        ...

    def wait(self, timeout: float | None = None) -> int:
        ...

    def terminate(self) -> None:
        ...

    def kill(self) -> None:
        ...


class _PtyReader:
    def __init__(
        self,
        raw_read: Callable[[int], str | bytes],
        *,
        on_eof: Callable[[], None] | None = None,
        eof_exceptions: tuple[type[BaseException], ...] = (OSError, EOFError),
    ) -> None:
        self._raw_read = raw_read
        self._on_eof = on_eof
        self._eof_exceptions = eof_exceptions
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._pending = ""
        self._closed = False

    def read(self, size: int = 1) -> str:
        if size == 0:
            return ""
        while not self._pending and not self._closed:
            try:
                chunk = self._raw_read(max(size, 1))
            except self._eof_exceptions:
                self._mark_eof()
                break
            if not chunk:
                self._mark_eof()
                break
            if isinstance(chunk, str):
                self._pending += chunk
            else:
                self._pending += self._decoder.decode(bytes(chunk), final=False)

        if size < 0:
            text = self._pending
            self._pending = ""
            return text
        text = self._pending[:size]
        self._pending = self._pending[size:]
        return text

    def _mark_eof(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pending += self._decoder.decode(b"", final=True)
        if self._on_eof is not None:
            self._on_eof()


class PosixPtyProcess:
    def __init__(
        self,
        cmd: Sequence[str],
        cwd: str,
        *,
        openpty: Callable[[], tuple[int, int]] | None = None,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        os_read: Callable[[int, int], bytes] = os.read,
        os_close: Callable[[int], None] = os.close,
    ) -> None:
        if openpty is None:
            import pty

            openpty = pty.openpty
        self._os_read = os_read
        self._os_close = os_close
        self._master_closed = False
        master_fd, slave_fd = openpty()
        self._master_fd = master_fd
        try:
            self._process = popen_factory(
                cmd,
                cwd=cwd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
            )
        except Exception:
            self._safe_close(slave_fd)
            self._close_master()
            raise
        self._safe_close(slave_fd)
        self.pid = self._process.pid
        self.stdout = _PtyReader(
            lambda size: self._os_read(self._master_fd, size),
            on_eof=self._close_master,
            eof_exceptions=(OSError,),
        )

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def terminate(self) -> None:
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()

    def _safe_close(self, fd: int) -> None:
        try:
            self._os_close(fd)
        except OSError:
            pass

    def _close_master(self) -> None:
        if self._master_closed:
            return
        self._master_closed = True
        self._safe_close(self._master_fd)


class WindowsPtyProcess:
    def __init__(
        self,
        cmd: Sequence[str],
        cwd: str,
        *,
        pty_module: object | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if pty_module is None:
            try:
                import winpty as pty_module
            except ImportError as exc:
                raise RuntimeError(
                    "pywinpty is required on Windows to run SteamCMD with real-time output. "
                    "Install dependencies with `pip install -r requirements.txt`."
                ) from exc
        self.args = cmd
        self._sleep = sleep
        self._monotonic = monotonic
        self._process = pty_module.PtyProcess.spawn(cmd, cwd=cwd)
        self.pid = self._process.pid
        self.stdout = _PtyReader(
            lambda size: self._process.read(size),
            eof_exceptions=(OSError, EOFError),
        )

    def poll(self) -> int | None:
        if self._process.isalive():
            return None
        status = self._process.exitstatus
        return 0 if status is None else status

    def wait(self, timeout: float | None = None) -> int:
        if timeout is None:
            while True:
                returncode = self.poll()
                if returncode is not None:
                    return returncode
                self._sleep(0.05)

        deadline = self._monotonic() + timeout
        while True:
            returncode = self.poll()
            if returncode is not None:
                return returncode
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(self.args, timeout)
            self._sleep(min(0.05, remaining))

    def terminate(self) -> None:
        self._process.terminate(force=False)

    def kill(self) -> None:
        self._process.terminate(force=True)


def spawn_pty_process(cmd: Sequence[str], cwd: str) -> PtyProcessLike:
    if os.name == "nt":
        return WindowsPtyProcess(cmd, cwd)
    return PosixPtyProcess(cmd, cwd)
