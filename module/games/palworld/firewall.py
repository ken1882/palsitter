from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from module.firewall import (
    ExecutableFirewallStatus,
    FirewallError,
    FirewallPermissionDenied,
    FirewallRepairUnavailable,
    FirewallService as UniversalFirewallService,
)
from module.games.palworld.config import PalworldProfile, windows_console_executable_path


RULE_PREFIX = "Palsitter-Palworld-"


@dataclass(frozen=True)
class FirewallStatus:
    supported: bool
    executable_path: str
    udp_port: int
    executable_allowed: bool = False
    port_allowed: bool = False
    executable_blocked: bool = False
    port_blocked: bool = False
    owned_block_rule_names: tuple[str, ...] = ()
    external_block_rule_names: tuple[str, ...] = ()
    error: str | None = None
    executable_supported: bool = True
    external_block_rule_specs: tuple[tuple[str, str], ...] = ()
    linux_block_rule_specs: tuple[tuple[str, str], ...] = ()

    @property
    def allowed(self) -> bool:
        return self.supported and not self.blocked and (
            self.executable_allowed or self.port_allowed
        )

    @property
    def blocked(self) -> bool:
        return self.executable_blocked or self.port_blocked

    @property
    def repairable(self) -> bool:
        return self.supported and not self.error and not self.allowed


def _rule_suffix(name: str) -> str:
    return hashlib.sha256(str(name).casefold().encode("utf-8")).hexdigest()[:16]


def program_rule_name(name: str) -> str:
    return f"{RULE_PREFIX}{_rule_suffix(name)}-Program"


def port_rule_name(name: str, port: int) -> str:
    return f"{RULE_PREFIX}{_rule_suffix(name)}-UDP-{int(port)}"


def resolve_executable(profile: PalworldProfile) -> Path:
    executable = Path(str(profile.executable))
    if not executable.is_absolute() and executable.parent == Path("."):
        executable = Path(str(profile.workdir)) / executable
    return executable.resolve(strict=False)


def firewall_executable_paths(profile: PalworldProfile) -> tuple[str, ...]:
    executable = resolve_executable(profile)
    paths = [str(executable)]
    console_executable = windows_console_executable_path(executable, profile.workdir)
    if console_executable is not None:
        paths.append(str(console_executable.resolve(strict=False)))
    return tuple(paths)


def firewall_effective_executable_path(profile: PalworldProfile) -> str:
    paths = firewall_executable_paths(profile)
    if len(paths) > 1 and Path(paths[-1]).is_file():
        return paths[-1]
    return paths[0]


class FirewallService(UniversalFirewallService):
    def check(
        self,
        profile: PalworldProfile,
        root_password: str | None = None,
    ) -> FirewallStatus:
        executable = str(resolve_executable(profile))
        port = int(profile.game_port)
        port_status = self.check_port(
            port,
            protocol="udp",
            root_password=root_password,
        )
        executable_statuses: tuple[ExecutableFirewallStatus, ...] = ()
        if self.backend == "windows" or self.backend == "test":
            executable_statuses = (
                self.check_executable(
                    firewall_effective_executable_path(profile)
                ),
            )
        error = port_status.error or next(
            (
                status.error
                for status in executable_statuses
                if status.error
            ),
            None,
        )
        block_names = tuple(
            sorted(
                {
                    *port_status.owned_block_rule_names,
                    *port_status.external_block_rule_names,
                    *(
                        name
                        for status in executable_statuses
                        for name in status.block_rule_names
                    ),
                }
            )
        )
        owned_names = {
            program_rule_name(profile.name).casefold(),
            port_rule_name(profile.name, port).casefold(),
            f"Palsitter-UDP-{port}".casefold(),
        }
        owned_blocks = tuple(
            name for name in block_names if name.casefold() in owned_names
        )
        external_blocks = tuple(
            name for name in block_names if name.casefold() not in owned_names
        )
        return FirewallStatus(
            supported=port_status.supported and not error,
            executable_path=executable,
            udp_port=port,
            executable_allowed=any(
                status.allowed for status in executable_statuses
            ),
            port_allowed=port_status.allowed,
            executable_blocked=any(
                status.blocked for status in executable_statuses
            ),
            port_blocked=port_status.blocked,
            owned_block_rule_names=owned_blocks,
            external_block_rule_names=external_blocks,
            error=error,
            executable_supported=bool(executable_statuses),
            external_block_rule_specs=port_status.external_block_rule_specs,
            linux_block_rule_specs=port_status.external_block_rule_specs,
        )

    def fix(
        self,
        profile: PalworldProfile,
        status: FirewallStatus,
        root_password: str | None = None,
    ) -> None:
        if status.error:
            raise FirewallError(status.error)
        if not status.supported:
            raise FirewallError(f"{self._firewall_name} is unavailable")
        if status.allowed:
            return
        self.ensure_port(
            int(profile.game_port),
            "udp",
            executable=firewall_effective_executable_path(profile),
            root_password=root_password,
        )


__all__ = [
    "FirewallError",
    "FirewallPermissionDenied",
    "FirewallRepairUnavailable",
    "FirewallService",
    "FirewallStatus",
    "firewall_effective_executable_path",
    "firewall_executable_paths",
    "port_rule_name",
    "program_rule_name",
    "resolve_executable",
]
