from __future__ import annotations

import datetime as dt
import json
import os
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping

from module.games.palworld.config import fixed_palserver_dir
from module.instances import profile_dir


_CACHE_LOCK = threading.RLock()
_BANLIST_LOCK = threading.RLock()


class PlayerCache:
    """Persist player rows returned by the REST API for one instance."""

    def __init__(self, name: str) -> None:
        self.path = profile_dir(name) / "players.json"

    def upsert(
        self,
        players: Iterable[Mapping[str, Any]],
        *,
        updated_at: str | None = None,
    ) -> list[dict[str, Any]]:
        timestamp = updated_at or dt.datetime.now(dt.timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        with _CACHE_LOCK:
            data = self._read()
            cached = {
                str(row.get("userId")): dict(row)
                for row in data["players"]
                if isinstance(row, dict) and row.get("userId")
            }
            order = list(cached)
            for player in players:
                if not isinstance(player, Mapping):
                    continue
                userid = str(player.get("userId") or "").strip()
                if not userid:
                    continue
                if userid not in cached:
                    cached[userid] = {}
                    order.append(userid)
                cached[userid].update(dict(player))
                cached[userid]["userId"] = userid
                cached[userid]["updated_at"] = timestamp
            data["players"] = [cached[userid] for userid in order]
            self._write(data)
            return [dict(row) for row in data["players"]]

    def names(self) -> dict[str, str]:
        with _CACHE_LOCK:
            data = self._read()
        return {
            str(row.get("userId")): str(row.get("name") or "-")
            for row in data["players"]
            if isinstance(row, dict) and row.get("userId")
        }

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            raw = {}
        players = raw.get("players", []) if isinstance(raw, dict) else []
        data = {"players": players if isinstance(players, list) else []}
        if isinstance(raw, dict) and "banned_userids" in raw:
            self._write(data)
        return data

    def _write(self, data: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(json.dumps(dict(data), indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
        except Exception:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise


class PalworldBanList:
    """Read the ban list maintained by PalServer, creating it when absent."""

    def __init__(self, name: str) -> None:
        self.path = (
            fixed_palserver_dir(name) / "Pal" / "Saved" / "SaveGames" / "banlist.txt"
        )

    def ids(self) -> list[str]:
        with _BANLIST_LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch(exist_ok=True)
            values = []
            seen = set()
            for line in self.path.read_text(encoding="utf-8-sig").splitlines():
                userid = line.strip()
                if userid and userid not in seen:
                    seen.add(userid)
                    values.append(userid)
            return values
