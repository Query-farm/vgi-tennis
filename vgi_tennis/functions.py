"""VGI table functions for ATP and WTA rankings, tournaments, matches and players."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Annotated, ClassVar

import pyarrow as pa
from vgi.arguments import Arg
from vgi.cache_control import CacheControl
from vgi.metadata import FunctionExample
from vgi.table_function import (
    ProcessParams,
    TableCardinality,
    TableFunctionGenerator,
    bind_fixed_schema,
    init_single_worker,
)
from vgi_rpc.rpc import OutputCollector

from vgi_tennis import api
from vgi_tennis.examples import (
    LOSSES_AND_CHAMPIONS_DESCRIPTION,
    LOSSES_AND_CHAMPIONS_SQL,
    SLAM_CHAMPIONS_RANKED_DESCRIPTION,
    SLAM_CHAMPIONS_RANKED_SQL,
    WINS_OVER_TOP_TEN_DESCRIPTION,
    WINS_OVER_TOP_TEN_SQL,
)
from vgi_tennis.meta import result_columns_schema
from vgi_tennis.schemas import (
    MATCH_SCHEMA,
    PLAYER_MATCH_SCHEMA,
    PLAYER_SCHEMA,
    RANKING_SCHEMA,
    SLAM_RESULT_SCHEMA,
    TOURNAMENT_SCHEMA,
    batch_from_rows,
    flatten_espn_matches,
    flatten_espn_player,
    flatten_espn_player_matches,
    flatten_espn_rankings,
    flatten_espn_tournaments,
    flatten_slam_results,
    flatten_wta_matches,
    flatten_wta_player,
    flatten_wta_player_matches,
    flatten_wta_rankings,
    flatten_wta_tournaments,
)

TOURS = ("ATP", "WTA")
TOURS_OR_ALL = ("ATP", "WTA", "ALL")
SOURCES = ("wta", "espn")
CURRENT_RANKING_DEPTH = 200
ESPN_RANKING_DEPTH = 150


def _emit(rows: list[dict], ttl: int, params: ProcessParams, out: OutputCollector) -> None:
    out.emit(batch_from_rows(rows, params.output_schema), cache_control=CacheControl(ttl=ttl))
    out.finish()


def _source(tour: str, source: str | None) -> str:
    """Resolve the id space for a tour: WTA defaults to the official API, ATP is ESPN-only."""
    resolved = source or ("wta" if tour == "WTA" else "espn")
    if tour == "ATP" and resolved != "espn":
        raise ValueError("ATP data is only available from ESPN; use source := 'espn' or omit it")
    return resolved


def _season(season: int | None) -> int:
    return season if season is not None else api.current_season()


def _tags(
    *, category: str, title: str, llm: str, md: str, schema: pa.Schema, examples: list[tuple[str, str]]
) -> dict[str, str]:
    return {
        "provider": "tennis",
        "vgi.category": category,
        "vgi.title": title,
        "vgi.result_columns_schema": result_columns_schema(schema),
        "vgi.doc_llm": llm,
        "vgi.doc_md": md,
        "vgi.example_queries": json.dumps([{"description": text, "sql": sql} for text, sql in examples]),
    }


_TOUR_ARG = Arg(0, arrow_type=pa.string(), choices=TOURS, doc="Tour: 'ATP' or 'WTA'.")
_TOUR_OR_ALL_ARG = Arg(
    "tour",
    default="ALL",
    arrow_type=pa.string(),
    choices=TOURS_OR_ALL,
    doc="'ATP' for men's draws, 'WTA' for women's draws, or 'ALL' (adds mixed doubles).",
)
_SOURCE_ARG = Arg(
    "source",
    default=None,
    arrow_type=pa.string(),
    choices=SOURCES,
    doc="Id space: 'wta' (official WTA API, WTA default) or 'espn' (the only ATP source).",
)
_SEASON_ARG = Arg(
    "season",
    default=None,
    arrow_type=pa.int32(),
    ge=1968,
    le=2100,
    doc="Season (calendar year); NULL means the current year.",
)


# ------------------------------------------------------------------ rankings


def _current_rankings() -> tuple[list[dict], int]:
    atp = api.fetch_espn_rankings("ATP")
    wta = api.fetch_wta_rankings("singles", CURRENT_RANKING_DEPTH)
    rows = flatten_espn_rankings(atp.payload, "ATP") + flatten_wta_rankings(wta.payload, "singles")
    return rows, min(atp.ttl, wta.ttl)


@dataclass(kw_only=True)
class RankingsArgs:
    """Arguments accepted by ``rankings``."""

    tour: Annotated[str, _TOUR_ARG]
    ranking_type: Annotated[
        str,
        Arg(
            "ranking_type",
            default="singles",
            arrow_type=pa.string(),
            choices=("singles", "doubles"),
            doc="singles or doubles; ATP doubles are not published by the ATP source.",
        ),
    ]
    max_rank: Annotated[
        int,
        Arg(
            "max_rank",
            default=200,
            arrow_type=pa.int32(),
            ge=1,
            le=2000,
            doc="Deepest ranking position to return (ATP stops at 150).",
        ),
    ]


_RANKINGS_SQL = (
    "SELECT ranking, player_name, country, points, movement "
    "FROM tennis.main.rankings('WTA', ranking_type := 'doubles', max_rank := 20) ORDER BY ranking"
)


@init_single_worker
@bind_fixed_schema
class RankingsFunction(TableFunctionGenerator[RankingsArgs]):
    """Current ATP or WTA ranking to a chosen depth."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = RANKING_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "rankings"
        description = "Current ATP or WTA singles or doubles ranking"
        categories = ["tennis", "rankings"]
        projection_pushdown = True
        tags = _tags(
            category="rankings",
            title="Tennis Rankings",
            llm=(
                "Current ranking for one tour, one row per ranked player: position, previous "
                "position, movement, points and player identity. WTA comes from the official WTA "
                "API (singles and doubles, any depth); ATP singles come from ESPN and stop at 150. "
                "ATP doubles are unavailable."
            ),
            md=(
                "The current ranking for `tour` ('ATP' or 'WTA'). `ranking_type` selects singles or "
                "doubles (WTA only for doubles) and `max_rank` limits the depth. `player_id` is in "
                "the id space named by `source`."
            ),
            schema=RANKING_SCHEMA,
            examples=[("The WTA doubles top 20.", _RANKINGS_SQL)],
        )
        examples = [FunctionExample(sql=_RANKINGS_SQL, description="The WTA doubles top 20")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Rankings default to 200 rows."""
        return TableCardinality(estimate=200, max=2000)

    @classmethod
    def process(cls, params: ProcessParams[RankingsArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit one ranking."""
        args = params.args
        if args.tour == "ATP":
            if args.ranking_type != "singles":
                raise ValueError(
                    "ATP doubles rankings are not available from ESPN; use ranking_type 'singles'"
                )
            fetched = api.fetch_espn_rankings("ATP")
            rows = [
                row
                for row in flatten_espn_rankings(fetched.payload, "ATP")
                if row["ranking"] <= args.max_rank
            ]
        else:
            fetched = api.fetch_wta_rankings(args.ranking_type, args.max_rank)
            rows = flatten_wta_rankings(fetched.payload, args.ranking_type)
        _emit(rows, fetched.ttl, params, out)


_CURRENT_RANKINGS_SQL = (
    "SELECT tour, ranking, player_name, country, points "
    "FROM tennis.main.all_current_rankings() WHERE ranking <= 3 ORDER BY tour, ranking"
)


@init_single_worker
@bind_fixed_schema
class CurrentRankingsFunction(TableFunctionGenerator[None]):
    """The scan behind ``current_rankings``."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = RANKING_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "all_current_rankings"
        description = "Current ATP and WTA singles rankings (scan behind current_rankings)"
        categories = ["tennis", "rankings"]
        projection_pushdown = True
        tags = _tags(
            category="rankings",
            title="Current Tennis Rankings Scan",
            llm=(
                "The scan behind the current_rankings table. Prefer the table; use rankings() for "
                "doubles or a different depth."
            ),
            md="Returns ATP (top 150) and WTA (top 200) singles. Selecting `current_rankings` is clearer.",
            schema=RANKING_SCHEMA,
            examples=[("Read the top three of each tour from the backing scan.", _CURRENT_RANKINGS_SQL)],
        )
        examples = [FunctionExample(sql=_CURRENT_RANKINGS_SQL, description="Top three of each tour")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """150 ATP plus 200 WTA rows."""
        return TableCardinality(estimate=350, max=400)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Fetch and emit both tours' singles rankings."""
        rows, ttl = _current_rankings()
        _emit(rows, ttl, params, out)


# ------------------------------------------------------------------- matches


_CURRENT_MATCHES_SQL = (
    "SELECT tour, tournament_name, draw, round, winner_name, loser_name, score "
    "FROM tennis.main.all_current_matches() WHERE status = 'completed' ORDER BY scheduled_at DESC LIMIT 20"
)


@init_single_worker
@bind_fixed_schema
class CurrentMatchesFunction(TableFunctionGenerator[None]):
    """The scan behind ``current_matches``."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = MATCH_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "all_current_matches"
        description = "Every match of the ATP and WTA tournaments in play today (scan behind current_matches)"
        categories = ["tennis", "matches"]
        projection_pushdown = True
        tags = _tags(
            category="matches",
            title="Current Tennis Matches Scan",
            llm=(
                "The scan behind the current_matches table. Prefer the table; use matches_on() for "
                "another day or tournament_matches() for a past tournament."
            ),
            md="Returns ESPN's current ATP and WTA scoreboards. Selecting `current_matches` is clearer.",
            schema=MATCH_SCHEMA,
            examples=[("Latest completed matches from the backing scan.", _CURRENT_MATCHES_SQL)],
        )
        examples = [FunctionExample(sql=_CURRENT_MATCHES_SQL, description="Latest completed matches")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """A typical week has a few hundred matches across both tours."""
        return TableCardinality(estimate=300, max=2000)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Fetch and emit both tours' scoreboards, de-duplicated."""
        atp = api.fetch_espn_scoreboard("ATP")
        wta = api.fetch_espn_scoreboard("WTA")
        events = [*atp.payload.get("events", []), *wta.payload.get("events", [])]
        _emit(flatten_espn_matches(events), min(atp.ttl, wta.ttl), params, out)


@dataclass(kw_only=True)
class MatchesOnArgs:
    """Arguments accepted by ``matches_on``."""

    tour: Annotated[
        str,
        Arg(0, arrow_type=pa.string(), choices=TOURS_OR_ALL, doc="'ATP', 'WTA', or 'ALL' (adds mixed)."),
    ]
    match_date: Annotated[date, Arg(1, arrow_type=pa.date32(), doc="Day whose matches to return (UTC).")]


_MATCHES_ON_SQL = (
    "SELECT draw, round, winner_name, loser_name, score "
    "FROM tennis.main.matches_on('ATP', DATE '2026-07-12') ORDER BY scheduled_at"
)


@init_single_worker
@bind_fixed_schema
class MatchesOnFunction(TableFunctionGenerator[MatchesOnArgs]):
    """Matches scheduled on one day."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = MATCH_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "matches_on"
        description = "ATP and WTA matches scheduled on one day, with set scores"
        categories = ["tennis", "matches"]
        projection_pushdown = True
        tags = _tags(
            category="matches",
            title="Tennis Matches On A Day",
            llm=(
                "Every match whose start time falls on match_date (UTC), from ESPN, one row per "
                "match with players, seeds, round, status, winner and set scores. tour 'ATP' keeps "
                "men's draws, 'WTA' women's, 'ALL' everything including mixed doubles. Both "
                "arguments are positional: `matches_on('ATP', DATE '2026-07-12')`."
            ),
            md=(
                "Matches from ESPN's scoreboard for the tournaments in play on `match_date`, kept to "
                "those scheduled on that UTC day. Ids are ESPN ids."
            ),
            schema=MATCH_SCHEMA,
            examples=[("Matches on Wimbledon 2026 men's final day.", _MATCHES_ON_SQL)],
        )
        examples = [FunctionExample(sql=_MATCHES_ON_SQL, description="Wimbledon 2026 final-day matches")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """A busy day has a few hundred matches."""
        return TableCardinality(estimate=150, max=2000)

    @classmethod
    def process(cls, params: ProcessParams[MatchesOnArgs], state: None, out: OutputCollector) -> None:
        """Fetch the day's scoreboards and keep that day's matches."""
        args = params.args
        tours = TOURS if args.tour == "ALL" else (args.tour,)
        fetched = [api.fetch_espn_scoreboard(tour, args.match_date) for tour in tours]
        events = [event for value in fetched for event in value.payload.get("events", [])]
        rows = flatten_espn_matches(events, tour=args.tour, day=args.match_date)
        _emit(rows, min(value.ttl for value in fetched), params, out)


# --------------------------------------------------------------- tournaments


@dataclass(kw_only=True)
class TournamentsArgs:
    """Arguments accepted by ``tournaments``."""

    tour: Annotated[str, _TOUR_ARG]
    season: Annotated[int | None, _SEASON_ARG]
    source: Annotated[str | None, _SOURCE_ARG]


_TOURNAMENTS_SQL = (
    "SELECT name, level, surface, start_date, singles_champion "
    "FROM tennis.main.tournaments('WTA', season := 2026) WHERE level IN ('Grand Slam', 'WTA 1000') "
    "ORDER BY start_date"
)


@init_single_worker
@bind_fixed_schema
class TournamentsFunction(TableFunctionGenerator[TournamentsArgs]):
    """One tour's tournament calendar for a season."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = TOURNAMENT_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "tournaments"
        description = "ATP or WTA tournament calendar for a season, with champions"
        categories = ["tennis", "tournaments"]
        projection_pushdown = True
        tags = _tags(
            category="tournaments",
            title="Tennis Tournament Calendar",
            llm=(
                "One row per tournament in a season: id, name, level, dates, location, status and "
                "singles/doubles champions. WTA (default source 'wta') adds surface, draw sizes and "
                "prize money and covers WTA 125 to Grand Slam. ATP comes from ESPN, which marks "
                "only Grand Slams in level. Pass tournament_id to tournament_matches()."
            ),
            md=(
                "The season calendar for `tour`. WTA rows come from the official WTA API unless "
                "`source := 'espn'`. ATP rows come from ESPN's monthly scoreboards; the first call "
                "for a season fetches twelve months and is then cached."
            ),
            schema=TOURNAMENT_SCHEMA,
            examples=[("2026 WTA Grand Slams and WTA 1000s with champions.", _TOURNAMENTS_SQL)],
        )
        examples = [FunctionExample(sql=_TOURNAMENTS_SQL, description="2026 WTA premier events")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """About 60 ATP and 120 WTA events a year."""
        return TableCardinality(estimate=120, max=400)

    @classmethod
    def process(cls, params: ProcessParams[TournamentsArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit the season calendar."""
        args = params.args
        season = _season(args.season)
        if _source(args.tour, args.source) == "wta":
            fetched = api.fetch_wta_tournaments(season)
            rows = flatten_wta_tournaments(fetched.payload)
        else:
            fetched = api.fetch_espn_season(args.tour, season)
            rows = flatten_espn_tournaments(fetched.payload, args.tour)
        _emit(rows, fetched.ttl, params, out)


@dataclass(kw_only=True)
class TournamentMatchesArgs:
    """Arguments accepted by ``tournament_matches``."""

    tour: Annotated[str, _TOUR_ARG]
    tournament_id: Annotated[
        int,
        Arg(1, arrow_type=pa.int32(), ge=1, doc="Tournament id from tournaments(), e.g. WTA 901, ESPN 188."),
    ]
    season: Annotated[int, Arg(2, arrow_type=pa.int32(), ge=1968, le=2100, doc="Season (calendar year).")]
    source: Annotated[str | None, _SOURCE_ARG]


_TOURNAMENT_MATCHES_SQL = (
    "SELECT round, winner_name, loser_name, score "
    "FROM tennis.main.tournament_matches('WTA', 901, 2026) "
    "WHERE draw_type = 'singles' AND NOT qualifying AND round IN ('Semifinal', 'Final')"
)


@init_single_worker
@bind_fixed_schema
class TournamentMatchesFunction(TableFunctionGenerator[TournamentMatchesArgs]):
    """Every match of one tournament edition."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = MATCH_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "tournament_matches"
        description = "Every singles and doubles match of one ATP or WTA tournament edition"
        categories = ["tennis", "matches", "tournaments"]
        projection_pushdown = True
        tags = _tags(
            category="matches",
            title="Tennis Tournament Draw Results",
            llm=(
                "All matches (qualifying and main draw, singles and doubles) of one tournament "
                "edition, one row per match with round, players, seeds, winner and set scores. "
                "Arguments are positional: tournament_matches('WTA', 901, 2026) is the 2026 "
                "Australian Open women's event; tournament_matches('ATP', 188, 2026) is Wimbledon "
                "men's draws from ESPN. Get ids from tournaments()."
            ),
            md=(
                "The full draw results for `tournament_id` in `season`. WTA uses the official WTA "
                "ids by default; ATP (and WTA with `source := 'espn'`) use ESPN tournament ids and "
                "return that tour's draws only."
            ),
            schema=MATCH_SCHEMA,
            examples=[
                ("Australian Open 2026 women's singles semifinals and final.", _TOURNAMENT_MATCHES_SQL)
            ],
        )
        examples = [FunctionExample(sql=_TOURNAMENT_MATCHES_SQL, description="AO 2026 women's final rounds")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """A Grand Slam has about 300 matches per tour."""
        return TableCardinality(estimate=150, max=1000)

    @classmethod
    def process(cls, params: ProcessParams[TournamentMatchesArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit one tournament's matches."""
        args = params.args
        if _source(args.tour, args.source) == "wta":
            fetched = api.fetch_wta_tournament_matches(args.tournament_id, args.season)
            rows = flatten_wta_matches(fetched.payload)
        else:
            fetched = api.fetch_espn_season(args.tour, args.season)
            event_id = f"{args.tournament_id}-{args.season}"
            events = [event for event in fetched.payload if event.get("id") == event_id]
            rows = flatten_espn_matches(events, tour=args.tour)
        _emit(rows, fetched.ttl, params, out)


@dataclass(kw_only=True)
class GrandSlamArgs:
    """Arguments accepted by ``grand_slam_results``."""

    season: Annotated[int, Arg(0, arrow_type=pa.int32(), ge=1968, le=2100, doc="Season (calendar year).")]
    tour: Annotated[str, _TOUR_OR_ALL_ARG]


_SLAM_SQL = (
    "SELECT tournament_name, draw, champion, runner_up, score "
    "FROM tennis.main.grand_slam_results(2026, tour := 'ATP') ORDER BY final_date, draw"
)


@init_single_worker
@bind_fixed_schema
class GrandSlamResultsFunction(TableFunctionGenerator[GrandSlamArgs]):
    """Grand Slam finals for a season."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = SLAM_RESULT_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "grand_slam_results"
        description = "Champions, runners-up and scores of every Grand Slam final in a season"
        categories = ["tennis", "grand-slams"]
        projection_pushdown = True
        tags = _tags(
            category="grand-slams",
            title="Grand Slam Finals",
            llm=(
                "One row per Grand Slam draw final (Australian Open, Roland Garros, Wimbledon, US "
                "Open): champion, runner-up, seeds, countries and score. tour 'ATP' keeps men's "
                "draws, 'WTA' women's, 'ALL' (default) adds mixed doubles. Season is positional: "
                "grand_slam_results(2026)."
            ),
            md=(
                "Grand Slam finals from ESPN, which carries every Slam draw. Slams still in progress "
                "show their final as scheduled once it is drawn."
            ),
            schema=SLAM_RESULT_SCHEMA,
            examples=[
                ("The 2026 men's Grand Slam finals.", _SLAM_SQL),
                (SLAM_CHAMPIONS_RANKED_DESCRIPTION, SLAM_CHAMPIONS_RANKED_SQL),
            ],
        )
        examples = [
            FunctionExample(sql=_SLAM_SQL, description="The 2026 men's Grand Slam finals"),
            FunctionExample(sql=SLAM_CHAMPIONS_RANKED_SQL, description=SLAM_CHAMPIONS_RANKED_DESCRIPTION),
        ]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Four Slams times five draws."""
        return TableCardinality(estimate=20, max=24)

    @classmethod
    def process(cls, params: ProcessParams[GrandSlamArgs], state: None, out: OutputCollector) -> None:
        """Scan the season and keep the Slam finals."""
        args = params.args
        fetched = api.fetch_espn_season("ATP", args.season)
        _emit(flatten_slam_results(fetched.payload, args.tour), fetched.ttl, params, out)


# ------------------------------------------------------------------- players


@dataclass(kw_only=True)
class PlayerArgs:
    """Arguments accepted by ``player``."""

    tour: Annotated[str, _TOUR_ARG]
    player_id: Annotated[int, Arg(1, arrow_type=pa.int32(), ge=1, doc="Player id in the source's id space.")]
    source: Annotated[str | None, _SOURCE_ARG]


_PLAYER_SQL = (
    "SELECT player_name, birth_date, height_inches, plays, debut_year FROM tennis.main.player('ATP', 3782)"
)


@init_single_worker
@bind_fixed_schema
class PlayerFunction(TableFunctionGenerator[PlayerArgs]):
    """One player's profile."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = PLAYER_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "player"
        description = "Profile of one ATP or WTA player"
        categories = ["tennis", "players"]
        projection_pushdown = True
        tags = _tags(
            category="players",
            title="Tennis Player Profile",
            llm=(
                "One row describing a player: name, birth date, and (ESPN) age, height, weight, "
                "playing hand and debut year, or (WTA) country. Positional: player('ATP', 3782) is "
                "Carlos Alcaraz (ESPN id); player('WTA', 324166) is Elena Rybakina (WTA id). Use "
                "source := 'espn' for a WTA player id taken from current_matches. Unknown ids "
                "return no rows."
            ),
            md="A single player profile. Find ids in `current_rankings` or match tables.",
            schema=PLAYER_SCHEMA,
            examples=[("Carlos Alcaraz's ESPN profile.", _PLAYER_SQL)],
        )
        examples = [FunctionExample(sql=_PLAYER_SQL, description="Carlos Alcaraz's profile")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """At most one row."""
        return TableCardinality(estimate=1, max=1)

    @classmethod
    def process(cls, params: ProcessParams[PlayerArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit one profile."""
        args = params.args
        if _source(args.tour, args.source) == "wta":
            fetched = api.fetch_wta_player(args.player_id)
            rows = flatten_wta_player(fetched.payload)
        else:
            fetched = api.fetch_espn_athlete(args.tour, args.player_id)
            rows = flatten_espn_player(fetched.payload, args.tour)
        _emit(rows, fetched.ttl, params, out)


@dataclass(kw_only=True)
class PlayerMatchesArgs:
    """Arguments accepted by ``player_matches``."""

    tour: Annotated[str, _TOUR_ARG]
    player_id: Annotated[int, Arg(1, arrow_type=pa.int32(), ge=1, doc="Player id in the source's id space.")]
    season: Annotated[int | None, _SEASON_ARG]
    source: Annotated[str | None, _SOURCE_ARG]


_PLAYER_MATCHES_SQL = (
    "SELECT tournament_name, round, opponent_name, result, score "
    "FROM tennis.main.player_matches('WTA', 324166, season := 2026) WHERE draw_type = 'singles'"
)


@init_single_worker
@bind_fixed_schema
class PlayerMatchesFunction(TableFunctionGenerator[PlayerMatchesArgs]):
    """One player's match results for a season."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = PLAYER_MATCH_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "player_matches"
        description = "One ATP or WTA player's match results for a season"
        categories = ["tennis", "players", "matches"]
        projection_pushdown = True
        tags = _tags(
            category="players",
            title="Tennis Player Season Results",
            llm=(
                "One row per match a player played in a season, from the player's point of view: "
                "tournament, round, partner, opponent, result W/L, score and status. WTA rows add "
                "entry rankings, ranking points and prize money earned at that tournament. "
                "Positional: player_matches('WTA', 324166, season := 2026). Byes are omitted."
            ),
            md=(
                "A player's season, one row per match. WTA uses the official WTA API and ids; ATP "
                "(and WTA with `source := 'espn'`) scans ESPN's season scoreboards for the ESPN id."
            ),
            schema=PLAYER_MATCH_SCHEMA,
            examples=[
                ("Elena Rybakina's 2026 singles results.", _PLAYER_MATCHES_SQL),
                (LOSSES_AND_CHAMPIONS_DESCRIPTION, LOSSES_AND_CHAMPIONS_SQL),
                (WINS_OVER_TOP_TEN_DESCRIPTION, WINS_OVER_TOP_TEN_SQL),
            ],
        )
        examples = [
            FunctionExample(sql=_PLAYER_MATCHES_SQL, description="Elena Rybakina's 2026 results"),
            FunctionExample(sql=LOSSES_AND_CHAMPIONS_SQL, description=LOSSES_AND_CHAMPIONS_DESCRIPTION),
            FunctionExample(sql=WINS_OVER_TOP_TEN_SQL, description=WINS_OVER_TOP_TEN_DESCRIPTION),
        ]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """A busy player has about 80 matches a season."""
        return TableCardinality(estimate=60, max=300)

    @classmethod
    def process(cls, params: ProcessParams[PlayerMatchesArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit one player's season."""
        args = params.args
        season = _season(args.season)
        if _source(args.tour, args.source) == "wta":
            fetched = api.fetch_wta_player_matches(args.player_id, season)
            rows = flatten_wta_player_matches(fetched.payload, args.player_id)
        else:
            fetched = api.fetch_espn_season(args.tour, season)
            rows = flatten_espn_player_matches(fetched.payload, args.tour, args.player_id)
        _emit(rows, fetched.ttl, params, out)


FUNCTIONS = [
    CurrentRankingsFunction,
    RankingsFunction,
    CurrentMatchesFunction,
    MatchesOnFunction,
    TournamentsFunction,
    TournamentMatchesFunction,
    GrandSlamResultsFunction,
    PlayerFunction,
    PlayerMatchesFunction,
]
