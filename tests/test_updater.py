from __future__ import annotations

import subprocess
from types import SimpleNamespace

from module.webui.pages import updater


def test_run_git_disables_interactive_credentials(monkeypatch):
    captured = {}

    def run_git(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run_git)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "credential.helper")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "store")
    monkeypatch.setenv("GIT_CONFIG_SOMETHING_ELSE", "inherited")
    monkeypatch.setenv("GIT_ASKPASS", "askpass")
    monkeypatch.setenv("SSH_ASKPASS", "ssh-askpass")
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i private-key")

    updater._run_git("fetch", "origin", "main")

    assert captured["args"] == [
        "git",
        "-c",
        "credential.helper=",
        "-c",
        "http.extraHeader=",
        "-c",
        "http.https://github.com/.extraHeader=",
        "fetch",
        "origin",
        "main",
    ]
    env = captured["kwargs"]["env"]
    assert {
        key for key in env if key.startswith("GIT_CONFIG_")
    } == {"GIT_CONFIG_NOSYSTEM"}
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "Never"
    assert "GIT_ASKPASS" not in env
    assert "SSH_ASKPASS" not in env
    assert "GIT_SSH_COMMAND" not in env


def test_run_git_works_with_a_credentialless_git_executable(tmp_path, monkeypatch):
    git = tmp_path / "git"
    git.write_text(
        "#!/bin/sh\n"
        "test \"$1\" = -c && test \"$2\" = 'credential.helper='\n"
        "test \"$3\" = -c && test \"$4\" = 'http.extraHeader='\n"
        "test \"$5\" = -c && test \"$6\" = 'http.https://github.com/.extraHeader='\n"
        "test \"$7\" = version\n",
        encoding="ascii",
    )
    git.chmod(0o755)
    monkeypatch.setenv("PALSITTER_GIT", str(git))

    result = updater._run_git("version")

    assert result.returncode == 0


def test_pull_update_uses_fast_forward_only(monkeypatch):
    calls = []

    def run_git(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(updater, "_run_git", run_git)

    assert updater._pull_update() is True
    assert calls == [
        (("pull", "--ff-only", "origin", "main"), {"timeout": 120})
    ]


def test_pull_update_failure_does_not_report_success(monkeypatch):
    diagnostics = []
    monkeypatch.setattr(
        updater,
        "_run_git",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stderr="fatal: public repository unavailable",
        ),
    )

    assert updater._pull_update(on_error=diagnostics.append) is False
    assert diagnostics == ["fatal: public repository unavailable"]
