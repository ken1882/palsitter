from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
import threading
from collections.abc import Iterator
from typing import Any, Callable, ClassVar, Mapping

from module.instances import InstanceRecord


@dataclass(frozen=True)
class OperationProgress:
    """Structured progress for an adapter-owned long-running operation."""

    kind: str
    phase: str
    percent: float | None = None
    message: str = ""
    error: str | None = None


@dataclass(frozen=True)
class UpdateInfo:
    installed_build_id: str | None = None
    available_build_id: str | None = None
    checked_at: dt.datetime | None = None
    status: str = "unknown"


@dataclass(frozen=True)
class AdapterEvent:
    timestamp: dt.datetime
    type: str
    message: str
    details: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class GameCapabilities:
    lifecycle: bool = False
    updates: bool = False
    backups: bool = False
    players: bool = False
    world_settings: bool = False
    save_import: bool = False


def _format_uptime(value: int | None) -> str:
    if value is None:
        return "-"
    days, remainder = divmod(max(0, value), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}d {hours:02d}h {minutes:02d}m {seconds:02d}s"


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "-"
    amount = float(max(0, value))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return "-"


@dataclass(frozen=True)
class InstanceStatusSummary(Mapping[str, object]):
    """Game-neutral dashboard data with a temporary mapping compatibility view."""

    server_name: str = "-"
    state: str = "inactive"
    current_players: int | None = None
    max_players: int | None = None
    current_fps: float | None = None
    average_fps: float | None = None
    uptime_seconds: int | None = None
    days: int | None = None
    cpu_percent: float | None = None
    memory_bytes: int | None = None
    game_version: str | None = None
    installed_build_id: str | None = None
    available_build_id: str | None = None
    latest_backup: str | None = None
    latest_backup_at: dt.datetime | None = None
    next_backup_at: dt.datetime | None = None
    endpoint_states: Mapping[str, str] = field(default_factory=dict)

    _LEGACY_KEYS: ClassVar[tuple[str, ...]] = (
        "server_name", "state", "players", "fps", "uptime", "memory",
        "latest_backup", "days", "cpu", "game_version",
    )

    def _legacy_value(self, key: str) -> object:
        if key == "players":
            if self.current_players is None and self.max_players is None:
                return "-"
            return f"{self.current_players if self.current_players is not None else '-'}/{self.max_players if self.max_players is not None else '-'}"
        if key == "fps":
            if self.current_fps is None and self.average_fps is None:
                return "-"
            current = f"{self.current_fps:.1f}" if self.current_fps is not None else "-"
            average = f"{self.average_fps:.1f}" if self.average_fps is not None else "-"
            return f"{current} / {average}"
        if key == "uptime":
            return _format_uptime(self.uptime_seconds)
        if key == "memory":
            return _format_bytes(self.memory_bytes)
        if key == "cpu":
            return f"{self.cpu_percent:.1f}%" if self.cpu_percent is not None else "-"
        if key in ("days", "game_version", "latest_backup"):
            value = getattr(self, key)
            return "-" if value is None else str(value)
        if key in ("server_name", "state"):
            return getattr(self, key)
        raise KeyError(key)

    def __getitem__(self, key: str) -> object:
        return self._legacy_value(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._LEGACY_KEYS)

    def __len__(self) -> int:
        return len(self._LEGACY_KEYS)


@dataclass(frozen=True)
class GameAdapter:
    id: str
    display_name: str
    runnable: bool
    webui_module: str
    capabilities: GameCapabilities = field(default_factory=GameCapabilities)

    def default_config(self, name: str) -> dict[str, Any]:
        if self.id == "satisfactory":
            return {}
        from module.games.palworld.config import new_profile

        return new_profile(name).to_game_config()

    def clone_config(
        self,
        source_name: str,
        source_config: Mapping[str, Any],
        new_name: str,
    ) -> dict[str, Any]:
        if self.id == "satisfactory":
            return dict(source_config)
        from module.games.palworld.config import clone_profile_config

        return clone_profile_config(source_name, source_config, new_name).to_game_config()

    def load_typed_profile(self, name: str, config: Mapping[str, Any]):
        if self.id != "palworld":
            return None
        from module.games.palworld.config import PalworldProfile

        return PalworldProfile.from_game_config(name, config)

    def is_installed(self, record: InstanceRecord) -> bool:
        if not self.capabilities.updates:
            return False
        from module.games.palworld.config import fixed_executable_path

        return fixed_executable_path(record.name).is_file()

    def update_on_start(self, record: InstanceRecord) -> bool:
        """Return whether this adapter requests an update for an installed start."""
        if not self.capabilities.updates:
            return False
        profile = self.load_typed_profile(record.name, record.game_config)
        return bool(getattr(profile, "update_on_start", True))

    def check_update(
        self,
        record: InstanceRecord,
        progress: Callable[[OperationProgress], None] | None = None,
        *,
        force: bool = False,
        log: Callable[[str], None] = print,
    ) -> UpdateInfo:
        if not self.capabilities.updates:
            raise RuntimeError(f"{self.display_name} does not support updates")
        from module.games.palworld.update import PalworldUpdateService

        profile = self.load_typed_profile(record.name, record.game_config)
        return PalworldUpdateService(profile, logger=log, progress=progress).check_update(force=force)

    def install_or_update(
        self,
        record: InstanceRecord,
        log=print,
        progress: Callable[[OperationProgress], None] | None = None,
        *,
        validate: bool | None = None,
    ) -> UpdateInfo:
        if not self.capabilities.updates:
            raise RuntimeError(f"{self.display_name} does not support updates")
        from module.games.palworld.update import PalworldUpdateService

        profile = self.load_typed_profile(record.name, record.game_config)
        if validate is None:
            validate = bool(profile.steam_validate)
        return PalworldUpdateService(profile, logger=log, progress=progress).install_or_update(
            validate=validate
        )

    def bootstrap(
        self,
        record: InstanceRecord,
        log,
        progress: Callable[[OperationProgress], None] | None = None,
    ) -> None:
        """Compatibility alias for the complete initial installation operation."""
        if not self.runnable:
            raise RuntimeError(f"{self.display_name} support is not implemented")
        self.install_or_update(record, log=log, progress=progress)

    def save_before_shutdown(self, record: InstanceRecord) -> None:
        if not self.runnable:
            raise RuntimeError(f"{self.display_name} support is not implemented")
        if self.id != "palworld":
            raise RuntimeError(f"{self.display_name} does not provide a save operation")
        from module.games.palworld.server import PalRestClient

        PalRestClient(self.load_typed_profile(record.name, record.game_config)).save()

    def is_running(self, record: InstanceRecord) -> bool:
        if not self.runnable:
            return False
        from module.games.palworld.server.status import instance_is_running

        return instance_is_running(self.load_typed_profile(record.name, record.game_config))

    def process_name(self, record: InstanceRecord) -> str | None:
        if not self.runnable:
            return None
        from pathlib import Path

        profile = self.load_typed_profile(record.name, record.game_config)
        return Path(profile.executable).name.lower()

    def resource_processes(self, record: InstanceRecord) -> tuple[object, ...]:
        if self.id != "palworld":
            return ()
        from module.games.palworld.server.status import matching_instance_processes

        profile = self.load_typed_profile(record.name, record.game_config)
        return tuple(matching_instance_processes(profile))

    def force_stop(self, record: InstanceRecord) -> None:
        if self.id != "palworld":
            return
        from module.games.palworld.server.manager import force_stop_process

        force_stop_process(self.load_typed_profile(record.name, record.game_config))

    def verify_managed(self, record: InstanceRecord) -> bool:
        if self.id != "palworld":
            return self.is_running(record)
        from module.games.palworld.server.agent import AgentClient

        profile = self.load_typed_profile(record.name, record.game_config)
        if os.name != "nt":
            return self.is_running(record)
        AgentClient.connect_existing(record.name).verify(profile)
        return True

    def supervise(
        self,
        record: InstanceRecord,
        log,
        stop_requested,
        state_callback,
        interval_seconds: int = 60,
        event_callback=lambda _: None,
        handoff_requested=lambda: False,
        force_stop_requested=lambda: False,
        adopt_managed: bool = False,
    ) -> None:
        if not self.runnable:
            raise RuntimeError(f"{self.display_name} support is not implemented")
        from module.games.palworld.backup import BackupService
        from module.games.palworld.server import (
            LifecycleEvent,
            PalRestClient,
            PalServerManager,
            RestartHistoryStore,
        )
        from module.games.palworld.server.agent import AgentClient

        profile = self.load_typed_profile(record.name, record.game_config)
        backup_service = BackupService(profile, logger=log, rest_client=PalRestClient(profile))
        history = RestartHistoryStore(record.name)

        def record_event(event) -> None:
            if isinstance(event, LifecycleEvent):
                try:
                    history.append(event)
                except (OSError, TypeError, ValueError) as exc:
                    log(f"Could not persist restart history: {exc}")
            event_callback(event)

        if profile.backup_interval_minutes > 0:
            threading.Thread(target=backup_service.scheduled_loop, daemon=True).start()
        PalServerManager(
            profile,
            logger=log,
            stop_requested=stop_requested,
            handoff_requested=handoff_requested,
            force_stop_requested=force_stop_requested,
            adopt_managed=adopt_managed,
            backup_service=backup_service,
            state_callback=state_callback,
            event_callback=record_event,
            agent_client_factory=AgentClient if os.name == "nt" else None,
        ).supervise_loop(interval_seconds)

    def record_audit_event(self, record: InstanceRecord, event: AdapterEvent) -> None:
        if self.id != "palworld":
            return
        from module.games.palworld.audit import AuditEvent, AuditStore

        AuditStore(record.name).append(
            AuditEvent(event.timestamp, event.type, event.message)
        )

    def backup_service(self, record: InstanceRecord, logger=print):
        if not self.runnable:
            return None
        from module.games.palworld.backup import BackupService
        from module.games.palworld.server import PalRestClient

        profile = self.load_typed_profile(record.name, record.game_config)
        return BackupService(profile, logger=logger, rest_client=PalRestClient(profile))

    def status_summary(
        self,
        record: InstanceRecord,
        resource_usage: Mapping[str, float | int] | None = None,
        *,
        rest_timeout: float | None = None,
    ) -> InstanceStatusSummary:
        if not self.runnable:
            return InstanceStatusSummary(state="unsupported")
        from module.games.palworld.server import get_pal_rest_cache
        from module.games.palworld.server.status import endpoint_status, instance_is_running

        profile = self.load_typed_profile(record.name, record.game_config)
        rest_cache = get_pal_rest_cache(record.name)
        running = instance_is_running(profile)
        try:
            endpoints = endpoint_status(profile, process_running=running)
        except Exception:
            endpoints = {}
        current_players = max_players = uptime_seconds = days = None
        current_fps = average_fps = None
        game_version = None
        rest_snapshot = rest_cache.snapshot()
        if running and endpoints.get("rest") == "open":
            if rest_snapshot.metrics is None or rest_snapshot.info is None:
                rest_cache.poll_once()
                rest_snapshot = rest_cache.snapshot()
            metrics = rest_snapshot.metrics
            if metrics is not None:
                try:
                    current_players = int(metrics.get("currentplayernum"))
                except (TypeError, ValueError):
                    pass
                try:
                    max_players = int(metrics.get("maxplayernum"))
                except (TypeError, ValueError):
                    pass
                try:
                    current_fps = float(metrics.get("serverfps"))
                    average_fps = float(metrics.get("serverfpsaverage", current_fps))
                except (TypeError, ValueError):
                    pass
                try:
                    uptime_seconds = max(0, int(metrics.get("uptime")))
                except (TypeError, ValueError):
                    pass
                try:
                    days = int(metrics.get("days"))
                except (TypeError, ValueError):
                    pass
        info = rest_snapshot.info
        if info is not None:
            version = info.get("version")
            if version not in (None, ""):
                game_version = str(version)
        if running:
            rest_cache.ensure_started()
        service = self.backup_service(record)
        latest = service.latest_backup() if service is not None else None
        latest_at = None
        if latest is not None:
            try:
                latest_at = dt.datetime.fromtimestamp(latest.stat().st_mtime)
            except OSError:
                pass
        return InstanceStatusSummary(
            server_name=profile.server_name,
            state="running" if running else "inactive",
            current_players=current_players,
            max_players=max_players,
            current_fps=current_fps,
            average_fps=average_fps,
            uptime_seconds=uptime_seconds,
            days=days,
            cpu_percent=(
                float(resource_usage["cpu_percent"])
                if resource_usage and resource_usage.get("cpu_percent") is not None
                else None
            ),
            memory_bytes=(
                int(resource_usage["memory_bytes"])
                if resource_usage and resource_usage.get("memory_bytes") is not None
                else None
            ),
            game_version=game_version,
            latest_backup=latest.name if latest else None,
            latest_backup_at=latest_at,
            endpoint_states=endpoints,
        )

    def after_create(self, record: InstanceRecord) -> None:
        if self.id != "palworld":
            return
        from module.games.palworld.config import sync_game_user_settings
        from module.games.palworld.worldsettings.service import ensure_world_settings

        profile = self.load_typed_profile(record.name, record.game_config)
        sync_game_user_settings(profile)
        ensure_world_settings(profile)


_GAMES = {
    "palworld": GameAdapter(
        "palworld",
        "Palworld",
        True,
        "module.games.palworld.webui",
        GameCapabilities(
            lifecycle=True,
            updates=True,
            backups=True,
            players=True,
            world_settings=True,
            save_import=True,
        ),
    ),
    "satisfactory": GameAdapter(
        "satisfactory",
        "Satisfactory",
        False,
        "module.games.satisfactory.webui",
    ),
}


def get_game(game_id: str) -> GameAdapter:
    try:
        return _GAMES[game_id]
    except KeyError as exc:
        raise ValueError(f"Unknown game: {game_id}") from exc


def list_games() -> tuple[GameAdapter, ...]:
    return tuple(_GAMES.values())
