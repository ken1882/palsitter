from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from module.games.palworld.config import PalworldProfile, windows_console_executable_path


POWERSHELL = "powershell.exe"
NETSH = "netsh.exe"
IPTABLES = "iptables"
UFW = "ufw"
FIREWALLD = "firewall-cmd"
RULE_PREFIX = "Palsitter-Palworld-"
_COMMAND_TIMEOUT = 15
_LINUX_BACKENDS = {"iptables", "ufw", "firewalld"}


class FirewallError(RuntimeError):
    pass


class FirewallRepairUnavailable(FirewallError):
    pass


class FirewallPermissionDenied(FirewallError):
    def __init__(self, message: str, command: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.command = tuple(str(value) for value in command)


def _command_error(error: BaseException, firewall_name: str = "Windows Firewall") -> str:
    if isinstance(error, subprocess.TimeoutExpired):
        return f"{firewall_name} command timed out"
    return str(error)


def _permission_denied(result: subprocess.CompletedProcess) -> bool:
    detail = f"{result.stderr or ''}\n{result.stdout or ''}".casefold()
    return any(
        marker in detail
        for marker in (
            "a password is required",
            "permission denied",
            "not authorized",
            "authorization failed",
            "polkit",
            "authentication is required",
            "incorrect password",
            "sorry, try again",
        )
    )


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


def _firewall_backend_is_active(
    backend: str,
    run_command: Callable[..., subprocess.CompletedProcess],
) -> bool:
    if backend == "firewalld":
        result = run_command(
            [FIREWALLD, "--state"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.returncode == 0 and str(result.stdout or "").strip().casefold() == "running"
    if backend == "ufw":
        result = run_command(
            [UFW, "status"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.returncode == 0 and "status: active" in str(result.stdout or "").casefold()
    if backend == "iptables":
        result = run_command(
            [IPTABLES, "-S", "INPUT"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.returncode == 0
    return False


def detect_firewall_backend(
    run_command: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str | None:
    """Prefer an active installed backend, then fall back to installed backends."""
    if os.name == "nt":
        return "windows"
    installed = [
        backend
        for backend, command in (
            ("firewalld", FIREWALLD),
            ("ufw", UFW),
            ("iptables", IPTABLES),
        )
        if shutil.which(command)
    ]
    for backend in installed:
        try:
            if _firewall_backend_is_active(backend, run_command):
                return backend
        except (OSError, subprocess.SubprocessError):
            continue
    return installed[0] if installed else None


def _port_spec_matches(specification: str, port: int) -> bool:
    value = str(specification).strip().casefold()
    if "/" in value:
        value, protocol = value.rsplit("/", 1)
        if protocol not in {"udp", "17"}:
            return False
    for item in value.split(","):
        item = item.strip()
        if "-" in item or ":" in item:
            first, _, last = item.partition("-")
            if not last:
                first, _, last = item.partition(":")
            try:
                if int(first) <= port <= int(last):
                    return True
            except ValueError:
                continue
        else:
            try:
                if int(item) == port:
                    return True
            except ValueError:
                continue
    return False


def _iptables_rules(stdout: str, port: int) -> tuple[bool, bool, tuple[str, ...], tuple[str, ...]]:
    allowed = blocked = False
    owned: list[str] = []
    external: list[str] = []
    for raw_line in str(stdout or "").splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("-A INPUT"):
            continue
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        if "-p" not in tokens:
            continue
        protocol = tokens[tokens.index("-p") + 1].casefold()
        if protocol not in {"udp", "17"}:
            continue
        port_values: list[str] = []
        for option in ("--dport", "--dports"):
            if option in tokens:
                port_values.append(tokens[tokens.index(option) + 1])
        if not any(_port_spec_matches(value, port) for value in port_values):
            continue
        jump = tokens[tokens.index("-j") + 1].casefold() if "-j" in tokens else ""
        if jump not in {"accept", "drop", "reject"}:
            continue
        if jump == "accept":
            allowed = True
        else:
            blocked = True
            comment = ""
            if "--comment" in tokens:
                comment = tokens[tokens.index("--comment") + 1]
            if comment.casefold().startswith(RULE_PREFIX.casefold()):
                owned.append(comment)
            else:
                external.append(comment or "(unnamed rule)")
    return allowed, blocked, tuple(sorted(set(owned))), tuple(sorted(set(external)))


def _ufw_rules(stdout: str, port: int) -> tuple[bool, bool, tuple[str, ...], tuple[str, ...]]:
    allowed = blocked = False
    owned: list[str] = []
    external: list[str] = []
    for raw_line in str(stdout or "").splitlines():
        line = raw_line.strip()
        if not line or line.casefold().startswith(("status:", "to", "--")):
            continue
        fields = line.split()
        if len(fields) < 2 or not _port_spec_matches(fields[0], port):
            continue
        action = fields[1].casefold()
        if action not in {"allow", "deny", "reject"}:
            continue
        comment = line.partition("#")[2].strip() if "#" in line else ""
        if action == "allow":
            allowed = True
        else:
            blocked = True
            if comment.casefold().startswith(RULE_PREFIX.casefold()):
                owned.append(comment)
            else:
                external.append(comment or "(unnamed rule)")
    return allowed, blocked, tuple(sorted(set(owned))), tuple(sorted(set(external)))


def _firewalld_port_rules(stdout: str, port: int) -> bool:
    return any(_port_spec_matches(value, port) for value in str(stdout or "").split())


def _firewalld_rich_rules(stdout: str, port: int) -> tuple[bool, tuple[str, ...]]:
    allowed = blocked = False
    external: list[str] = []
    for rule in str(stdout or "").splitlines():
        lowered = rule.casefold()
        if "port" not in lowered or not any(
            _port_spec_matches(match, port)
            for match in re.findall(r'port="([^"]+)"', rule)
        ):
            continue
        if not any(
            token in lowered
            for token in (
                'protocol="udp"',
                "protocol='udp'",
                'protocol value="udp"',
            )
        ):
            continue
        if re.search(r"(?:^|[ <])accept(?:[ />]|$)", lowered):
            allowed = True
        elif re.search(r"(?:^|[ <])(drop|reject)(?:[ />]|$)", lowered):
            blocked = True
            external.append(rule.strip() or "(unnamed rule)")
    return allowed, tuple(sorted(set(external))) if blocked else ()


def _firewalld_zones(run_command: Callable[..., subprocess.CompletedProcess]) -> list[str]:
    result = run_command(
        [FIREWALLD, "--get-active-zones"],
        capture_output=True,
        text=True,
        timeout=_COMMAND_TIMEOUT,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise FirewallError(detail or "firewalld query failed")
    zones = [line.strip() for line in str(result.stdout or "").splitlines() if line.strip() and ":" not in line]
    if zones:
        return zones
    result = run_command(
        [FIREWALLD, "--get-default-zone"],
        capture_output=True,
        text=True,
        timeout=_COMMAND_TIMEOUT,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise FirewallError(detail or "firewalld default zone query failed")
    zone = str(result.stdout or "").strip()
    return [zone] if zone else []


def _linux_fix_commands(payload: Mapping[str, Any]) -> list[list[str]]:
    backend = str(payload.get("backend") or "")
    port = str(int(payload["port"]))
    rule_name = str(payload.get("rule_name") or "")
    remove_names = [str(value) for value in payload.get("remove_names", ())]
    if backend == "iptables":
        commands = [
            [
                IPTABLES,
                "-D",
                "INPUT",
                "-p",
                "udp",
                "--dport",
                port,
                "-m",
                "comment",
                "--comment",
                name,
                "-j",
                "DROP",
            ]
            for name in remove_names
        ]
        commands.append(
            [
                IPTABLES,
                "-I",
                "INPUT",
                "-p",
                "udp",
                "--dport",
                port,
                "-m",
                "comment",
                "--comment",
                rule_name,
                "-j",
                "ACCEPT",
            ]
        )
        return commands
    if backend == "ufw":
        commands = [
            [UFW, "delete", "deny", f"{port}/udp", "comment", name]
            for name in remove_names
        ]
        commands.append([UFW, "allow", f"{port}/udp", "comment", rule_name])
        return commands
    if backend == "firewalld":
        return [
            [FIREWALLD, "--permanent", f"--add-port={port}/udp"],
            [FIREWALLD, "--reload"],
        ]
    raise FirewallError(f"Unsupported Linux firewall backend: {backend}")


class FirewallService:
    def __init__(
        self,
        *,
        run_command: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        elevated_runner: Callable[..., subprocess.CompletedProcess] | None = None,
        powershell: str = POWERSHELL,
        supported: bool | None = None,
        backend: str | None = None,
    ) -> None:
        self.run_command = run_command
        self._test_state_path = os.getenv("PALSITTER_TEST_FIREWALL_STATE")
        self._test_delay = float(os.getenv("PALSITTER_TEST_FIREWALL_DELAY", "0") or 0)
        if backend is not None and backend not in {"windows", *_LINUX_BACKENDS}:
            raise ValueError(f"Unsupported firewall backend: {backend}")
        if backend is None:
            if self._test_state_path:
                backend = "test"
            else:
                backend = detect_firewall_backend(self.run_command)
        self.backend = backend
        self.elevated_runner = elevated_runner or (
            self._run_test_elevated if self._test_state_path else self._run_elevated
        )
        self.powershell = powershell
        self.supported = (
            bool(self.backend)
            if supported is None
            else bool(supported)
        )

    def check(self, profile: PalworldProfile, root_password: str | None = None) -> FirewallStatus:
        executable = str(resolve_executable(profile))
        executable_paths = firewall_executable_paths(profile)
        port = int(profile.game_port)
        if not self.supported:
            return FirewallStatus(False, executable, port)
        if self._test_state_path:
            if self._test_delay > 0:
                time.sleep(self._test_delay)
            if (
                os.getenv("PALSITTER_TEST_FIREWALL_CHECK_REQUIRE_PASSWORD")
                and not root_password
            ):
                raise FirewallPermissionDenied(
                    "Authorization failed",
                    command=(FIREWALLD, "--get-active-zones"),
                )
            state = Path(self._test_state_path).read_text(encoding="utf-8").strip().casefold()
            return FirewallStatus(
                True,
                executable,
                port,
                executable_allowed=state == "open",
            )
        try:
            if self.backend == "windows":
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
                return self._status_from_windows_rules(profile, executable, port, executable_paths, rules)
            return self._check_linux(profile, executable, port, root_password)
        except FirewallPermissionDenied:
            raise
        except (OSError, subprocess.SubprocessError, FirewallError) as exc:
            return FirewallStatus(
                False,
                executable,
                port,
                error=_command_error(exc, self._firewall_name),
                executable_supported=self.backend == "windows",
            )

    @property
    def _firewall_name(self) -> str:
        return {
            "windows": "Windows Firewall",
            "iptables": "iptables",
            "ufw": "UFW",
            "firewalld": "firewalld",
        }.get(self.backend or "", "Firewall")

    def _status_from_windows_rules(
        self,
        profile: PalworldProfile,
        executable: str,
        port: int,
        executable_paths: Iterable[str],
        rules: Iterable[Mapping[str, Any]],
    ) -> FirewallStatus:

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

    def _run_linux_command(
        self,
        args: list[str],
        root_password: str | None = None,
    ) -> subprocess.CompletedProcess:
        command = list(args)
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": _COMMAND_TIMEOUT,
        }
        if root_password is not None and (not hasattr(os, "geteuid") or os.geteuid() != 0):
            if not shutil.which("sudo"):
                raise FirewallPermissionDenied(
                    "sudo is required for administrator authentication",
                    command=args,
                )
            command = ["sudo", "-S", "-p", "", *command]
            kwargs["input"] = root_password + "\n"
        result = self.run_command(command, **kwargs)
        if result.returncode:
            detail = (result.stderr or result.stdout or "").strip()
            if _permission_denied(result):
                raise FirewallPermissionDenied(
                    detail or "Administrator authentication is required",
                    command=args,
                )
            raise FirewallError(detail or f"{self._firewall_name} query failed")
        return result

    def _check_linux(
        self,
        profile: PalworldProfile,
        executable: str,
        port: int,
        root_password: str | None = None,
    ) -> FirewallStatus:
        if self.backend == "iptables":
            result = self._run_linux_command([IPTABLES, "-S", "INPUT"], root_password)
            allowed, blocked, owned, external = _iptables_rules(result.stdout, port)
        elif self.backend == "ufw":
            result = self._run_linux_command([UFW, "status"], root_password)
            if str(result.stdout or "").casefold().find("status: inactive") >= 0:
                allowed, blocked, owned, external = True, False, (), ()
            else:
                allowed, blocked, owned, external = _ufw_rules(result.stdout, port)
        elif self.backend == "firewalld":
            allowed = blocked = False
            owned = ()
            external_list: list[str] = []
            for zone in _firewalld_zones(
                lambda args, **kwargs: self._run_linux_command(args, root_password)
            ):
                result = self._run_linux_command(
                    [FIREWALLD, f"--zone={zone}", "--list-ports"], root_password
                )
                allowed |= _firewalld_port_rules(result.stdout, port)
                result = self._run_linux_command(
                    [FIREWALLD, f"--zone={zone}", "--list-rich-rules"], root_password
                )
                rich_allowed, rich_external = _firewalld_rich_rules(result.stdout, port)
                allowed |= rich_allowed
                external_list.extend(rich_external)
                blocked |= bool(rich_external)
            external = tuple(sorted(set(external_list)))
        else:
            raise FirewallError("No supported firewall backend is available")
        return FirewallStatus(
            True,
            executable,
            port,
            port_allowed=allowed,
            port_blocked=blocked,
            owned_block_rule_names=owned,
            external_block_rule_names=external,
            executable_supported=False,
        )

    def fix(
        self,
        profile: PalworldProfile,
        status: FirewallStatus,
        root_password: str | None = None,
    ) -> None:
        if not status.supported:
            raise FirewallError(f"{self._firewall_name} is unavailable")
        if status.allowed:
            return
        if status.external_block_rule_names:
            raise FirewallRepairUnavailable(
                "A matching third-party Block rule must be removed manually"
            )
        payload = self._fix_payload(profile, status)
        try:
            if root_password is None:
                result = self.elevated_runner(payload)
            elif self._test_state_path:
                result = self.elevated_runner(payload, root_password)
            else:
                result = self._run_elevated(payload, root_password)
        except (OSError, subprocess.SubprocessError) as exc:
            raise FirewallError(_command_error(exc, self._firewall_name)) from exc
        if result.returncode:
            detail = (result.stderr or result.stdout or "").strip()
            if self.backend in _LINUX_BACKENDS and _permission_denied(result):
                command = ()
                if self.backend in _LINUX_BACKENDS:
                    command = tuple(_linux_fix_commands(payload)[0])
                raise FirewallPermissionDenied(
                    detail or "Administrator authentication is required",
                    command=command,
                )
            raise FirewallError(detail or f"{self._firewall_name} repair failed")

    def _fix_payload(self, profile: PalworldProfile, status: FirewallStatus) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "backend": self.backend,
            "port": status.udp_port,
            "rule_name": port_rule_name(profile.name, status.udp_port),
            "remove_names": list(status.owned_block_rule_names),
        }
        if self.backend == "windows":
            payload.update(
                executable=status.executable_path,
                rule_name=program_rule_name(profile.name),
                display_name=f"Palsitter Palworld {profile.name} - PalServer.exe",
            )
        return payload

    def _run_elevated(
        self,
        payload: Mapping[str, Any],
        root_password: str | None = None,
    ) -> subprocess.CompletedProcess:
        if self.backend in _LINUX_BACKENDS:
            return self._run_linux_elevated(payload, root_password)
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

    def _run_linux_elevated(
        self,
        payload: Mapping[str, Any],
        root_password: str | None = None,
    ) -> subprocess.CompletedProcess:
        commands = _linux_fix_commands(payload)
        prefix: list[str] = []
        input_data: str | None = None
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            if shutil.which("sudo"):
                if root_password is None:
                    prefix = ["sudo", "-n"]
                else:
                    prefix = ["sudo", "-S", "-p", ""]
                    input_data = root_password + "\n"
            elif shutil.which("pkexec") and root_password is None:
                prefix = ["pkexec"]
            else:
                return subprocess.CompletedProcess(
                    [], 1, stdout="", stderr="No pkexec or sudo command is available"
                )
        last = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        for command in commands:
            last = self.run_command(
                [*prefix, *command],
                capture_output=True,
                text=True,
                timeout=_COMMAND_TIMEOUT,
                input=input_data,
            )
            if last.returncode:
                if _permission_denied(last):
                    detail = (last.stderr or last.stdout or "").strip()
                    raise FirewallPermissionDenied(
                        detail or "Administrator authentication is required",
                        command=command,
                    )
                return last
        return last

    def _run_test_elevated(
        self,
        payload: Mapping[str, Any],
        root_password: str | None = None,
    ) -> subprocess.CompletedProcess:
        if os.getenv("PALSITTER_TEST_FIREWALL_REQUIRE_PASSWORD") and not root_password:
            raise FirewallPermissionDenied(
                "sudo: a password is required",
                command=(
                    "firewall-cmd",
                    "--permanent",
                    f"--add-port={int(payload['port'])}/udp",
                ),
            )
        Path(str(self._test_state_path)).write_text("open", encoding="utf-8")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--elevated-fix":
        raise SystemExit(2)
    raise SystemExit(_elevated_helper_payload(sys.argv[2]))


__all__ = [
    "FirewallError",
    "FirewallPermissionDenied",
    "FirewallRepairUnavailable",
    "FirewallService",
    "FirewallStatus",
    "detect_firewall_backend",
    "firewall_executable_paths",
    "port_rule_name",
    "program_rule_name",
    "resolve_executable",
]
