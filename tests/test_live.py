"""Opt-in smoke tests against the public WTA API and ESPN tennis endpoints.

They pin historical facts (Australian Open 2026 = WTA 901, Wimbledon 2026 =
ESPN 188) and assert shape for moving data.
"""

from __future__ import annotations

import pytest

from vgi_tennis import api, schemas

pytestmark = pytest.mark.live


def test_current_rankings_both_tours() -> None:
    atp = schemas.flatten_espn_rankings(api.fetch_espn_rankings("ATP").payload, "ATP")
    wta = schemas.flatten_wta_rankings(api.fetch_wta_rankings("singles", 200).payload, "singles")
    assert len(atp) >= 100 and len(wta) == 200
    for rows in (atp, wta):
        rankings = [row["ranking"] for row in rows]
        assert rankings[0] == 1 and len(set(rankings)) == len(rankings)
        assert all(row["player_id"] and row["points"] for row in rows)
        points = [row["points"] for row in rows]
        assert points == sorted(points, reverse=True)


def test_wta_doubles_rankings() -> None:
    rows = schemas.flatten_wta_rankings(api.fetch_wta_rankings("doubles", 50).payload, "doubles")
    assert len(rows) == 50 and rows[0]["ranking"] == 1


def test_wta_and_espn_agree_on_the_wta_number_one() -> None:
    wta = api.fetch_wta_rankings("singles", 1).payload[0]["player"]["fullName"]
    espn = api.fetch_espn_rankings("WTA").payload["rankings"][0]["ranks"][0]["athlete"]["displayName"]
    assert wta == espn


def test_australian_open_2026_womens_draw() -> None:
    rows = schemas.flatten_wta_matches(api.fetch_wta_tournament_matches(901, 2026).payload)
    main_singles = [row for row in rows if row["draw_type"] == "singles" and not row["qualifying"]]
    assert len(main_singles) == 127
    final = next(row for row in main_singles if row["round"] == "Final")
    assert (final["winner_name"], final["loser_name"]) == ("Elena Rybakina", "Aryna Sabalenka")


def test_wta_calendar_2026() -> None:
    rows = schemas.flatten_wta_tournaments(api.fetch_wta_tournaments(2026).payload)
    assert len(rows) >= 100
    slams = {row["tournament_id"] for row in rows if row["is_grand_slam"]}
    assert {901, 903, 904, 905} <= slams


def test_wimbledon_2026_from_espn_season_scan() -> None:
    events = api.fetch_espn_season("ATP", 2026).payload
    tournaments = schemas.flatten_espn_tournaments(events, "ATP")
    assert len(tournaments) >= 55
    wimbledon = next(row for row in tournaments if row["tournament_id"] == 188)
    assert wimbledon["singles_champion"] == "Jannik Sinner"
    finals = schemas.flatten_slam_results(events)
    assert len(finals) == 20
    singles = [row for row in finals if row["draw"] in ("Men's Singles", "Women's Singles")]
    assert len(singles) == 8 and all(row["champion"] for row in singles)
    mixed = [row for row in finals if row["draw"] == "Mixed Doubles" and row["score"]]
    assert mixed and all("1-0" not in row["score"] for row in mixed)
    assert any("[1" in row["score"] for row in mixed)
    men = [row for row in schemas.flatten_espn_matches(events, tour="ATP") if row["event_id"] == "188-2026"]
    assert sum(1 for row in men if row["draw"] == "Men's Singles" and not row["qualifying"]) == 127


def test_both_sources_agree_on_wimbledon_2026_womens_champion() -> None:
    wta = schemas.flatten_wta_matches(api.fetch_wta_tournament_matches(904, 2026).payload)
    wta_final = next(row for row in wta if row["round"] == "Final" and row["draw_type"] == "singles")
    espn = schemas.flatten_slam_results(api.fetch_espn_season("ATP", 2026).payload, "WTA")
    espn_final = next(
        row for row in espn if row["tournament_name"] == "Wimbledon" and "Singles" in row["draw"]
    )
    assert wta_final["winner_name"] == espn_final["champion"] == "Linda Noskova"


def test_player_profiles_and_results() -> None:
    alcaraz = schemas.flatten_espn_player(api.fetch_espn_athlete("ATP", 3782).payload, "ATP")
    assert alcaraz[0]["player_name"] == "Carlos Alcaraz"
    rybakina = schemas.flatten_wta_player(api.fetch_wta_player(324166).payload)
    assert rybakina[0]["country"] == "KAZ"
    results = schemas.flatten_wta_player_matches(api.fetch_wta_player_matches(324166, 2026).payload, 324166)
    ao = [row for row in results if row["tournament_id"] == 901 and row["draw_type"] == "singles"]
    assert len(ao) == 7 and all(row["result"] == "W" for row in ao)
    espn = schemas.flatten_espn_player_matches(api.fetch_espn_season("ATP", 2026).payload, "ATP", 3782)
    assert sum(row["result"] == "W" for row in espn) >= 40


def test_current_scoreboards() -> None:
    events = [
        *api.fetch_espn_scoreboard("ATP").payload.get("events", []),
        *api.fetch_espn_scoreboard("WTA").payload.get("events", []),
    ]
    rows = schemas.flatten_espn_matches(events)
    assert all(row["match_id"] for row in rows)
    assert len({row["match_id"] for row in rows}) == len(rows)
