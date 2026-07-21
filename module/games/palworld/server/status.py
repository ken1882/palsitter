from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Callable

import psutil

from module.games.palworld.config import PalworldProfile, windows_console_executable_path
from module.games.palworld.worldsettings.service import load_world_settings


WINDOWS = os.name == "nt"


def _tcp_status(
    host: str,
    port: int,
    create_connection: Callable[..., object],
) -> str:
    try:
        connection = create_connection((host, port), timeout=1)
    except OSError:
        return "closed"
    close = getattr(connection, "close", None)
    if close is not None:
        close()
    return "open"


def endpoint_status(
    profile: PalworldProfile,
    *,
    process_running: bool | None = None,
    udp_connections: Callable[..., list] = psutil.net_connections,
    create_connection: Callable[..., object] = socket.create_connection,
    settings_loader: Callable[..., object] = load_world_settings,
) -> dict[str, str]:
    udp = "closed"
    if process_running is not False:
        try:
            for connection in udp_connections(kind="udp"):
                address = getattr(connection, "laddr", ())
                port = getattr(address, "port", None)
                if port is None and len(address) > 1:
                    port = address[1]
                if port == profile.game_port:
                    udp = "open"
                    break
        except (OSError, psutil.Error):
            pass

    rest = (
        _tcp_status(profile.rest_host, profile.rest_port, create_connection)
        if process_running is not False
        else "closed"
    )
    try:
        settings = settings_loader(profile).values
        rcon_enabled = bool(settings.get("RCONEnabled", settings.get("rcon_enabled", False)))
        rcon_port = int(settings.get("RCONPort", settings.get("rcon_port", 25575)))
    except (OSError, ValueError, TypeError):
        rcon_enabled = False
        rcon_port = 25575
    rcon = (
        _tcp_status("127.0.0.1", rcon_port, create_connection)
        if rcon_enabled and process_running is not False
        else "closed"
        if rcon_enabled
        else "disabled"
    )
    return {"udp": udp, "rest": rest, "rcon": rcon}


def _expected_executables(profile: PalworldProfile) -> set[str]:
    executable = Path(profile.executable)
    if not executable.is_absolute() and executable.parent == Path("."):
        executable = Path(profile.workdir) / executable
    expected = {os.path.normcase(os.path.abspath(executable))}
    console_executable = (
        windows_console_executable_path(executable, profile.workdir) if WINDOWS else None
    )
    if console_executable is not None and console_executable.is_file():
        expected.add(os.path.normcase(os.path.abspath(console_executable)))
    return expected


def matching_instance_processes(
    profile: PalworldProfile,
    *,
    process_iter: Callable[..., object] = psutil.process_iter,
) -> list[object]:
    """Return processes matching this instance's exact configured executable."""
    expected = _expected_executables(profile)
    expected_names = {Path(path).name.casefold() for path in expected}
    matches = []
    try:
        processes = process_iter(["name", "exe"])
        for process in processes:
            try:
                info = getattr(process, "info", {}) or {}
                name = str(info.get("name") or process.name())
                if name.casefold() not in expected_names:
                    continue
                executable = info.get("exe") or process.exe()
                if executable and os.path.normcase(os.path.abspath(executable)) in expected:
                    matches.append(process)
            except (OSError, psutil.Error):
                continue
    except (OSError, psutil.Error):
        return []
    return matches


def instance_is_running(
    profile: PalworldProfile,
    *,
    process_iter: Callable[..., object] = psutil.process_iter,
) -> bool:
    """Return whether this instance's exact configured executable is running."""
    return bool(matching_instance_processes(profile, process_iter=process_iter))


def rest_is_available(
    profile: PalworldProfile,
    *,
    process_iter: Callable[..., object] = psutil.process_iter,
    create_connection: Callable[..., object] = socket.create_connection,
) -> bool:
    if not instance_is_running(profile, process_iter=process_iter):
        return False
    return _tcp_status(profile.rest_host, profile.rest_port, create_connection) == "open"
