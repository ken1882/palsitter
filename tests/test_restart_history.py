import datetime as dt
import json
import signal

import pytest

from module.games.palworld.server.history import (
    LifecycleEvent,
    RestartHistoryStore,
    TerminationInfo,
    WINDOWS_NTSTATUS,
    classify_launch_error,
    classify_process_exit,
)


@pytest.mark.parametrize("code, expected", WINDOWS_NTSTATUS.items())
def test_windows_ntstatus_codes_are_classified(code, expected):
    symbol, summary = expected

    info = classify_process_exit(code, platform="nt")

    assert info.kind == "windows_exception"
    assert info.normalized_code == f"0x{code:08X}"
    assert info.symbol == symbol
    assert info.summary_code == summary


def test_signed_windows_access_violation_is_normalized():
    info = classify_process_exit(-1073741819, platform="nt")

    assert info.normalized_code == "0xC0000005"
    assert info.symbol == "STATUS_ACCESS_VIOLATION"
    assert info.summary_code == "access_violation"


def test_unknown_windows_exit_retains_decimal_and_hex():
    info = classify_process_exit(3, platform="nt")

    assert info.kind == "exit_code"
    assert info.raw_exit_code == 3
    assert info.normalized_code == "0x00000003"
    assert info.summary_code == "unrecognized_exit_code"


def test_zero_exit_code_is_ignored():
    info = classify_process_exit(0, platform="nt", output=["final output"])

    assert info.kind == "unknown"
    assert info.raw_exit_code is None
    assert info.normalized_code is None
    assert info.summary_code == "unknown"
    assert info.diagnostic_output == ("final output",)


@pytest.mark.parametrize(
    ("signal_name", "summary"),
    [
        ("SIGSEGV", "segmentation_fault"),
        ("SIGABRT", "abort"),
        ("SIGKILL", "forcibly_killed"),
    ],
)
def test_posix_signals_are_classified(signal_name, summary):
    if not hasattr(signal, signal_name):
        pytest.skip(f"{signal_name} unavailable")
    number = int(getattr(signal, signal_name))

    info = classify_process_exit(-number, platform="posix")

    assert info.kind == "posix_signal"
    assert info.symbol == signal_name
    assert info.summary_code == summary


def test_unknown_posix_exit_is_not_guessed():
    info = classify_process_exit(73, platform="posix")

    assert info.kind == "exit_code"
    assert info.summary_code == "unrecognized_exit_code"


def test_unknown_posix_signal_retains_its_number():
    info = classify_process_exit(-999, platform="posix")

    assert info.kind == "posix_signal"
    assert info.symbol == "SIGNAL_999"
    assert info.normalized_code == "999"
    assert info.summary_code == "unknown_signal"


@pytest.mark.parametrize(
    ("error", "summary"),
    [
        (PermissionError(13, "denied", "PalServer.exe"), "permission_denied"),
        (FileNotFoundError(2, "missing", "PalServer.exe"), "file_not_found"),
        (OSError(8, "bad executable", "PalServer.exe"), "os_error"),
    ],
)
def test_launch_errors_retain_os_fields(error, summary):
    info = classify_launch_error(error, "PalServer.exe")

    assert info.kind == "launch_error"
    assert info.summary_code == summary
    assert info.os_error["errno"] == error.errno
    assert info.os_error["filename"] == "PalServer.exe"


def test_diagnostic_output_is_bounded():
    info = classify_process_exit(
        1,
        platform="posix",
        output=["", *(f"line-{index}" for index in range(7)), "x" * 600],
    )

    assert len(info.diagnostic_output) == 5
    assert info.diagnostic_output[0] == "line-3"
    assert len(info.diagnostic_output[-1]) == 500


def test_restart_history_is_atomic_bounded_and_persistent(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    store = RestartHistoryStore("test")
    replacements = []
    real_replace = __import__("os").replace
    monkeypatch.setattr(
        "module.games.palworld.server.history.os.replace",
        lambda source, target: (replacements.append((source, target)), real_replace(source, target))[1],
    )
    for index in range(22):
        store.append(
            LifecycleEvent(
                dt.datetime(2026, 1, 1, 0, index),
                "crash",
                "restarted",
                detail={"index": index},
                termination=classify_process_exit(index, platform="nt"),
            )
        )

    loaded = RestartHistoryStore("test").load()

    assert len(loaded) == 20
    assert loaded[0].detail["index"] == 2
    assert loaded[-1].detail["index"] == 21
    assert loaded[-1].termination.raw_exit_code == 21
    assert replacements
    assert not list(store.path.parent.glob("*.tmp"))


def test_schedule_events_are_not_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    store = RestartHistoryStore("test")

    store.append(LifecycleEvent(dt.datetime.now(), "planned_restart", "scheduled"))

    assert store.load() == ()


def test_persisted_zero_exit_code_is_ignored_on_load(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    store = RestartHistoryStore("test")

    store.append(
        LifecycleEvent(
            dt.datetime.now(),
            "crash",
            "restarted",
            termination=TerminationInfo(
                "exit_code",
                raw_exit_code=0,
                normalized_code="0x0",
                summary_code="unrecognized_exit_code",
            ),
        )
    )

    loaded = store.load()
    assert loaded[0].termination.kind == "unknown"
    assert loaded[0].termination.raw_exit_code is None
    assert loaded[0].termination.normalized_code is None


def test_malformed_history_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))
    store = RestartHistoryStore("test")
    store.path.parent.mkdir(parents=True)
    store.path.write_text(json.dumps({"version": 99, "events": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported"):
        store.load()
