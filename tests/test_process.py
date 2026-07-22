from types import SimpleNamespace

from module import process


def test_pids_listening_on_filters_port_and_tcp_state(monkeypatch):
    connections = [
        SimpleNamespace(
            laddr=SimpleNamespace(port=22368),
            status=process.psutil.CONN_LISTEN,
            pid=10,
        ),
        SimpleNamespace(
            laddr=SimpleNamespace(port=22368),
            status=process.psutil.CONN_ESTABLISHED,
            pid=11,
        ),
        SimpleNamespace(
            laddr=SimpleNamespace(port=22369),
            status=process.psutil.CONN_LISTEN,
            pid=12,
        ),
    ]
    monkeypatch.setattr(process.psutil, "net_connections", lambda kind: connections)

    assert process.pids_listening_on(22368) == {10}


def test_kill_by_port_uses_netstat_fallback(monkeypatch):
    killed = []
    monkeypatch.setattr(process, "pids_listening_on", lambda port, proto="tcp": set())
    monkeypatch.setattr(process.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        process.subprocess,
        "check_output",
        lambda *args, **kwargs: "TCP 127.0.0.1:22368 0.0.0.0:0 LISTENING 42\n",
    )
    monkeypatch.setattr(process, "kill_process_tree", lambda pid, grace: killed.append((pid, grace)))

    assert process.kill_by_port(22368) == [42]
    assert killed == [(42, 5.0)]
