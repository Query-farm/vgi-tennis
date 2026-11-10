"""Flattening of recorded WTA and ESPN payloads into the typed schemas."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pyarrow as pa
import pytest
from fixture_server import load

from vgi_tennis import schemas
from vgi_tennis.schemas import (
    MATCH_SCHEMA,
    PLAYER_MATCH_SCHEMA,
    PLAYER_SCHEMA,
    RANKING_SCHEMA,
    SLAM_RESULT_SCHEMA,
    TOURNAMENT_SCHEMA,
    batch_from_rows,
)


def _events(name: str) -> list[dict]:
    return load(name)["events"]


def test_wta_rankings_derive_previous_position() -> None:
    rows = schemas.flatten_wta_rankings(load("wta_rankings_singles.json"), "singles")
    first = rows[0]
    assert first["player_name"] == "Elena Rybakina"
    assert first["ranking"] == 1 and first["points"] == 8099 and first["country"] == "KAZ"
    assert first["ranked_at"] == date(2026, 11, 9)
    assert first["birth_date"] == date(1999, 6, 17)
    assert first["previous_ranking"] == 1
    moved = schemas.flatten_wta_rankings([{"ranking": 19, "movement": 5, "player": {"id": 1}}], "singles")
    assert moved[0]["previous_ranking"] == 24
    batch_from_rows(rows, RANKING_SCHEMA)


def test_espn_rankings() -> None:
    rows = schemas.flatten_espn_rankings(load("espn_atp_rankings.json"), "ATP")
    assert len(rows) == 5
    assert rows[0]["player_name"] == "Alexander Zverev"
    assert rows[0]["points"] == 9550 and rows[0]["country"] == "GER" and rows[0]["source"] == "espn"
    assert rows[0]["ranked_at"] == date(2026, 11, 5)
    assert all(row["movement"] == row["previous_ranking"] - row["ranking"] for row in rows)
    batch_from_rows(rows, RANKING_SCHEMA)


def test_wta_tournament_draw_results() -> None:
    rows = schemas.flatten_wta_matches(load("wta_tournament_901_2026.json"))
    final = next(row for row in rows if row["round"] == "Final" and row["draw_type"] == "singles")
    assert final["winner_name"] == "Elena Rybakina"
    assert final["loser_name"] == "Aryna Sabalenka"
    assert final["winner_side"] == 2
    assert final["score"] == "6-4 4-6 6-4"
    assert (final["side1_sets_won"], final["side2_sets_won"]) == (1, 2)
    assert final["is_grand_slam"] and final["event_id"] == "901-2026"
    assert final["court"] == "Rod Laver Arena"
    assert final["side2_seed"] == 5
    semi = next(row for row in rows if row["winner_name"] == "Elena Rybakina" and row["round"] == "Semifinal")
    assert semi["score"] == "6-3 7-6(7)"
    qualifying = [row for row in rows if row["qualifying"]]
    assert qualifying and all(row["round"] == "Qualifying" for row in qualifying)
    doubles = next(row for row in rows if row["draw_type"] == "doubles")
    assert doubles["side1_partner_name"] and doubles["draw"] == "Women's Doubles"
    batch_from_rows(rows, MATCH_SCHEMA)


def test_wta_retirements_walkovers_and_match_tiebreaks() -> None:
    rows = schemas.flatten_wta_matches(load("wta_tournament_806_2026.json"))
    statuses = {row["status"] for row in rows}
    assert {"retired", "walkover"} <= statuses
    walkover = next(row for row in rows if row["status"] == "walkover")
    assert walkover["winner_side"] == 2 and walkover["sets"] == [] and walkover["score"] is None
    retired = next(row for row in rows if row["status"] == "retired")
    assert retired["winner_side"] == 1
    # Shape of a real WTA 1000 doubles match decided by a match tiebreak (Doha 2026).
    tiebreak = schemas.flatten_wta_matches(
        {
            "matches": [
                {
                    "MatchID": "QD001",
                    "DrawMatchType": "D",
                    "Winner": "3",
                    "ScoreSet1A": "6",
                    "ScoreSet1B": "3",
                    "ScoreSet2A": "2",
                    "ScoreSet2B": "6",
                    "ScoreSet3A": "0",
                    "ScoreSet3B": "1",
                    "ScoreTbSet3": "7",
                }
            ]
        }
    )[0]
    assert tiebreak["score"] == "3-6 6-2 [10-7]"
    assert (tiebreak["side1_sets_won"], tiebreak["side2_sets_won"]) == (1, 2)


def test_wta_tournament_calendar() -> None:
    rows = schemas.flatten_wta_tournaments(load("wta_tournaments_2026.json")["content"])
    ao = next(row for row in rows if row["tournament_id"] == 901)
    assert ao["is_grand_slam"] and ao["level"] == "Grand Slam" and ao["surface"] == "Hard"
    assert ao["singles_champion"] == "Elena Rybakina"
    assert ao["start_date"] == date(2026, 1, 18) and ao["status"] == "completed"
    assert ao["prize_money"] > 30_000_000
    brisbane = next(row for row in rows if row["tournament_id"] == 800)
    assert brisbane["doubles_champions"] == "Su-Wei Hsieh / Jelena Ostapenko"
    assert [row["start_date"] for row in rows] == sorted(row["start_date"] for row in rows)
    batch_from_rows(rows, TOURNAMENT_SCHEMA)


def test_wta_tournament_status_is_derived_when_missing() -> None:
    rows = schemas.flatten_wta_tournaments(
        [
            {"tournamentGroup": {"id": 1}, "year": 2099, "startDate": "2099-01-01", "endDate": "2099-01-07"},
            {"tournamentGroup": {"id": 2}, "year": 2000, "status": "inProgress"},
        ]
    )
    assert rows[0]["status"] == "upcoming"
    assert rows[1]["status"] == "in_progress"


def test_wta_player_and_results() -> None:
    profile = schemas.flatten_wta_player(load("wta_player_324166.json"))
    assert profile[0]["player_name"] == "Elena Rybakina" and profile[0]["country"] == "KAZ"
    assert schemas.flatten_wta_player(None) == []
    rows = schemas.flatten_wta_player_matches(load("wta_player_324166_matches_2026.json")["matches"], 324166)
    assert all(row["opponent_name"] for row in rows), "byes must be omitted"
    final = next(row for row in rows if row["tournament_level"] == "Grand Slam" and row["round"] == "Final")
    assert final["opponent_name"] == "Aryna Sabalenka" and final["result"] == "W"
    assert final["score"] == "6-4 4-6 6-4" and final["ranking_points"] == 2000
    walkover = next(row for row in rows if row["status"] == "walkover")
    assert walkover["result"] == "L" and walkover["opponent_name"] == "Leylah Fernandez"
    assert walkover["round"] == "Quarterfinal"
    doubles = schemas.flatten_wta_player_matches(
        load("wta_player_324166_matches_doubles.json")["matches"], 324166
    )[0]
    assert doubles["draw_type"] == "doubles" and doubles["partner_name"] == "Valeriya Deminova"
    assert doubles["opponent_partner_name"] == "Diana Enache" and doubles["result"] == "L"
    assert doubles["tournament_level"] == "ITF" and doubles["round"] == "Round of 16"
    batch_from_rows(rows, PLAYER_MATCH_SCHEMA)


def test_espn_matches_tiebreaks_seeds_and_doubles() -> None:
    rows = schemas.flatten_espn_matches(_events("espn_atp_scoreboard_202607.json"))
    final = next(row for row in rows if row["draw"] == "Men's Singles" and row["round"] == "Final")
    assert final["winner_name"] == "Jannik Sinner" and final["loser_name"] == "Alexander Zverev"
    assert final["score"] == "6-7(7) 7-6(2) 6-3 6-4"
    assert final["tour"] == "ATP" and final["is_grand_slam"] and final["event_id"] == "188-2026"
    assert final["tournament_id"] == 188 and final["season"] == 2026
    winner = final["winner_side"]
    assert final[f"side{winner}_seed"] == 1 and final[f"side{winner}_country"] == "ITA"
    assert final["sets"][0]["tiebreak_loser_points"] == 7
    doubles = next(row for row in rows if row["draw"] == "Men's Doubles" and row["round"] == "Final")
    assert doubles["side1_partner_id"] is not None and " / " in doubles["winner_name"]
    mixed = next(row for row in rows if row["draw"] == "Mixed Doubles")
    assert mixed["tour"] == "MIXED"
    assert {
        row["tour"]
        for row in schemas.flatten_espn_matches(_events("espn_atp_scoreboard_202607.json"), tour="WTA")
    } == {"WTA"}
    batch_from_rows(rows, MATCH_SCHEMA)


def test_espn_matches_filter_by_utc_day_and_deduplicate() -> None:
    events = _events("espn_atp_scoreboard_202607.json")
    rows = schemas.flatten_espn_matches(events + events, day=date(2026, 7, 12))
    assert rows and all(row["scheduled_at"].date() == date(2026, 7, 12) for row in rows)
    assert len({row["match_id"] for row in rows}) == len(rows)


def test_espn_retired_and_current_status() -> None:
    rows = schemas.flatten_espn_matches(
        _events("espn_atp_scoreboard.json") + _events("espn_wta_scoreboard.json")
    )
    assert rows and {row["status"] for row in rows} <= {
        "completed",
        "retired",
        "walkover",
        "scheduled",
        "in_progress",
        "other",
    }
    assert schemas._espn_status({"type": {"name": "STATUS_DELAYED", "state": "in"}})[0] == "in_progress"
    assert schemas._espn_status({"type": {"name": "STATUS_POSTPONED", "state": "post"}})[0] == "other"


def test_espn_tournaments_and_champions() -> None:
    rows = schemas.flatten_espn_tournaments(_events("espn_atp_scoreboard_202607.json"), "ATP")
    wimbledon = next(row for row in rows if row["tournament_id"] == 188)
    assert wimbledon["singles_champion"] == "Jannik Sinner"
    assert wimbledon["singles_champion_id"] == 3623
    assert wimbledon["doubles_champions"] == "Harri Heliovaara / Henry Patten"
    assert wimbledon["start_date"] == date(2026, 6, 22) and wimbledon["end_date"] == date(2026, 7, 12)
    assert (wimbledon["city"], wimbledon["country"]) == ("London", "Great Britain")
    assert wimbledon["level"] == "Grand Slam" and wimbledon["status"] == "completed"
    women = schemas.flatten_espn_tournaments(_events("espn_atp_scoreboard_202607.json"), "WTA")
    assert next(row for row in women if row["tournament_id"] == 188)["singles_champion"] == "Linda Noskova"
    batch_from_rows(rows, TOURNAMENT_SCHEMA)


def _espn_event(start: date, end: date, statuses: list[str]) -> dict:
    """An ESPN event whose own status claims STATUS_FINAL, as ESPN reports mid-week."""
    names = {"completed": "STATUS_FINAL", "scheduled": "STATUS_SCHEDULED"}
    return {
        "id": "148-2026",
        "date": f"{start.isoformat()}T05:00Z",
        "endDate": f"{(end + timedelta(days=1)).isoformat()}T04:59Z",
        "status": {"type": {"name": "STATUS_FINAL", "state": "post"}},
        "groupings": [
            {
                "grouping": {"slug": "mens-singles"},
                "competitions": [
                    {
                        "id": str(index),
                        "type": {"slug": "mens-singles"},
                        "status": {"type": {"name": names[s]}},
                    }
                    for index, s in enumerate(statuses)
                ],
            }
        ],
    }


def test_espn_tournament_status_ignores_event_level_final() -> None:
    today = datetime.now(UTC).date()
    week = timedelta(days=3)

    def status(start: date, end: date, statuses: list[str]) -> str:
        return schemas.flatten_espn_tournaments([_espn_event(start, end, statuses)], "ATP")[0]["status"]

    # Mid-week with matches still to play: ESPN says STATUS_FINAL, the event is in progress.
    assert status(today - week, today + week, ["completed", "scheduled"]) == "in_progress"
    # Every listed match finished but no singles final decided yet: still in progress.
    assert status(today - week, today + week, ["completed"]) == "in_progress"
    assert status(today + week, today + 2 * week, []) == "upcoming"
    assert status(today - 2 * week, today - week, ["scheduled"]) == "completed"


def test_grand_slam_finals() -> None:
    rows = schemas.flatten_slam_results(_events("espn_atp_scoreboard_202607.json"))
    assert len(rows) == 5
    singles = {row["draw"]: row for row in rows if "Singles" in row["draw"]}
    assert singles["Men's Singles"]["champion"] == "Jannik Sinner"
    assert singles["Men's Singles"]["runner_up_seed"] == 2
    assert singles["Women's Singles"]["champion"] == "Linda Noskova"
    assert singles["Women's Singles"]["champion_country"] == "CZE"
    assert [
        row["tour"] for row in schemas.flatten_slam_results(_events("espn_atp_scoreboard_202607.json"), "ATP")
    ] == ["ATP", "ATP"]
    batch_from_rows(rows, SLAM_RESULT_SCHEMA)


def test_espn_player_matches_cover_singles_and_doubles() -> None:
    events = _events("espn_atp_scoreboard_202607.json")
    rows = schemas.flatten_espn_player_matches(events, "ATP", 3623)
    assert [row["round"] for row in rows] == ["Semifinal", "Final"]
    assert all(row["result"] == "W" for row in rows)
    assert rows[-1]["opponent_name"] == "Alexander Zverev" and rows[-1]["player_seed"] == 1
    zverev = schemas.flatten_espn_player_matches(events, "ATP", 2375)
    assert zverev[-1]["result"] == "L"
    final = next(
        row
        for row in schemas.flatten_espn_matches(events)
        if row["draw"] == "Men's Doubles" and row["round"] == "Final"
    )
    partner = final["side1_partner_id"]
    doubles = schemas.flatten_espn_player_matches(events, "ATP", partner)
    assert doubles and doubles[-1]["draw_type"] == "doubles"
    assert doubles[-1]["partner_id"] == final["side1_player_id"]
    batch_from_rows(rows, PLAYER_MATCH_SCHEMA)


def test_espn_player_profile() -> None:
    rows = schemas.flatten_espn_player(load("espn_atp_athlete_3782.json"), "ATP")
    assert rows[0]["player_name"] == "Carlos Alcaraz"
    assert rows[0]["birth_date"] == date(2003, 5, 5)
    assert rows[0]["height_inches"] == 72 and rows[0]["weight_lbs"] == 163
    assert rows[0]["plays"] == "Right" and rows[0]["debut_year"] == 2018 and rows[0]["active"] is True
    assert schemas.flatten_espn_player(None, "ATP") == []
    batch_from_rows(rows, PLAYER_SCHEMA)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, None),
        ("", None),
        ("7", 7),
        (7.0, 7),
        ("33475.0", 33475),
        (7.5, None),
        ("x", None),
    ],
)
def test_to_int(value, expected) -> None:
    assert schemas.to_int(value) == expected


def test_small_parsers() -> None:
    assert schemas.espn_dob("20/4/1997") == date(1997, 4, 20)
    assert (
        schemas.espn_dob("31/2/1997") is None
        and schemas.espn_dob("1997") is None
        and schemas.espn_dob(5) is None
    )
    assert schemas.espn_height_inches("6' 6\"") == 78 and schemas.espn_height_inches(None) is None
    assert schemas.espn_weight_lbs("198 lbs") == 198 and schemas.espn_weight_lbs("") is None
    assert schemas.espn_weight_lbs(None) is None
    assert schemas.espn_event_date("2026-11-07T05:00Z") == date(2026, 11, 7)
    assert schemas.espn_event_date("2026-11-15T04:59Z", end=True) == date(2026, 11, 14)
    assert schemas.espn_event_date("2026-07-13T03:59Z", end=True) == date(2026, 7, 12)
    assert schemas.espn_event_date(None) is None
    assert schemas.to_timestamp("2026-07-12T15:05Z") == datetime(2026, 7, 12, 15, 5, tzinfo=UTC)
    assert schemas.to_timestamp("2026-07-12T15:05") == datetime(2026, 7, 12, 15, 5, tzinfo=UTC)
    assert schemas.to_timestamp("nope") is None and schemas.to_timestamp(None) is None
    assert schemas.to_date("2026-13-01") is None and schemas.to_date(None) is None
    assert schemas.tour_of_draw(None) is None and schemas.tour_of_draw("exhibition") is None
    assert schemas.espn_country({"flag": "https://a.espncdn.com/i/teamlogos/countries/500/usa.png"}) == "USA"
    assert schemas.espn_country({}) is None
    assert schemas.format_score([{"side1_games": None, "side2_games": 3}], None) is None
    # A match tiebreak is recorded as a 1-0 set plus the loser's points.
    match_tiebreak = [
        {"side1_games": 6, "side2_games": 4, "tiebreak_loser_points": None},
        {"side1_games": 3, "side2_games": 6, "tiebreak_loser_points": None},
        {"side1_games": 0, "side2_games": 1, "tiebreak_loser_points": 8},
    ]
    assert schemas.format_score(match_tiebreak, 2) == "4-6 6-3 [10-8]"
    assert schemas.format_score(match_tiebreak, None) == "6-4 3-6 [8-10]"
    assert schemas.format_score([{"side1_games": 1, "side2_games": 0, "tiebreak_loser_points": 11}], 1) == (
        "[13-11]"
    )
    assert schemas._wta_round("7", False) == "Round 7" and schemas._wta_round(None, False) is None
    assert schemas._wta_round("2", True) == "Qualifying Round 2" and schemas._wta_round("X", False) == "X"
    assert schemas._wta_player_round("R128", True) == "Qualifying Round of 128"
    assert schemas._wta_player_round("RR", False) == "RR" and schemas._wta_player_round(None, False) is None
    assert schemas._wta_status(None) is None and schemas._city_country(None) == (None, None)
    assert schemas._city_country("Indian Wells") == ("Indian Wells", None)


def test_batches_respect_projected_schemas() -> None:
    rows = schemas.flatten_wta_rankings(load("wta_rankings_singles.json"), "singles")
    projected = pa.schema([RANKING_SCHEMA.field("points"), RANKING_SCHEMA.field("player_name")])
    batch = batch_from_rows(rows, projected)
    assert batch.schema.names == ["points", "player_name"]
    assert batch.column("points").to_pylist()[0] == 8099
