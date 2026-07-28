import base64
import json
import subprocess
from types import SimpleNamespace

from module.debug_log import debug_log_path, log_command_result
from module.firewall import FirewallService
from module.webui.auth import WebAuth
from module.webui.global_audit import GlobalAuditEvent, GlobalAuditStore
from module.webui.settings import (
    DEFAULT_BIND_ADDRESS,
    WebUISettings,
    hash_password,
    interface_options,
    load_web_settings,
    resolve_bind_address,
    save_web_settings,
    verify_password,
)


def test_web_settings_defaults_and_preserves_existing_ui_settings(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(config_dir))
    assert load_web_settings().bind_address == DEFAULT_BIND_ADDRESS
    assert load_web_settings().auto_update is False
    assert load_web_settings().debug_mode is False

    config_dir.joinpath("webui").mkdir(parents=True)
    config_dir.joinpath("webui", "settings.json").write_text(
        json.dumps({"language": "en-US", "theme": "light"}), encoding="utf-8"
    )
    salt, digest = hash_password("secret")
    save_web_settings(
        WebUISettings(
            bind_address="192.168.1.5",
            auto_update=True,
            debug_mode=True,
            auth_enabled=True,
            auth_username="admin",
            auth_salt=salt,
            auth_password_hash=digest,
        )
    )
    data = json.loads(config_dir.joinpath("webui", "settings.json").read_text())
    assert data["language"] == "en-US"
    assert data["theme"] == "light"
    assert load_web_settings().auth_enabled is True
    assert load_web_settings().auto_update is True
    assert load_web_settings().debug_mode is True
    assert verify_password("secret", salt, digest)
    assert not verify_password("wrong", salt, digest)


def test_debug_command_log_requires_debug_mode(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(config_dir))

    log_command_result(
        "updater-git", ["git", "status"], returncode=0, stdout="clean\n"
    )
    assert not (config_dir / "webui" / "debug").exists()

    save_web_settings(WebUISettings(debug_mode=True))
    log_command_result(
        "updater-git", ["git", "status"], returncode=0, stdout="clean\n"
    )

    output = debug_log_path("updater-git").read_text(encoding="utf-8")
    assert "command: git status" in output
    assert "stdout: clean" in output
    assert "exit code: 0" in output


def test_interface_options_include_required_addresses_and_saved_missing_address(monkeypatch):
    monkeypatch.setattr(
        "module.webui.settings.psutil.net_if_addrs",
        lambda: {
            "Ethernet": [SimpleNamespace(family=2, address="192.168.1.5")],
        },
    )
    values = interface_options("10.0.0.5")
    assert [item["value"] for item in values[:2]] == ["127.0.0.1", "0.0.0.0"]
    assert values[-1] == {"label": "Saved address — 10.0.0.5", "value": "10.0.0.5"}


def test_bind_precedence_is_cli_then_environment_then_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    save_web_settings(WebUISettings(bind_address="192.168.1.5"))
    assert resolve_bind_address() == "192.168.1.5"
    monkeypatch.setenv("PALSITTER_HOST", "10.0.0.5")
    assert resolve_bind_address() == "10.0.0.5"
    assert resolve_bind_address("127.0.0.1") == "127.0.0.1"


def test_web_auth_audits_basic_success_and_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    salt, digest = hash_password("secret")
    auth = WebAuth(
        WebUISettings(
            auth_enabled=True,
            auth_username="admin",
            auth_salt=salt,
            auth_password_hash=digest,
        )
    )
    request = SimpleNamespace(
        remote_ip="192.168.1.20",
        headers={
            "Authorization": "Basic "
            + base64.b64encode(b"admin:secret").decode("ascii")
        },
    )
    assert auth.authorize(request).allowed
    request.headers["Authorization"] = "Basic " + base64.b64encode(b"admin:wrong").decode("ascii")
    assert not auth.authorize(request).allowed
    events = GlobalAuditStore().load()
    assert [event.type for event in events] == ["web_login_failure", "web_login_success"]
    assert all(event.source_ip == "192.168.1.20" for event in events)


def test_global_audit_is_monthly_and_deduplicated(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    from datetime import datetime, timezone

    event = GlobalAuditEvent(datetime(2026, 1, 2, tzinfo=timezone.utc), "web_login_success")
    store = GlobalAuditStore()
    store.append(event)
    store.append(event)
    assert store.load() == (event,)


def test_generic_web_firewall_check_uses_tcp_rules():
    payload = json.dumps(
        {
            "Name": "Palsitter Web",
            "Enabled": "True",
            "Direction": "In",
            "Action": "Allow",
            "Protocol": "TCP",
            "LocalPort": "22368",
        }
    )

    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=f"[{payload}]", stderr="")

    status = FirewallService(backend="windows", supported=True, run_command=run).check_port(22368)
    assert status.allowed is True
    assert status.protocol == "tcp"
