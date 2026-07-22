from module.config import Profile
from module.games.palworld.players_cache import PlayerCache
from module.games.palworld.server.api_cache import PalRestCache


class FakeRestClient:
    def __init__(self):
        self.info_calls = 0
        self.players_calls = 0
        self.metrics_calls = 0
        self.fail_players = False

    def info(self):
        self.info_calls += 1
        return {"version": f"v{self.info_calls}"}

    def players(self):
        self.players_calls += 1
        if self.fail_players:
            raise RuntimeError("players unavailable")
        return {"players": [{"userId": "steam_1", "level": self.players_calls}]}

    def metrics(self):
        self.metrics_calls += 1
        return {"currentplayernum": self.metrics_calls}


def _cache(client, identity, rest_open, updated):
    profile = Profile(name="test")
    return PalRestCache(
        "test",
        profile_loader=lambda _: profile,
        session_probe=lambda _: identity[0],
        rest_probe=lambda _: rest_open[0],
        client_factory=lambda _: client,
        players_updated=updated.append,
    )


def test_cache_fetches_info_once_per_session_and_polls_dynamic_data_every_three_seconds():
    client = FakeRestClient()
    identity = [(42, 100.0)]
    rest_open = [True]
    updated = []
    cache = _cache(client, identity, rest_open, updated)

    cache.poll_once(now=0)
    cache.poll_once(now=2.99)
    cache.poll_once(now=3)

    assert client.info_calls == 1
    assert client.players_calls == 2
    assert client.metrics_calls == 2
    assert updated[-1] == [{"userId": "steam_1", "level": 2}]
    assert cache.snapshot().info == {"version": "v1"}

    identity[0] = (43, 200.0)
    cache.poll_once(now=3.1)

    assert client.info_calls == 2
    assert client.players_calls == 3
    assert client.metrics_calls == 3
    assert cache.snapshot().info == {"version": "v2"}


def test_cache_waits_for_rest_and_retains_successful_dynamic_data_after_failure():
    client = FakeRestClient()
    identity = [(42, 100.0)]
    rest_open = [False]
    updated = []
    cache = _cache(client, identity, rest_open, updated)

    cache.poll_once(now=0)
    assert (client.info_calls, client.players_calls, client.metrics_calls) == (0, 0, 0)

    rest_open[0] = True
    cache.poll_once(now=1)
    first_players = cache.snapshot().players
    client.fail_players = True
    cache.poll_once(now=4)
    snapshot = cache.snapshot()

    assert client.info_calls == 1
    assert snapshot.players == first_players
    assert snapshot.players_error == "players unavailable"
    assert snapshot.metrics == {"currentplayernum": 2}


def test_cache_clears_session_data_when_server_stops():
    client = FakeRestClient()
    identity = [(42, 100.0)]
    cache = _cache(client, identity, [True], [])
    cache.poll_once(now=0)

    identity[0] = None
    cache.poll_once(now=1)

    assert cache.snapshot().session_active is False
    assert cache.snapshot().info == {"version": "v1"}
    assert cache.snapshot().players is None
    assert cache.snapshot().metrics is None


def test_cache_default_player_persistence_tracks_poll_interval(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path / "config"))
    client = FakeRestClient()
    profile = Profile(name="test")
    cache = PalRestCache(
        "test",
        profile_loader=lambda _: profile,
        session_probe=lambda _: (42, 100.0),
        rest_probe=lambda _: True,
        client_factory=lambda _: client,
        poll_interval=3,
    )

    cache.poll_once(now=0)
    cache.poll_once(now=3)

    row = PlayerCache("test").rows()[0]
    assert row["online"] is True
    assert row["last_login"]
    assert row["total_play_time_seconds"] == 6.0
