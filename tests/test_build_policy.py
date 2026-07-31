from module.webui import build_policy


def test_self_update_policy_honors_distribution_marker(tmp_path, monkeypatch):
    policy_path = tmp_path / "module" / "webui" / "build_policy.py"
    policy_path.parent.mkdir(parents=True)
    monkeypatch.setattr(build_policy, "__file__", str(policy_path))
    monkeypatch.delenv("PALSITTER_SELF_UPDATE", raising=False)

    assert build_policy.self_update_available() is True
    (tmp_path / build_policy.NO_UPDATE_MARKER).touch()
    assert build_policy.self_update_available() is False
    monkeypatch.setenv("PALSITTER_SELF_UPDATE", "1")
    assert build_policy.self_update_available() is False


def test_self_update_policy_supports_process_override(monkeypatch):
    monkeypatch.setenv("PALSITTER_SELF_UPDATE", "0")
    assert build_policy.self_update_available() is False
    monkeypatch.setenv("PALSITTER_SELF_UPDATE", "1")
    assert build_policy.self_update_available() is True
