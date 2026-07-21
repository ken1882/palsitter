from pathlib import Path
import threading
import time

from module.config import Profile
from module.games.palworld.config import fixed_executable_path, fixed_palserver_dir
from module.games.palworld.update import (
    PalworldUpdateService,
    appmanifest_path,
    parse_installed_build_id,
    parse_public_build_id,
    parse_steamcmd_progress,
)


class FakeProcess:
    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self.returncode = returncode

    def poll(self):
        return self.returncode


def _profile(tmp_path):
    steamcmd = tmp_path / "steamcmd" / "steamcmd.exe"
    executable = tmp_path / "steamcmd" / "steamapps" / "common" / "PalServer" / "PalServer.exe"
    return Profile(
        name="test",
        workdir=str(executable.parent),
        executable=str(executable),
        steamcmd=str(steamcmd),
    )


def _prepare_install(profile, build_id="100"):
    Path(profile.steamcmd).parent.mkdir(parents=True, exist_ok=True)
    Path(profile.steamcmd).write_text("stub", encoding="utf-8")
    Path(profile.executable).parent.mkdir(parents=True, exist_ok=True)
    Path(profile.executable).write_text("stub", encoding="utf-8")
    manifest = appmanifest_path(profile)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(f'"AppState"\n{{\n\t"buildid"\t\t"{build_id}"\n}}\n', encoding="utf-8")


def test_build_id_and_progress_parsers_select_public_branch():
    output = (
        '"branches"\n{\n'
        '\t"public"\n\t{\n\t\t"buildid"\t\t"12345"\n\t}\n'
        '\t"beta"\n\t{\n\t\t"buildid"\t\t"99999"\n\t}\n'
        '}\n'
    )

    assert parse_installed_build_id('"buildid" "100"') == "100"
    assert parse_public_build_id(output) == "12345"
    assert parse_steamcmd_progress(
        "Update state (0x61) downloading, progress: 14.79 (1 / 2)"
    ) == ("downloading", 14.79)


def test_public_build_parser_accepts_nested_and_compact_vdf_blocks():
    output = (
        '"branches" { "beta" { "buildid" "99999" } '
        '"public" { "metadata" { "timeupdated" "1" } "buildid" "12345" } }'
    )

    assert parse_public_build_id(output) == "12345"


def test_check_update_compares_manifest_with_public_build_and_emits_progress(tmp_path):
    profile = _profile(tmp_path)
    _prepare_install(profile, "100")
    captured = {}
    events = []
    logs = []

    def factory(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs["cwd"]
        return FakeProcess(
            [
                '"branches"\n',
                '{\n',
                '\t"public"\n',
                '\t{\n',
                '\t\t"buildid"\t\t"200"\n',
                '\t}\n',
                '}\n',
            ]
        )

    info = PalworldUpdateService(
        profile,
        logger=logs.append,
        progress=events.append,
        pty_process_factory=factory,
    ).check_update(force=True)

    assert info.installed_build_id == "100"
    assert info.available_build_id == "200"
    assert info.status == "update_available"
    assert captured["cmd"][1:] == [
        "+login",
        "anonymous",
        "+app_info_update",
        "1",
        "+app_info_print",
        "2394010",
        "+quit",
    ]
    assert captured["cwd"] == str(Path(profile.steamcmd).resolve().parent)
    assert events[-1].phase == "complete"
    assert any("SteamCMD:" in message for message in logs)


def test_appmanifest_path_uses_the_forced_server_install_directory(tmp_path):
    profile = _profile(tmp_path)

    assert appmanifest_path(profile) == (
        Path(profile.workdir).resolve() / "steamapps" / "appmanifest_2394010.acf"
    )


def test_check_update_uses_six_hour_cache(tmp_path):
    profile = _profile(tmp_path)
    _prepare_install(profile, "100")
    calls = []

    def factory(cmd, **kwargs):
        calls.append(cmd)
        return FakeProcess(['"public"\n{\n"buildid" "100"\n}\n'])

    service = PalworldUpdateService(
        profile,
        logger=lambda message: None,
        pty_process_factory=factory,
    )

    first = service.check_update(force=False)
    second = service.check_update(force=False)

    assert first.status == "up_to_date"
    assert second == first
    assert len(calls) == 1


def test_forced_check_bypasses_cached_update_information(tmp_path):
    profile = _profile(tmp_path)
    _prepare_install(profile, "100")
    calls = []
    outputs = iter([
        ['"public"\n{\n"buildid" "100"\n}\n'],
        ['"public"\n{\n"buildid" "200"\n}\n'],
    ])

    def factory(cmd, **kwargs):
        calls.append(cmd)
        return FakeProcess(next(outputs))

    service = PalworldUpdateService(
        profile,
        logger=lambda message: None,
        pty_process_factory=factory,
    )

    assert service.check_update(force=False).status == "up_to_date"
    forced = service.check_update(force=True)

    assert forced.status == "update_available"
    assert forced.available_build_id == "200"
    assert len(calls) == 2


def test_update_checks_are_serialized_across_instances(tmp_path):
    profiles = [_profile(tmp_path / "one"), _profile(tmp_path / "two")]
    for profile in profiles:
        _prepare_install(profile, "100")
    lock = threading.Lock()
    active = 0
    max_active = 0

    def factory(cmd, **kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.1)
        with lock:
            active -= 1
        return FakeProcess(['"public"\n{\n"buildid" "100"\n}\n'])

    services = [
        PalworldUpdateService(
            profile,
            logger=lambda message: None,
            pty_process_factory=factory,
        )
        for profile in profiles
    ]
    threads = [
        threading.Thread(target=lambda service=service: service.check_update(force=True))
        for service in services
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert max_active == 1


def test_check_update_failure_returns_unknown_instead_of_disabling_manual_update(tmp_path):
    profile = _profile(tmp_path)
    _prepare_install(profile, "100")
    events = []

    info = PalworldUpdateService(
        profile,
        logger=lambda message: None,
        progress=events.append,
        pty_process_factory=lambda *args, **kwargs: FakeProcess(["network failed\n"], 7),
    ).check_update(force=True)

    assert info.status == "unknown"
    assert info.installed_build_id == "100"
    assert events[-1].phase == "failed"
    assert "failed (7)" in events[-1].error


def test_install_or_update_builds_validate_command_and_reports_structured_progress(tmp_path):
    profile = _profile(tmp_path)
    Path(profile.steamcmd).parent.mkdir(parents=True)
    Path(profile.steamcmd).write_text("stub", encoding="utf-8")
    captured = {}
    events = []

    def factory(cmd, **kwargs):
        captured["cmd"] = cmd
        Path(profile.executable).parent.mkdir(parents=True, exist_ok=True)
        Path(profile.executable).write_text("stub", encoding="utf-8")
        manifest = appmanifest_path(profile)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text('"buildid" "300"', encoding="utf-8")
        return FakeProcess(
            ["Update state (0x61) downloading, progress: 42.50 (1 / 2)\r", "Success\n"]
        )

    info = PalworldUpdateService(
        profile,
        logger=lambda message: None,
        progress=events.append,
        pty_process_factory=factory,
    ).install_or_update(validate=True)

    assert captured["cmd"][1:] == [
        "+force_install_dir",
        str(Path(profile.workdir).resolve()),
        "+login",
        "anonymous",
        "+app_update",
        "2394010",
        "validate",
        "+quit",
    ]
    assert info.installed_build_id == "300"
    assert any(event.phase == "downloading" and event.percent == 42.5 for event in events)
    assert events[-1].phase == "complete"


def test_linux_install_or_update_validates_linux_server_executable(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("module.games.palworld.config.WINDOWS", False)
    monkeypatch.setattr("module.steamcmd.WINDOWS", False)
    profile = Profile(name="linux")
    profile.apply_fixed_paths()
    Path(profile.steamcmd).parent.mkdir(parents=True, exist_ok=True)
    Path(profile.steamcmd).write_text("stub", encoding="utf-8")
    captured = {}

    def factory(cmd, **kwargs):
        captured["cmd"] = cmd
        executable = fixed_executable_path("linux")
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text("stub", encoding="utf-8")
        return FakeProcess(["Success\n"])

    PalworldUpdateService(
        profile,
        logger=lambda message: None,
        pty_process_factory=factory,
    ).install_or_update()

    assert Path(profile.executable) == (
        fixed_palserver_dir("linux")
        / "Pal"
        / "Binaries"
        / "Linux"
        / "PalServer-Linux-Shipping"
    )
    assert captured["cmd"][1:4] == [
        "+@sSteamCmdForcePlatformType",
        "linux",
        "+force_install_dir",
    ]
