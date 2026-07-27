from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import tornado.ioloop
import tornado.web

from pywebio.platform.tornado import page, set_ioloop, webio_handler
from pywebio.session import Session
from pywebio.utils import STATIC_PATH, parse_file_size

from module.webui.auth import WebAuth
from module.webui.settings import DEFAULT_BIND_ADDRESS


def run_server(
    application: Any,
    *,
    host: str,
    port: int,
    static_dir: str | Path,
    auth: WebAuth,
) -> None:
    loop = tornado.ioloop.IOLoop.current()
    set_ioloop(loop)
    max_payload_size = parse_file_size("200M")
    page.MAX_PAYLOAD_SIZE = max_payload_size
    Session.debug = bool(os.environ.get("PYWEBIO_DEBUG", False))
    base_handler = webio_handler(application, cdn=True, reconnect_timeout=0)
    owner = auth

    class AuthenticatedWebIOHandler(base_handler):
        def prepare(self) -> None:
            if owner.authorize(self.request).allowed:
                return
            self.set_status(401)
            self.set_header("WWW-Authenticate", 'Basic realm="Palsitter"')
            self.set_header("Content-Type", "text/plain; charset=utf-8")
            self.finish("Authentication required")
            raise tornado.web.Finish()

    static_path = str(static_dir)
    handlers = [
        (r"/", AuthenticatedWebIOHandler),
        (r"/static/(.*)", tornado.web.StaticFileHandler, {"path": static_path}),
        (
            r"/(.*)",
            tornado.web.StaticFileHandler,
            {"path": str(STATIC_PATH), "default_filename": "index.html"},
        ),
    ]
    web_application = tornado.web.Application(
        handlers,
        debug=False,
        websocket_ping_interval=30,
        websocket_max_message_size=max_payload_size,
    )
    listen_addresses = (host,)
    if host not in {DEFAULT_BIND_ADDRESS, "0.0.0.0"}:
        listen_addresses += (DEFAULT_BIND_ADDRESS,)
    for address in listen_addresses:
        web_application.listen(port, address=address, max_buffer_size=max_payload_size)
    loop.start()


__all__ = ["run_server"]
