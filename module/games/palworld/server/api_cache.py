from __future__ import annotations

import socket
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Hashable

import psutil

from module.games.palworld.config import PalworldProfile, load_profile
from module.games.palworld.players_cache import PlayerCache
from module.games.palworld.server.rest import PalRestClient
from module.games.palworld.server.status import matching_instance_processes
from module.games.palworld.version_cache import read_version_cache, update_version_cache
from module.instances import profile_path


POLL_INTERVAL_SECONDS = 3.0


@dataclass(frozen=True)
class PalRestSnapshot:
    session_active: bool = False
    rest_open: bool = False
    info: dict | None = None
    players: dict | None = None
    metrics: dict | None = None
    game_data: dict | None = None
    info_error: str | None = None
    players_error: str | None = None
    metrics_error: str | None = None
    game_data_error: str | None = None


def _session_identity(profile: PalworldProfile) -> Hashable | None:
    identities = []
    for process in matching_instance_processes(profile):
        try:
            created_at = process.create_time()
        except (AttributeError, psutil.Error):
            created_at = None
        identities.append((getattr(process, "pid", id(process)), created_at))
    return tuple(sorted(identities)) or None


def _rest_open(profile: PalworldProfile) -> bool:
    try:
        connection = socket.create_connection(
            (profile.rest_host, profile.rest_port), timeout=1
        )
    except OSError:
        return False
    connection.close()
    return True


class PalRestCache:
    """Session-scoped cache for Palworld's read-only REST endpoints."""

    def __init__(
        self,
        name: str,
        *,
        profile_loader: Callable[[str], PalworldProfile] = load_profile,
        session_probe: Callable[[PalworldProfile], Hashable | None] = _session_identity,
        rest_probe: Callable[[PalworldProfile], bool] = _rest_open,
        client_factory: Callable[[PalworldProfile], PalRestClient] | None = None,
        players_updated: Callable[[list[dict]], object] | None = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        self.name = name
        self.profile_loader = profile_loader
        self.session_probe = session_probe
        self.rest_probe = rest_probe
        self.client_factory = client_factory or (
            lambda profile: PalRestClient(
                profile,
                timeout=5,
                availability_probe=lambda _: True,
            )
        )
        self.players_updated = players_updated or (
            lambda players: PlayerCache(name).upsert(
                players, poll_interval_seconds=self.poll_interval
            )
        )
        self.poll_interval = float(poll_interval)
        self._lock = threading.RLock()
        self._poll_lock = threading.Lock()
        self._session_identity: Hashable | None = None
        self._rest_open = False
        cached_version = read_version_cache(name).get("game_version")
        self._info: dict | None = (
            {"version": str(cached_version)}
            if cached_version not in (None, "")
            else None
        )
        self._players: dict | None = None
        self._metrics: dict | None = None
        self._game_data: dict | None = None
        self._info_error: str | None = None
        self._players_error: str | None = None
        self._metrics_error: str | None = None
        self._game_data_error: str | None = None
        self._info_attempted = False
        self._next_poll_at = 0.0
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def ensure_started(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._poll_loop,
                name=f"pal-rest-cache-{self.name}",
                daemon=True,
            )
            thread = self._thread
        thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def snapshot(self) -> PalRestSnapshot:
        with self._lock:
            return PalRestSnapshot(
                session_active=self._session_identity is not None,
                rest_open=self._rest_open,
                info=deepcopy(self._info),
                players=deepcopy(self._players),
                metrics=deepcopy(self._metrics),
                game_data=deepcopy(self._game_data),
                info_error=self._info_error,
                players_error=self._players_error,
                metrics_error=self._metrics_error,
                game_data_error=self._game_data_error,
            )

    def poll_once(self, now: float | None = None) -> None:
        with self._poll_lock:
            self._poll_once(now)

    def _poll_once(self, now: float | None = None) -> None:
        supplied_now = now is not None
        now = time.monotonic() if now is None else float(now)
        profile = self.profile_loader(self.name)
        identity = self.session_probe(profile)
        with self._lock:
            if identity != self._session_identity:
                self._begin_session(identity)
            if identity is None:
                return

        try:
            rest_open = bool(self.rest_probe(profile))
        except Exception:
            rest_open = False
        with self._lock:
            self._rest_open = rest_open
            if not profile.launch_enable_gamedata_api:
                self._game_data = None
                self._game_data_error = None
            if not rest_open:
                return
            fetch_info = not self._info_attempted
            if fetch_info:
                self._info_attempted = True
            fetch_polling_data = now >= self._next_poll_at
            if fetch_polling_data:
                self._next_poll_at = float("inf")

        def fetch_info_result() -> None:
            try:
                result = self.client_factory(profile).info()
                with self._lock:
                    self._info = result if isinstance(result, dict) else {}
                    self._info_error = None
                version = result.get("version") if isinstance(result, dict) else None
                if version not in (None, ""):
                    update_version_cache(self.name, game_version=str(version))
            except Exception as exc:
                with self._lock:
                    self._info_error = str(exc)

        def fetch_players_result() -> None:
            try:
                result = self.client_factory(profile).players()
                players = result if isinstance(result, dict) else {"players": []}
                rows = players.get("players", [])
                rows = rows if isinstance(rows, list) else []
                with self._lock:
                    self._players = players
                    self._players_error = None
                try:
                    self.players_updated(
                        [row for row in rows if isinstance(row, dict)]
                    )
                except (OSError, TypeError, ValueError):
                    pass
            except Exception as exc:
                with self._lock:
                    self._players_error = str(exc)

        def fetch_metrics_result() -> None:
            try:
                result = self.client_factory(profile).metrics()
                with self._lock:
                    self._metrics = result if isinstance(result, dict) else {}
                    self._metrics_error = None
            except Exception as exc:
                with self._lock:
                    self._metrics_error = str(exc)

        def fetch_game_data_result() -> None:
            try:
                result = self.client_factory(profile).game_data()
                with self._lock:
                    self._game_data = result if isinstance(result, dict) else {}
                    self._game_data_error = None
            except Exception as exc:
                with self._lock:
                    self._game_data_error = str(exc)

        targets = []
        if fetch_info:
            targets.append(fetch_info_result)
        if fetch_polling_data:
            targets.extend((fetch_players_result, fetch_metrics_result))
            if profile.launch_enable_gamedata_api:
                targets.append(fetch_game_data_result)
        threads = [threading.Thread(target=target) for target in targets]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if fetch_polling_data:
            completed_at = now if supplied_now else time.monotonic()
            with self._lock:
                self._next_poll_at = completed_at + self.poll_interval

    def _begin_session(self, identity: Hashable | None) -> None:
        self._session_identity = identity
        self._rest_open = False
        self._players = None
        self._metrics = None
        self._game_data = None
        self._info_error = None
        self._players_error = None
        self._metrics_error = None
        self._game_data_error = None
        self._info_attempted = False
        self._next_poll_at = 0.0

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except (FileNotFoundError, OSError, ValueError):
                pass
            snapshot = self.snapshot()
            if not snapshot.session_active:
                return
            if not snapshot.rest_open:
                delay = 1.0
            else:
                with self._lock:
                    next_poll_at = self._next_poll_at
                delay = max(0.1, next_poll_at - time.monotonic())
            self._stop_event.wait(delay)


_CACHES: dict[Path, PalRestCache] = {}
_CACHES_LOCK = threading.Lock()


def get_pal_rest_cache(name: str) -> PalRestCache:
    key = profile_path(name).resolve()
    with _CACHES_LOCK:
        cache = _CACHES.get(key)
        if cache is None:
            cache = PalRestCache(name)
            _CACHES[key] = cache
    return cache
