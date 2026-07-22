from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
from pathlib import Path

from pywebio import start_server

from module.webui.app import app
from module.webui.desktop_control import DesktopControlServer
from module.webui.restart import RESTART_EXIT_CODE
from module.webui.shutdown import shutdown_all
from module.webui.shutdown_workflow import (
    configure_completion,
    request_force_shutdown,
    start_workflow as start_shutdown_workflow,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Palsitter web GUI")
    parser.add_argument("--host", default=os.getenv("PALSITTER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PALSITTER_PORT", "22368")))
    parser.add_argument(
        "--desktop-server",
        action="store_true",
        help="Run one desktop-managed GUI server without the restart wrapper",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=int(os.getenv("PALSITTER_CONTROL_PORT", "22369")),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--server-child", action="store_true", help=argparse.SUPPRESS)
    return parser


def _run_server(host: str, port: int, loop_ready=None) -> None:
    import tornado.ioloop

    loop = tornado.ioloop.IOLoop.current()
    if loop_ready is not None:
        loop_ready(loop)
    else:
        configure_completion(lambda: loop.add_callback(loop.stop))
    log_dir = Path(os.getenv("PALSITTER_LOG_DIR", str(Path(__file__).resolve().parent / "logs")))
    log_dir.mkdir(parents=True, exist_ok=True)
    static_dir = Path(__file__).resolve().parent / "assets"
    start_server(app, host=host, port=port, debug=False, static_dir=str(static_dir))


def _run_desktop_server(host: str, port: int, control_port: int) -> int:
    loop_ready = threading.Event()
    loop_holder: dict[str, object] = {}

    def remember_loop(loop: object) -> None:
        loop_holder["loop"] = loop
        loop_ready.set()

    def stop_web_server() -> None:
        loop = loop_holder.get("loop")
        if loop is not None:
            loop.add_callback(loop.stop)

    configure_completion(stop_web_server, replace=True)

    control = DesktopControlServer(
        control_port,
        os.environ.get("PALSITTER_DESKTOP_TOKEN", ""),
        shutdown_all,
        stop_web_server,
        request_force_shutdown,
        start_shutdown_workflow,
    )
    control.start()
    web_thread = threading.Thread(
        target=_run_server,
        args=(host, port, remember_loop),
        name="palsitter-web-server",
        daemon=True,
    )
    web_thread.start()
    loop_ready.wait(timeout=10)
    web_thread.join()
    control.close()
    return 0


def main() -> int:
    args = _parser().parse_args()
    if args.desktop_server:
        return _run_desktop_server(args.host, args.port, args.control_port)
    if args.server_child:
        _run_server(args.host, args.port)
        return 0

    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--server-child",
    ]
    while True:
        child = subprocess.Popen(command, cwd=str(Path(__file__).resolve().parent))
        try:
            returncode = child.wait()
        except KeyboardInterrupt:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            return 130
        if returncode != RESTART_EXIT_CODE:
            return returncode


if __name__ == "__main__":
    raise SystemExit(main())
