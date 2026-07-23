from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

import requests

from module.games.palworld.config import PalworldProfile


class RestError(RuntimeError):
    pass


class PalRestClient:
    def __init__(
        self,
        profile: PalworldProfile,
        session: Optional[requests.Session] = None,
        *,
        timeout: float = 5,
        availability_probe: Callable[[PalworldProfile], bool] | None = None,
    ) -> None:
        self.profile = profile
        self.session = session or requests.Session()
        self.timeout = float(timeout)
        if availability_probe is None:
            from module.games.palworld.server.status import rest_is_available

            availability_probe = rest_is_available
        self.availability_probe = availability_probe
        self._availability_checked_at = 0.0
        self._availability = False

    @property
    def base_url(self) -> str:
        return f"http://{self.profile.rest_host}:{self.profile.rest_port}/v1/api"

    @property
    def auth(self) -> tuple[str, str]:
        return (self.profile.rest_username, self.profile.rest_password)

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        now = time.monotonic()
        if now - self._availability_checked_at >= 1.0:
            self._availability = bool(self.availability_probe(self.profile))
            self._availability_checked_at = now
        if not self._availability:
            raise RestError("Palworld server is not running or REST API is unavailable")
        url = f"{self.base_url}/{path.lstrip('/')}"
        response = self.session.request(
            method.upper(),
            url,
            auth=self.auth,
            timeout=kwargs.pop("timeout", self.timeout),
            **kwargs,
        )
        if response.status_code >= 400:
            raise RestError(f"{method.upper()} /{path}: HTTP {response.status_code}")
        if response.content:
            try:
                return response.json()
            except ValueError:
                return response.text
        return None

    def info(self) -> Dict[str, Any]:
        return self.request("GET", "info") or {}

    def players(self) -> Dict[str, Any]:
        return self.request("GET", "players") or {"players": []}

    def settings(self) -> Dict[str, Any]:
        return self.request("GET", "settings") or {}

    def metrics(self) -> Dict[str, Any]:
        return self.request("GET", "metrics") or {}

    def game_data(self) -> Dict[str, Any]:
        return self.request("GET", "game-data") or {}

    def announce(self, message: str) -> Any:
        return self.request("POST", "announce", json={"message": message})

    def save(self) -> Any:
        return self.request("POST", "save")

    def shutdown(
        self,
        *,
        waittime: int = 5,
        message: str = "Server will shutdown immediately",
    ) -> Any:
        # Palworld documents Save and Shutdown as separate operations and does
        # not promise that Shutdown flushes the world first.
        self.save()
        return self.request(
            "POST",
            "shutdown",
            json={
                "waittime": waittime,
                "message": message,
            },
        )

    def stop(self) -> Any:
        return self.request("POST", "stop")

    def kick(self, userid: str, message: str = "") -> Any:
        return self.request("POST", "kick", json={"userid": userid, "message": message})

    def ban(self, userid: str, message: str = "") -> Any:
        return self.request("POST", "ban", json={"userid": userid, "message": message})

    def unban(self, userid: str) -> Any:
        return self.request("POST", "unban", json={"userid": userid})
