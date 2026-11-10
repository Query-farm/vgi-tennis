"""Table-function behaviour against the replay server, without a SQL engine."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pyarrow as pa
import pytest

from vgi_tennis import functions as fn


class _Collector:
    def __init__(self) -> None:
        self.batches: list[pa.RecordBatch] = []
        self.cache_controls: list[object] = []
        self.finished = False

    def emit(self, batch: pa.RecordBatch, *, cache_control=None) -> None:
        self.batches.append(batch)
        self.cache_controls.append(cache_control)

    def finish(self) -> None:
        self.finished = True

    def rows(self) -> list[dict]:
        return pa.Table.from_batches(self.batches).to_pylist()


def _run(function, args=None) -> list[dict]:
    out = _Collector()
    params = SimpleNamespace(args=args, output_schema=function.FIXED_SCHEMA)
    function.process(params, None, out)
    assert out.finished and len(out.batches) == 1
    assert out.cache_controls[0].ttl >= 30
    return out.rows()


def test_current_rankings_cover_both_tours(replay) -> None:
    rows = _run(fn.CurrentRankingsFunction)
    assert {(row["tour"], row["source"]) for row in rows} == {("ATP", "espn"), ("WTA", "wta")}
    assert [row["player_name"] for row in rows if row["ranking"] == 1] == [
        "Alexander Zverev",
        "Elena Rybakina",
    ]


def test_rankings_by_tour_type_and_depth(replay) -> None:
    atp = _run(fn.RankingsFunction, fn.RankingsArgs(tour="ATP", ranking_type="singles", max_rank=3))
    assert [row["ranking"] for row in atp] == [1, 2, 3]
    wta = _run(fn.RankingsFunction, fn.RankingsArgs(tour="WTA", ranking_type="doubles", max_rank=200))
    assert {row["ranking_type"] for row in wta} == {"doubles"} and len(wta) == 3
    with pytest.raises(ValueError, match="ATP doubles"):
        _run(fn.RankingsFunction, fn.RankingsArgs(tour="ATP", ranking_type="doubles", max_rank=10))


def test_current_matches_merge_both_scoreboards(replay) -> None:
    rows = _run(fn.CurrentMatchesFunction)
    assert {row["tour"] for row in rows} == {"ATP", "WTA"}
    assert len({row["match_id"] for row in rows}) == len(rows)


def test_matches_on_keeps_that_day_and_tour(replay) -> None:
    rows = _run(fn.MatchesOnFunction, fn.MatchesOnArgs(tour="ATP", match_date=date(2026, 7, 12)))
    assert rows and {row["tour"] for row in rows} == {"ATP"}
    assert any(row["winner_name"] == "Jannik Sinner" for row in rows)
    everything = _run(fn.MatchesOnFunction, fn.MatchesOnArgs(tour="ALL", match_date=date(2026, 7, 12)))
    assert len(everything) >= len(rows)


def test_tournaments_from_each_source(replay) -> None:
    wta = _run(fn.TournamentsFunction, fn.TournamentsArgs(tour="WTA", season=2026, source=None))
    assert {row["source"] for row in wta} == {"wta"} and any(row["tournament_id"] == 901 for row in wta)
    atp = _run(fn.TournamentsFunction, fn.TournamentsArgs(tour="ATP", season=2026, source=None))
    assert {row["tournament_id"] for row in atp} == {188, 306}
    espn_wta = _run(fn.TournamentsFunction, fn.TournamentsArgs(tour="WTA", season=2026, source="espn"))
    assert espn_wta == []
    with pytest.raises(ValueError, match="only available from ESPN"):
        _run(fn.TournamentsFunction, fn.TournamentsArgs(tour="ATP", season=2026, source="wta"))


def test_tournament_matches_from_each_source(replay) -> None:
    wta = _run(
        fn.TournamentMatchesFunction,
        fn.TournamentMatchesArgs(tour="WTA", tournament_id=901, season=2026, source=None),
    )
    assert any(row["round"] == "Final" and row["winner_name"] == "Elena Rybakina" for row in wta)
    atp = _run(
        fn.TournamentMatchesFunction,
        fn.TournamentMatchesArgs(tour="ATP", tournament_id=188, season=2026, source=None),
    )
    assert (
        atp and {row["event_id"] for row in atp} == {"188-2026"} and {row["tour"] for row in atp} == {"ATP"}
    )
    missing = _run(
        fn.TournamentMatchesFunction,
        fn.TournamentMatchesArgs(tour="WTA", tournament_id=5, season=2026, source=None),
    )
    assert missing == []


def test_grand_slam_results(replay) -> None:
    rows = _run(fn.GrandSlamResultsFunction, fn.GrandSlamArgs(season=2026, tour="WTA"))
    assert {row["draw"] for row in rows} == {"Women's Singles", "Women's Doubles"}


def test_player_profiles(replay) -> None:
    assert (
        _run(fn.PlayerFunction, fn.PlayerArgs(tour="ATP", player_id=3782, source=None))[0]["plays"] == "Right"
    )
    assert (
        _run(fn.PlayerFunction, fn.PlayerArgs(tour="WTA", player_id=324166, source=None))[0]["country"]
        == "KAZ"
    )
    assert _run(fn.PlayerFunction, fn.PlayerArgs(tour="ATP", player_id=1, source=None)) == []


def test_player_matches(replay) -> None:
    wta = _run(
        fn.PlayerMatchesFunction, fn.PlayerMatchesArgs(tour="WTA", player_id=324166, season=2026, source=None)
    )
    assert wta and {row["source"] for row in wta} == {"wta"}
    atp = _run(
        fn.PlayerMatchesFunction, fn.PlayerMatchesArgs(tour="ATP", player_id=3623, season=2026, source=None)
    )
    assert [row["result"] for row in atp] == ["W", "W"]


def test_season_defaults_to_current_year(replay, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fn.api, "current_season", lambda: 2026)
    rows = _run(
        fn.PlayerMatchesFunction, fn.PlayerMatchesArgs(tour="WTA", player_id=324166, season=None, source=None)
    )
    assert rows and {row["season"] for row in rows} == {2026}


def test_cardinality_estimates_are_bounded() -> None:
    for function in fn.FUNCTIONS:
        estimate = function.cardinality(None)
        assert 0 < estimate.estimate <= estimate.max
