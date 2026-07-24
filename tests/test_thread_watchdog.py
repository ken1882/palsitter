import threading
import time

from module.thread_watchdog import ThreadWatchdog


def _wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached")


def test_thread_watchdog_restarts_after_target_failure():
    attempts = []
    started = threading.Event()
    errors = []

    def target():
        attempts.append(len(attempts) + 1)
        started.set()
        if len(attempts) == 1:
            raise RuntimeError("temporary failure")

    watchdog = ThreadWatchdog(
        target,
        name="test worker",
        restart_delay=0,
        logger=errors.append,
    )
    watchdog.start()

    assert started.wait(timeout=1)
    _wait_for(lambda: len(attempts) >= 2)
    watchdog.stop(timeout=1)

    assert errors[0] == "test worker failed; restarting: temporary failure"


def test_thread_watchdog_stops_when_health_check_is_false():
    active = threading.Event()
    started = threading.Event()
    runs = []

    watchdog = ThreadWatchdog(
        lambda: (runs.append(True), started.set()),
        name="test worker",
        should_run=active.is_set,
        restart_delay=0.01,
    )
    watchdog.start()
    thread = watchdog.thread
    assert thread is not None
    thread.join(timeout=1)
    assert runs == []
    assert not watchdog.alive

    active.set()
    watchdog.start()
    assert started.wait(timeout=1)
    watchdog.stop(timeout=1)
    run_count = len(runs)
    time.sleep(0.05)
    assert len(runs) == run_count
