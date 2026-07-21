import pytest

from module.games.palworld.webui import overview
from module.games.palworld.webui.overview import _log_type


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("12:34:56 Supervisor started", "palsitter"),
        ("12:34:56 PalServer: server output", "palserver"),
        ("12:34:56 SteamCMD: download output", "steamcmd"),
        ("12:34:56 UE4SS: mod output", "ue4ss"),
        ("12:34:56 [default] PalServer: supervised output", "palserver"),
        ("12:34:56 [default] SteamCMD: supervised output", "steamcmd"),
        ("12:34:56 [default] UE4SS: supervised output", "ue4ss"),
        ("12:34:56 UnknownSource: output", "palsitter"),
        ("message containing UE4SS: later", "palsitter"),
    ],
)
def test_overview_log_type_classification(line, expected):
    assert _log_type(line) == expected


def test_automatic_attach_adopts_managed_runtime(monkeypatch):
    class FakeManager:
        active = False
        operation_busy = False

        def __init__(self):
            self.start_calls = []

        def start(self, **kwargs):
            self.start_calls.append(kwargs)

    manager = FakeManager()
    monkeypatch.setattr(overview, "_manager", lambda name: manager)
    monkeypatch.setattr(overview, "load_profile", lambda name: object())
    monkeypatch.setattr(overview, "instance_is_running", lambda profile: True)
    monkeypatch.setattr(overview, "load_runtime", lambda name: {"ownership": "managed"})

    overview._auto_attach_running_server("palworld")

    assert manager.start_calls == [
        {
            "update": False,
            "reason": "automatic attach",
            "adopt_managed": True,
        }
    ]
