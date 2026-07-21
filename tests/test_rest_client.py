import pytest

from module.config import Profile
from module.server import PalRestClient, RestError


@pytest.fixture(autouse=True)
def _available_rest(monkeypatch):
    monkeypatch.setattr(
        "module.games.palworld.server.status.rest_is_available",
        lambda profile: True,
    )


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body
        self.content = b"x" if body is not None else b""
        self.text = str(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


def test_rest_client_blocks_http_when_server_or_rest_is_unavailable():
    session = FakeSession(FakeResponse(body={"ok": True}))
    client = PalRestClient(
        Profile(name="test"),
        session=session,
        availability_probe=lambda profile: False,
    )

    with pytest.raises(RestError, match="not running or REST API is unavailable"):
        client.metrics()

    assert session.calls == []


def test_rest_client_uses_basic_auth_and_shutdown_body():
    profile = Profile(name="test", rest_host="127.0.0.1", rest_port=1234, rest_username="u", rest_password="p")
    session = FakeSession(FakeResponse(body={"ok": True}))

    PalRestClient(profile, session=session).shutdown()

    assert [call[1].rsplit("/", 1)[-1] for call in session.calls] == ["save", "shutdown"]
    method, url, kwargs = session.calls[1]
    assert method == "POST"
    assert url == "http://127.0.0.1:1234/v1/api/shutdown"
    assert kwargs["auth"] == ("u", "p")
    assert kwargs["json"] == {
        "waittime": 5,
        "message": "Server will shutdown immediately",
    }


def test_rest_client_shutdown_accepts_waittime_and_message():
    profile = Profile(name="test", rest_host="127.0.0.1", rest_port=1234)
    session = FakeSession(FakeResponse(body={"ok": True}))

    PalRestClient(profile, session=session).shutdown(waittime=30, message="Maintenance")

    assert [call[1].rsplit("/", 1)[-1] for call in session.calls] == ["save", "shutdown"]
    assert session.calls[1][2]["json"] == {
        "waittime": 30,
        "message": "Maintenance",
    }


def test_rest_client_raises_visible_error_on_http_failure():
    session = FakeSession(FakeResponse(status_code=401, body={"error": "no"}))

    with pytest.raises(RestError):
        PalRestClient(Profile(name="test"), session=session).metrics()


def test_rest_client_kick_sends_userid_and_message():
    profile = Profile(name="test", rest_host="127.0.0.1", rest_port=1234, rest_username="u", rest_password="p")
    session = FakeSession(FakeResponse(body={"ok": True}))

    PalRestClient(profile, session=session).kick("steam_1", "bye")

    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "http://127.0.0.1:1234/v1/api/kick"
    assert kwargs["auth"] == ("u", "p")
    assert kwargs["json"] == {"userid": "steam_1", "message": "bye"}


def test_rest_client_ban_sends_userid_and_message():
    profile = Profile(name="test", rest_host="127.0.0.1", rest_port=1234, rest_username="u", rest_password="p")
    session = FakeSession(FakeResponse(body={"ok": True}))

    PalRestClient(profile, session=session).ban("steam_1", "no")

    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "http://127.0.0.1:1234/v1/api/ban"
    assert kwargs["json"] == {"userid": "steam_1", "message": "no"}


def test_rest_client_unban_sends_userid_only():
    profile = Profile(name="test", rest_host="127.0.0.1", rest_port=1234, rest_username="u", rest_password="p")
    session = FakeSession(FakeResponse(body={"ok": True}))

    PalRestClient(profile, session=session).unban("steam_1")

    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "http://127.0.0.1:1234/v1/api/unban"
    assert kwargs["json"] == {"userid": "steam_1"}


def test_rest_client_kick_raises_visible_error_on_http_failure():
    session = FakeSession(FakeResponse(status_code=401, body={"error": "no"}))

    with pytest.raises(RestError):
        PalRestClient(Profile(name="test"), session=session).kick("steam_1")


@pytest.mark.parametrize(
    ("method_name", "path", "body"),
    [
        ("info", "info", {"version": "v1"}),
        ("players", "players", {"players": [{"name": "Alice", "ping": 20}]}),
        ("settings", "settings", {"Difficulty": "Normal"}),
        ("metrics", "metrics", {"currentplayernum": 1, "serverfps": 60}),
    ],
)
def test_rest_get_methods_parse_success_and_use_basic_auth(method_name, path, body):
    profile = Profile(
        name="test",
        rest_host="127.0.0.1",
        rest_port=1234,
        rest_username="u",
        rest_password="p",
    )
    session = FakeSession(FakeResponse(body=body))

    assert getattr(PalRestClient(profile, session=session), method_name)() == body

    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url == f"http://127.0.0.1:1234/v1/api/{path}"
    assert kwargs["auth"] == ("u", "p")
    assert kwargs["timeout"] == 5


@pytest.mark.parametrize(
    ("method_name", "args", "path", "json_body"),
    [
        ("announce", ("hello",), "announce", {"message": "hello"}),
        ("save", (), "save", None),
        ("stop", (), "stop", None),
    ],
)
def test_rest_administration_methods_send_official_requests(
    method_name, args, path, json_body
):
    profile = Profile(
        name="test",
        rest_host="127.0.0.1",
        rest_port=1234,
        rest_username="u",
        rest_password="p",
    )
    session = FakeSession(FakeResponse(body={"ok": True}))

    getattr(PalRestClient(profile, session=session), method_name)(*args)

    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == f"http://127.0.0.1:1234/v1/api/{path}"
    assert kwargs["auth"] == ("u", "p")
    if json_body is None:
        assert "json" not in kwargs
    else:
        assert kwargs["json"] == json_body
