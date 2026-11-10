"""VGI worker exposing ATP and WTA tennis data as a read-only SQL catalog."""

from __future__ import annotations

import json
import os
import sys

from vgi import Worker
from vgi.catalog import Catalog, ConstraintInfo, Schema, Table, constraint_record

from vgi_tennis.functions import FUNCTIONS, CurrentMatchesFunction, CurrentRankingsFunction
from vgi_tennis.meta import column_comments
from vgi_tennis.schemas import MATCH_SCHEMA, RANKING_SCHEMA

SOURCE_URL = "https://github.com/Query-farm/vgi-tennis"

_CURRENT_RANKINGS_TAGS = {
    "provider": "tennis",
    "vgi.category": "rankings",
    "vgi.title": "Current Tennis Rankings",
    "vgi.keywords": json.dumps(["rankings", "world number one", "ranking points", "ATP", "WTA"]),
    "vgi.doc_llm": (
        "Current singles rankings for both tours, one row per player: ATP top 150 (from ESPN) and WTA "
        "top 200 (from the official WTA API). Filter tour = 'ATP' or 'WTA'. Use rankings() for WTA "
        "doubles or a deeper WTA list. player_id is in the id space named by source."
    ),
    "vgi.doc_md": (
        "Today's ATP and WTA singles rankings as an ordinary read-only table, with points, previous "
        "position and movement. Use `rankings()` for doubles or other depths."
    ),
    "vgi.example_queries": json.dumps(
        [
            {
                "description": "The top ten of each tour.",
                "sql": (
                    "SELECT tour, ranking, player_name, country, points "
                    "FROM tennis.main.current_rankings WHERE ranking <= 10 ORDER BY tour, ranking"
                ),
            },
            {
                "description": "Biggest climbers in the latest ATP release.",
                "sql": (
                    "SELECT ranking, player_name, movement FROM tennis.main.current_rankings "
                    "WHERE tour = 'ATP' ORDER BY movement DESC LIMIT 10"
                ),
            },
        ]
    ),
}

_CURRENT_MATCHES_TAGS = {
    "provider": "tennis",
    "vgi.category": "matches",
    "vgi.title": "Current Tennis Matches",
    "vgi.keywords": json.dumps(["scores", "live scores", "results", "order of play", "scoreboard"]),
    "vgi.doc_llm": (
        "Every match of the ATP and WTA tournaments in play right now (ESPN scoreboard), one row per "
        "match: tournament, draw, round, both sides with seeds, status, winner and set-by-set score. "
        "tour is ATP for men's draws, WTA for women's, MIXED for mixed doubles. Use matches_on() for "
        "another day and tournament_matches() for a past event."
    ),
    "vgi.doc_md": (
        "The current week's tennis matches across both tours, scheduled, live and completed. `score` "
        "reads from the winner's side; `sets` holds per-set games for each side."
    ),
    "vgi.example_queries": json.dumps(
        [
            {
                "description": "Completed singles results this week.",
                "sql": (
                    "SELECT tournament_name, round, winner_name, loser_name, score "
                    "FROM tennis.main.current_matches "
                    "WHERE status = 'completed' AND draw_type = 'singles' ORDER BY scheduled_at DESC"
                ),
            },
            {
                "description": "Match count and completion by tournament and draw.",
                "sql": (
                    "SELECT tournament_name, draw, count(*) AS matches, "
                    "count(*) FILTER (WHERE status = 'completed') AS completed "
                    "FROM tennis.main.current_matches GROUP BY ALL ORDER BY tournament_name, draw"
                ),
            },
        ]
    ),
}

_EXECUTABLE_EXAMPLES = json.dumps(
    [
        {
            "name": "current_rankings_cover_both_tours",
            "description": "Singles rankings are present for both the ATP and WTA.",
            "sql": "SELECT count(DISTINCT tour) AS tours FROM tennis.main.current_rankings",
            "expected_result": [{"tours": 2}],
        },
        {
            "name": "each_tour_has_a_number_one",
            "description": "Each tour has exactly one player ranked first.",
            "sql": (
                "SELECT bool_and(n = 1) AS ok FROM (SELECT tour, count(*) AS n "
                "FROM tennis.main.current_rankings WHERE ranking = 1 GROUP BY tour)"
            ),
            "expected_result": [{"ok": True}],
        },
        {
            "name": "current_matches_are_typed",
            "description": "Every current match has an id and a known status.",
            "sql": (
                "SELECT coalesce(bool_and(match_id IS NOT NULL AND status IN "
                "('scheduled','in_progress','completed','retired','walkover','other')), true) AS ok "
                "FROM tennis.main.current_matches"
            ),
            "expected_result": [{"ok": True}],
        },
        {
            "name": "ao_2026_womens_champion",
            "description": "Elena Rybakina won the 2026 Australian Open women's singles final.",
            "sql": (
                "SELECT winner_name FROM tennis.main.tournament_matches('WTA', 901, 2026) "
                "WHERE draw_type = 'singles' AND round = 'Final'"
            ),
            "expected_result": [{"winner_name": "Elena Rybakina"}],
        },
    ]
)

_AGENT_TEST_TASKS = json.dumps(
    [
        {
            "name": "world_number_ones",
            "prompt": "Who is currently ranked world number one on the ATP and WTA?",
        },
        {
            "name": "wta_doubles_leader",
            "prompt": "Who tops the current WTA doubles ranking, and with how many points?",
        },
        {
            "name": "wimbledon_2026_mens_final",
            "prompt": "Who won the 2026 Wimbledon men's singles final, and what was the score?",
        },
        {
            "name": "ao_2026_womens_final",
            "prompt": "Who won the 2026 Australian Open women's singles title and whom did she beat?",
        },
        {
            "name": "slam_champions_2026",
            "prompt": "List the 2026 Grand Slam men's and women's singles champions.",
        },
        {
            "name": "wta_1000_calendar",
            "prompt": "List the 2026 WTA 1000 tournaments with their surfaces and singles champions.",
        },
        {
            "name": "rybakina_season",
            "prompt": "How many singles matches did Elena Rybakina (WTA id 324166) win and lose in 2026?",
        },
        {
            "name": "alcaraz_profile",
            "prompt": "Show Carlos Alcaraz's player profile; his ESPN ATP player id is 3782.",
        },
        {
            "name": "wimbledon_final_day",
            "prompt": "Which ATP matches were played on 12 July 2026, and who won them?",
        },
        {
            "name": "current_week_results",
            "prompt": "Which tournaments are on this week, and how many matches are complete in each?",
        },
    ]
)


def _key_constraints(table: str, key: tuple[str, ...]) -> tuple[ConstraintInfo, ...]:
    """Primary-key and NOT NULL records for a read-only table keyed by ``key``."""
    records = [
        constraint_record(
            ["main"],
            table,
            "pk",
            "PRIMARY_KEY",
            [[column] for column in key],
            enforcement="ENFORCED",
            validation="ALL_ROWS",
        )
    ]
    records += [
        constraint_record(
            ["main"],
            table,
            f"{column}_not_null",
            "NOT_NULL",
            [[column]],
            enforcement="ENFORCED",
            validation="ALL_ROWS",
        )
        for column in key
    ]
    return tuple(records)


_CATEGORIES = [
    {
        "name": "rankings",
        "title": "Rankings",
        "description": "ATP and WTA singles and doubles rankings with points and movement.",
        "keywords": ["rankings", "points", "world number one"],
    },
    {
        "name": "matches",
        "title": "Matches",
        "description": "Live, daily and per-tournament match results with set scores.",
        "keywords": ["scores", "results", "draws", "sets"],
    },
    {
        "name": "tournaments",
        "title": "Tournaments",
        "description": "Season calendars with levels, surfaces, dates and champions.",
        "keywords": ["calendar", "tournaments", "champions"],
    },
    {
        "name": "grand-slams",
        "title": "Grand Slams",
        "description": "Australian Open, Roland Garros, Wimbledon and US Open finals.",
        "keywords": ["grand slam", "majors", "finals"],
    },
    {
        "name": "players",
        "title": "Players",
        "description": "Player profiles and season match logs.",
        "keywords": ["players", "profile", "season results"],
    },
]

TENNIS_CATALOG = Catalog(
    name="tennis",
    default_schema="main",
    source_url=SOURCE_URL,
    comment="Read-only ATP and WTA tennis rankings, tournaments and match results",
    tags={
        "provider": "tennis",
        "domain": "sports",
        "vgi.title": "Professional Tennis Data",
        "vgi.subtitle": "ATP and WTA rankings, draws, Grand Slam finals and set-by-set results.",
        "vgi.source_url": SOURCE_URL,
        "vgi.author": "Query Farm LLC <hello@query.farm>",
        "vgi.copyright": (
            "Worker (c) 2026 Query Farm LLC. Data is fetched on demand from the WTA's public API and "
            "ESPN's undocumented endpoints and remains subject to their terms; not affiliated with "
            "the ATP, WTA, ESPN or any Grand Slam."
        ),
        "vgi.license": "MIT",
        "vgi.support_contact": "https://github.com/Query-farm/vgi-tennis/issues",
        "vgi.support_policy_url": "https://github.com/Query-farm/vgi-tennis/blob/main/README.md",
        "vgi.keywords": json.dumps(["tennis", "ATP", "WTA", "Grand Slam", "rankings", "Wimbledon"]),
        "vgi.executable_examples": _EXECUTABLE_EXAMPLES,
        "vgi.agent_test_tasks": _AGENT_TEST_TASKS,
        "vgi.doc_llm": (
            "Read-only professional tennis data for the ATP and WTA tours: singles and doubles "
            "rankings, live and daily scoreboards, season calendars with champions, full draw "
            "results with set-by-set scores, Grand Slam finals, player profiles and player season "
            "logs. Start with the current tables for today; the functions take a tour ('ATP' or "
            "'WTA') plus a season, day, tournament or player. WTA data comes from the official WTA "
            "API (WTA ids); ATP data and the cross-tour scoreboards come from ESPN (ESPN ids). Every "
            "row carries a source column naming its id space; ids from one source do not match the "
            "other. Leading required arguments are positional, e.g. "
            "tournament_matches('WTA', 901, 2026) for the 2026 Australian Open women's event."
        ),
        "vgi.doc_md": (
            "# ATP and WTA tennis\n\n"
            "Rankings, tournament calendars, full draw results, Grand Slam finals, player profiles "
            "and player season logs, fetched on demand and returned as typed Arrow tables.\n\n"
            "## Sources and ids\n\n"
            "WTA data comes from the official WTA API. ATP data, live scoreboards and Grand Slam "
            "finals come from ESPN's undocumented endpoints. Player and tournament ids are specific "
            "to their `source`.\n\n"
            "## Scores\n\n"
            "`score` reads from the winner's side with the tiebreak loser's points in brackets, "
            "e.g. `7-6(5) 6-3`. `sets` holds per-set games for side 1 and side 2."
        ),
    },
    schemas=[
        Schema(
            path=["main"],
            comment="ATP and WTA rankings, tournaments, matches and players",
            tags={
                "provider": "tennis",
                "domain": "sports",
                "vgi.title": "Tennis",
                "vgi.keywords": json.dumps(["tennis", "rankings", "matches", "tournaments", "players"]),
                "vgi.categories": json.dumps(_CATEGORIES),
                "vgi.doc_llm": (
                    "Use current_rankings for today's singles rankings and current_matches for this "
                    "week's matches. Parameterized functions cover doubles and deeper rankings, other "
                    "days, season calendars, full tournament draws, Grand Slam finals, player "
                    "profiles and player season results."
                ),
                "vgi.doc_md": (
                    "## Professional tennis\n\n"
                    "Rows are player-, tournament- or match-grain. Current tables need no arguments; "
                    "functions take a tour ('ATP' or 'WTA') as their first, positional argument "
                    "except `grand_slam_results(season)`.\n\n"
                    "WTA rows default to the official WTA API and its ids; ATP rows and the "
                    "cross-tour scoreboards come from ESPN and use ESPN ids. The `source` column "
                    "on every row says which id space applies."
                ),
                "vgi.example_queries": _CURRENT_RANKINGS_TAGS["vgi.example_queries"],
            },
            functions=FUNCTIONS,
            tables=[
                Table(
                    name="current_rankings",
                    function=CurrentRankingsFunction,
                    comment="Current ATP (top 150) and WTA (top 200) singles rankings, one row per player",
                    tags=_CURRENT_RANKINGS_TAGS,
                    column_comments=column_comments(RANKING_SCHEMA),
                    constraints=_key_constraints("current_rankings", ("tour", "player_id")),
                    cardinality_estimate=350,
                ),
                Table(
                    name="current_matches",
                    function=CurrentMatchesFunction,
                    comment="Every match of the ATP and WTA tournaments in play this week, one row per match",
                    tags=_CURRENT_MATCHES_TAGS,
                    column_comments=column_comments(MATCH_SCHEMA),
                    constraints=_key_constraints("current_matches", ("match_id",)),
                    cardinality_estimate=300,
                ),
            ],
        )
    ],
)


class TennisWorker(Worker):
    """Worker process hosting the read-only tennis catalog."""

    catalog = TENNIS_CATALOG


def main() -> None:
    """Run over stdio by default, or HTTP when VGI's ``--http`` flag is supplied."""
    TennisWorker.main()


def main_http() -> None:
    """Run HTTP with production-safe configuration requirements."""
    if not os.environ.get("VGI_SIGNING_KEY"):
        raise SystemExit(
            "VGI_SIGNING_KEY is required for HTTP mode so state remains valid across restarts and replicas"
        )
    auth_configured = bool(os.environ.get("VGI_BEARER_TOKENS") or os.environ.get("VGI_JWT_ISSUER"))
    auth_configured = auth_configured or os.environ.get("VGI_PROXY_PROOF_MODE", "").lower() == "require"
    if not auth_configured and os.environ.get("VGI_ALLOW_ANONYMOUS") != "1":
        raise SystemExit(
            "HTTP authentication is required; configure bearer/JWT/proxy proof, or explicitly set "
            "VGI_ALLOW_ANONYMOUS=1 for an intentionally public deployment"
        )
    argv = sys.argv[1:]
    if "--http" not in argv:
        argv = ["--http", *argv]
    if "--describe" not in argv and "--no-describe" not in argv:
        argv = ["--no-describe", *argv]
    if not any(value == "--cors-origins" or value.startswith("--cors-origins=") for value in argv):
        argv = ["--cors-origins", "", *argv]
    sys.argv = [sys.argv[0], *argv]
    TennisWorker.main()
