from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
import requests

from module.games.palworld.config import (
    PalworldProfile,
    fixed_executable_path,
    fixed_palserver_dir,
)
from module.games.palworld.server.status import instance_is_running


GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "palsitter",
}
DOWNLOAD_CHUNK_SIZE = 64 * 1024

PALWORLD_UE4SS_RELEASE_TAG = "experimental-palworld"
PALWORLD_UE4SS_RELEASE_PAGE = (
    "https://github.com/Okaetsu/RE-UE4SS/releases/tag/experimental-palworld"
)
PALWORLD_UE4SS_DOWNLOAD_URL = (
    "https://github.com/Okaetsu/RE-UE4SS/releases/download/"
    "experimental-palworld/UE4SS-Palworld.zip"
)

_SETTING_RE = re.compile(
    r"^(?P<indent>[ \t]*)bUseUObjectArrayCache[ \t]*=.*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class UE4SSRelease:
    tag: str
    name: str
    asset_name: str
    download_url: str
    prerelease: bool = False

    @property
    def label(self) -> str:
        return f"{self.tag} — {self.asset_name}"


PALWORLD_UE4SS_RELEASE = UE4SSRelease(
    tag=PALWORLD_UE4SS_RELEASE_TAG,
    name=PALWORLD_UE4SS_RELEASE_TAG,
    asset_name="UE4SS-Palworld.zip",
    download_url=PALWORLD_UE4SS_DOWNLOAD_URL,
    prerelease=True,
)


def _configured_palworld_release() -> UE4SSRelease:
    download_url = os.getenv("PALSITTER_TEST_UE4SS_DOWNLOAD_URL")
    if not download_url:
        return PALWORLD_UE4SS_RELEASE
    return UE4SSRelease(
        tag=PALWORLD_UE4SS_RELEASE.tag,
        name=PALWORLD_UE4SS_RELEASE.name,
        asset_name=PALWORLD_UE4SS_RELEASE.asset_name,
        download_url=download_url,
        prerelease=PALWORLD_UE4SS_RELEASE.prerelease,
    )


@dataclass(frozen=True)
class InstalledMod:
    name: str
    enabled: bool = True


@dataclass(frozen=True)
class ModsStatus:
    supported: bool
    reason: str | None
    reason_key: str | None
    server_installed: bool
    ue4ss_installed: bool
    ue4ss_version: str | None
    ue4ss_layout: str | None
    lua_mods: tuple[InstalledMod, ...]
    pak_mods: tuple[InstalledMod, ...]
    lua_dir: Path | None
    pak_dir: Path | None


class UE4SSService:
    def __init__(
        self,
        profile: PalworldProfile,
        *,
        session: requests.Session | None = None,
        running_probe: Callable[[PalworldProfile], bool] = instance_is_running,
        platform_supported: bool | None = None,
    ) -> None:
        self.profile = profile
        self.session = session or requests.Session()
        self.running_probe = running_probe
        forced_platform = os.getenv("PALSITTER_TEST_UE4SS_PLATFORM_SUPPORTED")
        self.platform_supported = (
            forced_platform == "1"
            if platform_supported is None and forced_platform is not None
            else os.name == "nt"
            if platform_supported is None
            else platform_supported
        )
        self.root = fixed_palserver_dir(profile.name)
        self.win64 = self.root / "Pal" / "Binaries" / "Win64"
        self.marker_path = self.win64 / ".palsitter-mods.json"

    def list_releases(self, limit: int = 1) -> tuple[UE4SSRelease, ...]:
        del limit
        return (_configured_palworld_release(),)

    def resolve_release(self, tag: str) -> UE4SSRelease:
        if str(tag).casefold() != PALWORLD_UE4SS_RELEASE_TAG.casefold():
            raise ValueError(f"Unsupported Palworld UE4SS release: {tag!r}")
        return _configured_palworld_release()

    def status(self) -> ModsStatus:
        server_installed = fixed_executable_path(self.profile.name).is_file()
        marker = self._read_marker()
        layout = self._detect_layout(marker.get("layout"))
        installed = layout is not None
        lua_dir = self._mods_dir(layout) if installed else None
        pak_dir = self.root / "Pal" / "Content" / "Paks" if server_installed else None
        supported = self.platform_supported and server_installed
        reason = None
        reason_key = None
        if not self.platform_supported:
            reason = (
                "UE4SS Lua/C++ management is not supported for native Linux Palworld "
                "servers."
            )
            reason_key = "mods.native_linux_unsupported"
        elif not server_installed:
            reason = "Install the Palworld server before managing UE4SS."
            reason_key = "mods.server_not_installed"
        version = str(marker.get("version")) if installed and marker.get("version") else None
        return ModsStatus(
            supported=supported,
            reason=reason,
            reason_key=reason_key,
            server_installed=server_installed,
            ue4ss_installed=installed,
            ue4ss_version=version,
            ue4ss_layout=layout,
            lua_mods=self._list_lua_mods(lua_dir),
            pak_mods=self._list_pak_mods(pak_dir),
            lua_dir=lua_dir,
            pak_dir=pak_dir,
        )

    def log_path(self) -> Path | None:
        marker = self._read_marker()
        layout = self._detect_layout(marker.get("layout"))
        if layout is None:
            return None
        directory = self.win64 / "ue4ss" if layout == "nested" else self.win64
        return directory / "UE4SS.log"

    def install(self, tag: str) -> UE4SSRelease:
        self._ensure_manageable()
        release = self.resolve_release(tag)
        current_marker = self._read_marker()
        current_layout = self._detect_layout(current_marker.get("layout"))

        with tempfile.TemporaryDirectory(prefix="palsitter-ue4ss-") as temporary:
            temporary_dir = Path(temporary)
            archive_path = temporary_dir / release.asset_name
            staging_dir = temporary_dir / "extracted"
            self._download(release.download_url, archive_path)
            tracked = _extract_validated_archive(archive_path, staging_dir)
            new_layout = _detect_staged_layout(staging_dir)
            if current_layout and current_layout != new_layout:
                _copy_missing_tree(self._mods_dir(current_layout), _staged_mods_dir(staging_dir, new_layout))
            self.win64.mkdir(parents=True, exist_ok=True)
            _merge_tree(staging_dir, self.win64)
            self._patch_settings(new_layout)
            self._remove_obsolete_install_paths(current_marker, current_layout, tracked)
            self._write_marker(
                {
                    "version": release.tag,
                    "asset": release.asset_name,
                    "layout": new_layout,
                    "paths": sorted(tracked, key=str.casefold),
                }
            )
        return release

    def uninstall(self) -> None:
        self._ensure_manageable()
        marker = self._read_marker()
        layout = self._detect_layout(marker.get("layout"))
        if layout is None:
            return
        paths = marker.get("paths")
        if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
            paths = _fallback_paths(layout)
        for relative in paths:
            self._remove_relative_path(relative, preserve_mods=True)
        try:
            self.marker_path.unlink()
        except FileNotFoundError:
            pass

    def set_pak_enabled(self, name: str, enabled: bool) -> InstalledMod:
        source = self._resolve_pak_path(name)
        currently_enabled = not source.name.casefold().endswith(".disabled")
        if currently_enabled == bool(enabled):
            return InstalledMod(self._relative_pak_name(source), currently_enabled)
        destination = (
            source.with_name(source.name[: -len(".disabled")])
            if enabled
            else source.with_name(f"{source.name}.disabled")
        )
        if destination.exists():
            raise FileExistsError(f"Pak mod already exists: {self._relative_pak_name(destination)}")
        source.rename(destination)
        return InstalledMod(self._relative_pak_name(destination), bool(enabled))

    def delete_pak(self, name: str) -> None:
        self._resolve_pak_path(name).unlink()

    def _ensure_manageable(self) -> None:
        status = self.status()
        if not status.supported:
            raise RuntimeError(status.reason or "UE4SS management is unavailable")
        if self.running_probe(self.profile):
            raise RuntimeError("Stop the Palworld server before changing UE4SS.")

    def _download(self, url: str, destination: Path) -> None:
        response = self.session.get(url, headers=GITHUB_HEADERS, stream=True, timeout=30)
        response.raise_for_status()
        with destination.open("wb") as output:
            iterator = getattr(response, "iter_content", None)
            if callable(iterator):
                for chunk in iterator(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if chunk:
                        output.write(chunk)
            else:
                output.write(response.content)

    def _read_marker(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.marker_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_marker(self, payload: dict[str, Any]) -> None:
        self.win64.mkdir(parents=True, exist_ok=True)
        temporary = self.marker_path.with_name(f".{self.marker_path.name}.new")
        try:
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.marker_path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _detect_layout(self, preferred: Any = None) -> str | None:
        candidates = []
        if preferred in ("nested", "flat"):
            candidates.append(str(preferred))
        candidates.extend(layout for layout in ("nested", "flat") if layout not in candidates)
        for layout in candidates:
            dll = self.win64 / "ue4ss" / "UE4SS.dll" if layout == "nested" else self.win64 / "UE4SS.dll"
            if dll.is_file():
                return layout
        return None

    def _mods_dir(self, layout: str | None) -> Path:
        return self.win64 / "ue4ss" / "Mods" if layout == "nested" else self.win64 / "Mods"

    def _settings_path(self, layout: str) -> Path:
        return (
            self.win64 / "ue4ss" / "UE4SS-settings.ini"
            if layout == "nested"
            else self.win64 / "UE4SS-settings.ini"
        )

    def _patch_settings(self, layout: str) -> None:
        path = self._settings_path(layout)
        if not path.is_file():
            raise ValueError("UE4SS archive did not install UE4SS-settings.ini")
        text = path.read_bytes().decode("utf-8")
        path.write_bytes(patch_object_cache_setting(text).encode("utf-8"))

    def _remove_obsolete_install_paths(
        self,
        marker: dict[str, Any],
        old_layout: str | None,
        new_paths: set[str],
    ) -> None:
        if old_layout is None:
            return
        old_paths = marker.get("paths")
        if not isinstance(old_paths, list) or not all(isinstance(item, str) for item in old_paths):
            old_paths = _fallback_paths(old_layout)
        for relative in old_paths:
            if relative not in new_paths:
                self._remove_relative_path(relative)

    def _remove_relative_path(self, relative: str, *, preserve_mods: bool = False) -> None:
        normalized = str(relative).replace("\\", "/").strip("/")
        if not normalized or "/" in normalized or normalized in (".", ".."):
            return
        if preserve_mods and normalized.casefold() == "mods":
            return
        target = (self.win64 / normalized).resolve()
        root = self.win64.resolve()
        if target.parent != root:
            return
        if target.is_dir() and not target.is_symlink():
            if not preserve_mods:
                shutil.rmtree(target)
                return
            for child in target.iterdir():
                if child.name.casefold() == "mods" and child.is_dir():
                    continue
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            try:
                target.rmdir()
            except OSError:
                pass
        else:
            try:
                target.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _list_lua_mods(directory: Path | None) -> tuple[InstalledMod, ...]:
        if directory is None or not directory.is_dir():
            return ()
        mods = [
            InstalledMod(entry.name)
            for entry in directory.iterdir()
            if entry.is_dir() and not entry.is_symlink() and entry.name.casefold() != "shared"
        ]
        return tuple(sorted(mods, key=lambda item: item.name.casefold()))

    @staticmethod
    def _list_pak_mods(directory: Path | None) -> tuple[InstalledMod, ...]:
        if directory is None or not directory.is_dir():
            return ()
        mods: list[InstalledMod] = []
        for base, prefix in (
            (directory, ""),
            (directory / "LogicMods", "LogicMods/"),
            (directory / "~mods", "~mods/"),
        ):
            if not base.is_dir():
                continue
            for entry in base.iterdir():
                if (
                    entry.is_file()
                    and not entry.is_symlink()
                    and (
                        entry.name.casefold().endswith(".pak")
                        or entry.name.casefold().endswith(".pak.disabled")
                    )
                    and not entry.name.casefold().startswith("pal-")
                ):
                    mods.append(
                        InstalledMod(
                            prefix + entry.name,
                            enabled=not entry.name.casefold().endswith(".disabled"),
                        )
                    )
        return tuple(sorted(mods, key=lambda item: item.name.casefold()))

    def _resolve_pak_path(self, name: str) -> Path:
        pak_dir = self.root / "Pal" / "Content" / "Paks"
        if not fixed_executable_path(self.profile.name).is_file():
            raise RuntimeError("Install the Palworld server before managing Pak mods.")
        normalized = str(name).replace("\\", "/").strip("/")
        parts = normalized.split("/")
        valid_location = len(parts) == 1 or (
            len(parts) == 2 and parts[0] in ("LogicMods", "~mods")
        )
        filename = parts[-1] if parts else ""
        folded = filename.casefold()
        if (
            not valid_location
            or filename in ("", ".", "..")
            or folded.startswith("pal-")
            or not (folded.endswith(".pak") or folded.endswith(".pak.disabled"))
        ):
            raise ValueError(f"Invalid Pak mod path: {name}")
        target = pak_dir.joinpath(*parts)
        if not target.is_file() or target.is_symlink():
            raise FileNotFoundError(f"Pak mod not found: {normalized}")
        return target

    def _relative_pak_name(self, path: Path) -> str:
        pak_dir = self.root / "Pal" / "Content" / "Paks"
        return path.relative_to(pak_dir).as_posix()


def default_release_tag(releases: Iterable[UE4SSRelease]) -> str | None:
    choices = tuple(releases)
    for release in choices:
        if release.tag.casefold() == PALWORLD_UE4SS_RELEASE_TAG.casefold():
            return release.tag
    for release in choices:
        if release.prerelease:
            return release.tag
    return choices[0].tag if choices else None


def patch_object_cache_setting(text: str) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        body = line[: -len(ending)] if ending else line
        match = _SETTING_RE.fullmatch(body)
        if match:
            lines[index] = f"{match.group('indent')}bUseUObjectArrayCache = false{ending}"
            return "".join(lines)
    stripped = text.rstrip("\r\n")
    return f"{stripped}{newline if stripped else ''}bUseUObjectArrayCache = false{newline}"


def _safe_archive_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    path = Path(normalized)
    return (
        bool(normalized)
        and not normalized.startswith("/")
        and not re.match(r"^[A-Za-z]:/", normalized)
        and not path.is_absolute()
        and ".." not in path.parts
    )


def _extract_validated_archive(archive_path: Path, destination: Path) -> set[str]:
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if not members:
            raise ValueError("UE4SS archive is empty")
        tracked: set[str] = set()
        for member in members:
            if not _safe_archive_name(member.filename):
                raise ValueError("UE4SS archive contains an unsafe path")
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("UE4SS archive contains a symbolic link")
            first = member.filename.replace("\\", "/").lstrip("./").split("/", 1)[0]
            if first:
                tracked.add(first)
        archive.extractall(destination)
    _detect_staged_layout(destination)
    return tracked


def _detect_staged_layout(directory: Path) -> str:
    nested = directory / "ue4ss" / "UE4SS.dll"
    flat = directory / "UE4SS.dll"
    if nested.is_file() and not flat.exists():
        layout = "nested"
    elif flat.is_file() and not nested.exists():
        layout = "flat"
    else:
        raise ValueError("UE4SS archive has an unsupported installation layout")
    settings = (
        directory / "ue4ss" / "UE4SS-settings.ini"
        if layout == "nested"
        else directory / "UE4SS-settings.ini"
    )
    if not settings.is_file():
        raise ValueError("UE4SS archive does not contain UE4SS-settings.ini")
    return layout


def _staged_mods_dir(directory: Path, layout: str) -> Path:
    return directory / "ue4ss" / "Mods" if layout == "nested" else directory / "Mods"


def _copy_missing_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    for item in source.rglob("*"):
        if item.is_symlink():
            continue
        relative = item.relative_to(source)
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


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


def _fallback_paths(layout: str) -> list[str]:
    if layout == "nested":
        return ["dwmapi.dll", "ue4ss"]
    return ["dwmapi.dll", "UE4SS.dll", "UE4SS-settings.ini", "Mods"]
