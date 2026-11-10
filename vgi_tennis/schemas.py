"""Arrow schemas and flattening for WTA API and ESPN tennis payloads.

Two upstreams with different shapes feed one set of typed tables:

* the official WTA API (``api.wtatennis.com``) for WTA rankings, tournaments,
  draws, players and player results;
* ESPN's undocumented tennis endpoints for ATP data, live scoreboards and a
  uniform cross-tour match shape.

Player identifiers are source-specific. Every row carries a ``source`` column
(``wta`` or ``espn``) saying which id space its ``*_id`` columns use.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pyarrow as pa

from vgi_tennis.meta import field

TIMESTAMP = pa.timestamp("us", tz="UTC")

SET_TYPE = pa.struct(
    [
        pa.field("set_number", pa.int32()),
        pa.field("side1_games", pa.int32()),
        pa.field("side2_games", pa.int32()),
        pa.field("tiebreak_loser_points", pa.int32()),
    ]
)

# ------------------------------------------------------------------ schemas

RANKING_SCHEMA = pa.schema(
    [
        field("tour", pa.string(), "ATP or WTA."),
        field("ranking_type", pa.string(), "singles or doubles."),
        field("source", pa.string(), "Id space of player_id: wta (official WTA API) or espn."),
        field("ranked_at", pa.date32(), "Date of the ranking release."),
        field("ranking", pa.int32(), "Current ranking position (1 = best)."),
        field("previous_ranking", pa.int32(), "Position in the previous release."),
        field(
            "movement",
            pa.int32(),
            "Count of places gained since the previous release; negative means dropped.",
        ),
        field("points", pa.int32(), "Ranking points (count) counting toward the ranking."),
        field("player_id", pa.int32(), "Player id in the source's id space."),
        field("player_name", pa.string(), "Player full name."),
        field("first_name", pa.string(), "Player first name."),
        field("last_name", pa.string(), "Player last name."),
        field("country", pa.string(), "Three-letter country code."),
        field("birth_date", pa.date32(), "Date of birth (WTA source only)."),
        field("age", pa.int32(), "Age in years (ESPN source only)."),
        field("tournaments_played", pa.int32(), "Tournaments counted (WTA source only)."),
    ]
)

MATCH_SCHEMA = pa.schema(
    [
        field("tour", pa.string(), "ATP (men's draws), WTA (women's draws) or MIXED (mixed doubles)."),
        field("source", pa.string(), "Id space of the id columns: espn or wta."),
        field("season", pa.int32(), "Season (calendar year)."),
        field("tournament_id", pa.int32(), "Tournament id in the source's id space; stable across seasons."),
        field("event_id", pa.string(), "Tournament edition id, '<tournament_id>-<season>'."),
        field("tournament_name", pa.string(), "Sponsor or official name of the tournament edition."),
        field("is_grand_slam", pa.bool_(), "Whether the tournament is a Grand Slam."),
        field("match_id", pa.string(), "Source match id."),
        field("draw", pa.string(), "Draw, e.g. Men's Singles or Women's Doubles."),
        field("draw_type", pa.string(), "singles or doubles."),
        field("qualifying", pa.bool_(), "Whether this is a qualifying-draw match."),
        field("round", pa.string(), "Round name, e.g. Round 1, Quarterfinal, Final."),
        field("scheduled_at", TIMESTAMP, "Scheduled or actual start time (UTC)."),
        field("status", pa.string(), "scheduled, in_progress, completed, retired, walkover or other."),
        field("status_detail", pa.string(), "Source status text."),
        field("court", pa.string(), "Court name."),
        field(
            "side1_player_id", pa.int32(), "Id of the side 1 (first-listed) player, in the source's id space."
        ),
        field("side1_player_name", pa.string(), "Full name of the side 1 (first-listed) player."),
        field("side1_country", pa.string(), "Three-letter country code of the side 1 player."),
        field("side1_partner_id", pa.int32(), "Id of the side 1 doubles partner; NULL in singles."),
        field("side1_partner_name", pa.string(), "Name of the side 1 doubles partner; NULL in singles."),
        field("side1_seed", pa.int32(), "Tournament seeding of side 1 (1 = top seed); NULL if unseeded."),
        field(
            "side2_player_id",
            pa.int32(),
            "Id of the side 2 (second-listed) player, in the source's id space.",
        ),
        field("side2_player_name", pa.string(), "Full name of the side 2 (second-listed) player."),
        field("side2_country", pa.string(), "Three-letter country code of the side 2 player."),
        field("side2_partner_id", pa.int32(), "Id of the side 2 doubles partner; NULL in singles."),
        field("side2_partner_name", pa.string(), "Name of the side 2 doubles partner; NULL in singles."),
        field("side2_seed", pa.int32(), "Tournament seeding of side 2 (1 = top seed); NULL if unseeded."),
        field(
            "winner_side",
            pa.int32(),
            "Winning side number (1 or 2, matching the side1_/side2_ columns); NULL until decided.",
        ),
        field("winner_name", pa.string(), "Winning player or pair."),
        field("loser_name", pa.string(), "Losing player or pair."),
        field("score", pa.string(), "Score from the winner's view; tiebreak loser points in brackets."),
        field("side1_sets_won", pa.int32(), "Count of sets won by side 1."),
        field("side2_sets_won", pa.int32(), "Count of sets won by side 2."),
        field("sets", pa.list_(SET_TYPE), "Per-set games for each side; UNNEST for set-level analysis."),
    ]
)

TOURNAMENT_SCHEMA = pa.schema(
    [
        field("tour", pa.string(), "ATP or WTA."),
        field("source", pa.string(), "Id space of the id columns: wta or espn."),
        field("season", pa.int32(), "Season (calendar year)."),
        field("tournament_id", pa.int32(), "Source tournament id; pass to tournament_matches()."),
        field("event_id", pa.string(), "Tournament edition id, '<tournament_id>-<season>'."),
        field("name", pa.string(), "Tournament name."),
        field("level", pa.string(), "Tier, e.g. Grand Slam, WTA 1000 (ESPN gives Grand Slam only)."),
        field("is_grand_slam", pa.bool_(), "Whether the tournament is a Grand Slam."),
        field("surface", pa.string(), "Court surface (WTA source only)."),
        field("indoor_outdoor", pa.string(), "I for indoor or O for outdoor (WTA source only)."),
        field("city", pa.string(), "Host city."),
        field("country", pa.string(), "Host country."),
        field("start_date", pa.date32(), "First day."),
        field("end_date", pa.date32(), "Last day."),
        field("singles_draw_size", pa.int32(), "Singles main-draw size (WTA source only)."),
        field("doubles_draw_size", pa.int32(), "Doubles main-draw size (WTA source only)."),
        field("prize_money", pa.int64(), "Total prize money (WTA source only)."),
        field("prize_money_currency", pa.string(), "Prize-money currency."),
        field("status", pa.string(), "upcoming, in_progress or completed."),
        field("singles_champion_id", pa.int32(), "Singles champion player id."),
        field("singles_champion", pa.string(), "Singles champion name."),
        field("doubles_champions", pa.string(), "Doubles champion pair, 'A / B'."),
    ]
)

PLAYER_SCHEMA = pa.schema(
    [
        field("tour", pa.string(), "ATP or WTA."),
        field("source", pa.string(), "Id space of player_id: wta or espn."),
        field("player_id", pa.int32(), "Player id in the source's id space."),
        field("player_name", pa.string(), "Full name."),
        field("first_name", pa.string(), "First name."),
        field("last_name", pa.string(), "Last name."),
        field("country", pa.string(), "Three-letter country code (WTA source only)."),
        field("birth_date", pa.date32(), "Date of birth."),
        field("age", pa.int32(), "Age in years (ESPN source only)."),
        field("height_inches", pa.int32(), "Height in inches (ESPN source only)."),
        field("weight_lbs", pa.int32(), "Weight in pounds (ESPN source only)."),
        field("plays", pa.string(), "Playing hand, Right or Left (ESPN source only)."),
        field("debut_year", pa.int32(), "First professional season (ESPN source only)."),
        field("active", pa.bool_(), "Whether the player is active (ESPN source only)."),
    ]
)

PLAYER_MATCH_SCHEMA = pa.schema(
    [
        field("tour", pa.string(), "ATP or WTA."),
        field("source", pa.string(), "Id space of the id columns: wta or espn."),
        field("season", pa.int32(), "Season (calendar year)."),
        field("player_id", pa.int32(), "The requested player's id."),
        field("tournament_id", pa.int32(), "Source tournament id."),
        field("tournament_name", pa.string(), "Tournament name."),
        field("tournament_level", pa.string(), "Tournament tier, e.g. Grand Slam or WTA 500."),
        field("surface", pa.string(), "Court surface (WTA source only)."),
        field("tournament_start_date", pa.date32(), "Tournament start date."),
        field("match_id", pa.string(), "Source match id (ESPN source only)."),
        field("scheduled_at", TIMESTAMP, "Match start time (ESPN source only)."),
        field("draw_type", pa.string(), "singles or doubles."),
        field("qualifying", pa.bool_(), "Whether this is a qualifying-draw match."),
        field("round", pa.string(), "Round name."),
        field("partner_id", pa.int32(), "Doubles partner id."),
        field("partner_name", pa.string(), "Doubles partner name."),
        field("opponent_id", pa.int32(), "Opponent id."),
        field("opponent_name", pa.string(), "Opponent name."),
        field("opponent_country", pa.string(), "Opponent country code."),
        field("opponent_partner_name", pa.string(), "Opponent's doubles partner."),
        field("player_seed", pa.int32(), "The player's seed."),
        field("opponent_seed", pa.int32(), "Opponent seed."),
        field("player_rank", pa.int32(), "The player's ranking at entry (WTA source only)."),
        field("opponent_rank", pa.int32(), "Opponent ranking at entry (WTA source only)."),
        field("result", pa.string(), "W or L for the player; NULL if undecided."),
        field("score", pa.string(), "Score from the winner's view."),
        field("status", pa.string(), "completed, retired, walkover, in_progress or scheduled."),
        field("ranking_points", pa.int32(), "Ranking points earned at the tournament (WTA source only)."),
        field("prize_money_won", pa.int64(), "Prize money earned at the tournament (WTA source only)."),
    ]
)

SLAM_RESULT_SCHEMA = pa.schema(
    [
        field("season", pa.int32(), "Season (calendar year)."),
        field("tournament_id", pa.int32(), "ESPN tournament id."),
        field("tournament_name", pa.string(), "Australian Open, Roland Garros, Wimbledon or US Open."),
        field("tour", pa.string(), "ATP (men's), WTA (women's) or MIXED."),
        field("draw", pa.string(), "Draw, e.g. Men's Singles."),
        field("final_date", pa.date32(), "Date of the final (UTC)."),
        field("champion", pa.string(), "Winning player or pair."),
        field("champion_country", pa.string(), "Champion country code (first player for pairs)."),
        field("champion_seed", pa.int32(), "Champion seed."),
        field("runner_up", pa.string(), "Losing finalist or pair."),
        field("runner_up_country", pa.string(), "Runner-up country code."),
        field("runner_up_seed", pa.int32(), "Runner-up seed."),
        field("score", pa.string(), "Final score from the champion's view."),
        field("status", pa.string(), "completed, retired, walkover, in_progress or scheduled."),
    ]
)

# ------------------------------------------------------------------ parsing


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def to_int(value: Any) -> int | None:
    """Parse an integer-like value without treating booleans or blanks as numbers."""
    if value is None or isinstance(value, bool) or value == "":
        return None
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return int(number) if number.is_integer() else None


def to_text(value: Any) -> str | None:
    """Return a stripped string, or NULL for blanks and non-strings."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def to_timestamp(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp (ESPN's minute precision included) as UTC."""
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(text)
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def to_date(value: Any) -> date | None:
    """Parse an ISO date or the date part of an ISO timestamp."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def espn_event_date(value: Any, *, end: bool = False) -> date | None:
    """Return the US-Eastern calendar day of an ESPN tournament boundary.

    ESPN starts events at Eastern midnight (04:00Z in summer, 05:00Z in winter)
    and ends them at 23:59 Eastern (03:59Z or 04:59Z the next UTC day). Shifting
    starts back four hours and ends back five recovers the Eastern day in both
    daylight and standard time without a time-zone database.
    """
    stamp = to_timestamp(value)
    if stamp is None:
        return None
    return (stamp - timedelta(hours=5 if end else 4)).date()


def espn_dob(value: Any) -> date | None:
    """ESPN tennis displays dates of birth as day/month/year."""
    if not isinstance(value, str):
        return None
    parts = value.split("/")
    if len(parts) != 3:
        return None
    try:
        return date(int(parts[2]), int(parts[1]), int(parts[0]))
    except ValueError:
        return None


_HEIGHT = re.compile(r"(\d+)'\s*(\d+)")
_FLAG = re.compile(r"/countries/\d+/([a-z]{2,3})\.png", re.IGNORECASE)


def espn_height_inches(value: Any) -> int | None:
    """Parse ESPN's 6' 0\" display height as inches."""
    match = _HEIGHT.search(value) if isinstance(value, str) else None
    return int(match.group(1)) * 12 + int(match.group(2)) if match else None


def espn_weight_lbs(value: Any) -> int | None:
    """Parse ESPN's '163 lbs' display weight."""
    if not isinstance(value, str):
        return None
    return to_int(value.split()[0]) if value.split() else None


def espn_country(athlete: Mapping[str, Any]) -> str | None:
    """Recover the three-letter country code from ESPN's flag image path."""
    flag = athlete.get("flag")
    href = flag.get("href") if isinstance(flag, Mapping) else flag
    match = _FLAG.search(href) if isinstance(href, str) else None
    return match.group(1).upper() if match else None


def tour_of_draw(slug: str | None) -> str | None:
    """Map an ESPN draw slug to the tour that owns it."""
    if not slug:
        return None
    if slug.startswith("mens-"):
        return "ATP"
    if slug.startswith("womens-"):
        return "WTA"
    if slug.startswith("mixed-"):
        return "MIXED"
    return None


def format_score(sets: Sequence[Mapping[str, Any]], winner_side: int | None) -> str | None:
    """Render sets from the winner's view (side 1 if undecided), e.g. '7-6(5) 6-3'."""
    parts: list[str] = []
    for value in sets:
        first, second = value.get("side1_games"), value.get("side2_games")
        if first is None or second is None:
            continue
        if winner_side == 2:
            first, second = second, first
        text = f"{first}-{second}"
        if value.get("tiebreak_loser_points") is not None:
            text += f"({value['tiebreak_loser_points']})"
        parts.append(text)
    return " ".join(parts) or None


def _sets_won(sets: Sequence[Mapping[str, Any]]) -> tuple[int | None, int | None]:
    if not sets:
        return None, None
    first = second = 0
    for value in sets:
        one, two = value.get("side1_games"), value.get("side2_games")
        if one is None or two is None or one == two:
            continue
        if one > two:
            first += 1
        else:
            second += 1
    return first, second


def _espn_status(status: Mapping[str, Any]) -> tuple[str, str | None]:
    kind = _mapping(status.get("type"))
    name = kind.get("name")
    named = {
        "STATUS_FINAL": "completed",
        "STATUS_RETIRED": "retired",
        "STATUS_WALKOVER": "walkover",
        "STATUS_SCHEDULED": "scheduled",
        "STATUS_IN_PROGRESS": "in_progress",
    }
    if name in named:
        result = named[name]
    else:
        result = {"pre": "scheduled", "in": "in_progress"}.get(kind.get("state"), "other")
    return result, to_text(kind.get("detail") or kind.get("description"))


def _espn_side(competitor: Mapping[str, Any]) -> dict[str, Any]:
    """Return player/partner ids, names and country for one ESPN competitor."""
    ids = [to_int(value) for value in str(competitor.get("id") or "").split("-")]
    if competitor.get("type") == "team":
        athletes = [_mapping(value) for value in _mapping(competitor.get("roster")).get("athletes") or []]
    else:
        athletes = [_mapping(competitor.get("athlete"))]
    names = [to_text(value.get("displayName")) for value in athletes]
    display = to_text(_mapping(competitor.get("roster")).get("displayName")) or " / ".join(
        name for name in names if name
    )
    return {
        "player_id": ids[0] if ids else None,
        "player_name": names[0] if names else None,
        "country": espn_country(athletes[0]) if athletes else None,
        "partner_id": ids[1] if len(ids) > 1 else None,
        "partner_name": names[1] if len(names) > 1 else None,
        "seed": to_int(_mapping(competitor.get("curatedRank")).get("current")),
        "display": display or None,
        "winner": competitor.get("winner") is True,
        "linescores": [_mapping(value) for value in competitor.get("linescores") or []],
        "ids": {value for value in ids if value is not None},
    }


def _espn_sets(side1: Mapping[str, Any], side2: Mapping[str, Any]) -> list[dict[str, Any]]:
    sets: list[dict[str, Any]] = []
    for number, (one, two) in enumerate(zip(side1["linescores"], side2["linescores"], strict=False), 1):
        games1, games2 = to_int(one.get("value")), to_int(two.get("value"))
        tb1, tb2 = to_int(one.get("tiebreak")), to_int(two.get("tiebreak"))
        loser_points = None
        if tb1 is not None and tb2 is not None:
            loser_points = min(tb1, tb2)
        elif tb1 is not None or tb2 is not None:
            loser_points = tb1 if tb1 is not None else tb2
        sets.append(
            {
                "set_number": number,
                "side1_games": games1,
                "side2_games": games2,
                "tiebreak_loser_points": loser_points,
            }
        )
    return sets


def _espn_event_parts(event: Mapping[str, Any]) -> tuple[int | None, int | None]:
    event_id = str(event.get("id") or "")
    tournament, _, season = event_id.partition("-")
    return to_int(tournament), to_int(season) or to_int(_mapping(event.get("season")).get("year"))


def espn_competitions(
    events: Iterable[Mapping[str, Any]],
) -> Iterable[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]]:
    """Yield (event, grouping, competition) for every match in ESPN scoreboard events."""
    for event in events:
        for grouping in event.get("groupings") or []:
            grouping = _mapping(grouping)
            for competition in grouping.get("competitions") or []:
                if isinstance(competition, Mapping):
                    yield event, _mapping(grouping.get("grouping")), competition


def flatten_espn_match(
    event: Mapping[str, Any], grouping: Mapping[str, Any], competition: Mapping[str, Any]
) -> dict[str, Any]:
    """Flatten one ESPN competition into a MATCH_SCHEMA row."""
    competitors = sorted(
        (_mapping(value) for value in competition.get("competitors") or []),
        key=lambda value: to_int(value.get("order")) or 0,
    )
    empty = _espn_side({})
    side1 = _espn_side(competitors[0]) if competitors else empty
    side2 = _espn_side(competitors[1]) if len(competitors) > 1 else empty
    sets = _espn_sets(side1, side2)
    status, detail = _espn_status(_mapping(competition.get("status")))
    winner_side = 1 if side1["winner"] else 2 if side2["winner"] else None
    won1, won2 = _sets_won(sets)
    kind = _mapping(competition.get("type"))
    slug = to_text(kind.get("slug")) or to_text(grouping.get("slug"))
    round_name = to_text(_mapping(competition.get("round")).get("displayName"))
    tournament_id, season = _espn_event_parts(event)
    return {
        "tour": tour_of_draw(slug),
        "source": "espn",
        "season": season,
        "tournament_id": to_int(competition.get("tournamentId")) or tournament_id,
        "event_id": to_text(event.get("id")),
        "tournament_name": to_text(event.get("name")),
        "is_grand_slam": event.get("major") is True,
        "match_id": to_text(competition.get("id")),
        "draw": to_text(kind.get("text")) or to_text(grouping.get("displayName")),
        "draw_type": "doubles" if slug and slug.endswith("doubles") else "singles",
        "qualifying": bool(round_name and round_name.startswith("Qualifying")),
        "round": round_name,
        "scheduled_at": to_timestamp(competition.get("startDate") or competition.get("date")),
        "status": status,
        "status_detail": detail,
        "court": to_text(_mapping(competition.get("venue")).get("court")),
        **{f"side1_{key}": side1[key] for key in _SIDE_KEYS},
        **{f"side2_{key}": side2[key] for key in _SIDE_KEYS},
        "winner_side": winner_side,
        "winner_name": side1["display"] if winner_side == 1 else side2["display"] if winner_side else None,
        "loser_name": side2["display"] if winner_side == 1 else side1["display"] if winner_side else None,
        "score": format_score(sets, winner_side),
        "side1_sets_won": won1,
        "side2_sets_won": won2,
        "sets": sets,
    }


_SIDE_KEYS = ("player_id", "player_name", "country", "partner_id", "partner_name", "seed")


def flatten_espn_matches(
    events: Iterable[Mapping[str, Any]], *, tour: str = "ALL", day: date | None = None
) -> list[dict[str, Any]]:
    """Flatten ESPN events to match rows, de-duplicated and optionally filtered.

    ``tour`` keeps one tour's draws (ATP = men's, WTA = women's) or ``ALL``
    (including mixed doubles). ``day`` keeps matches scheduled on that UTC date.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event, grouping, competition in espn_competitions(events):
        row = flatten_espn_match(event, grouping, competition)
        key = row["match_id"] or f"{row['event_id']}:{len(rows)}"
        if key in seen:
            continue
        if tour != "ALL" and row["tour"] != tour:
            continue
        if day is not None and (row["scheduled_at"] is None or row["scheduled_at"].date() != day):
            continue
        seen.add(key)
        rows.append(row)
    return rows


def _city_country(text: str | None) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    city, _, country = text.rpartition(", ")
    return (city or country or None), (country if city else None)


def _espn_event_status(event: Mapping[str, Any]) -> str | None:
    state = _mapping(_mapping(event.get("status")).get("type")).get("state")
    return {"pre": "upcoming", "in": "in_progress", "post": "completed"}.get(state)


def flatten_espn_tournaments(events: Iterable[Mapping[str, Any]], tour: str) -> list[dict[str, Any]]:
    """Flatten ESPN events to TOURNAMENT_SCHEMA rows, with champions from the finals."""
    prefix = "mens-" if tour == "ATP" else "womens-"
    rows: list[dict[str, Any]] = []
    for event in events:
        tournament_id, season = _espn_event_parts(event)
        city, country = _city_country(to_text(_mapping(event.get("venue")).get("displayName")))
        champions: dict[str, dict[str, Any]] = {}
        statuses: set[str] = set()
        for _event, grouping, competition in espn_competitions([event]):
            row = flatten_espn_match(event, grouping, competition)
            statuses.add(row["status"])
            slug = to_text(_mapping(competition.get("type")).get("slug")) or ""
            if row["round"] == "Final" and slug.startswith(prefix) and row["winner_side"]:
                champions[row["draw_type"]] = row
        status = _espn_event_status(event)
        if status is None and statuses:
            status = "in_progress" if statuses & {"in_progress", "scheduled"} else "completed"
        singles = champions.get("singles")
        winner_side = singles["winner_side"] if singles else None
        rows.append(
            {
                "tour": tour,
                "source": "espn",
                "season": season,
                "tournament_id": tournament_id,
                "event_id": to_text(event.get("id")),
                "name": to_text(event.get("name")),
                "level": "Grand Slam" if event.get("major") is True else None,
                "is_grand_slam": event.get("major") is True,
                "surface": None,
                "indoor_outdoor": None,
                "city": city,
                "country": country,
                "start_date": espn_event_date(event.get("date")),
                "end_date": espn_event_date(event.get("endDate"), end=True),
                "singles_draw_size": None,
                "doubles_draw_size": None,
                "prize_money": None,
                "prize_money_currency": None,
                "status": status,
                "singles_champion_id": singles[f"side{winner_side}_player_id"] if singles else None,
                "singles_champion": singles["winner_name"] if singles else None,
                "doubles_champions": champions["doubles"]["winner_name"] if "doubles" in champions else None,
            }
        )
    rows.sort(key=lambda row: (row["start_date"] or date.max, row["tournament_id"] or 0))
    return rows


def flatten_slam_results(events: Iterable[Mapping[str, Any]], tour: str = "ALL") -> list[dict[str, Any]]:
    """One row per Grand Slam draw final."""
    rows: list[dict[str, Any]] = []
    majors = [event for event in events if event.get("major") is True]
    for row in flatten_espn_matches(majors, tour=tour):
        if row["round"] != "Final" or row["qualifying"]:
            continue
        side = row["winner_side"]
        other = 3 - side if side else None
        rows.append(
            {
                "season": row["season"],
                "tournament_id": row["tournament_id"],
                "tournament_name": row["tournament_name"],
                "tour": row["tour"],
                "draw": row["draw"],
                "final_date": row["scheduled_at"].date() if row["scheduled_at"] else None,
                "champion": row["winner_name"],
                "champion_country": row[f"side{side}_country"] if side else None,
                "champion_seed": row[f"side{side}_seed"] if side else None,
                "runner_up": row["loser_name"],
                "runner_up_country": row[f"side{other}_country"] if other else None,
                "runner_up_seed": row[f"side{other}_seed"] if other else None,
                "score": row["score"],
                "status": row["status"],
            }
        )
    rows.sort(key=lambda row: (row["final_date"] or date.max, row["draw"] or ""))
    return rows


def flatten_espn_player_matches(
    events: Iterable[Mapping[str, Any]], tour: str, player_id: int
) -> list[dict[str, Any]]:
    """Player-perspective rows for every ESPN match involving ``player_id``."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event, grouping, competition in espn_competitions(events):
        competitors = sorted(
            (_mapping(value) for value in competition.get("competitors") or []),
            key=lambda value: to_int(value.get("order")) or 0,
        )
        if len(competitors) != 2:
            continue
        sides = [_espn_side(value) for value in competitors]
        mine = next((index for index, side in enumerate(sides) if player_id in side["ids"]), None)
        if mine is None:
            continue
        match = flatten_espn_match(event, grouping, competition)
        if match["match_id"] in seen:
            continue
        seen.add(match["match_id"])
        me, them = sides[mine], sides[1 - mine]
        partner_id = me["partner_id"] if me["player_id"] == player_id else me["player_id"]
        partner_name = me["partner_name"] if me["player_id"] == player_id else me["player_name"]
        result = None
        if match["winner_side"]:
            result = "W" if match["winner_side"] == mine + 1 else "L"
        rows.append(
            {
                "tour": tour,
                "source": "espn",
                "season": match["season"],
                "player_id": player_id,
                "tournament_id": match["tournament_id"],
                "tournament_name": match["tournament_name"],
                "tournament_level": "Grand Slam" if match["is_grand_slam"] else None,
                "surface": None,
                "tournament_start_date": espn_event_date(event.get("date")),
                "match_id": match["match_id"],
                "scheduled_at": match["scheduled_at"],
                "draw_type": match["draw_type"],
                "qualifying": match["qualifying"],
                "round": match["round"],
                "partner_id": partner_id if match["draw_type"] == "doubles" else None,
                "partner_name": partner_name if match["draw_type"] == "doubles" else None,
                "opponent_id": them["player_id"],
                "opponent_name": them["player_name"],
                "opponent_country": them["country"],
                "opponent_partner_name": them["partner_name"],
                "player_seed": me["seed"],
                "opponent_seed": them["seed"],
                "player_rank": None,
                "opponent_rank": None,
                "result": result,
                "score": match["score"],
                "status": match["status"],
                "ranking_points": None,
                "prize_money_won": None,
            }
        )
    rows.sort(key=lambda row: (row["scheduled_at"] or datetime.max.replace(tzinfo=UTC), row["match_id"]))
    return rows


def flatten_espn_rankings(payload: Mapping[str, Any], tour: str) -> list[dict[str, Any]]:
    """Flatten ESPN's singles ranking."""
    rows: list[dict[str, Any]] = []
    for ranking in payload.get("rankings") or []:
        ranking = _mapping(ranking)
        ranked_at = to_date(ranking.get("update"))
        for entry in ranking.get("ranks") or []:
            entry = _mapping(entry)
            athlete = _mapping(entry.get("athlete"))
            current, previous = to_int(entry.get("current")), to_int(entry.get("previous"))
            rows.append(
                {
                    "tour": tour,
                    "ranking_type": "singles",
                    "source": "espn",
                    "ranked_at": ranked_at,
                    "ranking": current,
                    "previous_ranking": previous,
                    "movement": previous - current if previous and current else None,
                    "points": to_int(entry.get("points")),
                    "player_id": to_int(athlete.get("id")),
                    "player_name": to_text(athlete.get("displayName")),
                    "first_name": to_text(athlete.get("firstName")),
                    "last_name": to_text(athlete.get("lastName")),
                    "country": to_text(athlete.get("citizenshipCountry")) or espn_country(athlete),
                    "birth_date": None,
                    "age": to_int(athlete.get("age")),
                    "tournaments_played": None,
                }
            )
    return rows


def flatten_espn_player(payload: Mapping[str, Any] | None, tour: str) -> list[dict[str, Any]]:
    """Flatten an ESPN common-v3 athlete profile."""
    athlete = _mapping(_mapping(payload).get("athlete"))
    if not athlete.get("id"):
        return []
    return [
        {
            "tour": tour,
            "source": "espn",
            "player_id": to_int(athlete.get("id")),
            "player_name": to_text(athlete.get("fullName")) or to_text(athlete.get("displayName")),
            "first_name": to_text(athlete.get("firstName")),
            "last_name": to_text(athlete.get("lastName")),
            "country": None,
            "birth_date": espn_dob(athlete.get("displayDOB")),
            "age": to_int(athlete.get("age")),
            "height_inches": espn_height_inches(athlete.get("displayHeight")),
            "weight_lbs": espn_weight_lbs(athlete.get("displayWeight")),
            "plays": to_text(_mapping(athlete.get("hand")).get("displayValue")),
            "debut_year": to_int(athlete.get("debutYear")),
            "active": athlete.get("active") if isinstance(athlete.get("active"), bool) else None,
        }
    ]


# ---------------------------------------------------------------------- WTA


def flatten_wta_rankings(payload: Sequence[Mapping[str, Any]], ranking_type: str) -> list[dict[str, Any]]:
    """Flatten the official WTA ranking."""
    rows: list[dict[str, Any]] = []
    for entry in payload:
        player = _mapping(entry.get("player"))
        ranking, movement = to_int(entry.get("ranking")), to_int(entry.get("movement"))
        rows.append(
            {
                "tour": "WTA",
                "ranking_type": ranking_type,
                "source": "wta",
                "ranked_at": to_date(entry.get("rankedAt")),
                "ranking": ranking,
                "previous_ranking": ranking + movement if ranking and movement is not None else None,
                "movement": movement,
                "points": to_int(entry.get("points")),
                "player_id": to_int(player.get("id")),
                "player_name": to_text(player.get("fullName")),
                "first_name": to_text(player.get("firstName")),
                "last_name": to_text(player.get("lastName")),
                "country": to_text(player.get("countryCode")),
                "birth_date": to_date(player.get("dateOfBirth")),
                "age": None,
                "tournaments_played": to_int(entry.get("tournamentsPlayed")),
            }
        )
    return rows


def _wta_status(value: Any) -> str | None:
    text = to_text(value)
    if text is None:
        return None
    return {
        "past": "completed",
        "inprogress": "in_progress",
        "live": "in_progress",
        "future": "upcoming",
    }.get(text.lower().replace("_", "").replace(" ", ""), text.lower())


def flatten_wta_tournaments(payload: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Flatten the WTA tournament calendar."""
    rows: list[dict[str, Any]] = []
    today = datetime.now(UTC).date()
    for entry in payload:
        group = _mapping(entry.get("tournamentGroup"))
        season = to_int(entry.get("year"))
        tournament_id = to_int(group.get("id"))
        start, end = to_date(entry.get("startDate")), to_date(entry.get("endDate"))
        status = _wta_status(entry.get("status"))
        if status is None and start and end:
            status = "upcoming" if start > today else "completed" if end < today else "in_progress"
        singles_id = singles_name = doubles = None
        for winner in entry.get("winners") or []:
            winner = _mapping(winner)
            single = _mapping(_mapping(winner.get("singles")).get("player"))
            if single:
                singles_id, singles_name = to_int(single.get("id")), to_text(single.get("fullName"))
            pair = [
                to_text(_mapping(_mapping(value).get("player")).get("fullName"))
                for value in winner.get("doubles") or []
            ]
            if any(pair):
                doubles = " / ".join(name for name in pair if name)
        level = to_text(entry.get("level")) or to_text(group.get("level"))
        rows.append(
            {
                "tour": "WTA",
                "source": "wta",
                "season": season,
                "tournament_id": tournament_id,
                "event_id": f"{tournament_id}-{season}" if tournament_id and season else None,
                "name": to_text(entry.get("title")) or to_text(group.get("name")),
                "level": level,
                "is_grand_slam": level == "Grand Slam",
                "surface": to_text(entry.get("surface")),
                "indoor_outdoor": to_text(entry.get("inOutdoor")),
                "city": to_text(entry.get("city")),
                "country": to_text(entry.get("country")),
                "start_date": start,
                "end_date": end,
                "singles_draw_size": to_int(entry.get("singlesDrawSize")) or None,
                "doubles_draw_size": to_int(entry.get("doublesDrawSize")) or None,
                "prize_money": to_int(entry.get("prizeMoney")),
                "prize_money_currency": to_text(entry.get("prizeMoneyCurrency")),
                "status": status,
                "singles_champion_id": singles_id,
                "singles_champion": singles_name,
                "doubles_champions": doubles,
            }
        )
    rows.sort(key=lambda row: (row["start_date"] or date.max, row["tournament_id"] or 0))
    return rows


# WTA draw codes: Winner 2/3 = side A/B won, 4/5 = A/B by retirement, 6/7 = A/B by walkover.
_WTA_WINNER = {"2": (1, "completed"), "3": (2, "completed"), "4": (1, "retired"), "5": (2, "retired")}
_WTA_WINNER |= {"6": (1, "walkover"), "7": (2, "walkover")}
_WTA_ROUND = {"Q": "Quarterfinal", "S": "Semifinal", "F": "Final"}


def _wta_round(code: Any, qualifying: bool) -> str | None:
    text = to_text(code)
    if qualifying:
        return f"Qualifying Round {text}" if text and text.isdigit() else "Qualifying"
    if text is None:
        return None
    return _WTA_ROUND.get(text, f"Round {text}" if text.isdigit() else text)


def _wta_name(first: Any, last: Any) -> str | None:
    return " ".join(part for part in (to_text(first), to_text(last)) if part) or None


def flatten_wta_matches(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten a WTA tournament's draws into MATCH_SCHEMA rows."""
    tournament = _mapping(payload.get("tournament"))
    group = _mapping(tournament.get("tournamentGroup"))
    rows: list[dict[str, Any]] = []
    for match in payload.get("matches") or []:
        match = _mapping(match)
        season = to_int(match.get("EventYear")) or to_int(tournament.get("year"))
        tournament_id = to_int(match.get("EventID")) or to_int(group.get("id"))
        doubles = match.get("DrawMatchType") == "D"
        qualifying = match.get("DrawLevelType") == "Q"
        winner_side, status = _WTA_WINNER.get(str(match.get("Winner") or ""), (None, None))
        state = to_text(match.get("MatchState"))
        if status is None:
            status = {"F": "completed", "P": "in_progress", "L": "in_progress"}.get(state or "", "scheduled")
        sets = []
        for number in range(1, 6):
            one, two = to_int(match.get(f"ScoreSet{number}A")), to_int(match.get(f"ScoreSet{number}B"))
            if one is None and two is None:
                continue
            sets.append(
                {
                    "set_number": number,
                    "side1_games": one,
                    "side2_games": two,
                    "tiebreak_loser_points": to_int(match.get(f"ScoreTbSet{number}")),
                }
            )
        names = {}
        for side, suffix in ((1, "A"), (2, "B")):
            player = _wta_name(match.get(f"PlayerNameFirst{suffix}"), match.get(f"PlayerNameLast{suffix}"))
            partner = _wta_name(match.get(f"PlayerNameFirst{suffix}2"), match.get(f"PlayerNameLast{suffix}2"))
            names[side] = (player, partner, " / ".join(name for name in (player, partner) if name) or None)
        won1, won2 = _sets_won(sets)
        level = to_text(group.get("level")) or to_text(tournament.get("level"))
        row: dict[str, Any] = {
            "tour": "WTA",
            "source": "wta",
            "season": season,
            "tournament_id": tournament_id,
            "event_id": f"{tournament_id}-{season}" if tournament_id and season else None,
            "tournament_name": to_text(tournament.get("title")) or to_text(group.get("name")),
            "is_grand_slam": match.get("GrandSlam") is True or level == "Grand Slam",
            "match_id": to_text(match.get("MatchID")),
            "draw": "Women's Doubles" if doubles else "Women's Singles",
            "draw_type": "doubles" if doubles else "singles",
            "qualifying": qualifying,
            "round": _wta_round(match.get("RoundID"), qualifying),
            "scheduled_at": to_timestamp(match.get("MatchTimeStamp")),
            "status": status,
            "status_detail": to_text(match.get("ResultString")) or state,
            "court": to_text(_mapping(match.get("Venue")).get("name")),
            "winner_side": winner_side,
            "winner_name": names[winner_side][2] if winner_side else None,
            "loser_name": names[3 - winner_side][2] if winner_side else None,
            "score": format_score(sets, winner_side),
            "side1_sets_won": won1,
            "side2_sets_won": won2,
            "sets": sets,
        }
        for side, suffix in ((1, "A"), (2, "B")):
            row[f"side{side}_player_id"] = to_int(match.get(f"PlayerID{suffix}"))
            row[f"side{side}_player_name"] = names[side][0]
            row[f"side{side}_country"] = to_text(match.get(f"PlayerCountry{suffix}"))
            row[f"side{side}_partner_id"] = to_int(match.get(f"PlayerID{suffix}2"))
            row[f"side{side}_partner_name"] = names[side][1]
            row[f"side{side}_seed"] = to_int(match.get(f"Seed{suffix}"))
        rows.append(row)
    rows.sort(
        key=lambda row: (row["scheduled_at"] or datetime.max.replace(tzinfo=UTC), row["match_id"] or "")
    )
    return rows


def flatten_wta_player(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Flatten a WTA player profile."""
    player = _mapping(payload)
    if not player.get("id"):
        return []
    return [
        {
            "tour": "WTA",
            "source": "wta",
            "player_id": to_int(player.get("id")),
            "player_name": to_text(player.get("fullName")),
            "first_name": to_text(player.get("firstName")),
            "last_name": to_text(player.get("lastName")),
            "country": to_text(player.get("countryCode")),
            "birth_date": to_date(player.get("dateOfBirth")),
            "age": None,
            "height_inches": None,
            "weight_lbs": None,
            "plays": None,
            "debut_year": None,
            "active": None,
        }
    ]


_WTA_PLAYER_ROUND = {"Q": "Quarterfinal", "S": "Semifinal", "F": "Final"}
_WTA_REASON = {"W": "completed", "R": "retired", "D": "walkover"}
_WTA_LEVEL = {"GS": "Grand Slam", "PM": "WTA 1000", "P": "WTA 500", "IS": "WTA 250", "I": "WTA 250"}


def _wta_player_round(code: Any, qualifying: bool) -> str | None:
    text = to_text(code)
    if text is None:
        return None
    if text in _WTA_PLAYER_ROUND:
        name = _WTA_PLAYER_ROUND[text]
    elif text.startswith("R") and text[1:].isdigit():
        name = f"Round of {text[1:]}"
    else:
        name = text
    return f"Qualifying {name}" if qualifying else name


def _person(value: Any) -> Mapping[str, Any]:
    return _mapping(value)


def flatten_wta_player_matches(payload: Iterable[Mapping[str, Any]], player_id: int) -> list[dict[str, Any]]:
    """Player-perspective rows from the WTA player-results endpoint; byes are omitted."""
    rows: list[dict[str, Any]] = []
    for match in payload:
        if match.get("reason_code") == "B" or str(match.get("player_2") or "").upper() == "BYE":
            continue
        tournament = _mapping(match.get("tournament"))
        group = _mapping(tournament.get("tournamentGroup"))
        opponent, partner = _person(match.get("opponent")), _person(match.get("partner"))
        opponent_partner = _person(match.get("opponent_partner"))
        qualifying = match.get("qpm_flag") == "Q"
        winner = to_int(match.get("winner"))
        scores = to_text(match.get("scores"))
        rows.append(
            {
                "tour": "WTA",
                "source": "wta",
                "season": to_int(match.get("tourn_year")) or to_int(tournament.get("year")),
                "player_id": player_id,
                "tournament_id": to_int(match.get("tourn_nbr")) or to_int(group.get("id")),
                "tournament_name": to_text(tournament.get("title")) or to_text(match.get("TournamentName")),
                "tournament_level": to_text(group.get("level"))
                or _WTA_LEVEL.get(to_text(match.get("TournamentLevel")) or "")
                or to_text(match.get("TournamentLevel")),
                "surface": to_text(tournament.get("surface")) or to_text(match.get("Surface")),
                "tournament_start_date": to_date(match.get("StartDate")),
                "match_id": None,
                "scheduled_at": None,
                "draw_type": "doubles" if match.get("s_d_flag") == "D" else "singles",
                "qualifying": qualifying,
                "round": _wta_player_round(match.get("round_name"), qualifying),
                "partner_id": to_int(partner.get("id")),
                "partner_name": to_text(partner.get("fullName")),
                "opponent_id": to_int(opponent.get("id")) or to_int(match.get("player_2")),
                "opponent_name": to_text(opponent.get("fullName")),
                "opponent_country": to_text(opponent.get("countryCode")),
                "opponent_partner_name": to_text(opponent_partner.get("fullName")),
                "player_seed": to_int(match.get("seed_1")),
                "opponent_seed": to_int(match.get("seed_2")),
                "player_rank": to_int(match.get("rank_1")) or None,
                "opponent_rank": to_int(match.get("rank_2")) or None,
                "result": "W" if winner == 1 else "L" if winner == 2 else None,
                "score": " ".join(scores.split()) if scores else None,
                "status": _WTA_REASON.get(to_text(match.get("reason_code")) or "", "completed")
                if winner in (1, 2)
                else "scheduled",
                "ranking_points": to_int(match.get("points_1")),
                "prize_money_won": to_int(match.get("PrizeWon")),
            }
        )
    rows.sort(key=lambda row: (row["tournament_start_date"] or date.max, row["tournament_id"] or 0))
    return rows


# -------------------------------------------------------------------- batches


def batch_from_rows(rows: Sequence[Mapping[str, Any]], schema: pa.Schema) -> pa.RecordBatch:
    """Build a record batch in the declared (possibly projected) column order."""
    return pa.RecordBatch.from_arrays(
        [pa.array([row.get(value.name) for row in rows], type=value.type) for value in schema],
        schema=schema,
    )
