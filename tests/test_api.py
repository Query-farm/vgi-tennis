"""Tests for the HTTP boundary: retries, caching, errors and pagination."""

from __future__ import annotations

import gzip
import json

import httpx
import pytest

from vgi_tennis import __version__, api


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api.time, "sleep", lambda seconds: None)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sends_honest_user_agent_and_reads_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENNIS_WTA_API_BASE_URL", "https://example.test/tennis/")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": 1})

    with _client(handler) as client:
        result = api.fetch_wta_player(1, client=client)

    assert result.payload == {"id": 1}
    assert str(seen[0].url) == "https://example.test/tennis/players/1"
    assert seen[0].headers["User-Agent"] == (
        f"vgi-tennis/{__version__} (+https://github.com/Query-farm/vgi-tennis)"
    )


def test_retries_transient_statuses_then_succeeds() -> None:
    statuses = iter([503, 429, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        headers = {"Retry-After": "1"} if status == 429 else {}
        return httpx.Response(status, json={"ok": status == 200}, headers=headers)

    with _client(handler) as client:
        result = api.get_json("test", "https://example.test/x", client=client)
    assert result.payload == {"ok": True}


def test_gives_up_after_max_attempts() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(502, text="bad gateway")

    with _client(handler) as client, pytest.raises(api.TennisApiError, match="HTTP 502.*bad gateway"):
        api.get_json("ESPN", "https://example.test/x", client=client)
    assert calls == api.MAX_ATTEMPTS


def test_transport_errors_are_retried() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json=[])

    with _client(handler) as client:
        assert api.get_json("test", "https://example.test/x", client=client).payload == []
    assert attempts == 3


def test_transport_errors_eventually_raise() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with _client(handler) as client, pytest.raises(httpx.ConnectError):
        api.get_json("test", "https://example.test/x", client=client)


def test_responses_are_cached_for_the_longer_of_origin_and_policy_ttl() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"n": calls}, headers={"Cache-Control": "public, max-age=900"})

    with _client(handler) as client:
        first = api.get_json("test", "https://example.test/x", {"a": 1}, ttl=60, client=client)
        second = api.get_json("test", "https://example.test/x", {"a": 1}, ttl=60, client=client)
        other = api.get_json("test", "https://example.test/x", {"a": 2}, ttl=60, client=client)
    assert first.ttl == 900
    assert second.payload == {"n": 1}
    assert 0 < second.ttl <= 900
    assert other.payload == {"n": 2}
    assert calls == 2


def test_missing_documents_decode_to_none_and_are_cached() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, json={"code": 404})

    with _client(handler) as client:
        assert api.fetch_espn_athlete("ATP", 99, client=client).payload is None
        assert api.fetch_espn_athlete("ATP", 99, client=client).payload is None
    assert calls == 1


def test_unlabelled_gzip_bodies_are_decompressed() -> None:
    body = gzip.compress(json.dumps({"events": []}).encode())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"Content-Type": "application/json"})

    with _client(handler) as client:
        assert api.fetch_espn_scoreboard("ATP", client=client).payload == {"events": []}


def test_non_json_success_is_rejected() -> None:
    with (
        _client(lambda request: httpx.Response(200, text="<html>")) as client,
        pytest.raises(ValueError, match="non-JSON"),
    ):
        api.get_json("ESPN", "https://example.test/x", client=client)


def test_retry_delay_honours_retry_after_forms() -> None:
    assert api._retry_delay(httpx.Response(429, headers={"Retry-After": "2"}), 0) == 2
    assert api._retry_delay(httpx.Response(429, headers={"Retry-After": "999"}), 0) == api.MAX_RETRY_DELAY
    dated = httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})
    assert api._retry_delay(dated, 0) <= 0.3125
    assert 0.25 <= api._retry_delay(httpx.Response(429, headers={"Retry-After": "soon"}), 0) <= 0.3125
    assert api._retry_delay(None, 10) <= api.MAX_RETRY_DELAY


def test_cache_evicts_least_recently_used_entries_by_size() -> None:
    cache = api._ResponseCache(max_bytes=10)
    cache.store("a", b"12345", 60)
    cache.store("b", b"12345", 60)
    assert cache.lookup("a") is not None
    cache.store("c", b"12345", 60)
    assert cache.lookup("b") is None
    assert cache.lookup("a") is not None
    cache.store("huge", b"x" * 11, 60)
    assert cache.lookup("huge") is None
    cache.store("zero", b"1", 0)
    assert cache.lookup("zero") is None


def test_cache_entries_expire(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = api._ResponseCache(max_bytes=100)
    now = [1000.0]
    monkeypatch.setattr(api.time, "monotonic", lambda: now[0])
    cache.store("a", b"1", 5)
    cache.store("a", b"2", 5)
    assert cache.lookup("a") == (b"2", 5)
    now[0] += 6
    assert cache.lookup("a") is None


def test_wta_rankings_page_until_depth() -> None:
    pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        pages.append(request.url.params["page"])
        assert request.url.params["pageSize"] == "100"
        assert request.url.params["metric"] == "DOUBLES"
        assert request.url.params["type"] == "rankDoubles"
        return httpx.Response(200, json=[{"ranking": page * 100 + n + 1} for n in range(100)])

    with _client(handler) as client:
        result = api.fetch_wta_rankings("doubles", 150, client=client)
    assert pages == ["0", "1"]
    assert [row["ranking"] for row in result.payload][-1] == 150


def test_wta_rankings_validate_arguments() -> None:
    with pytest.raises(ValueError, match="ranking_type"):
        api.fetch_wta_rankings("mixed")
    with pytest.raises(ValueError, match="max_rank"):
        api.fetch_wta_rankings("singles", 0)


def test_wta_tournaments_follow_pages_to_num_entries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["excludeLevels"] == "ITF"
        assert request.url.params["from"] == "2025-01-01"
        page = int(request.url.params["page"])
        size = 100 if page == 0 else 3
        content = [{"tournamentGroup": {"id": page * 100 + n}} for n in range(size)]
        return httpx.Response(200, json={"pageInfo": {"numEntries": 103}, "content": content})

    with _client(handler) as client:
        assert len(api.fetch_wta_tournaments(2025, client=client).payload) == 103


def test_wta_tournament_matches_unknown_event_is_empty() -> None:
    with _client(lambda request: httpx.Response(404)) as client:
        assert api.fetch_wta_tournament_matches(1, 2026, client=client).payload == {"matches": []}


def test_wta_player_matches_page_and_tolerate_missing_player() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["year"] == "2026"
        page = int(request.url.params["page"])
        return httpx.Response(200, json={"matches": [{"n": n} for n in range(100 if page == 0 else 1)]})

    with _client(handler) as client:
        assert len(api.fetch_wta_player_matches(7, 2026, client=client).payload) == 101
    with _client(lambda request: httpx.Response(404)) as client:
        assert api.fetch_wta_player_matches(8, 2026, client=client).payload == []


@pytest.mark.parametrize(
    ("payload", "call"),
    [
        ({"rows": []}, lambda client: api.fetch_wta_rankings(client=client)),
        ([], lambda client: api.fetch_wta_tournaments(2026, client=client)),
        ({"x": 1}, lambda client: api.fetch_wta_tournament_matches(1, 2026, client=client)),
        ([], lambda client: api.fetch_wta_player_matches(1, 2026, client=client)),
        ({"events": {}}, lambda client: api.fetch_espn_scoreboard("WTA", client=client)),
        ([], lambda client: api.fetch_espn_season("ATP", 2020, client=client)),
        ({}, lambda client: api.fetch_espn_rankings("ATP", client=client)),
    ],
)
def test_malformed_payloads_are_rejected(payload, call) -> None:
    with _client(lambda request: httpx.Response(200, json=payload)) as client, pytest.raises(ValueError):
        call(client)


def test_espn_season_scans_twelve_months_and_deduplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENNIS_ESPN_SITE_BASE_URL", "https://espn.test/tennis")
    months: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tennis/atp/scoreboard"
        months.append(request.url.params["dates"])
        events = [
            {"id": "188-2024", "season": {"year": 2024}},
            {"id": "1-2023", "season": {"year": 2023}},
        ]
        return httpx.Response(200, json={"events": events})

    with _client(handler) as client:
        result = api.fetch_espn_season("atp", 2024, client=client)
    assert months == [f"2024{month:02d}" for month in range(1, 13)]
    assert [event["id"] for event in result.payload] == ["188-2024"]
    assert result.ttl == api.HISTORICAL_TTL


def test_scoreboard_day_parameter_and_tour_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import date

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["dates"] == "20260705"
        return httpx.Response(200, json={"events": []})

    with _client(handler) as client:
        assert api.fetch_espn_scoreboard("ATP", date(2026, 7, 5), client=client).payload == {"events": []}
    with pytest.raises(ValueError, match="tour"):
        api.fetch_espn_rankings("ITF")


def test_season_ttl_distinguishes_past_seasons() -> None:
    assert api.season_ttl(api.current_season()) == api.SEASON_TTL
    assert api.season_ttl(api.current_season() - 1) == api.HISTORICAL_TTL


def test_owned_client_is_used_when_none_is_supplied(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"id": 2}))
    real = httpx.Client

    def fake_client(**kwargs):
        assert kwargs["follow_redirects"] is True
        return real(transport=transport, **kwargs)

    monkeypatch.setattr(api.httpx, "Client", fake_client)
    assert api.fetch_wta_player(2).payload == {"id": 2}
