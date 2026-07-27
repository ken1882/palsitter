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
import tempfile
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
OutputLogger = Callable[[str], None]


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


def _process_failure_detail(result: subprocess.CompletedProcess) -> str:
    details = []
    if result.stderr:
        details.append(f"stderr: {str(result.stderr).strip()}")
    if result.stdout:
        details.append(f"stdout: {str(result.stdout).strip()}")
    details.append(f"exit code: {result.returncode}")
    return "\n".join(details)


def _read_elevated_result(path: Path) -> tuple[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", ""
    if not isinstance(data, Mapping):
        return "", ""
    return str(data.get("stdout") or ""), str(data.get("stderr") or "")


def _write_elevated_result(path: Path | None, result: subprocess.CompletedProcess) -> None:
    if path is None:
        return
    try:
        path.write_text(
            json.dumps(
                {
                    "stdout": result.stdout or "",
                    "stderr": result.stderr or "",
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def _merge_process_output(primary: str | None, redirected: str) -> str:
    values = [str(value) for value in (primary, redirected) if value]
    return "\n".join(value.rstrip("\r\n") for value in values)


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
        return self.supported and not self.allowed


@dataclass(frozen=True)
class PortFirewallStatus:
    supported: bool
    port: int
    protocol: str
    allowed: bool = False
    blocked: bool = False
    external_block_rule_names: tuple[str, ...] = ()
    error: str | None = None
    owned_block_rule_names: tuple[str, ...] = ()
    external_block_rule_specs: tuple[tuple[str, str], ...] = ()

    @property
    def repairable(self) -> bool:
        return self.supported and not self.error and (not self.allowed or self.blocked)


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


def _replace_rule_block(rule_name: str) -> str:
    return (
        f"$existing = @(Get-NetFirewallRule -Name {_quote_powershell(rule_name)} "
        "-ErrorAction SilentlyContinue); "
        "$existing | Remove-NetFirewallRule -ErrorAction Stop; "
    )


def _fix_script(executable: str, rule_name: str, display_name: str, remove_names: Iterable[str]) -> str:
    removals = ",".join(_quote_powershell(value) for value in remove_names)
    if removals:
        remove_block = (
            f"foreach ($name in @({removals})) {{ "
            "$rules = @(Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue); "
            "if (-not $rules) { "
            "$rules = @(Get-NetFirewallRule -DisplayName $name -ErrorAction Stop) } "
            "$rules | Remove-NetFirewallRule -ErrorAction Stop }"
        )
    else:
        remove_block = ""
    return (
        "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'; "
        f"{_replace_rule_block(rule_name)}{remove_block} "
        "New-NetFirewallRule "
        f"-Name {_quote_powershell(rule_name)} "
        f"-DisplayName {_quote_powershell(display_name)} "
        "-Direction Inbound -Action Allow -Enabled True -Profile Any "
        f"-Program {_quote_powershell(executable)} | Out-Null"
    )


def _fix_port_script(
    port: int,
    protocol: str,
    rule_name: str,
    display_name: str,
    remove_names: Iterable[str],
) -> str:
    removals = ",".join(_quote_powershell(value) for value in remove_names)
    if removals:
        remove_block = (
            f"foreach ($name in @({removals})) {{ "
            "$rules = @(Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue); "
            "if (-not $rules) { "
            "$rules = @(Get-NetFirewallRule -DisplayName $name -ErrorAction Stop) } "
            "$rules | Remove-NetFirewallRule -ErrorAction Stop }"
        )
    else:
        remove_block = ""
    return (
        "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'; "
        f"{_replace_rule_block(rule_name)}{remove_block} "
        "New-NetFirewallRule "
        f"-Name {_quote_powershell(rule_name)} "
        f"-DisplayName {_quote_powershell(display_name)} "
        "-Direction Inbound -Action Allow -Enabled True -Profile Any "
        f"-Protocol {_quote_powershell(protocol)} -LocalPort {int(port)} | Out-Null"
    )


def _is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _elevated_helper_payload(payload: str) -> int:
    data = json.loads(base64.b64decode(payload).decode("utf-8"))
    result_path = data.get("result_path")
    result_file = Path(str(result_path)) if result_path else None
    if not _is_admin():
        _write_elevated_result(
            result_file,
            subprocess.CompletedProcess([], 740, stdout="", stderr="Not elevated"),
        )
        return 740
    if data.get("kind") == "port":
        script = _fix_port_script(
            int(data["port"]),
            str(data["protocol"]),
            str(data["rule_name"]),
            str(data["display_name"]),
            data.get("remove_names", ()),
        )
    else:
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
        _write_elevated_result(
            result_file,
            subprocess.CompletedProcess([], 124, stdout="", stderr=str(exc)),
        )
        print(_command_error(exc), file=sys.stderr)
        return 124
    _write_elevated_result(result_file, result)
    if result.returncode:
        for output in (result.stderr, result.stdout):
            if output:
                print(output, file=sys.stderr, end="")
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


def _matches_protocol(rule: Mapping[str, Any], protocol: str = "udp") -> bool:
    protocol = protocol.casefold()
    numbers = {"udp": "17", "tcp": "6"}
    return any(_fold(value) in {protocol, numbers.get(protocol, protocol)} for value in _flatten(rule.get("Protocol")))


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


def _windows_rule_query_script() -> str:
    return (
        "$ErrorActionPreference = 'Stop'; "
        "$ProgressPreference = 'SilentlyContinue'; "
        "$rules = @( "
        "Get-NetFirewallRule -Direction Inbound -Enabled True -Action Allow "
        "-ErrorAction SilentlyContinue; "
        "Get-NetFirewallRule -Direction Inbound -Enabled True -Action Block "
        "-ErrorAction SilentlyContinue "
        ") | Sort-Object -Property Name -Unique; "
        "$rules | ForEach-Object { "
        "$rule = $_; "
        "$port = $rule | Get-NetFirewallPortFilter; "
        "$application = $rule | Get-NetFirewallApplicationFilter; "
        "[pscustomobject]@{ "
        "Name = $rule.Name; "
        "Enabled = $rule.Enabled; "
        "Direction = $rule.Direction; "
        "Action = $rule.Action; "
        "Program = @($application.Program); "
        "Protocol = @($port.Protocol); "
        "LocalPort = @($port.LocalPort) "
        "} "
        "} | ConvertTo-Json -Compress -Depth 4"
    )


def _windows_port_rule_query_script() -> str:
    return (
        "$ErrorActionPreference = 'Stop'; "
        "$ProgressPreference = 'SilentlyContinue'; "
        "$rules = @( "
        "Get-NetFirewallRule -Direction Inbound -Enabled True -Action Allow "
        "-ErrorAction SilentlyContinue; "
        "Get-NetFirewallRule -Direction Inbound -Enabled True -Action Block "
        "-ErrorAction SilentlyContinue "
        ") | Sort-Object -Property Name -Unique; "
        "$rules | ForEach-Object { "
        "$rule = $_; "
        "$port = $rule | Get-NetFirewallPortFilter; "
        "[pscustomobject]@{ "
        "Name = $rule.Name; "
        "Enabled = $rule.Enabled; "
        "Direction = $rule.Direction; "
        "Action = $rule.Action; "
        "Protocol = @($port.Protocol); "
        "LocalPort = @($port.LocalPort) "
        "} "
        "} | ConvertTo-Json -Compress -Depth 4"
    )


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


def _port_spec_matches(specification: str, port: int, protocol_name: str = "udp") -> bool:
    value = str(specification).strip().casefold()
    if "/" in value:
        value, protocol = value.rsplit("/", 1)
        expected = protocol_name.casefold()
        if protocol not in {expected, {"udp": "17", "tcp": "6"}.get(expected, expected)}:
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


def _iptables_rules(
    stdout: str, port: int, protocol_name: str = "udp"
) -> tuple[bool, bool, tuple[str, ...], tuple[str, ...], tuple[tuple[str, str], ...]]:
    allowed = blocked = False
    owned: list[str] = []
    external: list[str] = []
    external_specs: list[tuple[str, str]] = []
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
        if protocol not in {protocol_name.casefold(), {"udp": "17", "tcp": "6"}.get(protocol_name.casefold(), protocol_name.casefold())}:
            continue
        port_values: list[str] = []
        for option in ("--dport", "--dports"):
            if option in tokens:
                port_values.append(tokens[tokens.index(option) + 1])
        if not any(_port_spec_matches(value, port, protocol_name) for value in port_values):
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
                external_specs.append((jump, comment))
    return (
        allowed,
        blocked,
        tuple(sorted(set(owned))),
        tuple(sorted(set(external))),
        tuple(sorted(set(external_specs))),
    )


def _ufw_rules(
    stdout: str, port: int, protocol_name: str = "udp"
) -> tuple[bool, bool, tuple[str, ...], tuple[str, ...], tuple[tuple[str, str], ...]]:
    allowed = blocked = False
    owned: list[str] = []
    external: list[str] = []
    external_specs: list[tuple[str, str]] = []
    for raw_line in str(stdout or "").splitlines():
        line = raw_line.strip()
        if not line or line.casefold().startswith(("status:", "to", "--")):
            continue
        fields = line.split()
        if len(fields) < 2 or not _port_spec_matches(fields[0], port, protocol_name):
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
                external_specs.append((action, comment))
    return (
        allowed,
        blocked,
        tuple(sorted(set(owned))),
        tuple(sorted(set(external))),
        tuple(sorted(set(external_specs))),
    )


def _firewalld_port_rules(stdout: str, port: int, protocol_name: str = "udp") -> bool:
    return any(_port_spec_matches(value, port, protocol_name) for value in str(stdout or "").split())


def _firewalld_rich_rules(stdout: str, port: int, protocol_name: str = "udp") -> tuple[bool, tuple[str, ...]]:
    allowed = blocked = False
    external: list[str] = []
    for rule in str(stdout or "").splitlines():
        lowered = rule.casefold()
        if "port" not in lowered or not any(
            _port_spec_matches(match, port, protocol_name)
            for match in re.findall(r'port="([^"]+)"', rule)
        ):
            continue
        if not any(
            token in lowered
            for token in (
                f'protocol="{protocol_name}"',
                f"protocol='{protocol_name}'",
                f'protocol value="{protocol_name}"',
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
    protocol = str(payload.get("protocol") or "udp").casefold()
    rule_name = str(payload.get("rule_name") or "")
    remove_names = [str(value) for value in payload.get("remove_names", ())]
    if backend == "iptables":
        commands = []
        for jump, comment in payload.get("remove_block_rules", ()):
            command = [IPTABLES, "-D", "INPUT", "-p", protocol, "--dport", port]
            if comment:
                command.extend(["-m", "comment", "--comment", str(comment)])
            command.extend(["-j", str(jump).upper()])
            commands.append(command)
        commands.extend(
            [
                [
                    IPTABLES,
                    "-D",
                    "INPUT",
                    "-p",
                    protocol,
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
        )
        commands.append(
            [
                IPTABLES,
                "-I",
                "INPUT",
                "-p",
                protocol,
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
        commands = []
        for action, comment in payload.get("remove_block_rules", ()):
            command = [UFW, "delete", str(action), f"{port}/{protocol}"]
            if comment:
                command.extend(["comment", str(comment)])
            commands.append(command)
        commands.extend(
            [UFW, "delete", "deny", f"{port}/{protocol}", "comment", name]
            for name in remove_names
        )
        commands.append([UFW, "allow", f"{port}/{protocol}", "comment", rule_name])
        return commands
    if backend == "firewalld":
        commands = [
            [
                FIREWALLD,
                "--permanent",
                f"--zone={zone}",
                f"--remove-rich-rule={rule}",
            ]
            for zone, rule in payload.get("remove_rich_rules", ())
        ]
        commands.extend(
            [
                [FIREWALLD, "--permanent", f"--add-port={port}/{protocol}"],
                [FIREWALLD, "--reload"],
            ]
        )
        return commands
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
        logger: OutputLogger | None = None,
    ) -> None:
        self.run_command = run_command
        self.logger = logger
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
            block_name = state.partition(":")[2].strip()
            return FirewallStatus(
                True,
                executable,
                port,
                executable_allowed=state == "open",
                port_blocked=bool(block_name),
                external_block_rule_names=(block_name,) if block_name else (),
            )
        try:
            if self.backend == "windows":
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
                    rules = self._windows_rules(result.stdout)
                except subprocess.TimeoutExpired:
                    rules = self._windows_rules("")
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

    def _windows_rules(self, netsh_stdout: str) -> list[Mapping[str, Any]]:
        rules = _rules_from_output(netsh_stdout)
        if any(
            {"Enabled", "Direction", "Action"}.issubset(rule)
            for rule in rules
        ):
            return rules
        result = self.run_command(
            _powershell_args(_windows_rule_query_script(), self.powershell),
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout or "").strip()
            raise FirewallError(detail or "Windows Firewall PowerShell query failed")
        return _rules_from_output(result.stdout)

    def _windows_port_rules(self) -> list[Mapping[str, Any]]:
        result = self.run_command(
            _powershell_args(_windows_port_rule_query_script(), self.powershell),
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout or "").strip()
            raise FirewallError(detail or "Windows Firewall port query failed")
        return _rules_from_output(result.stdout)

    def _windows_port_rules_from_netsh(self, netsh_stdout: str) -> list[Mapping[str, Any]]:
        rules = _rules_from_output(netsh_stdout)
        if any(
            {"Enabled", "Direction", "Action"}.issubset(rule)
            for rule in rules
        ):
            return rules
        return self._windows_port_rules()

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
        external_specs: list[tuple[str, str]] = []
        linux_block_specs: list[tuple[str, str]] = []
        if self.backend == "iptables":
            result = self._run_linux_command([IPTABLES, "-S", "INPUT"], root_password)
            allowed, blocked, owned, external, linux_block_specs = _iptables_rules(
                result.stdout, port
            )
        elif self.backend == "ufw":
            result = self._run_linux_command([UFW, "status"], root_password)
            if str(result.stdout or "").casefold().find("status: inactive") >= 0:
                allowed, blocked, owned, external = True, False, (), ()
            else:
                allowed, blocked, owned, external, linux_block_specs = _ufw_rules(
                    result.stdout, port
                )
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
                external_specs.extend((zone, rule) for rule in rich_external)
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
            external_block_rule_specs=tuple(sorted(set(external_specs))),
            linux_block_rule_specs=tuple(sorted(set(linux_block_specs))),
        )

    def check_port(
        self,
        port: int,
        *,
        protocol: str = "tcp",
        root_password: str | None = None,
    ) -> PortFirewallStatus:
        """Check a generic inbound port using the same platform backends as Palworld."""
        port = int(port)
        protocol = protocol.casefold()
        if protocol not in {"tcp", "udp"}:
            raise ValueError("protocol must be tcp or udp")
        if not self.supported:
            return PortFirewallStatus(False, port, protocol)
        try:
            if self._test_state_path:
                if self._test_delay > 0:
                    time.sleep(self._test_delay)
                state = Path(self._test_state_path).read_text(encoding="utf-8").strip().casefold()
                return PortFirewallStatus(
                    True,
                    port,
                    protocol,
                    allowed=state == "open",
                    blocked=state.startswith("blocked:"),
                    external_block_rule_names=(state.partition(":")[2],)
                    if state.startswith("blocked:") and state.partition(":")[2]
                    else (),
                )
            if self.backend == "windows":
                try:
                    result = self.run_command(
                        [NETSH, "advfirewall", "firewall", "show", "rule", "name=all", "verbose"],
                        capture_output=True,
                        text=True,
                        timeout=_COMMAND_TIMEOUT,
                    )
                    if result.returncode:
                        detail = (result.stderr or result.stdout or "").strip()
                        raise FirewallError(detail or "Windows Firewall query failed")
                    rules = self._windows_port_rules_from_netsh(result.stdout)
                except subprocess.TimeoutExpired:
                    rules = self._windows_port_rules()
                allowed = blocked = False
                external: list[str] = []
                for rule in rules:
                    if not _matches_protocol(rule, protocol) or not _matches_port(rule, port, ()):
                        continue
                    name = str(rule.get("Name") or "(unnamed rule)")
                    if _rule_is_enabled_allow(rule):
                        allowed = True
                    elif _rule_is_enabled_block(rule):
                        blocked = True
                        external.append(name)
                return PortFirewallStatus(
                    True, port, protocol, allowed, blocked, tuple(sorted(set(external)))
                )
            if self.backend == "iptables":
                result = self._run_linux_command([IPTABLES, "-S", "INPUT"], root_password)
                allowed, blocked, owned, external, block_specs = _iptables_rules(
                    result.stdout, port, protocol
                )
            elif self.backend == "ufw":
                result = self._run_linux_command([UFW, "status"], root_password)
                if "status: inactive" in str(result.stdout or "").casefold():
                    allowed, blocked, owned, external, block_specs = True, False, (), (), ()
                else:
                    allowed, blocked, owned, external, block_specs = _ufw_rules(
                        result.stdout, port, protocol
                    )
            elif self.backend == "firewalld":
                allowed = blocked = False
                owned = ()
                external_list: list[str] = []
                block_specs: list[tuple[str, str]] = []
                for zone in _firewalld_zones(
                    lambda args, **kwargs: self._run_linux_command(args, root_password)
                ):
                    result = self._run_linux_command(
                        [FIREWALLD, f"--zone={zone}", "--list-ports"], root_password
                    )
                    allowed |= _firewalld_port_rules(result.stdout, port, protocol)
                    result = self._run_linux_command(
                        [FIREWALLD, f"--zone={zone}", "--list-rich-rules"], root_password
                    )
                    rich_allowed, rich_external = _firewalld_rich_rules(
                        result.stdout, port, protocol
                    )
                    allowed |= rich_allowed
                    blocked |= bool(rich_external)
                    external_list.extend(rich_external)
                    block_specs.extend((zone, rule) for rule in rich_external)
                external = tuple(sorted(set(external_list)))
            else:
                raise FirewallError("No supported firewall backend is available")
            return PortFirewallStatus(
                True,
                port,
                protocol,
                allowed,
                blocked,
                external,
                owned_block_rule_names=tuple(sorted(set(owned))),
                external_block_rule_specs=tuple(sorted(set(block_specs))),
            )
        except FirewallPermissionDenied:
            raise
        except (OSError, subprocess.SubprocessError, FirewallError) as exc:
            return PortFirewallStatus(
                False, port, protocol, error=_command_error(exc, self._firewall_name)
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
        self._run_fix_payload(self._fix_payload(profile, status), root_password)

    def fix_port(
        self,
        status: PortFirewallStatus,
        root_password: str | None = None,
    ) -> None:
        if not status.supported:
            raise FirewallError(f"{self._firewall_name} is unavailable")
        if status.allowed and not status.blocked:
            return
        self._run_fix_payload(self._fix_port_payload(status), root_password)

    def _run_fix_payload(
        self,
        payload: Mapping[str, Any],
        root_password: str | None = None,
    ) -> None:
        try:
            if root_password is None:
                result = self.elevated_runner(payload)
            elif self._test_state_path:
                result = self.elevated_runner(payload, root_password)
            else:
                result = self._run_elevated(payload, root_password)
        except (OSError, subprocess.SubprocessError) as exc:
            raise FirewallError(_command_error(exc, self._firewall_name)) from exc
        self._log_process_output(result)
        if result.returncode:
            detail = _process_failure_detail(result)
            if self.backend in _LINUX_BACKENDS and _permission_denied(result):
                command = ()
                if self.backend in _LINUX_BACKENDS:
                    command = tuple(_linux_fix_commands(payload)[0])
                raise FirewallPermissionDenied(
                    detail or "Administrator authentication is required",
                    command=command,
                )
            raise FirewallError(detail or f"{self._firewall_name} repair failed")

    def _log_process_output(self, result: subprocess.CompletedProcess) -> None:
        if self.logger is None:
            return
        for stream_name, output in (("stderr", result.stderr), ("stdout", result.stdout)):
            if not output:
                continue
            for line in str(output).splitlines():
                if line.strip():
                    self.logger(f"{stream_name}: {line}")
        if result.returncode:
            self.logger(f"exit code: {result.returncode}")

    def _fix_payload(self, profile: PalworldProfile, status: FirewallStatus) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "backend": self.backend,
            "port": status.udp_port,
            "rule_name": port_rule_name(profile.name, status.udp_port),
            "remove_names": list(status.owned_block_rule_names)
            if self.backend in _LINUX_BACKENDS
            else list(
                dict.fromkeys(
                    [*status.owned_block_rule_names, *status.external_block_rule_names]
                )
            ),
            "remove_rich_rules": [
                list(spec) for spec in status.external_block_rule_specs
            ],
            "remove_block_rules": [
                list(spec) for spec in status.linux_block_rule_specs
            ],
        }
        if self.backend == "windows":
            payload.update(
                executable=status.executable_path,
                rule_name=program_rule_name(profile.name),
                display_name=f"Palsitter Palworld {profile.name} - PalServer.exe",
            )
        return payload

    def _fix_port_payload(self, status: PortFirewallStatus) -> dict[str, Any]:
        protocol = status.protocol.casefold()
        return {
            "kind": "port",
            "backend": self.backend,
            "port": status.port,
            "protocol": protocol,
            "rule_name": f"Palsitter-Web-{protocol.upper()}-{status.port}",
            "display_name": f"Palsitter Web {protocol.upper()} {status.port}",
            "remove_names": list(
                dict.fromkeys(
                    [
                        *status.owned_block_rule_names,
                        *(
                            ()
                            if self.backend in _LINUX_BACKENDS
                            else status.external_block_rule_names
                        ),
                    ]
                )
            ),
            "remove_rich_rules": [
                list(spec) for spec in status.external_block_rule_specs
            ],
            "remove_block_rules": [
                list(spec) for spec in status.external_block_rule_specs
            ],
        }

    def _run_elevated(
        self,
        payload: Mapping[str, Any],
        root_password: str | None = None,
    ) -> subprocess.CompletedProcess:
        if self.backend in _LINUX_BACKENDS:
            return self._run_linux_elevated(payload, root_password)
        temporary_dir = Path(tempfile.mkdtemp(prefix="palsitter-firewall-"))
        result_path = temporary_dir / "result.json"
        try:
            elevated_payload = dict(payload)
            elevated_payload["result_path"] = str(result_path)
            encoded = base64.b64encode(
                json.dumps(elevated_payload).encode("utf-8")
            ).decode("ascii")
            script = (
                "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'; "
                f"$p = Start-Process -FilePath {_quote_powershell(sys.executable)} "
                f"-ArgumentList @('-m','module.games.palworld.firewall','--elevated-fix',{_quote_powershell(encoded)}) "
                "-Verb RunAs -Wait -PassThru; exit $p.ExitCode"
            )
            result = self.run_command(
                _powershell_args(script, self.powershell),
                capture_output=True,
                text=True,
                timeout=_COMMAND_TIMEOUT * 2,
            )
            child_stdout, child_stderr = _read_elevated_result(result_path)
            return subprocess.CompletedProcess(
                result.args,
                result.returncode,
                stdout=_merge_process_output(
                    result.stdout, child_stdout
                ),
                stderr=_merge_process_output(
                    result.stderr, child_stderr
                ),
            )
        finally:
            shutil.rmtree(temporary_dir, ignore_errors=True)

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
                    f"--add-port={int(payload['port'])}/{payload.get('protocol', 'udp')}",
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
    "PortFirewallStatus",
    "detect_firewall_backend",
    "firewall_executable_paths",
    "port_rule_name",
    "program_rule_name",
    "resolve_executable",
]
