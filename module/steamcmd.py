from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

STEAMCMD_WINDOWS_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"
STEAMCMD_LINUX_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"
DOWNLOAD_CHUNK_SIZE = 64 * 1024
WINDOWS = os.name == "nt"
LINUX_STEAMCMD_LOADER = Path("/lib/ld-linux.so.2")

ProgressCallback = Callable[[str, float | None, str], None]


def steamcmd_download_url() -> str:
    return STEAMCMD_WINDOWS_URL if WINDOWS else STEAMCMD_LINUX_URL


def steamcmd_platform_args() -> list[str]:
    """Return SteamCMD arguments that select the host's server platform."""
    return [] if WINDOWS else ["+@sSteamCmdForcePlatformType", "linux"]


def linux_steamcmd_runtime_error(steamcmd: Path) -> str | None:
    if WINDOWS:
        return None
    linux32_binary = steamcmd.parent / "linux32" / "steamcmd"
    if linux32_binary.exists() and not LINUX_STEAMCMD_LOADER.exists():
        return (
            "Linux SteamCMD is installed, but this system is missing the 32-bit "
            f"runtime loader {LINUX_STEAMCMD_LOADER}. SteamCMD is a 32-bit binary. "
            "On Ubuntu/WSL, run script/linux/install-dependencies.sh or install "
            "the required compatibility libraries with: "
            "sudo dpkg --add-architecture i386 && sudo apt update && "
            "sudo apt install lib32gcc-s1 libc6:i386 libstdc++6:i386"
        )
    return None


def validate_steamcmd_runtime(steamcmd: Path) -> None:
    error = linux_steamcmd_runtime_error(steamcmd)
    if error is not None:
        raise RuntimeError(error)


def ensure_steamcmd_at(
    steamcmd: Path,
    *,
    log: Callable[[str], None] | None = None,
    progress: ProgressCallback | None = None,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> Path:
    if steamcmd.exists():
        validate_steamcmd_runtime(steamcmd)
        return steamcmd

    target_dir = steamcmd.parent
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    url = steamcmd_download_url()
    if log is not None:
        log(f"Downloading SteamCMD: {url}")
    if progress is not None:
        progress("download", 0.0, "Downloading SteamCMD")

    fd, archive_name = tempfile.mkstemp(
        prefix=".steamcmd-download-",
        suffix=".zip" if os.name == "nt" else ".tar.gz",
        dir=target_dir.parent,
    )
    os.close(fd)
    archive_path = Path(archive_name)
    staging_dir: Path | None = None
    try:
        with opener(url, timeout=30) as response, archive_path.open("wb") as output:
            total = _response_content_length(response)
            downloaded = 0
            while True:
                try:
                    chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                except TypeError:
                    # Preserve compatibility with small test/custom openers whose
                    # response only exposes read() without a size parameter.
                    chunk = response.read()
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if progress is not None:
                    percent = min(100.0, downloaded * 100.0 / total) if total else None
                    progress("download", percent, f"Downloaded {downloaded} bytes")

        if log is not None:
            log("Validating SteamCMD archive")
        if progress is not None:
            progress("validate_archive", None, "Validating SteamCMD archive")
        _validate_archive(archive_path)

        staging_dir = Path(
            tempfile.mkdtemp(prefix=".steamcmd-extract-", dir=target_dir.parent)
        )
        if log is not None:
            log("Extracting SteamCMD")
        if progress is not None:
            progress("extract", None, "Extracting SteamCMD")
        _extract_archive(archive_path, staging_dir)
        _merge_tree(staging_dir, target_dir)

        if os.name != "nt":
            script = target_dir / "steamcmd.sh"
            if script.exists() and not steamcmd.exists():
                steamcmd.write_text(
                    "#!/bin/sh\nexec \"$(dirname \"$0\")/steamcmd.sh\" \"$@\"\n",
                    encoding="utf-8",
                )
                steamcmd.chmod(0o755)

        if not steamcmd.exists():
            raise FileNotFoundError(steamcmd)
        validate_steamcmd_runtime(steamcmd)
    finally:
        try:
            archive_path.unlink()
        except FileNotFoundError:
            pass
        if staging_dir is not None:
            shutil.rmtree(staging_dir, ignore_errors=True)

    if log is not None:
        log(f"SteamCMD is ready: {steamcmd}")
    if progress is not None:
        progress("ready", 100.0, "SteamCMD is ready")
    return steamcmd


def _response_content_length(response: object) -> int | None:
    value = None
    getheader = getattr(response, "getheader", None)
    if callable(getheader):
        value = getheader("Content-Length")
    if value is None:
        headers = getattr(response, "headers", None)
        if headers is not None:
            try:
                value = headers.get("Content-Length")
            except AttributeError:
                pass
    try:
        length = int(value)
    except (TypeError, ValueError):
        return None
    return length if length > 0 else None


def _safe_archive_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    path = Path(normalized)
    return not path.is_absolute() and ".." not in path.parts


def _validate_archive(path: Path) -> None:
    expected = "steamcmd.exe" if os.name == "nt" else "steamcmd.sh"
    if os.name == "nt":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if not names or any(not _safe_archive_name(name) for name in names):
                raise ValueError("SteamCMD archive contains an unsafe path")
    else:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if (
                not names
                or any(not _safe_archive_name(name) for name in names)
                or any(member.issym() or member.islnk() for member in members)
            ):
                raise ValueError("SteamCMD archive contains an unsafe path")
    if not any(Path(name.replace("\\", "/")).name == expected for name in names):
        raise ValueError(f"SteamCMD archive does not contain {expected}")


def _extract_archive(path: Path, target: Path) -> None:
    if os.name == "nt":
        with zipfile.ZipFile(path) as archive:
            archive.extractall(target)
    else:
        with tarfile.open(path, mode="r:gz") as archive:
            archive.extractall(target, filter="data")


def _merge_tree(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            temporary = destination.with_name(f".{destination.name}.new")
            try:
                shutil.copy2(item, temporary)
                os.replace(temporary, destination)
            finally:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass


def ensure_steamcmd(
    profile_name: str,
    *,
    log: Callable[[str], None] | None = None,
    progress: ProgressCallback | None = None,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> Path:
    """Compatibility wrapper for Palworld callers."""
    from module.games.palworld.config import fixed_steamcmd_path

    return ensure_steamcmd_at(
        fixed_steamcmd_path(profile_name),
        log=log,
        progress=progress,
        opener=opener,
    )
