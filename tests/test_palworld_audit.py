import datetime as dt
import json

import pytest

from module.games.palworld.audit import (
    AuditEvent,
    AuditStore,
    audit_path,
    format_palserver_log_line,
    parse_palserver_audit_line,
)
from module.webui.pagination_table import TableColumn, _validate_columns


UTC = dt.timezone.utc


def test_audit_path_uses_normalized_event_month(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_PROFILE_DIR", str(tmp_path))
    timestamp = dt.datetime(2026, 7, 1, 0, 30, tzinfo=UTC)
    assert audit_path("default", timestamp).name == "audit-202607.jsonl"
    assert audit_path(
        "default", dt.datetime(2026, 7, 1, 0, 30, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    ).name == "audit-202606.jsonl"


def test_audit_store_loads_months_in_order_and_filters_files(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_PROFILE_DIR", str(tmp_path))
    store = AuditStore("default")
    older = AuditEvent(dt.datetime(2026, 6, 30, 23, 59, tzinfo=UTC), "server_start", "June")
    newer = AuditEvent(dt.datetime(2026, 7, 1, 0, 1, tzinfo=UTC), "server_stop", "July")
    store.append(newer)
    store.append(older)
    assert [event.message for event in store.load()] == ["July", "June"]
    assert [event.message for event in store.load(older.timestamp, older.timestamp)] == ["June"]


def test_audit_store_skips_malformed_records(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_PROFILE_DIR", str(tmp_path))
    path = audit_path("default", dt.datetime(2026, 7, 1, tzinfo=UTC))
    path.parent.mkdir(parents=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("not json\n")
        handle.write(json.dumps(AuditEvent(
            dt.datetime(2026, 7, 1, tzinfo=UTC), "server_start", "valid"
        ).to_dict()) + "\n")
    assert [event.message for event in AuditStore("default").load()] == ["valid"]


def test_audit_store_deduplicates_identical_source_events(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_PROFILE_DIR", str(tmp_path))
    event = AuditEvent(dt.datetime(2026, 7, 1, tzinfo=UTC), "player_login", "Alice")
    store = AuditStore("default")
    store.append(event)
    store.append(event)
    assert store.load() == (event,)


@pytest.mark.parametrize(
    ("line", "event_type", "message", "admin_password"),
    [
        (
            "[2026-07-17 20:20:47] [LOG] Alice joined the server. (User id: steam_1, Player id: ABC)",
            "player_login",
            "Alice (steam_1) joined the server",
            None,
        ),
        (
            "[2026-07-17 21:00:47] [LOG] Alice left the server. (User id: steam_1)",
            "player_logout",
            "Alice (steam_1) left the server",
            None,
        ),
        (
            "[2026-07-17 21:10:47] [LOG] Alice executed the command. adminpassword secret",
            "game_command",
            "Alice executed: adminpassword (result: success)",
            "secret",
        ),
        (
            "[2026-07-17 21:11:47] [LOG] Alice executed the command. adminpassword wrong",
            "game_command",
            "Alice executed: adminpassword (result: fail)",
            "secret",
        ),
    ],
)
def test_parse_palserver_audit_line(line, event_type, message, admin_password):
    event = parse_palserver_audit_line(line, admin_password)
    assert event is not None
    assert event.type == event_type
    assert event.message == message
    assert event.timestamp.tzinfo is not None


def test_format_palserver_log_line_sanitizes_admin_password():
    line = "[2026-07-17 21:10:47] [LOG] Alice executed the command. adminpassword secret"
    assert format_palserver_log_line(line, "secret") == (
        "[2026-07-17 21:10:47] [LOG] "
        "Alice executed: adminpassword (result: success)"
    )
    assert "secret" not in format_palserver_log_line(line, "different")


def test_pagination_table_requires_datetime_timestamp_column():
    with pytest.raises(ValueError):
        _validate_columns((TableColumn("created", "Created"),))
    assert _validate_columns((TableColumn("timestamp", "Timestamp", "datetime"),))
