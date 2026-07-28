from __future__ import annotations

import subprocess
import sys


def _run(label: str, *pytest_args: str) -> int:
    print(f"\n{label}", flush=True)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *pytest_args],
        check=False,
    )
    return result.returncode


def main() -> int:
    unit_result = _run(
        "Unit and service tests (serial)",
        "-n",
        "0",
        "-m",
        "not playwright",
    )
    if unit_result:
        return unit_result
    serial_gui_result = _run(
        "Timing-sensitive Playwright GUI tests (serial)",
        "-n",
        "0",
        "-m",
        "serial_playwright",
    )
    if serial_gui_result:
        return serial_gui_result
    return _run(
        "Remaining Playwright GUI tests (2 workers)",
        "-n",
        "2",
        "--dist=load",
        "-m",
        "playwright and not serial_playwright",
    )


if __name__ == "__main__":
    raise SystemExit(main())
