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
    monkeypatch.setattr(session, "info", SimpleNamespace(server_host=server_host))
    assert session.is_local_browser_session() is expected
