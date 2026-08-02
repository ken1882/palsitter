from __future__ import annotations

import gzip
import io
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Mapping

from module.games.palworld.config import PalworldProfile, fixed_palserver_dir


MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
COPY_CHUNK_SIZE = 1024 * 1024
UNSUPPORTED_FORMAT = (
    "Unsupported format, please extract to a folder if it's a compressed archive or "
    "follow the mod's manual install instruction."
)
PALSCHEMA_REQUIRED = "Install PalSchema before uploading this mod"

PAK_ROOT = "root"
PAK_LOGIC_MODS = "logicmods"
PAK_TILDE_MODS = "mods"
PAK_DESTINATIONS = frozenset((PAK_ROOT, PAK_LOGIC_MODS, PAK_TILDE_MODS))

_ARCHIVE_SUFFIXES = (
    ".zip",
    ".7z",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".gz",
    ".rar",
    ".bz2",
    ".xz",
)
_PAK_SUFFIXES = (".pak", ".utoc", ".ucas")
_PALSCHEMA_CATEGORIES = frozenset(
    (
        "appearance",
        "blueprints",
        "buildings",
        "enums",
        "helpguide",
        "items",
        "paks",
        "pals",
        "raw",
        "skins",
        "spawns",
        "translations",
    )
)
_PALSCHEMA_DATA_SUFFIXES = (".json", ".jsonc")
_WINDOWS_DEVICES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


class UploadError(RuntimeError):
    pass


class UploadCancelled(UploadError):
    pass


class UnsupportedUpload(UploadError):
    pass


@dataclass(frozen=True)
class UploadFile:
    path: str
    content: bytes


@dataclass(frozen=True)
class UploadItem:
    name: str
    content: bytes | None = None
    files: tuple[UploadFile, ...] = ()

    @classmethod
    def archive(cls, name: str, content: bytes) -> "UploadItem":
        return cls(name=name, content=bytes(content))

    @classmethod
    def folder(cls, name: str, files: Iterable[UploadFile]) -> "UploadItem":
        return cls(name=name, files=tuple(files))

    @property
    def size(self) -> int:
        if self.content is not None:
            return len(self.content)
        return sum(len(item.content) for item in self.files)


@dataclass(frozen=True)
class DetectedMod:
    id: str
    item_index: int
    kind: str
    name: str
    source_prefix: str
    members: tuple[str, ...]
    pak_destination: str | None = None

    @property
    def needs_pak_destination(self) -> bool:
        return self.kind == "pak" and self.pak_destination is None


@dataclass(frozen=True)
class InspectedItem:
    source: UploadItem
    units: tuple[DetectedMod, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class BatchInspection:
    items: tuple[InspectedItem, ...]

    @property
    def units(self) -> tuple[DetectedMod, ...]:
        return tuple(unit for item in self.items for unit in item.units)


@dataclass(frozen=True)
class ItemResult:
    name: str
    status: str
    message: str = ""


@dataclass(frozen=True)
class BatchResult:
    items: tuple[ItemResult, ...]

    @property
    def installed_count(self) -> int:
        return sum(item.status == "success" for item in self.items)


ProgressCallback = Callable[[int, int, str, str], None]


_LOCKS_GUARD = threading.Lock()
_INSTANCE_LOCKS: dict[str, threading.Lock] = {}


def _instance_lock(root: Path) -> threading.Lock:
    key = os.path.normcase(str(root.resolve()))
    with _LOCKS_GUARD:
        return _INSTANCE_LOCKS.setdefault(key, threading.Lock())


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise UploadCancelled("Upload cancelled")


def _test_inspection_delay(cancel_event: threading.Event | None) -> None:
    value = os.getenv("PALSITTER_TEST_MOD_UPLOAD_DELAY", "")
    if not value:
        return
    deadline = time.monotonic() + max(0.0, float(value))
    while time.monotonic() < deadline:
        _check_cancel(cancel_event)
        time.sleep(max(0.0, min(0.05, deadline - time.monotonic())))


def _safe_relative_path(value: str) -> PurePosixPath:
    normalized = str(value).replace("\\", "/").strip("/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise UploadError(f"Unsafe upload path: {value}")
    for part in path.parts:
        base = part.rstrip(" .").split(".", 1)[0].casefold()
        if not part.rstrip(" .") or base in _WINDOWS_DEVICES or ":" in part:
            raise UploadError(f"Unsafe upload path: {value}")
    return path


def _validated_members(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        path = _safe_relative_path(value)
        normalized = path.as_posix()
        folded = normalized.casefold()
        if folded in seen:
            raise UploadError(f"Duplicate upload path: {normalized}")
        seen.add(folded)
        result.append(normalized)
        if len(result) > MAX_ARCHIVE_MEMBERS:
            raise UploadError("Archive contains too many files")
    return tuple(result)


def _copy_stream(source, destination: Path, cancel_event: threading.Event | None) -> int:
    total = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        while True:
            _check_cancel(cancel_event)
            chunk = source.read(COPY_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_EXPANDED_BYTES:
                raise UploadError("Expanded upload is too large")
            output.write(chunk)
    return total


def _archive_kind(name: str, content: bytes) -> str | None:
    stream = io.BytesIO(content)
    if zipfile.is_zipfile(stream):
        return "zip"
    if content.startswith(b"7z\xbc\xaf'\x1c"):
        return "7z"
    if content.startswith(b"\x1f\x8b"):
        return "gzip"
    stream.seek(0)
    try:
        if tarfile.is_tarfile(stream):
            return "tar"
    except (OSError, tarfile.TarError):
        pass
    if name.casefold().endswith(_ARCHIVE_SUFFIXES):
        raise UnsupportedUpload(UNSUPPORTED_FORMAT)
    return None


def _extract_zip(content: bytes, destination: Path, cancel_event: threading.Event | None) -> None:
    total = 0
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = archive.infolist()
        _validated_members(member.filename for member in members if not member.is_dir())
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise UploadError("Archive contains too many files")
        for member in members:
            _check_cancel(cancel_event)
            if member.flag_bits & 0x1:
                raise UnsupportedUpload(UNSUPPORTED_FORMAT)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise UploadError("Archive links are not supported")
            if member.is_dir():
                continue
            total += member.file_size
            if total > MAX_EXPANDED_BYTES:
                raise UploadError("Expanded upload is too large")
            target = destination / _safe_relative_path(member.filename)
            with archive.open(member) as source:
                _copy_stream(source, target, cancel_event)


def _extract_open_tar(
    archive: tarfile.TarFile,
    destination: Path,
    cancel_event: threading.Event | None,
) -> None:
    total = 0
    members = archive.getmembers()
    _validated_members(member.name for member in members if member.isfile())
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise UploadError("Archive contains too many files")
    for member in members:
        _check_cancel(cancel_event)
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise UploadError("Archive links and special files are not supported")
        if member.isdir():
            continue
        if not member.isfile():
            raise UploadError("Unsupported archive member")
        total += member.size
        if total > MAX_EXPANDED_BYTES:
            raise UploadError("Expanded upload is too large")
        source = archive.extractfile(member)
        if source is None:
            raise UploadError(f"Could not read archive member: {member.name}")
        with source:
            _copy_stream(source, destination / _safe_relative_path(member.name), cancel_event)


def _extract_tar(content: bytes, destination: Path, cancel_event: threading.Event | None) -> None:
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as archive:
        _extract_open_tar(archive, destination, cancel_event)


def _extract_tar_path(path: Path, destination: Path, cancel_event: threading.Event | None) -> None:
    with tarfile.open(path, mode="r:*") as archive:
        _extract_open_tar(archive, destination, cancel_event)


def _extract_7z(content: bytes, destination: Path, cancel_event: threading.Event | None) -> None:
    try:
        import py7zr
    except ImportError as exc:  # pragma: no cover - packaging guarantees this dependency
        raise UploadError("7z support is unavailable") from exc
    parameters = {"max_extract_size": MAX_EXPANDED_BYTES}
    try:
        archive_context = py7zr.SevenZipFile(io.BytesIO(content), mode="r", **parameters)
    except TypeError:  # pragma: no cover - protects development environments with an old py7zr
        archive_context = py7zr.SevenZipFile(io.BytesIO(content), mode="r")
    with archive_context as archive:
        if archive.needs_password():
            raise UnsupportedUpload(UNSUPPORTED_FORMAT)
        entries = archive.list()
        names = [entry.filename for entry in entries]
        _validated_members(names)
        if len(entries) > MAX_ARCHIVE_MEMBERS:
            raise UploadError("Archive contains too many files")
        total = 0
        for entry in entries:
            if getattr(entry, "is_symlink", False) or getattr(entry, "is_hardlink", False):
                raise UploadError("Archive links are not supported")
            total += int(getattr(entry, "uncompressed", 0) or 0)
        if total > MAX_EXPANDED_BYTES:
            raise UploadError("Expanded upload is too large")
        _check_cancel(cancel_event)
        archive.extractall(path=destination)
        _check_cancel(cancel_event)


def _materialize(item: UploadItem, destination: Path, cancel_event: threading.Event | None) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if item.files:
        paths = _validated_members(file.path for file in item.files)
        total = 0
        for file, relative in zip(item.files, paths):
            _check_cancel(cancel_event)
            total += len(file.content)
            if total > MAX_EXPANDED_BYTES:
                raise UploadError("Expanded upload is too large")
            target = destination / PurePosixPath(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(file.content)
        return
    if item.content is None:
        raise UnsupportedUpload(UNSUPPORTED_FORMAT)
    kind = _archive_kind(item.name, item.content)
    if kind == "zip":
        _extract_zip(item.content, destination, cancel_event)
        return
    if kind == "7z":
        _extract_7z(item.content, destination, cancel_event)
        return
    if kind == "tar":
        _extract_tar(item.content, destination, cancel_event)
        return
    if kind == "gzip":
        output_name = item.name[:-3] if item.name.casefold().endswith(".gz") else item.name + ".out"
        expanded_path = destination / _safe_relative_path(Path(output_name).name)
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(item.content), mode="rb") as source:
                _copy_stream(source, expanded_path, cancel_event)
        except (EOFError, OSError) as exc:
            raise UnsupportedUpload(UNSUPPORTED_FORMAT) from exc
        if tarfile.is_tarfile(expanded_path):
            _extract_tar_path(expanded_path, destination, cancel_event)
            expanded_path.unlink()
            return
        with expanded_path.open("rb") as handle:
            signature = handle.read(8)
        if zipfile.is_zipfile(expanded_path) or signature.startswith(b"7z\xbc\xaf'\x1c"):
            raise UnsupportedUpload(UNSUPPORTED_FORMAT)
        if not output_name.casefold().endswith(_PAK_SUFFIXES):
            raise UnsupportedUpload(UNSUPPORTED_FORMAT)
        return
    if item.name.casefold().endswith(_PAK_SUFFIXES):
        (destination / _safe_relative_path(Path(item.name).name)).write_bytes(item.content)
        return
    raise UnsupportedUpload(UNSUPPORTED_FORMAT)


def _all_files(root: Path) -> tuple[str, ...]:
    values = [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()]
    return _validated_members(values)


def _content_root(root: Path, files: tuple[str, ...]) -> Path:
    current = root
    current_files = files
    meaningful = {
        "pal",
        "mods",
        "palschema",
        "logicmods",
        "~mods",
        *_PALSCHEMA_CATEGORIES,
    }
    while current_files:
        first = {PurePosixPath(value).parts[0] for value in current_files}
        if len(first) != 1:
            break
        component = next(iter(first))
        without_component = tuple(
            PurePosixPath(value).relative_to(component).as_posix() for value in current_files
        )
        if any(
            value.casefold() in ("scripts/main.lua", "dlls/main.dll")
            for value in without_component
        ):
            break
        if component.casefold() in meaningful or any(len(PurePosixPath(value).parts) == 1 for value in current_files):
            break
        candidate = current / component
        if not candidate.is_dir():
            break
        current = candidate
        current_files = without_component
    return current


def _server_install_rule(payload: object) -> bool:
    if isinstance(payload, dict):
        if any(key.casefold() == "isserver" and value is True for key, value in payload.items()):
            return True
        return any(_server_install_rule(value) for value in payload.values())
    if isinstance(payload, list):
        return any(_server_install_rule(value) for value in payload)
    return False


def _pak_destination(parts: tuple[str, ...]) -> str | None:
    folded = [part.casefold() for part in parts]
    if "logicmods" in folded:
        return PAK_LOGIC_MODS
    if "~mods" in folded:
        return PAK_TILDE_MODS
    if "paks" in folded:
        return PAK_ROOT
    return None


def _unit_id(item_index: int, kind: str, name: str, ordinal: int) -> str:
    return f"{item_index}:{kind}:{name.casefold()}:{ordinal}"


def _classify(root: Path, item_index: int) -> tuple[DetectedMod, ...]:
    original_files = _all_files(root)
    if not original_files:
        raise UnsupportedUpload(UNSUPPORTED_FORMAT)
    content_root = _content_root(root, original_files)
    files = _all_files(content_root)
    paths = {value.casefold(): value for value in files}

    nested_archives = [
        value
        for value in files
        if value.casefold().endswith(_ARCHIVE_SUFFIXES)
    ]
    if nested_archives:
        raise UnsupportedUpload(UNSUPPORTED_FORMAT)

    info_path = paths.get("info.json")
    if info_path:
        try:
            payload = json.loads((content_root / PurePosixPath(info_path)).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise UploadError("Invalid Info.json") from exc
        package = payload.get("PackageName") if isinstance(payload, dict) else None
        if not isinstance(package, str) or _safe_relative_path(package).as_posix() != package:
            raise UploadError("Invalid Info.json PackageName")
        if not _server_install_rule(payload):
            raise UploadError("This package does not support Palworld dedicated servers")
        return (
            DetectedMod(
                id=_unit_id(item_index, "official", package, 0),
                item_index=item_index,
                kind="official",
                name=package,
                source_prefix=".",
                members=files,
            ),
        )

    basenames = {PurePosixPath(value).name.casefold() for value in files}
    if "ue4ss.dll" in basenames and "dwmapi.dll" in basenames:
        raise UploadError("UE4SS loader detected; use the UE4SS mod loader section")

    units: list[DetectedMod] = []
    claimed_prefixes: set[str] = set()
    claimed_palschema_files: set[str] = set()
    ordinal = 0

    for value in files:
        parts = PurePosixPath(value).parts
        folded = tuple(part.casefold() for part in parts)
        kind = None
        prefix_parts: tuple[str, ...] | None = None
        if len(parts) >= 3 and folded[-2:] == ("scripts", "main.lua"):
            kind = "ue4ss"
            prefix_parts = parts[:-2]
        elif len(parts) >= 3 and folded[-2:] == ("dlls", "main.dll"):
            kind = "ue4ss"
            prefix_parts = parts[:-2]
        if not kind or not prefix_parts:
            continue
        prefix = PurePosixPath(*prefix_parts).as_posix()
        key = prefix.casefold()
        if key in claimed_prefixes:
            continue
        claimed_prefixes.add(key)
        name = prefix_parts[-1]
        members = tuple(
            candidate
            for candidate in files
            if candidate.casefold() == key or candidate.casefold().startswith(key + "/")
        )
        units.append(
            DetectedMod(
                id=_unit_id(item_index, kind, name, ordinal),
                item_index=item_index,
                kind=kind,
                name=name,
                source_prefix=prefix,
                members=members,
            )
        )
        ordinal += 1

    palschema_candidates: dict[str, tuple[str, str]] = {}
    for value in files:
        path = PurePosixPath(value)
        parts = path.parts
        folded = tuple(part.casefold() for part in parts)
        for index in range(len(parts) - 3):
            if folded[index : index + 2] == ("palschema", "mods"):
                prefix_parts = parts[: index + 3]
                prefix = PurePosixPath(*prefix_parts).as_posix()
                palschema_candidates.setdefault(
                    prefix.casefold(), (prefix, prefix_parts[-1])
                )
        valid_category = (
            len(parts) >= 2
            and folded[-2] in _PALSCHEMA_CATEGORIES
            and (
                path.suffix.casefold() in _PALSCHEMA_DATA_SUFFIXES
                or (folded[-2] == "paks" and path.suffix.casefold() == ".pak")
            )
        )
        if not valid_category:
            continue
        for index in range(len(parts) - 3):
            if folded[index] == "mods" and index + 2 < len(parts):
                prefix_parts = parts[: index + 2]
                prefix = PurePosixPath(*prefix_parts).as_posix()
                palschema_candidates.setdefault(
                    prefix.casefold(), (prefix, prefix_parts[-1])
                )
        if (
            content_root != root
            and len(parts) >= 2
            and folded[0] in _PALSCHEMA_CATEGORIES
        ):
            palschema_candidates.setdefault(".", (".", content_root.name))

    for prefix, name in palschema_candidates.values():
        key = prefix.casefold()
        members = tuple(
            candidate
            for candidate in files
            if prefix == "."
            or candidate.casefold() == key
            or candidate.casefold().startswith(key + "/")
        )
        if not members:
            continue
        units.append(
            DetectedMod(
                id=_unit_id(item_index, "palschema", name, ordinal),
                item_index=item_index,
                kind="palschema",
                name=name,
                source_prefix=prefix,
                members=members,
            )
        )
        claimed_palschema_files.update(member.casefold() for member in members)
        ordinal += 1

    pak_groups: dict[tuple[str, str], list[str]] = {}
    for value in files:
        path = PurePosixPath(value)
        if path.suffix.casefold() not in _PAK_SUFFIXES:
            continue
        key = (path.parent.as_posix().casefold(), path.stem.casefold())
        pak_groups.setdefault(key, []).append(value)
    for (_, _), members in pak_groups.items():
        if any(member.casefold() in claimed_palschema_files for member in members):
            continue
        representative = PurePosixPath(members[0])
        name = representative.stem
        units.append(
            DetectedMod(
                id=_unit_id(item_index, "pak", name, ordinal),
                item_index=item_index,
                kind="pak",
                name=name,
                source_prefix=representative.parent.as_posix(),
                members=tuple(sorted(members, key=str.casefold)),
                pak_destination=_pak_destination(representative.parts[:-1]),
            )
        )
        ordinal += 1

    if not units:
        raise UnsupportedUpload(UNSUPPORTED_FORMAT)
    return tuple(units)


@dataclass
class _SwapTransaction:
    backup_root: Path
    changes: list[tuple[Path, Path | None]] = field(default_factory=list)

    def replace(self, staged: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if target.exists() or target.is_symlink():
            backup = self.backup_root / f"{len(self.changes)}-{target.name}"
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, backup)
        self.changes.append((target, backup))
        os.replace(staged, target)

    def rollback(self) -> None:
        for target, backup in reversed(self.changes):
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
            if backup is not None and backup.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, target)


class ModUploadService:
    def __init__(
        self,
        profile: PalworldProfile,
        *,
        platform_supported: bool | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.profile = profile
        self.root = fixed_palserver_dir(profile.name)
        self.platform_supported = os.name == "nt" if platform_supported is None else platform_supported
        self.logger = logger or (lambda _: None)

    def inspect(
        self,
        items: Iterable[UploadItem],
        *,
        cancel_event: threading.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> BatchInspection:
        sources = tuple(items)
        if sum(item.size for item in sources) > MAX_UPLOAD_BYTES:
            raise UploadError("Selected uploads exceed the 200 MiB limit")
        inspected: list[InspectedItem] = []
        total = len(sources)
        for index, item in enumerate(sources):
            _check_cancel(cancel_event)
            if progress:
                progress(index, total, "inspect", item.name)
            try:
                _test_inspection_delay(cancel_event)
                with tempfile.TemporaryDirectory(prefix="palsitter-upload-inspect-") as temporary:
                    root = Path(temporary)
                    _materialize(item, root, cancel_event)
                    units = _classify(root, index)
                inspected.append(InspectedItem(source=item, units=units))
            except UploadCancelled:
                raise
            except (UploadError, OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
                message = str(exc) or UNSUPPORTED_FORMAT
                inspected.append(InspectedItem(source=item, error=message))
                self.logger(f"Mod upload inspection failed for {item.name}: {message}")
        return BatchInspection(tuple(inspected))

    def _ue4ss_mods_dir(self) -> Path | None:
        win64 = self.root / "Pal" / "Binaries" / "Win64"
        if (win64 / "ue4ss" / "UE4SS.dll").is_file():
            return win64 / "ue4ss" / "Mods"
        if (win64 / "UE4SS.dll").is_file():
            return win64 / "Mods"
        return None

    def palschema_installed(self) -> bool:
        mods_dir = self._ue4ss_mods_dir()
        return bool(mods_dir and (mods_dir / "PalSchema" / "dlls" / "main.dll").is_file())

    def _destination(self, unit: DetectedMod, decisions: Mapping[str, str]) -> Path:
        if unit.kind == "official":
            return self.root / "Mods" / "Workshop" / unit.name
        if unit.kind in ("ue4ss", "palschema"):
            mods_dir = self._ue4ss_mods_dir()
            if mods_dir is None:
                raise UploadError("Install UE4SS before uploading this mod")
            if unit.kind == "palschema":
                return mods_dir / "PalSchema" / "mods" / unit.name
            return mods_dir / unit.name
        destination = unit.pak_destination or decisions.get(unit.id)
        if destination not in PAK_DESTINATIONS:
            raise UploadError(f"Choose a Pak destination for {unit.name}")
        paks = self.root / "Pal" / "Content" / "Paks"
        if destination == PAK_LOGIC_MODS:
            return paks / "LogicMods"
        if destination == PAK_TILDE_MODS:
            return paks / "~mods"
        return paks

    def conflicts(
        self, inspection: BatchInspection, decisions: Mapping[str, str]
    ) -> tuple[str, ...]:
        found: list[str] = []
        seen: set[str] = set()
        for unit in inspection.units:
            try:
                destination = self._destination(unit, decisions)
            except UploadError:
                continue
            if unit.kind == "pak":
                targets = [destination / PurePosixPath(member).name for member in unit.members]
            else:
                targets = [destination]
            for target in targets:
                key = os.path.normcase(str(target.resolve()))
                if target.exists() or key in seen:
                    found.append(unit.name)
                    break
                seen.add(key)
        return tuple(dict.fromkeys(found))

    def install(
        self,
        inspection: BatchInspection,
        decisions: Mapping[str, str],
        *,
        cancel_event: threading.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> BatchResult:
        lock = _instance_lock(self.root)
        if not lock.acquire(blocking=False):
            raise UploadError("Another mod upload is already running for this instance")
        try:
            return self._install_locked(inspection, decisions, cancel_event, progress)
        finally:
            lock.release()

    def _install_locked(
        self,
        inspection: BatchInspection,
        decisions: Mapping[str, str],
        cancel_event: threading.Event | None,
        progress: ProgressCallback | None,
    ) -> BatchResult:
        results: list[ItemResult] = []
        total = len(inspection.items)
        cancelled = False
        self.root.mkdir(parents=True, exist_ok=True)
        for index, item in enumerate(inspection.items):
            if cancelled or (cancel_event is not None and cancel_event.is_set()):
                cancelled = True
                results.append(ItemResult(item.source.name, "skipped", "Upload cancelled"))
                continue
            if item.error:
                results.append(ItemResult(item.source.name, "failed", item.error))
                continue
            try:
                if progress:
                    progress(index, total, "extract", item.source.name)
                self._install_item(item, decisions, cancel_event, progress, index, total)
                results.append(ItemResult(item.source.name, "success"))
                self.logger(f"Mod upload installed: {item.source.name}")
            except UploadCancelled as exc:
                cancelled = True
                results.append(ItemResult(item.source.name, "cancelled", str(exc)))
                self.logger(f"Mod upload cancelled: {item.source.name}")
            except Exception as exc:
                results.append(ItemResult(item.source.name, "failed", str(exc)))
                self.logger(f"Mod upload failed for {item.source.name}: {exc}")
        return BatchResult(tuple(results))

    def _install_item(
        self,
        item: InspectedItem,
        decisions: Mapping[str, str],
        cancel_event: threading.Event | None,
        progress: ProgressCallback | None,
        index: int,
        total: int,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix=".palsitter-upload-", dir=self.root) as temporary:
            transaction_root = Path(temporary)
            extracted = transaction_root / "extracted"
            _materialize(item.source, extracted, cancel_event)
            content_root = _content_root(extracted, _all_files(extracted))
            stage_root = transaction_root / "stage"
            backup_root = transaction_root / "rollback"
            transaction = _SwapTransaction(backup_root)
            prepared: list[tuple[DetectedMod, Path, Path]] = []
            for unit_index, unit in enumerate(item.units):
                _check_cancel(cancel_event)
                if not self.platform_supported and unit.kind in ("official", "ue4ss", "palschema"):
                    raise UploadError("This mod type is not supported on native Linux")
                if unit.kind == "palschema" and not self.palschema_installed():
                    raise UploadError(PALSCHEMA_REQUIRED)
                target = self._destination(unit, decisions)
                staged = stage_root / str(unit_index)
                if unit.kind == "pak":
                    staged.mkdir(parents=True)
                    for member in unit.members:
                        shutil.copy2(content_root / PurePosixPath(member), staged / PurePosixPath(member).name)
                else:
                    source = content_root if unit.source_prefix == "." else content_root / PurePosixPath(unit.source_prefix)
                    shutil.copytree(source, staged)
                    if unit.kind == "ue4ss":
                        (staged / "enabled.txt").touch(exist_ok=True)
                prepared.append((unit, staged, target))
            _check_cancel(cancel_event)
            if progress:
                progress(index, total, "install", item.source.name)
            try:
                for unit, staged, target in prepared:
                    if unit.kind == "pak":
                        for source in sorted(staged.iterdir(), key=lambda path: path.name.casefold()):
                            transaction.replace(source, target / source.name)
                    else:
                        transaction.replace(staged, target)
                    if unit.kind == "official":
                        self._enable_official_package(unit.name, transaction_root, transaction)
            except Exception:
                transaction.rollback()
                raise
            if progress:
                progress(index, total, "cleanup", item.source.name)

    def _enable_official_package(
        self, package: str, transaction_root: Path, transaction: _SwapTransaction
    ) -> None:
        path = self.root / "Mods" / "PalModSettings.ini"
        try:
            original = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            original = "[PalModSettings]\n"
        lines = original.splitlines()
        section = None
        active = False
        global_flag = False
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1].casefold()
            if section != "palmodsettings":
                continue
            key, separator, value = stripped.partition("=")
            if not separator:
                continue
            if key.casefold() == "bglobalenablemod":
                lines[index] = "bGlobalEnableMod=true"
                global_flag = True
            elif key.casefold() == "activemodlist" and value.strip().casefold() == package.casefold():
                active = True
        if "[palmodsettings]" not in {line.strip().casefold() for line in lines}:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append("[PalModSettings]")
        if not global_flag:
            lines.append("bGlobalEnableMod=true")
        if not active:
            lines.append(f"ActiveModList={package}")
        staged = transaction_root / "PalModSettings.ini.new"
        staged.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        transaction.replace(staged, path)


__all__ = [
    "BatchInspection",
    "BatchResult",
    "DetectedMod",
    "InspectedItem",
    "ItemResult",
    "MAX_ARCHIVE_MEMBERS",
    "MAX_EXPANDED_BYTES",
    "MAX_UPLOAD_BYTES",
    "ModUploadService",
    "PAK_DESTINATIONS",
    "PAK_LOGIC_MODS",
    "PAK_ROOT",
    "PAK_TILDE_MODS",
    "PALSCHEMA_REQUIRED",
    "UNSUPPORTED_FORMAT",
    "UnsupportedUpload",
    "UploadCancelled",
    "UploadError",
    "UploadFile",
    "UploadItem",
]
