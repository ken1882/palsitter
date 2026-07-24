from __future__ import annotations

import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from module.webui.shutdown import ShutdownResult


class DesktopControlServer:
    def __init__(
        self,
        port: int,
        token: str,
        shutdown: Callable[[], ShutdownResult],
        on_success: Callable[[], None],
        force_shutdown: Callable[[], ShutdownResult] | None = None,
        start_shutdown: Callable[[], ShutdownResult] | None = None,
        gui_only_shutdown: Callable[[], ShutdownResult] | None = None,
    ) -> None:
        if not token:
            raise ValueError("PALSITTER_DESKTOP_TOKEN is required")
        self._shutdown = shutdown
        self._force_shutdown = force_shutdown
        self._start_shutdown = start_shutdown
        self._gui_only_shutdown = gui_only_shutdown
        self._on_success = on_success
        self._token = token.encode("utf-8")

        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:
                return

            def _send(self, status: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                if self.path not in {
                    "/desktop/shutdown",
                    "/desktop/force-shutdown",
                    "/desktop/gui-only",
                }:
                    self._send(404, {"ok": False, "error": "Not found"})
                    return
                supplied = self.headers.get("X-Palsitter-Token", "").encode("utf-8")
                if not hmac.compare_digest(supplied, owner._token):
                    self._send(401, {"ok": False, "error": "Unauthorized"})
                    return
                shutdown = owner._shutdown
                on_success = owner._on_success
                if self.path == "/desktop/force-shutdown":
                    shutdown = owner._force_shutdown
                    on_success = owner._on_success if owner._start_shutdown is None else None
                elif self.path == "/desktop/gui-only":
                    shutdown = owner._gui_only_shutdown
                elif owner._start_shutdown is not None:
                    shutdown = owner._start_shutdown
                    on_success = None
                if shutdown is None:
                    self._send(404, {"ok": False, "error": "Force shutdown is unavailable"})
                    return
                result = shutdown()
                self._send(200 if result.ok else 409, result.payload())
                if result.ok and on_success is not None:
                    threading.Thread(target=on_success, daemon=True).start()

        class DesktopHTTPServer(ThreadingHTTPServer):
            daemon_threads = True

        self._server = DesktopHTTPServer(("127.0.0.1", port), Handler)
        self.port = self._server.server_address[1]
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)


__all__ = ["DesktopControlServer"]
