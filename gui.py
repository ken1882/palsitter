from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from pywebio import start_server

from module.webui.app import app
from module.webui.restart import RESTART_EXIT_CODE


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Palsitter web GUI")
    parser.add_argument("--host", default=os.getenv("PALSITTER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PALSITTER_PORT", "22368")))
    parser.add_argument("--server-child", action="store_true", help=argparse.SUPPRESS)
    return parser


def _run_server(host: str, port: int) -> None:
    Path("logs").mkdir(exist_ok=True)
    static_dir = Path(__file__).resolve().parent / "assets"
    start_server(app, host=host, port=port, debug=False, static_dir=str(static_dir))


def main() -> int:
    args = _parser().parse_args()
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
