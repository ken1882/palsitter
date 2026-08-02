from types import SimpleNamespace

import pytest

from module.webui import session


@pytest.mark.parametrize(
    ("server_host", "expected"),
    [
        ("127.0.0.1:22368", True),
        ("localhost:22368", True),
        ("192.168.1.5:22368", False),
        ("0.0.0.0:22368", False),
    ],
)
def test_is_local_browser_session(server_host, expected, monkeypatch):
    monkeypatch.delenv("PALSITTER_DESKTOP_TOKEN", raising=False)
    monkeypatch.setattr(session, "info", SimpleNamespace(server_host=server_host))
    assert session.is_local_browser_session() is expected


def test_desktop_token_marks_nonlocalhost_browser_session_as_local(monkeypatch):
    token = "desktop-session-token"
    monkeypatch.setenv("PALSITTER_DESKTOP_TOKEN", token)
    monkeypatch.setattr(
        session,
        "info",
        SimpleNamespace(
            server_host="192.168.1.5:22368",
            request=SimpleNamespace(
                headers={},
                cookies={
                    session.DESKTOP_SESSION_COOKIE: SimpleNamespace(value=token)
                },
            ),
        ),
    )

    assert session.is_local_browser_session() is True


def test_invalid_desktop_token_does_not_mark_remote_browser_session_as_local(monkeypatch):
    monkeypatch.setenv("PALSITTER_DESKTOP_TOKEN", "expected-token")
    monkeypatch.setattr(
        session,
        "info",
        SimpleNamespace(
            server_host="192.168.1.5:22368",
            request=SimpleNamespace(
                headers={},
                cookies={
                    session.DESKTOP_SESSION_COOKIE: SimpleNamespace(value="wrong-token")
                },
            ),
        ),
    )

    assert session.is_local_browser_session() is False
