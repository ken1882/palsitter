import base64
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from module.games.palworld.config import PalworldProfile
from module.games.palworld.firewall import (
    FirewallPermissionDenied,
    FirewallService,
    _fix_port_script,
    _fix_script,
    detect_firewall_backend,
    port_rule_name,
    program_rule_name,
)


def _profile(tmp_path):
    executable = tmp_path / "PalServer.exe"
    return PalworldProfile(
        name="Test Server",
        workdir=str(tmp_path),
        executable=str(executable),
        game_port=8211,
    )


def _rule(
    *,
    name="external-rule",
    action="Allow",
    enabled="True",
    direction="Inbound",
    program=None,
    protocol=None,
    local_port=None,
):
    return {
        "Name": name,
        "Action": action,
        "Enabled": enabled,
        "Direction": direction,
        "Program": [] if program is None else [program],
        "Protocol": [] if protocol is None else [protocol],
        "LocalPort": [] if local_port is None else [local_port],
    }


def _runner(rules):
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=json.dumps(rules),
            stderr="",
        )

    return run


def test_generic_tcp_port_fix_uses_port_rule_payload():
    captured = {}
    service = FirewallService(
        backend="windows",
        supported=True,
        run_command=_runner(
            [
                _rule(
                    name="web-block",
                    action="Block",
                    protocol="TCP",
                    local_port="22368",
                )
            ]
        ),
        elevated_runner=lambda payload: captured.update(payload)
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    status = service.check_port(22368, protocol="tcp")
    service.fix_port(status)

    assert captured == {
        "kind": "port",
        "backend": "windows",
        "port": 22368,
        "protocol": "tcp",
        "rule_name": "Palsitter-Web-TCP-22368",
        "display_name": "Palsitter Web TCP 22368",
        "remove_names": ["web-block"],
        "remove_rich_rules": [],
        "remove_block_rules": [],
    }


def test_backend_detection_prefers_active_installed_backend(monkeypatch):
    monkeypatch.setattr(
        "module.games.palworld.firewall.shutil.which",
        lambda command: f"/usr/bin/{command}",
    )

    def run(args, **kwargs):
        if args == ["firewall-cmd", "--state"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="not running")
        if args == ["ufw", "status"]:
            return subprocess.CompletedProcess(args, 0, stdout="Status: active\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    assert detect_firewall_backend(run) == "ufw"


def test_backend_detection_falls_back_to_installed_backend(monkeypatch):
    monkeypatch.setattr(
        "module.games.palworld.firewall.shutil.which",
        lambda command: "/usr/bin/firewall-cmd" if command == "firewall-cmd" else None,
    )

    assert detect_firewall_backend(
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, stdout="", stderr="stopped")
    ) == "firewalld"


def test_check_accepts_matching_executable_rule(tmp_path):
    profile = _profile(tmp_path)
    service = FirewallService(
        backend="windows",
        supported=True,
        run_command=_runner(
            [_rule(program=str(Path(profile.executable).resolve()).upper())]
        ),
    )

    result = service.check(profile)

    assert result.allowed
    assert result.executable_allowed
    assert not result.port_allowed


def test_check_accepts_palworld_console_executable_rule(tmp_path):
    profile = _profile(tmp_path)
    console = Path(profile.workdir) / "Pal" / "Binaries" / "Win64" / "PalServer-Win64-Shipping-Cmd.exe"
    service = FirewallService(
        backend="windows",
        supported=True,
        run_command=_runner([_rule(program=str(console).upper())]),
    )

    result = service.check(profile)

    assert result.allowed
    assert result.executable_allowed


def test_check_accepts_matching_udp_port_rule(tmp_path):
    profile = _profile(tmp_path)
    service = FirewallService(
        backend="windows",
        supported=True,
        run_command=_runner([_rule(protocol="17", local_port="8211")]),
    )

    result = service.check(profile)

    assert result.allowed
    assert result.port_allowed
    assert not result.executable_allowed


def test_port_matching_ignores_rules_for_other_executables(tmp_path):
    profile = _profile(tmp_path)
    other_executable = r"C:\Program Files\Epic Games\Launcher\Portal\EpicGamesLauncher.exe"
    service = FirewallService(
        backend="windows",
        supported=True,
        run_command=_runner(
            [
                _rule(program=other_executable, protocol="UDP", local_port="Any"),
                _rule(
                    name="other-block",
                    action="Block",
                    program=other_executable,
                    protocol="UDP",
                    local_port="Any",
                ),
            ]
        ),
    )

    result = service.check(profile)

    assert not result.port_allowed
    assert not result.port_blocked
    assert result.external_block_rule_names == ()


def test_check_uses_fast_non_admin_netsh_rule_query(tmp_path):
    profile = _profile(tmp_path)
    captured = {}
    output = """\
Rule Name: Palserver UDP
----------------------------------------------------------------------
Enabled: Yes
Direction: In
Protocol: UDP
LocalPort: 8211
Action: Allow

"""

    def run(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

    result = FirewallService(backend="windows", supported=True, run_command=run).check(profile)

    assert result.allowed
    assert result.port_allowed
    assert captured["args"][:5] == [
        "netsh.exe",
        "advfirewall",
        "firewall",
        "show",
        "rule",
    ]
    assert captured["args"][-1] == "verbose"


def test_windows_port_check_falls_back_for_localized_netsh_output():
    calls = []
    localized_netsh = "規則名稱: Palsitter Web TCP 22368\n已啟用: 是\n方向: 入\n"
    powershell_rules = json.dumps(
        [_rule(name="Palsitter-Web-TCP-22368", protocol="TCP", local_port="22368")]
    )

    def run(args, **kwargs):
        calls.append(args[0])
        if args[0] == "netsh.exe":
            return subprocess.CompletedProcess(args, 0, stdout=localized_netsh, stderr="")
        return subprocess.CompletedProcess(args, 0, stdout=powershell_rules, stderr="")

    status = FirewallService(backend="windows", supported=True, run_command=run).check_port(
        22368, protocol="tcp"
    )

    assert status.allowed
    assert calls == ["netsh.exe", "powershell.exe"]


def test_windows_port_check_falls_back_when_netsh_times_out():
    calls = []
    powershell_rules = json.dumps(
        [_rule(name="Palsitter-Web-TCP-22368", protocol="TCP", local_port="22368")]
    )

    def run(args, **kwargs):
        calls.append(args[0])
        if args[0] == "netsh.exe":
            raise subprocess.TimeoutExpired(args, 15)
        return subprocess.CompletedProcess(args, 0, stdout=powershell_rules, stderr="")

    status = FirewallService(backend="windows", supported=True, run_command=run).check_port(
        22368, protocol="tcp"
    )

    assert status.allowed
    assert calls == ["netsh.exe", "powershell.exe"]


def test_check_sanitizes_command_timeout_error(tmp_path):
    profile = _profile(tmp_path)

    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired(["powershell.exe", "-EncodedCommand", "secret"], 15)

    result = FirewallService(backend="windows", supported=True, run_command=run).check(profile)

    assert result.error == "Windows Firewall command timed out"
    assert "EncodedCommand" not in result.error


def test_check_reports_blocked_when_no_matching_allow_exists(tmp_path):
    profile = _profile(tmp_path)
    result = FirewallService(backend="windows", supported=True, run_command=_runner([])).check(profile)

    assert not result.allowed
    assert not result.blocked
    assert result.repairable


@pytest.mark.parametrize(
    "rule",
    [
        _rule(enabled="False", program="C:\\PalServer.exe"),
        _rule(direction="Outbound", program="C:\\PalServer.exe"),
        _rule(protocol="TCP", local_port="8211"),
    ],
)
def test_check_ignores_non_matching_rules(tmp_path, rule):
    profile = _profile(tmp_path)

    result = FirewallService(backend="windows", supported=True, run_command=_runner([rule])).check(profile)

    assert not result.allowed


def test_matching_block_rule_takes_precedence(tmp_path):
    profile = _profile(tmp_path)
    executable = str(Path(profile.executable).resolve())
    result = FirewallService(
        backend="windows",
        supported=True,
        run_command=_runner(
            [
                _rule(program=executable),
                _rule(name="third-party-block", action="Block", program=executable),
            ]
        ),
    ).check(profile)

    assert result.executable_allowed
    assert result.blocked
    assert not result.allowed
    assert result.external_block_rule_names == ("third-party-block",)
    assert result.repairable


def test_fix_creates_owned_program_rule_and_removes_owned_block(tmp_path):
    profile = _profile(tmp_path)
    captured = {}

    def elevated(payload):
        captured.update(payload)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    status = FirewallService(backend="windows", supported=True, run_command=_runner([])).check(profile)
    FirewallService(backend="windows", supported=True, elevated_runner=elevated).fix(profile, status)

    assert captured["executable"] == str(Path(profile.executable).resolve())
    assert captured["rule_name"] == program_rule_name(profile.name)
    assert captured["remove_names"] == []


def test_fix_removes_external_block_rule(tmp_path):
    profile = _profile(tmp_path)
    captured = {}

    def elevated(payload):
        captured.update(payload)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    service = FirewallService(
        backend="windows",
        supported=True,
        run_command=_runner([]),
        elevated_runner=elevated,
    )
    status = service.check(profile)
    status = status.__class__(
        **{**status.__dict__, "external_block_rule_names": ("third-party",), "port_blocked": True}
    )

    service.fix(profile, status)

    assert captured["remove_names"] == ["third-party"]


def test_windows_fix_suppresses_powershell_progress_clixml():
    script = _fix_script("C:\\PalServer.exe", "rule-name", "display", ())

    assert "$ProgressPreference = 'SilentlyContinue'" in script
    assert "Get-NetFirewallRule -Name 'rule-name'" in script


def test_windows_port_fix_replaces_existing_owned_rule():
    script = _fix_port_script(22368, "tcp", "Palsitter-Web-TCP-22368", "display", ())

    assert "Get-NetFirewallRule -Name 'Palsitter-Web-TCP-22368'" in script
    assert "Remove-NetFirewallRule -ErrorAction Stop" in script


def test_windows_fix_removes_netsh_rule_names_as_display_names():
    script = _fix_script("C:\\PalServer.exe", "rule-name", "display", ("Pal",))

    assert "Get-NetFirewallRule -Name $name" in script
    assert "Get-NetFirewallRule -DisplayName $name" in script


def test_fix_surfaces_elevated_helper_failure(tmp_path):
    profile = _profile(tmp_path)
    service = FirewallService(
        backend="windows",
        supported=True,
        run_command=_runner([]),
        elevated_runner=lambda payload: SimpleNamespace(
            returncode=1223, stdout="", stderr="User cancelled UAC"
        ),
    )
    status = service.check(profile)

    with pytest.raises(Exception, match="User cancelled UAC"):
        service.fix(profile, status)


def test_fix_includes_raw_stdout_stderr_and_exit_code(tmp_path):
    profile = _profile(tmp_path)
    logs = []
    service = FirewallService(
        backend="windows",
        supported=True,
        logger=logs.append,
        run_command=_runner([]),
        elevated_runner=lambda payload: SimpleNamespace(
            returncode=1,
            stdout="raw stdout reason",
            stderr="raw stderr reason",
        ),
    )
    status = service.check(profile)

    with pytest.raises(Exception) as error:
        service.fix(profile, status)

    message = str(error.value)
    assert "stderr: raw stderr reason" in message
    assert "stdout: raw stdout reason" in message
    assert "exit code: 1" in message
    assert logs == [
        "stderr: raw stderr reason",
        "stdout: raw stdout reason",
        "exit code: 1",
    ]


def test_windows_elevated_fix_collects_helper_output_without_redirect_parameters(
    monkeypatch,
):
    captured = {}

    def run(args, **kwargs):
        captured["script"] = base64.b64decode(args[-1]).decode("utf-16le")
        return subprocess.CompletedProcess(args, 1, stdout="outer", stderr="")

    monkeypatch.setattr(
        "module.games.palworld.firewall._read_elevated_result",
        lambda path: ("child stdout", "child stderr"),
    )
    service = FirewallService(
        backend="windows",
        supported=True,
        run_command=run,
    )

    result = service._run_elevated({})

    assert "-Verb RunAs" in captured["script"]
    assert "-RedirectStandardOutput" not in captured["script"]
    assert "-RedirectStandardError" not in captured["script"]
    assert "outer\nchild stdout" == result.stdout
    assert result.stderr == "child stderr"


def test_non_windows_status_is_unsupported(tmp_path):
    result = FirewallService(supported=False).check(_profile(tmp_path))

    assert not result.supported
    assert result.error is None
    assert port_rule_name("Test Server", 8211).startswith("Palsitter-Palworld-")


def test_iptables_checks_udp_port_and_does_not_claim_executable_match(tmp_path):
    profile = _profile(tmp_path)
    captured = {}

    def run(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="-P INPUT DROP\n-A INPUT -p udp -m udp --dport 8211 -j ACCEPT\n",
            stderr="",
        )

    result = FirewallService(supported=True, backend="iptables", run_command=run).check(profile)

    assert result.allowed
    assert result.port_allowed
    assert not result.executable_supported
    assert captured["args"] == ["iptables", "-S", "INPUT"]


def test_iptables_matching_block_rule_prevents_repair(tmp_path):
    profile = _profile(tmp_path)
    result = FirewallService(
        supported=True,
        backend="iptables",
        run_command=lambda args, **kwargs: subprocess.CompletedProcess(
            args,
            0,
            stdout="-A INPUT -p udp --dport 8211 -j DROP\n",
            stderr="",
        ),
    ).check(profile)

    assert result.blocked
    assert result.external_block_rule_names == ("(unnamed rule)",)
    assert result.repairable


def test_iptables_fix_removes_detected_external_block_rule(tmp_path):
    profile = _profile(tmp_path)
    captured = {}
    service = FirewallService(
        supported=True,
        backend="iptables",
        run_command=lambda args, **kwargs: subprocess.CompletedProcess(
            args,
            0,
            stdout="-A INPUT -p udp --dport 8211 -m comment --comment third-party -j REJECT\n",
            stderr="",
        ),
        elevated_runner=lambda payload: captured.update(payload)
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    service.fix(profile, service.check(profile))

    assert captured["remove_block_rules"] == [["reject", "third-party"]]


def test_ufw_checks_allow_and_deny_rules(tmp_path):
    profile = _profile(tmp_path)
    result = FirewallService(
        supported=True,
        backend="ufw",
        run_command=lambda args, **kwargs: subprocess.CompletedProcess(
            args,
            0,
            stdout=(
                "Status: active\n\n"
                "To                         Action      From\n"
                "--                         ------      ----\n"
                "8211/udp                   ALLOW       Anywhere\n"
                "8211/udp                   DENY        192.0.2.10\n"
            ),
            stderr="",
        ),
    ).check(profile)

    assert result.port_allowed
    assert result.blocked
    assert result.external_block_rule_names == ("(unnamed rule)",)
    assert result.repairable
    assert not result.allowed


def test_inactive_ufw_is_treated_as_open(tmp_path):
    profile = _profile(tmp_path)
    result = FirewallService(
        supported=True,
        backend="ufw",
        run_command=lambda args, **kwargs: subprocess.CompletedProcess(
            args, 0, stdout="Status: inactive\n", stderr=""
        ),
    ).check(profile)

    assert result.allowed
    assert result.port_allowed


def test_firewalld_checks_zone_ports_and_rich_rule_blocks(tmp_path):
    profile = _profile(tmp_path)

    def run(args, **kwargs):
        if args == ["firewall-cmd", "--get-active-zones"]:
            return subprocess.CompletedProcess(args, 0, stdout="public\n  interfaces: eth0\n", stderr="")
        if args[-1] == "--list-ports":
            return subprocess.CompletedProcess(args, 0, stdout="8211/udp 25565/tcp\n", stderr="")
        return subprocess.CompletedProcess(
            args,
            0,
            stdout='<rule family="ipv4"><port port="8211" protocol="udp"/><drop/></rule>\n',
            stderr="",
        )

    result = FirewallService(supported=True, backend="firewalld", run_command=run).check(profile)

    assert result.port_allowed
    assert result.blocked
    assert result.external_block_rule_names
    assert result.external_block_rule_specs == (
        ("public", '<rule family="ipv4"><port port="8211" protocol="udp"/><drop/></rule>'),
    )
    assert not result.allowed


def test_firewalld_permission_denial_is_preserved_for_ui_authentication(tmp_path):
    profile = _profile(tmp_path)

    def run(args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr="Authorization failed. Make sure polkit agent is running.",
        )

    with pytest.raises(FirewallPermissionDenied, match="Authorization failed"):
        FirewallService(
            supported=True, backend="firewalld", run_command=run
        ).check(profile)


def test_linux_fix_adds_only_the_configured_udp_port(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("module.games.palworld.firewall.os.geteuid", lambda: 0)
    service = FirewallService(supported=True, backend="iptables", run_command=run)
    status = service.check(profile)
    service.fix(profile, status)

    assert commands[0] == ["iptables", "-S", "INPUT"]
    assert commands[1][-4:] == ["--comment", port_rule_name(profile.name, 8211), "-j", "ACCEPT"]


def test_linux_firewalld_fix_adds_permanent_port_and_reloads(tmp_path):
    profile = _profile(tmp_path)
    captured = {}

    def elevated(payload):
        captured.update(payload)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    status = FirewallService(
        supported=True,
        backend="firewalld",
        run_command=lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
    ).check(profile)
    FirewallService(supported=True, backend="firewalld", elevated_runner=elevated).fix(profile, status)

    assert captured["backend"] == "firewalld"
    assert captured["port"] == 8211


def test_linux_firewalld_fix_removes_external_rich_rule(tmp_path):
    profile = _profile(tmp_path)
    captured = {}
    rich_rule = '<rule family="ipv4"><port port="8211" protocol="udp"/><drop/></rule>'

    def run(args, **kwargs):
        if args == ["firewall-cmd", "--get-active-zones"]:
            return subprocess.CompletedProcess(args, 0, stdout="public\n", stderr="")
        if args[-1] == "--list-ports":
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout=rich_rule + "\n", stderr="")

    def elevated(payload):
        captured.update(payload)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    service = FirewallService(
        supported=True,
        backend="firewalld",
        run_command=run,
        elevated_runner=elevated,
    )
    service.fix(profile, service.check(profile))

    assert captured["remove_rich_rules"] == [["public", rich_rule]]


def test_linux_sudo_password_is_sent_only_to_stdin_after_permission_denied(
    tmp_path, monkeypatch
):
    profile = _profile(tmp_path)
    calls = []

    monkeypatch.setattr("module.games.palworld.firewall.os.geteuid", lambda: 1000)
    monkeypatch.setattr(
        "module.games.palworld.firewall.shutil.which",
        lambda command: "/usr/bin/sudo" if command == "sudo" else None,
    )

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args == ["iptables", "-S", "INPUT"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if kwargs.get("input") == "root-secret\n":
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="sudo: a password is required")

    service = FirewallService(supported=True, backend="iptables", run_command=run)
    status = service.check(profile)

    with pytest.raises(FirewallPermissionDenied):
        service.fix(profile, status)

    service.fix(profile, status, root_password="root-secret")

    retry_args, retry_kwargs = calls[-1]
    assert retry_args[:4] == ["sudo", "-S", "-p", ""]
    assert "root-secret" not in retry_args
    assert retry_kwargs["input"] == "root-secret\n"
