from __future__ import annotations

import os
import re
import secrets
import string
import math
from dataclasses import dataclass, field, fields as dataclass_fields
from pathlib import Path
from typing import Any, Mapping

import psutil

from module.instances import (
    InstanceRecord,
    load_profile_template,
    list_instances,
    load_instance,
    profile_dated_log_path,
    profile_dir,
    rename_instance,
    safe_profile_name,
    save_instance,
)


PALWORLD_SERVER_APP_ID = "2394010"
PALWORLD_CONFIG_VERSION = 6
DEDICATED_SERVER_NAME_RE = re.compile(r"^[0-9A-Z]{32}$")
ADMIN_PASSWORD_RE = re.compile(r"^[a-z0-9]{8}$")
WINDOWS = os.name == "nt"

_LAUNCH_SWITCHES = {
    "-useperfthreads": "launch_useperfthreads",
    "-noasyncloadingthread": "launch_no_async_loading_thread",
    "-usemultithreadfords": "launch_use_multithread_for_ds",
    "-publiclobby": "launch_public_lobby",
    "-logformat": "launch_log_format",
    "-enable-gamedata-api": "launch_enable_gamedata_api",
}
_WORKER_THREADS_PREFIX = "-numberofworkerthreadsserver="
_QUERY_PORT_PREFIX = "-queryport="
ENGINE_CONFIG_SECTION = "[/Script/OnlineSubsystemUtils.IpNetDriver]"


def _split_launch_args(args: list[str]) -> tuple[dict[str, Any], list[str]]:
    """Split the legacy argument list without losing unknown argument ordering."""
    values: dict[str, Any] = {field_name: False for field_name in _LAUNCH_SWITCHES.values()}
    values["launch_worker_threads_server"] = None
    values["query_port"] = 27015
    extra: list[str] = []
    for raw in args:
        argument = str(raw).strip()
        if not argument:
            continue
        folded = argument.casefold()
        field_name = _LAUNCH_SWITCHES.get(folded)
        if field_name is not None:
            values[field_name] = True
            continue
        if folded.startswith(_WORKER_THREADS_PREFIX):
            value = argument.split("=", 1)[1]
            try:
                worker_threads = int(value)
            except ValueError:
                extra.append(argument)
                continue
            if worker_threads > 0:
                values["launch_worker_threads_server"] = worker_threads
                continue
        if folded.startswith(_QUERY_PORT_PREFIX):
            try:
                query_port = int(argument.split("=", 1)[1])
            except ValueError:
                extra.append(argument)
                continue
            if 0 <= query_port <= 65535:
                values["query_port"] = query_port
                continue
        extra.append(argument)
    return values, extra


def _is_structured_launch_argument(argument: str) -> bool:
    folded = str(argument).strip().casefold()
    return (
        folded in _LAUNCH_SWITCHES
        or folded.startswith(_WORKER_THREADS_PREFIX)
        or folded.startswith(_QUERY_PORT_PREFIX)
    )


def legacy_memory_restart_mb(percent: float, total_physical_bytes: int) -> int:
    if float(percent) <= 0 or int(total_physical_bytes) <= 0:
        return 0
    return math.ceil((int(total_physical_bytes) / (1024 * 1024)) * float(percent) / 100)


def generate_dedicated_server_name() -> str:
    alphabet = string.digits + string.ascii_uppercase
    return "".join(secrets.choice(alphabet) for _ in range(32))


def generate_admin_password() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def executable_workdir(executable: str) -> str | None:
    raw = str(executable or "").strip()
    if not raw:
        return None
    path = Path(raw)
    parent = path.parent
    if str(parent) in ("", ".") and "\\" in raw:
        parent_text = raw.rsplit("\\", 1)[0]
        return parent_text or None
    if str(parent) in ("", "."):
        return None
    return str(parent)


def windows_console_executable_path(executable: str | Path, workdir: str | Path) -> Path | None:
    path = Path(executable)
    if not path.is_absolute() and path.parent == Path("."):
        path = Path(workdir) / path
    if path.name.casefold() != "palserver.exe":
        return None
    return path.parent / "Pal" / "Binaries" / "Win64" / "PalServer-Win64-Shipping-Cmd.exe"


def fixed_steamcmd_path(name: str) -> Path:
    executable = "steamcmd.exe" if WINDOWS else "steamcmd"
    return profile_dir(name) / "steamcmd" / executable


def fixed_palserver_dir(name: str) -> Path:
    return profile_dir(name) / "steamcmd" / "steamapps" / "common" / "PalServer"


def server_executable_relative_path() -> Path:
    if WINDOWS:
        return Path("PalServer.exe")
    return Path("Pal") / "Binaries" / "Linux" / "PalServer-Linux-Shipping"


def fixed_executable_path(name: str) -> Path:
    return fixed_palserver_dir(name) / server_executable_relative_path()


def fixed_server_launcher_path(name: str) -> Path:
    return fixed_palserver_dir(name) / "PalServer.sh"


def profile_server_output_path(name: str, when=None) -> Path:
    return profile_dated_log_path(name, "palserver", when)


def fixed_backup_source(name: str) -> Path:
    return fixed_palserver_dir(name) / "Pal" / "Saved" / "SaveGames" / "0"


def fixed_backup_dir(name: str) -> Path:
    path = profile_dir(name) / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def server_config_dir_name() -> str:
    return "WindowsServer" if WINDOWS else "LinuxServer"


def game_user_settings_path(name: str) -> Path:
    return (
        fixed_palserver_dir(name)
        / "Pal"
        / "Saved"
        / "Config"
        / server_config_dir_name()
        / "GameUserSettings.ini"
    )


def engine_settings_path(name: str) -> Path:
    return (
        fixed_palserver_dir(name)
        / "Pal"
        / "Saved"
        / "Config"
        / server_config_dir_name()
        / "Engine.ini"
    )


def _read_ini_section(path: Path, section: str) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        lines = handle.read().splitlines()
    section_index = next((i for i, line in enumerate(lines) if line.strip() == section), None)
    if section_index is None:
        return {}
    end = next(
        (i for i in range(section_index + 1, len(lines)) if lines[i].strip().startswith("[")),
        len(lines),
    )
    values: dict[str, str] = {}
    for line in lines[section_index + 1 : end]:
        if "=" not in line or line.lstrip().startswith((";", "#")):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _update_ini_section(path: Path, section: str, values: Mapping[str, str]) -> None:
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as handle:
            text = handle.read()
        newline = "\r\n" if "\r\n" in text else "\n"
        lines = text.splitlines()
        had_trailing_newline = text.endswith(("\n", "\r"))
    else:
        newline = "\r\n" if WINDOWS else "\n"
        lines = []
        had_trailing_newline = True
    section_index = next((i for i, line in enumerate(lines) if line.strip() == section), None)
    if section_index is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(section)
        lines.extend(f"{key}={value}" for key, value in values.items())
    else:
        end = next(
            (i for i in range(section_index + 1, len(lines)) if lines[i].strip().startswith("[")),
            len(lines),
        )
        for key, value in values.items():
            prefix = f"{key}="
            setting_index = next(
                (i for i in range(section_index + 1, end) if lines[i].strip().startswith(prefix)),
                None,
            )
            if setting_index is None:
                lines.insert(end, f"{key}={value}")
                end += 1
            else:
                lines[setting_index] = f"{key}={value}"
    output = newline.join(lines)
    if had_trailing_newline or lines:
        output += newline
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(output)


def _engine_profile_values(profile: "PalworldProfile") -> dict[str, str]:
    return {
        "NetServerMaxTickRate": str(int(profile.net_server_max_tick_rate)),
        "ConnectionTimeout": str(float(profile.connection_timeout)),
        "InitialConnectTimeout": str(float(profile.initial_connect_timeout)),
    }


def sync_engine_settings(profile: "PalworldProfile") -> None:
    _update_ini_section(engine_settings_path(profile.name), ENGINE_CONFIG_SECTION, _engine_profile_values(profile))


def sync_game_user_settings(profile: "PalworldProfile") -> None:
    path = game_user_settings_path(profile.name)
    section = "[/Script/Pal.PalGameLocalSettings]"
    setting = f"DedicatedServerName={profile.dedicated_server_name}"
    newline = "\r\n"
    lines: list[str] = []
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if "\r\n" not in text and "\n" in text:
            newline = "\n"
        lines = text.splitlines()
    section_index = next((i for i, line in enumerate(lines) if line.strip() == section), None)
    if section_index is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([section, setting])
    else:
        end = next((i for i in range(section_index + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
        setting_index = next((i for i in range(section_index + 1, end) if lines[i].strip().startswith("DedicatedServerName=")), None)
        if setting_index is None:
            lines.insert(end, setting)
        else:
            lines[setting_index] = setting
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(newline.join(lines) + newline, encoding="utf-8", newline="")


@dataclass
class PalworldProfile:
    name: str
    config_version: int = PALWORLD_CONFIG_VERSION
    server_name: str = "Palworld Server"
    workdir: str = "./tmp"
    executable: str = "PalServer.exe"
    # Kept as an in-memory compatibility view for callers that have not moved to
    # the structured launch fields. Version 2 profiles do not persist this key.
    executable_args: list[str] = field(default_factory=list)
    launch_useperfthreads: bool = False
    launch_no_async_loading_thread: bool = False
    launch_use_multithread_for_ds: bool = False
    launch_worker_threads_server: int | None = None
    launch_public_lobby: bool = False
    launch_log_format: bool = False
    launch_enable_gamedata_api: bool = True
    extra_args: list[str] = field(default_factory=list)
    game_port: int = 8211
    query_port: int = 27015
    net_server_max_tick_rate: int = 30
    connection_timeout: float = 30.0
    initial_connect_timeout: float = 60.0
    suppress_rest_access_logs: bool = True
    steamcmd: str = "steamcmd"
    update_on_start: bool = True
    auto_update: bool = True
    auto_update_idle_minutes: int = 30
    steam_validate: bool = False
    rest_host: str = "localhost"
    rest_port: int = 8212
    rest_username: str = "admin"
    rest_password: str = ""
    backup_source: str = "./tmp/Pal/Saved/SaveGames/0"
    backup_dir: str = "./backups"
    backup_interval_minutes: float = 60
    backup_retention_count: int = 20
    skip_backup_when_no_players: bool = True
    memory_restart_mb: int = 0
    # Version 1 compatibility only. The manager converts this percentage to an
    # absolute process-tree RSS threshold using total physical memory.
    memory_restart_percent: float = 0.0
    memory_restart_countdown_minutes: int = 10
    shutdown_wait_seconds: int = 30
    restart_on_crash: bool = True
    self_heal_enabled: bool = True
    self_heal_trigger_frame_minutes: int = 30
    self_heal_trigger_crash_times: int = 2
    crash_restart_limit_per_hour: int = 5
    planned_restart_mode: str = "off"
    planned_restart_interval_hours: float = 24.0
    planned_restart_daily_time: str = "04:00"
    planned_restart_countdown_minutes: int = 10
    dedicated_server_name: str = field(default_factory=generate_dedicated_server_name)
    world_settings: dict[str, Any] = field(default_factory=dict)
    _derived_executable_args: list[str] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        # Direct constructors using the old API still work. Structured fields
        # take precedence when both representations are supplied.
        if self.executable_args and not self._has_structured_launch_values():
            self._apply_legacy_launch_args(self.executable_args)
        self._refresh_executable_args()

    def _has_structured_launch_values(self) -> bool:
        return any(
            (
                self.launch_useperfthreads,
                self.launch_no_async_loading_thread,
                self.launch_use_multithread_for_ds,
                self.launch_worker_threads_server is not None,
                self.launch_public_lobby,
                self.launch_log_format,
                bool(self.extra_args),
            )
        )

    def _apply_legacy_launch_args(self, args: list[str]) -> None:
        parsed, extra = _split_launch_args(list(args or []))
        if not any(
            str(argument).strip().casefold() == "-enable-gamedata-api"
            for argument in args
        ):
            parsed["launch_enable_gamedata_api"] = self.launch_enable_gamedata_api
        for field_name, value in parsed.items():
            setattr(self, field_name, value)
        self.extra_args = extra

    def _refresh_executable_args(self) -> None:
        effective = self.build_executable_args()
        self.executable_args = effective
        self._derived_executable_args = list(effective)

    def _sync_compatibility_launch_args(self) -> None:
        if list(self.executable_args or []) != self._derived_executable_args:
            self._apply_legacy_launch_args(list(self.executable_args or []))
        self._refresh_executable_args()

    def build_executable_args(self) -> list[str]:
        duplicate = next(
            (argument for argument in self.extra_args if _is_structured_launch_argument(argument)),
            None,
        )
        if duplicate is not None:
            raise ValueError(
                f"Advanced arguments cannot duplicate structured launch option: {duplicate}"
            )
        args: list[str] = []
        if self.launch_useperfthreads:
            args.append("-useperfthreads")
        if self.launch_no_async_loading_thread:
            args.append("-NoAsyncLoadingThread")
        if self.launch_use_multithread_for_ds:
            args.append("-UseMultithreadForDS")
        if self.launch_worker_threads_server is not None:
            worker_threads = int(self.launch_worker_threads_server)
            if worker_threads <= 0:
                raise ValueError("NumberOfWorkerThreadsServer must be greater than zero.")
            args.append(f"-NumberOfWorkerThreadsServer={worker_threads}")
        if self.launch_public_lobby:
            args.append("-publiclobby")
        if self.launch_log_format:
            args.append("-logformat")
        if self.launch_enable_gamedata_api:
            args.append("-enable-gamedata-api")
        args.extend(str(argument).strip() for argument in self.extra_args if str(argument).strip())
        return args

    def validate_restart_schedule(self) -> None:
        mode = str(self.planned_restart_mode or "off").casefold()
        if mode not in {"off", "interval", "daily"}:
            raise ValueError("Planned restart mode must be off, interval, or daily.")
        self.planned_restart_mode = mode
        if mode == "interval" and float(self.planned_restart_interval_hours) <= 0:
            raise ValueError("Planned restart interval must be greater than zero.")
        if mode == "daily" and not re.fullmatch(
            r"(?:[01]\d|2[0-3]):[0-5]\d", str(self.planned_restart_daily_time)
        ):
            raise ValueError("Planned restart daily time must use 24-hour HH:MM.")
        if int(self.planned_restart_countdown_minutes) < 0:
            raise ValueError("Planned restart countdown cannot be negative.")
        if int(self.memory_restart_mb) < 0:
            raise ValueError("Memory restart threshold cannot be negative.")
        if int(self.crash_restart_limit_per_hour) < 1:
            raise ValueError("Crash restart limit must be at least one.")
        if int(self.self_heal_trigger_frame_minutes) < 1:
            raise ValueError("Self-heal trigger frame must be at least one minute.")
        if int(self.self_heal_trigger_crash_times) < 1:
            raise ValueError("Self-heal trigger crash times must be at least one.")
        self.validate_update_settings()

    def validate_update_settings(self) -> None:
        try:
            idle_minutes = int(self.auto_update_idle_minutes)
        except (TypeError, ValueError) as exc:
            raise ValueError("Idle shutdown for update must be a positive integer.") from exc
        if idle_minutes < 1:
            raise ValueError("Idle shutdown for update must be at least one minute.")
        self.auto_update_idle_minutes = idle_minutes

    def validate_engine_settings(self) -> None:
        try:
            query_port = int(self.query_port)
        except (TypeError, ValueError) as exc:
            raise ValueError("Query port must be an integer from 0 to 65535.") from exc
        if not 0 <= query_port <= 65535:
            raise ValueError("Query port must be an integer from 0 to 65535.")
        self.query_port = query_port
        try:
            tick_rate = int(self.net_server_max_tick_rate)
        except (TypeError, ValueError) as exc:
            raise ValueError("Net server max tick rate must be an integer from 1 to 120.") from exc
        if not 1 <= tick_rate <= 120:
            raise ValueError("Net server max tick rate must be an integer from 1 to 120.")
        self.net_server_max_tick_rate = tick_rate
        for field_name, value in (
            ("Connection timeout", self.connection_timeout),
            ("Initial connect timeout", self.initial_connect_timeout),
        ):
            try:
                numeric = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field_name} must be greater than zero.") from exc
            if not math.isfinite(numeric) or numeric <= 0:
                raise ValueError(f"{field_name} must be greater than zero.")
            setattr(self, field_name.replace(" ", "_").lower(), numeric)

    @classmethod
    def from_game_config(cls, name: str, data: Mapping[str, Any]) -> "PalworldProfile":
        values = dict(data)
        if "backup_interval_minutes" not in values and "backup_interval_seconds" in values:
            values["backup_interval_minutes"] = float(values["backup_interval_seconds"]) / 60
        structured_launch_keys = {
            "launch_useperfthreads",
            "launch_no_async_loading_thread",
            "launch_use_multithread_for_ds",
            "launch_worker_threads_server",
            "launch_public_lobby",
            "launch_log_format",
            "launch_enable_gamedata_api",
            "extra_args",
        }
        if not structured_launch_keys.intersection(values) and "executable_args" in values:
            parsed, extra = _split_launch_args(list(values.get("executable_args") or []))
            if not any(
                str(argument).strip().casefold() == "-enable-gamedata-api"
                for argument in values.get("executable_args") or []
            ):
                parsed["launch_enable_gamedata_api"] = True
            values.update(parsed)
            values["extra_args"] = extra
        values["config_version"] = PALWORLD_CONFIG_VERSION
        defaults = cls(name=name).to_game_config()
        defaults.update(values)
        allowed = {item.name for item in dataclass_fields(cls) if item.init} - {"name"}
        profile = cls(name=name, **{key: value for key, value in defaults.items() if key in allowed})
        profile.world_settings = dict(profile.world_settings or {})
        if (
            "launch_enable_gamedata_api" not in values
            and "EnableGameDataAPI" in profile.world_settings
        ):
            profile.launch_enable_gamedata_api = bool(
                profile.world_settings["EnableGameDataAPI"]
            )
        profile._sync_world_network_settings()
        profile.extra_args = list(profile.extra_args or [])
        if profile.launch_worker_threads_server is not None:
            profile.launch_worker_threads_server = int(profile.launch_worker_threads_server)
        profile.query_port = int(profile.query_port)
        profile.net_server_max_tick_rate = int(profile.net_server_max_tick_rate)
        profile.connection_timeout = float(profile.connection_timeout)
        profile.initial_connect_timeout = float(profile.initial_connect_timeout)
        profile.auto_update_idle_minutes = int(profile.auto_update_idle_minutes)
        profile.memory_restart_mb = int(profile.memory_restart_mb or 0)
        profile.crash_restart_limit_per_hour = int(profile.crash_restart_limit_per_hour)
        profile.self_heal_trigger_frame_minutes = int(
            profile.self_heal_trigger_frame_minutes
        )
        profile.self_heal_trigger_crash_times = int(
            profile.self_heal_trigger_crash_times
        )
        profile._refresh_executable_args()
        profile.validate_restart_schedule()
        profile.apply_fixed_paths("backup_dir" in values and values.get("backup_dir") not in (None, "", "./backups"))
        return profile

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PalworldProfile":
        values = dict(data)
        return cls.from_game_config(str(values.pop("name", "default")), values)

    def apply_fixed_paths(self, keep_backup_dir: bool = True) -> None:
        self.server_name = self.name
        self.executable = str(fixed_executable_path(self.name))
        self.steamcmd = str(fixed_steamcmd_path(self.name))
        self.backup_source = str(fixed_backup_source(self.name))
        if not keep_backup_dir:
            self.backup_dir = str(fixed_backup_dir(self.name))
        self.workdir = str(fixed_palserver_dir(self.name))

    def _sync_world_network_settings(self) -> None:
        if "PublicPort" in self.world_settings:
            self.game_port = int(self.world_settings["PublicPort"])
        if "RESTAPIPort" in self.world_settings:
            self.rest_host = "localhost"
            self.rest_port = int(self.world_settings["RESTAPIPort"])
            self.rest_username = "admin"
        if "AdminPassword" in self.world_settings:
            self.rest_password = str(self.world_settings["AdminPassword"])

    def to_game_config(self) -> dict[str, Any]:
        self._sync_world_network_settings()
        self._sync_compatibility_launch_args()
        self.validate_restart_schedule()
        self.validate_engine_settings()
        result = {
            "config_version": PALWORLD_CONFIG_VERSION,
            "launch_useperfthreads": self.launch_useperfthreads,
            "launch_no_async_loading_thread": self.launch_no_async_loading_thread,
            "launch_use_multithread_for_ds": self.launch_use_multithread_for_ds,
            "launch_worker_threads_server": self.launch_worker_threads_server,
            "launch_public_lobby": self.launch_public_lobby,
            "launch_log_format": self.launch_log_format,
            "launch_enable_gamedata_api": self.launch_enable_gamedata_api,
            "extra_args": list(self.extra_args),
            "game_port": self.game_port,
            "query_port": self.query_port,
            "net_server_max_tick_rate": self.net_server_max_tick_rate,
            "connection_timeout": self.connection_timeout,
            "initial_connect_timeout": self.initial_connect_timeout,
            "suppress_rest_access_logs": self.suppress_rest_access_logs,
            "update_on_start": self.update_on_start,
            "auto_update": self.auto_update,
            "auto_update_idle_minutes": self.auto_update_idle_minutes,
            "steam_validate": self.steam_validate,
            "rest_host": self.rest_host,
            "rest_port": self.rest_port,
            "rest_username": self.rest_username,
            "rest_password": self.rest_password,
            "backup_dir": self.backup_dir,
            "backup_interval_minutes": self.backup_interval_minutes,
            "backup_retention_count": self.backup_retention_count,
            "skip_backup_when_no_players": self.skip_backup_when_no_players,
            "memory_restart_mb": self.memory_restart_mb,
            "memory_restart_countdown_minutes": self.memory_restart_countdown_minutes,
            "shutdown_wait_seconds": self.shutdown_wait_seconds,
            "restart_on_crash": self.restart_on_crash,
            "self_heal_enabled": self.self_heal_enabled,
            "self_heal_trigger_frame_minutes": self.self_heal_trigger_frame_minutes,
            "self_heal_trigger_crash_times": self.self_heal_trigger_crash_times,
            "crash_restart_limit_per_hour": self.crash_restart_limit_per_hour,
            "planned_restart_mode": self.planned_restart_mode,
            "planned_restart_interval_hours": self.planned_restart_interval_hours,
            "planned_restart_daily_time": self.planned_restart_daily_time,
            "planned_restart_countdown_minutes": self.planned_restart_countdown_minutes,
            "dedicated_server_name": self.dedicated_server_name,
            "world_settings": dict(self.world_settings or {}),
        }
        if float(self.memory_restart_percent or 0) > 0 and int(self.memory_restart_mb) == 0:
            result["memory_restart_percent"] = self.memory_restart_percent
        return result

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, **self.to_game_config()}


def _allocate_ports() -> dict[str, int]:
    fields = ("game_port", "query_port", "rest_port")
    used = {key: set() for key in fields}
    for record in list_instances():
        if record.game != "palworld":
            continue
        profile = PalworldProfile.from_game_config(record.name, record.game_config)
        for key in fields:
            used[key].add(getattr(profile, key))
    allocated: dict[str, int] = {}
    defaults = PalworldProfile(name="_")
    for key in fields:
        port = getattr(defaults, key)
        while port in used[key]:
            port += 1
        allocated[key] = port
    return allocated


def _world_defaults() -> dict[str, Any]:
    from module.games.palworld.worldsettings.schema import WORLD_OPTION_FIELDS

    return {item.key: item.default for item in WORLD_OPTION_FIELDS}


def new_profile(name: str, template: Mapping[str, Any] | None = None) -> PalworldProfile:
    profile = PalworldProfile.from_game_config(
        name,
        template if template is not None else load_profile_template("palworld"),
    )
    allocated = _allocate_ports()
    if profile.launch_worker_threads_server is None:
        profile.launch_worker_threads_server = max(1, (os.cpu_count() or 2) - 1)
    profile.game_port = allocated["game_port"]
    profile.query_port = allocated["query_port"]
    profile.rest_port = allocated["rest_port"]
    profile.rest_password = generate_admin_password()
    if profile.backup_dir in ("", "./backups"):
        profile.backup_dir = str(fixed_backup_dir(name))
    world_settings = _world_defaults()
    world_settings.update(profile.world_settings)
    profile.world_settings = world_settings
    profile.world_settings.update({
        "PublicPort": profile.game_port,
        "RESTAPIPort": profile.rest_port,
        "AdminPassword": profile.rest_password,
    })
    profile.apply_fixed_paths(keep_backup_dir=True)
    return profile


def clone_profile_config(source_name: str, data: Mapping[str, Any], new_name: str) -> PalworldProfile:
    profile = PalworldProfile.from_game_config(source_name, data)
    old_default_backup = str(fixed_backup_dir(source_name))
    if profile.backup_dir in ("", "./backups", old_default_backup):
        profile.backup_dir = str(fixed_backup_dir(new_name))
    profile.name = new_name
    profile.dedicated_server_name = generate_dedicated_server_name()
    allocated = _allocate_ports()
    profile.game_port = allocated["game_port"]
    profile.query_port = allocated["query_port"]
    profile.rest_port = allocated["rest_port"]
    profile.rest_password = generate_admin_password()
    profile.world_settings = dict(profile.world_settings)
    profile.world_settings.update({
        "PublicPort": profile.game_port,
        "RESTAPIEnabled": True,
        "RESTAPIPort": profile.rest_port,
        "AdminPassword": profile.rest_password,
    })
    profile.apply_fixed_paths(keep_backup_dir=True)
    return profile


def load_profile(name: str) -> PalworldProfile:
    record = load_instance(name)
    if record.game != "palworld":
        raise ValueError(f"Profile {name!r} is not a Palworld instance")
    profile = PalworldProfile.from_game_config(record.name, record.game_config)
    needs_save = (
        int(record.game_config.get("config_version") or 1) < PALWORLD_CONFIG_VERSION
        or "executable_args" in record.game_config
        or "memory_restart_percent" in record.game_config
    )
    engine_values = _read_ini_section(engine_settings_path(name), ENGINE_CONFIG_SECTION)
    for field_name, ini_name, caster in (
        ("net_server_max_tick_rate", "NetServerMaxTickRate", int),
        ("connection_timeout", "ConnectionTimeout", float),
        ("initial_connect_timeout", "InitialConnectTimeout", float),
    ):
        if field_name in record.game_config:
            continue
        try:
            value = caster(engine_values[ini_name])
            if (field_name == "net_server_max_tick_rate" and not 1 <= value <= 120) or (
                field_name != "net_server_max_tick_rate" and value <= 0
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            continue
        setattr(profile, field_name, value)
        needs_save = True
    if any(field_name not in record.game_config for field_name in (
        "net_server_max_tick_rate", "connection_timeout", "initial_connect_timeout"
    )):
        needs_save = True
    if (
        "memory_restart_mb" not in record.game_config
        and float(record.game_config.get("memory_restart_percent") or 0) > 0
    ):
        profile.memory_restart_mb = legacy_memory_restart_mb(
            float(record.game_config["memory_restart_percent"]),
            int(psutil.virtual_memory().total),
        )
        profile.memory_restart_percent = 0.0
        needs_save = True
    elif "memory_restart_mb" in record.game_config:
        profile.memory_restart_percent = 0.0
    if not DEDICATED_SERVER_NAME_RE.fullmatch(profile.dedicated_server_name):
        profile.dedicated_server_name = generate_dedicated_server_name()
        needs_save = True
    if needs_save:
        save_profile(profile)
    return profile


def save_profile(profile: PalworldProfile) -> None:
    if not DEDICATED_SERVER_NAME_RE.fullmatch(profile.dedicated_server_name):
        raise ValueError("Dedicated server name must be 32 uppercase letters or digits.")
    profile.apply_fixed_paths(keep_backup_dir=profile.backup_dir not in ("", "./backups"))
    save_instance(InstanceRecord(profile.name, "palworld", profile.to_game_config()))
    sync_game_user_settings(profile)
    sync_engine_settings(profile)


def rename_profile(name: str, new_name: str) -> PalworldProfile:
    profile = load_profile(name)
    replacement = safe_profile_name(new_name)
    old_default_backup = str(profile_dir(profile.name) / "backups")
    if profile.backup_dir == old_default_backup:
        profile.backup_dir = str(profile_dir(replacement) / "backups")
    profile.name = replacement
    profile.apply_fixed_paths(keep_backup_dir=True)
    record = rename_instance(
        name,
        replacement,
        game_config=profile.to_game_config(),
    )
    profile.name = record.name
    sync_game_user_settings(profile)
    sync_engine_settings(profile)
    return profile
