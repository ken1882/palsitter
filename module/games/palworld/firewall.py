from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from module.games.palworld.config import PalworldProfile, windows_console_executable_path


POWERSHELL = "powershell.exe"
NETSH = "netsh.exe"
RULE_PREFIX = "Palsitter-Palworld-"
_COMMAND_TIMEOUT = 15


class FirewallError(RuntimeError):
    pass


class FirewallRepairUnavailable(FirewallError):
    pass


def _command_error(error: BaseException) -> str:
    if isinstance(error, subprocess.TimeoutExpired):
        return "Windows Firewall command timed out"
    return str(error)


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
        return (
            self.supported
            and not self.allowed
            and not self.external_block_rule_names
        )


def _rule_suffix(name: str) -> str:
    digest = hashlib.sha256(str(name).casefold().encode("utf-8")).hexdigest()[:16]
    return digest


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


def _powershell_args(script: str, powershell: str = POWERSHELL) -> list[str]:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded,
    ]


def _quote_powershell(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _fix_script(executable: str, rule_name: str, display_name: str, remove_names: Iterable[str]) -> str:
    removals = ",".join(_quote_powershell(value) for value in remove_names)
    if removals:
        remove_block = (
            f"foreach ($name in @({removals})) {{ "
            "Remove-NetFirewallRule -Name $name -ErrorAction Stop }"
        )
    else:
        remove_block = ""
    return (
        "$ErrorActionPreference = 'Stop'; "
        f"{remove_block} "
        "New-NetFirewallRule "
        f"-Name {_quote_powershell(rule_name)} "
        f"-DisplayName {_quote_powershell(display_name)} "
        "-Direction Inbound -Action Allow -Enabled True -Profile Any "
        f"-Program {_quote_powershell(executable)} | Out-Null"
    )


def _is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _elevated_helper_payload(payload: str) -> int:
    if not _is_admin():
        return 740
    data = json.loads(base64.b64decode(payload).decode("utf-8"))
    script = _fix_script(
        str(data["executable"]),
        str(data["rule_name"]),
        str(data["display_name"]),
        data.get("remove_names", ()),
    )
    try:
        result = subprocess.run(
            _powershell_args(script),
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT,
        )
    except subprocess.SubprocessError as exc:
        print(_command_error(exc), file=sys.stderr)
        return 124
    if result.returncode:
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        return result.returncode
    return 0


def _flatten(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(item for child in value for item in _flatten(child))
    return (str(value),)


def _fold(value: Any) -> str:
    return str(value or "").strip().casefold()


def _matches_program(rule: Mapping[str, Any], executable: str) -> bool:
    target = os.path.normcase(os.path.abspath(executable)).casefold()
    for value in _flatten(rule.get("Program")):
        if _fold(value) == "any":
            continue
        try:
            candidate = os.path.normcase(os.path.abspath(value.strip())).casefold()
        except (TypeError, ValueError):
            continue
        if candidate == target:
            return True
    return False


def _program_is_unrestricted(rule: Mapping[str, Any]) -> bool:
    programs = _flatten(rule.get("Program"))
    return not programs or any(_fold(value) in {"any", "*"} for value in programs)


def _matches_protocol(rule: Mapping[str, Any]) -> bool:
    return any(_fold(value) in {"udp", "17"} for value in _flatten(rule.get("Protocol")))


def _matches_port(rule: Mapping[str, Any], port: int, executables: Iterable[str]) -> bool:
    if not _program_is_unrestricted(rule) and not any(
        _matches_program(rule, executable) for executable in executables
    ):
        return False
    for raw in _flatten(rule.get("LocalPort")):
        for token in raw.split(","):
            token = token.strip().casefold()
            if token in {"any", "*"}:
                return True
            if "-" in token:
                first, _, last = token.partition("-")
                try:
                    if int(first) <= port <= int(last):
                        return True
                except ValueError:
                    continue
            else:
                try:
                    if int(token) == port:
                        return True
                except ValueError:
                    continue
    return False


def _rules_from_output(stdout: str) -> list[Mapping[str, Any]]:
    if not str(stdout or "").lstrip().startswith(("[", "{")):
        return _rules_from_netsh_output(stdout)
    try:
        value = json.loads(stdout or "[]")
    except json.JSONDecodeError as exc:
        raise FirewallError("Windows Firewall returned invalid rule data") from exc
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise FirewallError("Windows Firewall returned an unexpected rule format")
    return value


def _rules_from_netsh_output(stdout: str) -> list[Mapping[str, Any]]:
    rules: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    fields = {
        "rule name": "Name",
        "enabled": "Enabled",
        "direction": "Direction",
        "action": "Action",
        "program": "Program",
        "protocol": "Protocol",
        "localport": "LocalPort",
    }
    for line in str(stdout or "").splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                rules.append(current)
                current = {}
            continue
        key, separator, value = stripped.partition(":")
        if not separator:
            continue
        field = fields.get(key.strip().casefold())
        if field is not None:
            current[field] = value.strip()
    if current:
        rules.append(current)
    return rules


def _rule_is_enabled_allow(rule: Mapping[str, Any]) -> bool:
    return _fold(rule.get("Direction")) in {"in", "inbound"} and _fold(rule.get("Enabled")) in {
        "true",
        "yes",
        "1",
    } and _fold(rule.get("Action")) == "allow"


def _rule_is_enabled_block(rule: Mapping[str, Any]) -> bool:
    return _fold(rule.get("Direction")) in {"in", "inbound"} and _fold(rule.get("Enabled")) in {
        "true",
        "yes",
        "1",
    } and _fold(rule.get("Action")) == "block"


class FirewallService:
    def __init__(
        self,
        *,
        run_command: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        elevated_runner: Callable[[Mapping[str, Any]], subprocess.CompletedProcess] | None = None,
        powershell: str = POWERSHELL,
        supported: bool | None = None,
    ) -> None:
        self.run_command = run_command
        self._test_state_path = os.getenv("PALSITTER_TEST_FIREWALL_STATE")
        self._test_delay = float(os.getenv("PALSITTER_TEST_FIREWALL_DELAY", "0") or 0)
        self.elevated_runner = elevated_runner or (
            self._run_test_elevated if self._test_state_path else self._run_elevated
        )
        self.powershell = powershell
        self.supported = (
            os.name == "nt" or bool(self._test_state_path)
            if supported is None
            else bool(supported)
        )

    def check(self, profile: PalworldProfile) -> FirewallStatus:
        executable = str(resolve_executable(profile))
        executable_paths = firewall_executable_paths(profile)
        port = int(profile.game_port)
        if not self.supported:
            return FirewallStatus(False, executable, port)
        if self._test_state_path:
            if self._test_delay > 0:
                time.sleep(self._test_delay)
            state = Path(self._test_state_path).read_text(encoding="utf-8").strip().casefold()
            return FirewallStatus(
                True,
                executable,
                port,
                executable_allowed=state == "open",
            )
        try:
            result = self.run_command(
                [
                    NETSH,
                    "advfirewall",
                    "firewall",
                    "show",
                    "rule",
                    "name=all",
                    "verbose",
                ],
                capture_output=True,
                text=True,
                timeout=_COMMAND_TIMEOUT,
            )
            if result.returncode:
                detail = (result.stderr or result.stdout or "").strip()
                raise FirewallError(detail or "Windows Firewall query failed")
            rules = _rules_from_output(result.stdout)
        except (OSError, subprocess.SubprocessError, FirewallError) as exc:
            return FirewallStatus(False, executable, port, error=_command_error(exc))

        executable_allowed = False
        port_allowed = False
        executable_blocked = False
        port_blocked = False
        owned_blocks: list[str] = []
        external_blocks: list[str] = []
        owned_names = {
            program_rule_name(profile.name).casefold(),
            port_rule_name(profile.name, port).casefold(),
        }
        for rule in rules:
            program_match = any(
                _matches_program(rule, candidate) for candidate in executable_paths
            )
            port_match = _matches_protocol(rule) and _matches_port(
                rule, port, executable_paths
            )
            if _rule_is_enabled_allow(rule):
                executable_allowed |= program_match
                port_allowed |= port_match
            elif _rule_is_enabled_block(rule) and (program_match or port_match):
                name = str(rule.get("Name") or "")
                executable_blocked |= program_match
                port_blocked |= port_match
                if name.casefold() in owned_names:
                    owned_blocks.append(name)
                else:
                    external_blocks.append(name or "(unnamed rule)")
        return FirewallStatus(
            True,
            executable,
            port,
            executable_allowed,
            port_allowed,
            executable_blocked,
            port_blocked,
            tuple(sorted(set(owned_blocks))),
            tuple(sorted(set(external_blocks))),
        )

    def fix(self, profile: PalworldProfile, status: FirewallStatus) -> None:
        if not status.supported:
            raise FirewallError("Windows Firewall is unavailable")
        if status.allowed:
            return
        if status.external_block_rule_names:
            raise FirewallRepairUnavailable(
                "A matching third-party Block rule must be removed manually"
            )
        payload = {
            "executable": status.executable_path,
            "rule_name": program_rule_name(profile.name),
            "display_name": f"Palsitter Palworld {profile.name} - PalServer.exe",
            "remove_names": list(status.owned_block_rule_names),
        }
        try:
            result = self.elevated_runner(payload)
        except (OSError, subprocess.SubprocessError) as exc:
            raise FirewallError(_command_error(exc)) from exc
        if result.returncode:
            detail = (result.stderr or result.stdout or "").strip()
            raise FirewallError(detail or "Windows Firewall repair failed")

    def _run_elevated(self, payload: Mapping[str, Any]) -> subprocess.CompletedProcess:
        encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        script = (
            "$ErrorActionPreference = 'Stop'; "
            f"$p = Start-Process -FilePath {_quote_powershell(sys.executable)} "
            f"-ArgumentList @('-m','module.games.palworld.firewall','--elevated-fix',{_quote_powershell(encoded)}) "
            "-Verb RunAs -Wait -PassThru; exit $p.ExitCode"
        )
        return self.run_command(
            _powershell_args(script, self.powershell),
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT * 2,
        )

    def _run_test_elevated(self, payload: Mapping[str, Any]) -> subprocess.CompletedProcess:
        Path(str(self._test_state_path)).write_text("open", encoding="utf-8")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--elevated-fix":
        raise SystemExit(2)
    raise SystemExit(_elevated_helper_payload(sys.argv[2]))


__all__ = [
    "FirewallError",
    "FirewallRepairUnavailable",
    "FirewallService",
    "FirewallStatus",
    "firewall_executable_paths",
    "port_rule_name",
    "program_rule_name",
    "resolve_executable",
]
