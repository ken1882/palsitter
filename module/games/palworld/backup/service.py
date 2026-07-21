from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional

from module.games.palworld.config import PalworldProfile


SAVE_DIR_PATTERN = re.compile(r"^([0-9]|[A-Z])+$")


@dataclass
class BackupResult:
    path: Optional[Path]
    files_added: int
    deleted: List[Path]
    skipped: bool = False


@dataclass(frozen=True)
class BuiltInBackupInfo:
    world_id: str
    category: str
    name: str
    path: Path
    size_bytes: int
    modified_at: dt.datetime


@dataclass(frozen=True)
class BackupRunResult:
    status: str
    backup: Optional[BackupResult] = None
    flush_attempted: bool = False
    flush_succeeded: bool = False
    flush_error: Optional[str] = None
    reason: Optional[str] = None
    error: Optional[str] = None

    @property
    def may_be_stale(self) -> bool:
        return self.flush_attempted and not self.flush_succeeded


class ServerOwnershipState(str, Enum):
    INACTIVE = "inactive"
    OWNED_RUNNING = "owned_running"
    EXTERNAL_ATTACHED = "external_attached"


@dataclass(frozen=True)
class RestoreResult:
    backup_path: Path
    safety_backup_path: Path
    initial_state: ServerOwnershipState
    restarted: bool


class BackupService:
    def __init__(
        self,
        profile: PalworldProfile,
        logger: Callable[[str], None] = print,
        sleep: Callable[[float], None] = time.sleep,
        rest_client=None,
    ) -> None:
        self.profile = profile
        self.log = logger
        self.sleep = sleep
        self.rest_client = rest_client
        self._stop_event = threading.Event()

    def eligible_save_dirs(self) -> List[Path]:
        base_dir = Path(self.profile.backup_source)
        if not base_dir.exists():
            raise FileNotFoundError(f"Backup source does not exist: {base_dir}")
        return [
            path
            for path in base_dir.iterdir()
            if path.is_dir()
            and not path.is_symlink()
            and SAVE_DIR_PATTERN.fullmatch(path.name)
        ]

    def create_backup(
        self, timestamp: Optional[dt.datetime] = None, *, enforce_retention: bool = True
    ) -> BackupResult:
        base_dir = Path(self.profile.backup_source)
        backup_dir = Path(self.profile.backup_dir)

        timestamp = timestamp or dt.datetime.now()
        backup_file = backup_dir / f"{timestamp.strftime('%Y.%m.%d.%H%M%S')}.zip"
        suffix = 2
        while backup_file.exists():
            backup_file = backup_dir / (
                f"{timestamp.strftime('%Y.%m.%d.%H%M%S')}-{suffix}.zip"
            )
            suffix += 1

        files_to_add: list[tuple[Path, Path]] = []
        for save_dir in self.eligible_save_dirs():
            for root, dirs, files in os.walk(save_dir):
                root_path = Path(root)
                dirs[:] = [
                    name
                    for name in dirs
                    if name.lower() != "backup"
                    and not (root_path / name).is_symlink()
                ]
                for filename in files:
                    full_path = root_path / filename
                    if full_path.is_symlink():
                        continue
                    files_to_add.append((full_path, full_path.relative_to(base_dir)))

        if not files_to_add:
            self.log(f"Backup skipped: no save files found in {base_dir}")
            return BackupResult(path=None, files_added=0, deleted=[], skipped=True)

        backup_dir.mkdir(parents=True, exist_ok=True)
        temporary = backup_file.with_name(
            f".{backup_file.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            with zipfile.ZipFile(
                temporary,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as zipf:
                for full_path, rel_path in files_to_add:
                    zipf.write(full_path, rel_path)
            os.replace(temporary, backup_file)
        except Exception:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise

        deleted = self.enforce_retention() if enforce_retention else []
        self.log(f"Backup created: {backup_file} ({len(files_to_add)} files)")
        for path in deleted:
            self.log(f"Old backup deleted: {path}")
        return BackupResult(path=backup_file, files_added=len(files_to_add), deleted=deleted)

    def create_backup_with_flush(
        self,
        timestamp: Optional[dt.datetime] = None,
        *,
        enforce_retention: bool = True,
    ) -> BackupRunResult:
        """Attempt a REST world flush and create a backup regardless of flush failure."""
        flush_attempted = self.rest_client is not None
        flush_succeeded = False
        flush_error: Optional[str] = None
        if flush_attempted:
            try:
                self.rest_client.save()
                flush_succeeded = True
            except Exception as exc:
                flush_error = str(exc)
                self.log(
                    "World flush failed before backup; continuing with on-disk data: "
                    f"{exc}"
                )
        try:
            backup = self.create_backup(timestamp, enforce_retention=enforce_retention)
        except Exception as exc:
            return BackupRunResult(
                status="failed",
                flush_attempted=flush_attempted,
                flush_succeeded=flush_succeeded,
                flush_error=flush_error,
                error=str(exc),
            )
        return BackupRunResult(
            status="skipped" if backup.skipped else "created",
            backup=backup,
            flush_attempted=flush_attempted,
            flush_succeeded=flush_succeeded,
            flush_error=flush_error,
            reason="no_save_files" if backup.skipped else None,
        )

    def run_scheduled_backup(self) -> BackupRunResult:
        """Run one scheduler eligibility/backup cycle and return an explicit outcome."""
        if self._scheduled_backup_should_skip():
            self.log("Backup skipped: no players online")
            return BackupRunResult(status="skipped", reason="no_players")
        try:
            backup = self.create_backup()
        except Exception as exc:
            return BackupRunResult(status="failed", error=str(exc))
        return BackupRunResult(
            status="skipped" if backup.skipped else "created",
            backup=backup,
            reason="no_save_files" if backup.skipped else None,
        )

    def enforce_retention(self) -> List[Path]:
        backup_dir = Path(self.profile.backup_dir)
        if not backup_dir.exists():
            return []
        backups = sorted(
            [path for path in backup_dir.glob("*.zip") if path.is_file()],
            key=lambda path: (path.name, path.stat().st_mtime),
        )
        deleted = []
        while len(backups) > int(self.profile.backup_retention_count):
            old = backups.pop(0)
            old.unlink()
            deleted.append(old)
        return deleted

    def latest_backup(self) -> Optional[Path]:
        backup_dir = Path(self.profile.backup_dir)
        if not backup_dir.exists():
            return None
        backups = [path for path in backup_dir.glob("*.zip") if path.is_file()]
        if not backups:
            return None
        return max(backups, key=lambda path: path.stat().st_mtime)

    def backup_before(self, cutoff: dt.datetime) -> Optional[Path]:
        backup_dir = Path(self.profile.backup_dir)
        if not backup_dir.exists():
            return None
        cutoff_ts = cutoff.timestamp()
        backups = [
            path
            for path in backup_dir.glob("*.zip")
            if path.is_file() and path.stat().st_mtime < cutoff_ts
        ]
        if not backups:
            return None
        return max(backups, key=lambda path: path.stat().st_mtime)

    def restore(self, backup_path: Path) -> None:
        backup_path = Path(backup_path)
        if backup_path.is_dir():
            self._restore_builtin_backup(backup_path)
            return
        base_dir = Path(self.profile.backup_source)
        base_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(backup_path) as zipf:
            base_resolved = base_dir.resolve()
            for member in zipf.infolist():
                target = (base_dir / member.filename).resolve()
                if target != base_resolved and base_resolved not in target.parents:
                    raise ValueError(f"Backup contains an unsafe path: {member.filename}")
            zipf.extractall(base_dir)
        self.log(f"Restored {backup_path} into {base_dir}")

    def _restore_builtin_backup(self, backup_path: Path) -> None:
        if backup_path.is_symlink():
            raise ValueError("Built-in backup must not be a symbolic link")
        world_dir = Path(self.profile.backup_source) / self.profile.dedicated_server_name
        resolved_world = world_dir.resolve()
        resolved_backup = backup_path.resolve()
        if (
            backup_path.parent.name.casefold() != "world"
            or backup_path.parent.parent.name.casefold() != "backup"
            or backup_path.parent.parent.parent.resolve() != resolved_world
        ):
            raise ValueError(
                "Built-in backup must be inside the active world's backup/world directory"
            )

        files: list[tuple[Path, Path]] = []
        for root, dirs, filenames in os.walk(backup_path):
            root_path = Path(root)
            dirs[:] = [
                name for name in dirs if not (root_path / name).is_symlink()
            ]
            for filename in filenames:
                source = root_path / filename
                if source.is_file() and not source.is_symlink():
                    files.append((source, source.relative_to(backup_path)))
        if not files:
            raise ValueError(f"Built-in backup contains no save files: {backup_path}")

        world_dir.mkdir(parents=True, exist_ok=True)
        for source, relative in files:
            target = (world_dir / relative).resolve()
            if target != resolved_world and resolved_world not in target.parents:
                raise ValueError(f"Built-in backup contains an unsafe path: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        self.log(f"Restored built-in backup {resolved_backup} into {world_dir}")

    def restore_preserving_state(
        self,
        backup_path: Path,
        *,
        initial_state: ServerOwnershipState | str,
        stop_server: Optional[Callable[[], None]] = None,
        wait_until_inactive: Optional[Callable[[float], bool]] = None,
        kill_server: Optional[Callable[[], None]] = None,
        start_server: Optional[Callable[[], None]] = None,
        stop_timeout: Optional[float] = None,
    ) -> RestoreResult:
        """Restore with a mandatory safety backup and preserve server ownership state.

        An owned server is restarted. An initially inactive or externally attached
        server remains inactive. Externally attached servers are never force-killed.
        """
        state = ServerOwnershipState(initial_state)
        timeout = (
            float(stop_timeout)
            if stop_timeout is not None
            else max(10.0, float(self.profile.shutdown_wait_seconds) + 10.0)
        )
        was_owned = state is ServerOwnershipState.OWNED_RUNNING
        restarted = False
        restart_attempted = False

        if was_owned and start_server is None:
            raise ValueError("start_server is required for an owned running server")

        if state is not ServerOwnershipState.INACTIVE:
            if stop_server is None or wait_until_inactive is None:
                raise ValueError(
                    "stop_server and wait_until_inactive are required for a running server"
                )
            stop_server()
            stopped = bool(wait_until_inactive(timeout))
            if not stopped and was_owned:
                if kill_server is None:
                    raise RuntimeError(
                        "Owned server did not stop and no kill callback was provided"
                    )
                kill_server()
                stopped = bool(wait_until_inactive(5.0))
            if not stopped:
                raise RuntimeError("Server is still running; restore was not attempted")

        try:
            safety = self.create_backup(enforce_retention=False)
            if safety.skipped or safety.path is None:
                raise RuntimeError("Safety backup could not be created; restore was not attempted")
            self.restore(Path(backup_path))
            self.enforce_retention()
            if was_owned:
                assert start_server is not None
                restart_attempted = True
                start_server()
                restarted = True
            return RestoreResult(
                backup_path=Path(backup_path),
                safety_backup_path=safety.path,
                initial_state=state,
                restarted=restarted,
            )
        except Exception:
            if (
                was_owned
                and not restarted
                and not restart_attempted
                and start_server is not None
            ):
                start_server()
            raise

    def delete_backup(self, backup_path: Path) -> None:
        backup_path = Path(backup_path)
        backup_dir = Path(self.profile.backup_dir).resolve()
        if backup_path.resolve().parent != backup_dir or backup_path.suffix.lower() != ".zip":
            raise ValueError("Backup file must be a .zip inside the configured backup directory")
        backup_path.unlink()
        self.log(f"Backup deleted: {backup_path}")

    def list_backups(self) -> List[Path]:
        backup_dir = Path(self.profile.backup_dir)
        if not backup_dir.exists():
            return []
        return sorted(
            (path for path in backup_dir.glob("*.zip") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    def list_builtin_backups(self) -> List[BuiltInBackupInfo]:
        """List world snapshots for the currently active Palworld world."""
        save_dir = (
            Path(self.profile.backup_source) / self.profile.dedicated_server_name
        )
        if not save_dir.is_dir() or save_dir.is_symlink():
            return []

        backups: list[BuiltInBackupInfo] = []
        backup_roots = [
            path
            for path in save_dir.iterdir()
            if path.name.casefold() == "backup"
            and path.is_dir()
            and not path.is_symlink()
        ]
        for backup_root in backup_roots:
            for category_dir in backup_root.iterdir():
                if (
                    category_dir.name.casefold() != "world"
                    or not category_dir.is_dir()
                    or category_dir.is_symlink()
                ):
                    continue
                for snapshot_dir in category_dir.iterdir():
                    if not snapshot_dir.is_dir() or snapshot_dir.is_symlink():
                        continue
                    files: list[Path] = []
                    for root, dirs, filenames in os.walk(snapshot_dir):
                        root_path = Path(root)
                        dirs[:] = [
                            name
                            for name in dirs
                            if not (root_path / name).is_symlink()
                        ]
                        files.extend(
                            path
                            for filename in filenames
                            if (path := root_path / filename).is_file()
                            and not path.is_symlink()
                        )
                    modified = max(
                        (path.stat().st_mtime for path in files),
                        default=snapshot_dir.stat().st_mtime,
                    )
                    backups.append(
                        BuiltInBackupInfo(
                            world_id=save_dir.name,
                            category=category_dir.name,
                            name=snapshot_dir.name,
                            path=snapshot_dir,
                            size_bytes=sum(path.stat().st_size for path in files),
                            modified_at=dt.datetime.fromtimestamp(modified),
                        )
                    )
        return sorted(
            backups,
            key=lambda backup: (
                backup.modified_at,
                backup.world_id,
                backup.category,
                backup.name,
            ),
            reverse=True,
        )

    def _scheduled_backup_should_skip(self) -> bool:
        if not self.profile.skip_backup_when_no_players or self.rest_client is None:
            return False
        try:
            return int(self.rest_client.metrics().get("currentplayernum", -1)) == 0
        except Exception as exc:
            self.log(f"Could not check online players before backup: {exc}")
            return False

    def stop(self) -> None:
        self._stop_event.set()

    def scheduled_loop(self) -> None:
        depth = 0
        interval = max(1, int(float(self.profile.backup_interval_minutes) * 60))
        delay = interval
        while not self._stop_event.is_set():
            if self._stop_event.wait(delay):
                return
            try:
                if self._scheduled_backup_should_skip():
                    self.log("Backup skipped: no players online")
                else:
                    self.create_backup()
                depth = 0
                delay = interval
            except FileNotFoundError as exc:
                if "Backup source does not exist" in str(exc):
                    delay = interval
                    continue
                self.log(f"Error during backup: {exc}")
                depth += 1
                if depth > 5:
                    self.log("Max retry depth reached; waiting until next backup interval")
                    depth = 0
                    delay = interval
                else:
                    delay = 10
            except Exception as exc:
                self.log(f"Error during backup: {exc}")
                depth += 1
                if depth > 5:
                    self.log("Max retry depth reached; waiting until next backup interval")
                    depth = 0
                    delay = interval
                else:
                    delay = 10
