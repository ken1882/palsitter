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

    updater._run_git("fetch", "origin", "main")

    env = captured["kwargs"]["env"]
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "credential.helper"
    assert env["GIT_CONFIG_VALUE_0"] == ""
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "Never"


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
    monkeypatch.setattr(
        updater,
        "_run_git",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )

    assert updater._pull_update() is False
