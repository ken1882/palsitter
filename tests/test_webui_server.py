from types import SimpleNamespace

from module.webui import server


def test_run_server_also_listens_on_localhost_for_specific_bind_address(monkeypatch, tmp_path):
    listeners = []

    class FakeApplication:
        def __init__(self, *args, **kwargs):
            pass

        def listen(self, port, *, address, max_buffer_size):
            listeners.append((port, address, max_buffer_size))

    loop = SimpleNamespace(start=lambda: None)
    monkeypatch.setattr(server.tornado.ioloop.IOLoop, "current", lambda: loop)
    monkeypatch.setattr(server, "set_ioloop", lambda value: None)
    monkeypatch.setattr(server, "webio_handler", lambda *args, **kwargs: object)
    monkeypatch.setattr(server.tornado.web, "Application", FakeApplication)
    monkeypatch.setattr(server, "parse_file_size", lambda value: 123)
    monkeypatch.setenv("PALSITTER_LOG_DIR", str(tmp_path / "logs"))

    server.run_server(
        object(),
        host="192.168.1.5",
        port=22368,
        static_dir=tmp_path,
        auth=object(),
    )

    assert listeners == [
        (22368, "192.168.1.5", 123),
        (22368, "127.0.0.1", 123),
    ]


def test_run_server_does_not_duplicate_localhost_listener(monkeypatch, tmp_path):
    listeners = []

    class FakeApplication:
        def __init__(self, *args, **kwargs):
            pass

        def listen(self, port, *, address, max_buffer_size):
            listeners.append(address)

    loop = SimpleNamespace(start=lambda: None)
    monkeypatch.setattr(server.tornado.ioloop.IOLoop, "current", lambda: loop)
    monkeypatch.setattr(server, "set_ioloop", lambda value: None)
    monkeypatch.setattr(server, "webio_handler", lambda *args, **kwargs: object)
    monkeypatch.setattr(server.tornado.web, "Application", FakeApplication)
    monkeypatch.setattr(server, "parse_file_size", lambda value: 123)
    monkeypatch.setenv("PALSITTER_LOG_DIR", str(tmp_path / "logs"))

    for host in ("127.0.0.1", "0.0.0.0"):
        listeners.clear()
        server.run_server(object(), host=host, port=22368, static_dir=tmp_path, auth=object())
        assert listeners == [host]
