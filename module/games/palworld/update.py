from __future__ import annotations

import datetime as dt
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Callable

from module.games.palworld.config import PALWORLD_SERVER_APP_ID, PalworldProfile
from module.games.registry import OperationProgress, UpdateInfo
from module.pty_process import PtyProcessLike, spawn_pty_process
from module.steamcmd import ensure_steamcmd_at, steamcmd_platform_args


UPDATE_CACHE_TTL = dt.timedelta(hours=6)
STEAMCMD_SELF_UPDATE_MARKER = "Update complete, launching"
STEAMCMD_SILENCE_NOTICE_SECONDS = 30
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
STEAMCMD_PROGRESS_RE = re.compile(
    r"Update state \(0x[0-9a-fA-F]+\)\s*([^,]*),\s*progress:\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
BUILID_RE = re.compile(r'"buildid"\s*(?::\s*)?"(\d+)"', re.IGNORECASE)
PUBLIC_BRANCH_START_RE = re.compile(r'"public"\s*(?::\s*)?\{', re.IGNORECASE)

_CHECK_LOCK = threading.Lock()
_CHECK_CACHE: dict[str, UpdateInfo] = {}


def appmanifest_path(profile: PalworldProfile) -> Path:
    return Path(profile.workdir).resolve() / "steamapps" / f"appmanifest_{PALWORLD_SERVER_APP_ID}.acf"


def parse_installed_build_id(text: str) -> str | None:
    match = BUILID_RE.search(text)
    return match.group(1) if match else None


def read_installed_build_id(path: Path) -> str | None:
    try:
        return parse_installed_build_id(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def parse_public_build_id(output: str) -> str | None:
    cleaned = ANSI_ESCAPE_RE.sub("", output)
    for public in PUBLIC_BRANCH_START_RE.finditer(cleaned):
        body = _vdf_block_body(cleaned, public.end() - 1)
        match = BUILID_RE.search(body)
        if match is not None:
            return match.group(1)
    return None


def _vdf_block_body(text: str, opening_brace: int) -> str:
    """Return a VDF block body, tolerating nested objects and compact output."""
    depth = 0
    quoted = False
    escaped = False
    for index in range(opening_brace, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[opening_brace + 1 : index]
    return text[opening_brace + 1 :]


def parse_steamcmd_progress(line: str) -> tuple[str, float] | None:
    match = STEAMCMD_PROGRESS_RE.search(ANSI_ESCAPE_RE.sub("", line))
    if match is None:
        return None
    phase = match.group(1).strip().lower().replace(" ", "_") or "updating"
    return phase, min(100.0, max(0.0, float(match.group(2))))


class PalworldUpdateService:
    def __init__(
        self,
        profile: PalworldProfile,
        *,
        logger: Callable[[str], None] = print,
        progress: Callable[[OperationProgress], None] | None = None,
        pty_process_factory: Callable[..., PtyProcessLike] = spawn_pty_process,
        now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self.profile = profile
        self.log = logger
        self.progress = progress
        self.pty_process_factory = pty_process_factory
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))

    @property
    def installed(self) -> bool:
        return Path(self.profile.executable).is_file()

    @property
    def _cache_key(self) -> str:
        return str(appmanifest_path(self.profile))

    def _emit(
        self,
        kind: str,
        phase: str,
        percent: float | None = None,
        message: str = "",
        error: str | None = None,
    ) -> None:
        if self.progress is not None:
            self.progress(OperationProgress(kind, phase, percent, message, error))

    def _ensure_steamcmd(self, kind: str) -> Path:
        return ensure_steamcmd_at(
            Path(self.profile.steamcmd),
            log=self.log,
            progress=lambda phase, percent, message: self._emit(
                kind, f"steamcmd_{phase}", percent, message
            ),
        )

    def check_update(self, *, force: bool = False) -> UpdateInfo:
        installed_build = read_installed_build_id(appmanifest_path(self.profile))
        checked_at = self.now()
        if not self.installed:
            info = UpdateInfo(installed_build, None, checked_at, "not_installed")
            self._emit("check_update", "complete", 100.0, "Server is not installed")
            return info

        with _CHECK_LOCK:
            cached = _CHECK_CACHE.get(self._cache_key)
            if (
                not force
                and cached is not None
                and cached.checked_at is not None
                and checked_at - cached.checked_at < UPDATE_CACHE_TTL
                and cached.installed_build_id == installed_build
            ):
                self._emit("check_update", "cached", 100.0, "Using cached update information")
                return cached

            self._emit("check_update", "checking", None, "Checking the public Steam branch")
            try:
                steamcmd = self._ensure_steamcmd("check_update")
                args = [
                    str(steamcmd.resolve()),
                    *steamcmd_platform_args(),
                    "+login",
                    "anonymous",
                    "+app_info_update",
                    "1",
                    "+app_info_print",
                    PALWORLD_SERVER_APP_ID,
                    "+quit",
                ]
                returncode, last_line, output = self._run_steamcmd(args, "check_update")
                if returncode != 0:
                    detail = f": {last_line}" if last_line else ""
                    raise RuntimeError(f"SteamCMD update check failed ({returncode}){detail}")
                available_build = parse_public_build_id(output)
                if available_build is None:
                    raise ValueError("SteamCMD did not report the public branch build ID")
                if installed_build is None:
                    status = "unknown"
                elif installed_build == available_build:
                    status = "up_to_date"
                else:
                    status = "update_available"
                info = UpdateInfo(installed_build, available_build, checked_at, status)
                _CHECK_CACHE[self._cache_key] = info
                self._emit("check_update", "complete", 100.0, "Update check completed")
                return info
            except Exception as exc:
                self.log(f"Update check failed: {exc}")
                info = UpdateInfo(installed_build, None, checked_at, "unknown")
                _CHECK_CACHE[self._cache_key] = info
                self._emit("check_update", "failed", None, "Update check failed", str(exc))
                return info

    def install_or_update(self, *, validate: bool = False) -> UpdateInfo:
        was_installed = self.installed
        kind = "validate" if validate else ("update" if was_installed else "install")
        self._emit(kind, "preparing", 0.0, "Preparing SteamCMD")
        try:
            steamcmd = self._ensure_steamcmd(kind)
            install_dir = Path(self.profile.workdir).resolve()
            install_dir.mkdir(parents=True, exist_ok=True)
            args = [
                str(steamcmd.resolve()),
                *steamcmd_platform_args(),
                "+force_install_dir",
                str(install_dir),
                "+login",
                "anonymous",
                "+app_update",
                PALWORLD_SERVER_APP_ID,
            ]
            if validate:
                args.append("validate")
            args.append("+quit")
            self._emit(kind, "updating", 0.0, "Running SteamCMD")
            returncode, last_line, _ = self._run_steamcmd(args, kind)
            if returncode != 0 and STEAMCMD_SELF_UPDATE_MARKER in last_line:
                self.log("SteamCMD updated itself; retrying server update")
                returncode, last_line, _ = self._run_steamcmd(args, kind)
            if returncode != 0:
                detail = f": {last_line}" if last_line else ""
                raise RuntimeError(f"SteamCMD update failed ({returncode}){detail}")
            if not self.installed:
                raise FileNotFoundError(
                    f"PalServer executable not found after SteamCMD completed: {self.profile.executable}"
                )
            installed_build = read_installed_build_id(appmanifest_path(self.profile))
            info = UpdateInfo(
                installed_build,
                installed_build,
                self.now(),
                "up_to_date" if installed_build is not None else "unknown",
            )
            with _CHECK_LOCK:
                _CHECK_CACHE.pop(self._cache_key, None)
            self.log("Validation completed" if validate else "Update completed")
            self._emit(kind, "complete", 100.0, "Operation completed")
            return info
        except Exception as exc:
            self._emit(kind, "failed", None, "Operation failed", str(exc))
            raise

    def _run_steamcmd(self, args: list[str], kind: str) -> tuple[int, str, str]:
        fake_log = os.getenv("PALSITTER_FAKE_STEAMCMD_CALLS")
        if fake_log:
            with open(fake_log, "a", encoding="utf-8") as handle:
                handle.write(" ".join(args[1:]) + "\n")
            installed = read_installed_build_id(appmanifest_path(self.profile)) or "0"
            output = (
                '"branches"\n{\n\t"public"\n\t{\n'
                f'\t\t"buildid"\t\t"{installed}"\n\t}}\n}}\n'
            )
            return 0, "Success", output

        process = self.pty_process_factory(args, cwd=str(Path(self.profile.steamcmd).resolve().parent))
        if process.stdout is None:
            raise RuntimeError("SteamCMD did not expose an output stream")
        output_lines: queue.Queue[str] = queue.Queue()

        def read_output() -> None:
            buffer = ""
            read = getattr(process.stdout, "read", None)

            def push_chunk(chunk: str) -> None:
                nonlocal buffer
                for char in chunk:
                    if char in ("\r", "\n"):
                        if buffer:
                            output_lines.put(buffer)
                        buffer = ""
                    else:
                        buffer += char

            if read is None:
                for chunk in process.stdout:
                    push_chunk(chunk)
            else:
                while True:
                    chunk = read(1)
                    if not chunk:
                        break
                    push_chunk(chunk)
            if buffer:
                output_lines.put(buffer)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        all_lines: list[str] = []
        last_line = ""
        last_output_at = time.monotonic()
        silence_logged_at = last_output_at

        def handle_line(line: str) -> None:
            nonlocal last_line, last_output_at, silence_logged_at
            text = ANSI_ESCAPE_RE.sub("", line.rstrip()).lstrip()
            if not text:
                return
            all_lines.append(text)
            last_line = text
            last_output_at = time.monotonic()
            silence_logged_at = last_output_at
            parsed = parse_steamcmd_progress(text)
            if parsed is not None:
                phase, percent = parsed
                self._emit(kind, phase, percent, text)
            self.log(f"SteamCMD: {text}")

        while True:
            returncode = process.poll()
            if returncode is not None:
                reader.join(timeout=1)
                while True:
                    try:
                        handle_line(output_lines.get_nowait())
                    except queue.Empty:
                        break
                return returncode, last_line, "\n".join(all_lines)
            try:
                handle_line(output_lines.get(timeout=1))
                continue
            except queue.Empty:
                pass
            now = time.monotonic()
            if (
                now - last_output_at >= STEAMCMD_SILENCE_NOTICE_SECONDS
                and now - silence_logged_at >= STEAMCMD_SILENCE_NOTICE_SECONDS
            ):
                self.log(f"SteamCMD still running; no output for {int(now - last_output_at)}s")
                silence_logged_at = now
