from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any


REST_ACCESS_LOG_RE = re.compile(
    r"REST accessed endpoint /v1/api/(?:players|metrics|game-data)(?:\s|$)"
)


def is_suppressible_rest_access_log(line: str | bytes) -> bool:
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    return REST_ACCESS_LOG_RE.search(line) is not None


class PalServerLogWriter:
    """Persist non-empty PalServer output with normalized line endings."""

    def __init__(
        self,
        writer: Any,
        *,
        line_filter: Callable[[bytes], bool] | None = None,
    ) -> None:
        self._writer = writer
        self._line_filter = line_filter
        self._pending = bytearray()

    @property
    def path(self):
        return self._writer.path

    def write(self, data: bytes) -> int:
        self._pending.extend(data)
        self._write_complete_lines()
        return len(data)

    def _write_complete_lines(self) -> None:
        while self._pending:
            newline = self._pending.find(b"\n")
            carriage_return = self._pending.find(b"\r")
            delimiters = [index for index in (newline, carriage_return) if index >= 0]
            delimiter = min(delimiters) if delimiters else None
            if delimiter is None:
                return
            line = bytes(self._pending[:delimiter])
            end = delimiter + 1
            if self._pending[delimiter] == 13 and end < len(self._pending):
                if self._pending[end] == 10:
                    end += 1
            del self._pending[:end]
            if line.strip(b" \t") and (
                self._line_filter is None or not self._line_filter(line)
            ):
                self._writer.write(line + b"\n")

    def flush(self) -> None:
        self._writer.flush()

    def fileno(self) -> int:
        return self._writer.fileno()

    def close(self) -> None:
        if self._pending and self._pending.strip(b" \t") and (
            self._line_filter is None or not self._line_filter(bytes(self._pending))
        ):
            self._writer.write(bytes(self._pending) + b"\n")
        self._pending.clear()
        self._writer.close()
