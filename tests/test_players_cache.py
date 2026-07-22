import json

from module.games.palworld.players_cache import PalworldBanList, PlayerCache


def test_player_cache_upserts_rows_and_resolves_names(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    cache = PlayerCache("default")

    cache.upsert(
        [
            {"userId": "steam_1", "name": "Alice", "level": 17},
            {"userId": "steam_2", "name": "Bob", "level": 9},
        ],
        updated_at="2026-07-17T01:00:00Z",
    )
    cache.upsert(
        [{"userId": "steam_1", "name": "Alice Updated", "level": 18}],
        updated_at="2026-07-17T02:00:00Z",
    )
    payload = json.loads(cache.path.read_text(encoding="utf-8"))
    assert payload == {"players": [
        {
            "userId": "steam_1",
            "name": "Alice Updated",
            "level": 18,
            "updated_at": "2026-07-17T02:00:00Z",
        },
        {
            "userId": "steam_2",
            "name": "Bob",
            "level": 9,
            "updated_at": "2026-07-17T01:00:00Z",
        },
    ]}
    assert cache.names() == {
        "steam_1": "Alice Updated",
        "steam_2": "Bob",
    }

    payload["banned_userids"] = ["steam_1"]
    cache.path.write_text(json.dumps(payload), encoding="utf-8")
    assert cache.names()["steam_1"] == "Alice Updated"
    assert "banned_userids" not in json.loads(cache.path.read_text(encoding="utf-8"))


def test_player_cache_skips_rows_without_user_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    cache = PlayerCache("default")

    assert cache.upsert([{"name": "Missing ID"}], updated_at="now") == []


def test_player_cache_tracks_login_and_play_time_across_online_transitions(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    cache = PlayerCache("default")

    cache.upsert(
        [{"userId": "steam_1", "name": "Alice"}],
        updated_at="2026-07-17T01:00:00Z",
        poll_interval_seconds=3,
    )
    cache.upsert(
        [{"userId": "steam_1", "name": "Alice"}],
        updated_at="2026-07-17T01:00:03Z",
        poll_interval_seconds=3,
    )
    first = cache.rows()[0]
    assert first["online"] is True
    assert first["last_login"] == "2026-07-17T01:00:00Z"
    assert first["total_play_time_seconds"] == 6.0

    cache.upsert([], updated_at="2026-07-17T01:00:06Z", poll_interval_seconds=3)
    offline = cache.rows()[0]
    assert offline["online"] is False
    assert offline["last_login"] == "2026-07-17T01:00:00Z"
    assert offline["total_play_time_seconds"] == 6.0

    cache.upsert(
        [{"userId": "steam_1", "name": "Alice"}],
        updated_at="2026-07-17T01:00:09Z",
        poll_interval_seconds=3,
    )
    logged_in_again = cache.rows()[0]
    assert logged_in_again["online"] is True
    assert logged_in_again["last_login"] == "2026-07-17T01:00:09Z"
    assert logged_in_again["total_play_time_seconds"] == 9.0


def test_palworld_ban_list_touches_missing_file_and_reads_unique_ids(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    banlist = PalworldBanList("default")

    assert not banlist.path.exists()
    assert banlist.ids() == []
    assert banlist.path.is_file()

    banlist.path.write_text(
        "steam_1\n\nsteam_2\nsteam_1\n",
        encoding="utf-8",
    )
    assert banlist.ids() == ["steam_1", "steam_2"]
