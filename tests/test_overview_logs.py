from types import SimpleNamespace

import pytest

from module.games import OperationProgress
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


def test_scheduler_update_operation_can_be_stopped_during_steamcmd_download():
    manager = SimpleNamespace(
        operation_busy=True,
        operation_progress=OperationProgress("update", "downloading", 20.0),
        state="updating",
    )

    assert overview._can_stop_update(manager)


def test_low_disk_warning_waits_for_confirmation(monkeypatch):
    class FakeManager:
        def __init__(self):
            self.start_calls = 0

        def start(self):
            self.start_calls += 1

    class FakePopup:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    manager = FakeManager()
    buttons = []
    monkeypatch.setattr(overview, "_manager", lambda name: manager)
    monkeypatch.setattr(overview, "fixed_palserver_dir", lambda name: overview.Path("C:/server"))
    monkeypatch.setattr(
        overview,
        "_available_disk_space",
        lambda path: overview.LOW_DISK_SPACE_BYTES - 1,
    )
    monkeypatch.setattr(overview, "popup", lambda *args, **kwargs: FakePopup())
    monkeypatch.setattr(overview, "close_popup", lambda: None)
    monkeypatch.setattr(overview, "toast", lambda *args, **kwargs: None)
    monkeypatch.setattr(overview, "put_warning", lambda *args, **kwargs: None)
    monkeypatch.setattr(overview, "put_row", lambda *args, **kwargs: None)
    monkeypatch.setattr(overview, "_update_scheduler_controls", lambda name: None)
    monkeypatch.setattr(overview, "_status_code", lambda name: 0)
    monkeypatch.setattr(overview, "_set_status", lambda state: None)
    monkeypatch.setattr(
        overview,
        "put_button",
        lambda label, **kwargs: buttons.append((label, kwargs)) or buttons[-1],
    )

    overview._confirm_low_disk_start("default")

    assert manager.start_calls == 0
    continue_button = next(
        button for button in buttons if button[0] == "Continue"
    )
    continue_button[1]["onclick"]()
    assert manager.start_calls == 1
