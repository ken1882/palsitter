import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from module.games.palworld.config import PalworldProfile
from module.games.palworld.firewall import (
    FirewallRepairUnavailable,
    FirewallService,
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


def test_check_accepts_matching_executable_rule(tmp_path):
    profile = _profile(tmp_path)
    service = FirewallService(
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
        supported=True,
        run_command=_runner([_rule(program=str(console).upper())]),
    )

    result = service.check(profile)

    assert result.allowed
    assert result.executable_allowed


def test_check_accepts_matching_udp_port_rule(tmp_path):
    profile = _profile(tmp_path)
    service = FirewallService(
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

    result = FirewallService(supported=True, run_command=run).check(profile)

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


def test_check_sanitizes_command_timeout_error(tmp_path):
    profile = _profile(tmp_path)

    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired(["powershell.exe", "-EncodedCommand", "secret"], 15)

    result = FirewallService(supported=True, run_command=run).check(profile)

    assert result.error == "Windows Firewall command timed out"
    assert "EncodedCommand" not in result.error


def test_check_reports_blocked_when_no_matching_allow_exists(tmp_path):
    profile = _profile(tmp_path)
    result = FirewallService(supported=True, run_command=_runner([])).check(profile)

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

    result = FirewallService(supported=True, run_command=_runner([rule])).check(profile)

    assert not result.allowed


def test_matching_block_rule_takes_precedence(tmp_path):
    profile = _profile(tmp_path)
    executable = str(Path(profile.executable).resolve())
    result = FirewallService(
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
    assert not result.repairable


def test_fix_creates_owned_program_rule_and_removes_owned_block(tmp_path):
    profile = _profile(tmp_path)
    captured = {}

    def elevated(payload):
        captured.update(payload)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    status = FirewallService(supported=True, run_command=_runner([])).check(profile)
    FirewallService(supported=True, elevated_runner=elevated).fix(profile, status)

    assert captured["executable"] == str(Path(profile.executable).resolve())
    assert captured["rule_name"] == program_rule_name(profile.name)
    assert captured["remove_names"] == []


def test_fix_rejects_external_block_rule(tmp_path):
    profile = _profile(tmp_path)
    service = FirewallService(supported=True, run_command=_runner([]))
    status = service.check(profile)
    status = status.__class__(
        **{**status.__dict__, "external_block_rule_names": ("third-party",), "port_blocked": True}
    )

    with pytest.raises(FirewallRepairUnavailable):
        service.fix(profile, status)


def test_fix_surfaces_elevated_helper_failure(tmp_path):
    profile = _profile(tmp_path)
    service = FirewallService(
        supported=True,
        run_command=_runner([]),
        elevated_runner=lambda payload: SimpleNamespace(
            returncode=1223, stdout="", stderr="User cancelled UAC"
        ),
    )
    status = service.check(profile)

    with pytest.raises(Exception, match="User cancelled UAC"):
        service.fix(profile, status)


def test_non_windows_status_is_unsupported(tmp_path):
    result = FirewallService(supported=False).check(_profile(tmp_path))

    assert not result.supported
    assert result.error is None
    assert port_rule_name("Test Server", 8211).startswith("Palsitter-Palworld-")
