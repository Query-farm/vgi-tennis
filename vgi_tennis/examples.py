"""Example queries that combine several of the catalog's tables and functions.

Each SQL string is defined once here and referenced from the schema-level example list and from
the example list of the object that drives the query.
"""

from __future__ import annotations

ATP_RANKING_UPSETS_DESCRIPTION = (
    "This week's completed ATP singles matches won by the lower-ranked player, joining "
    "current_matches to current_rankings for both players (ATP ids are ESPN ids on both sides; "
    "players outside the top 150 drop out)."
)
ATP_RANKING_UPSETS_SQL = (
    "SELECT m.tournament_name, m.round, m.winner_name, w.ranking AS winner_rank, "
    "m.loser_name, l.ranking AS loser_rank, m.score "
    "FROM tennis.main.current_matches AS m "
    "JOIN tennis.main.current_rankings AS w ON w.tour = 'ATP' "
    "AND w.player_id = if(m.winner_side = 1, m.side1_player_id, m.side2_player_id) "
    "JOIN tennis.main.current_rankings AS l ON l.tour = 'ATP' "
    "AND l.player_id = if(m.winner_side = 1, m.side2_player_id, m.side1_player_id) "
    "WHERE m.tour = 'ATP' AND m.draw_type = 'singles' AND m.status = 'completed' "
    "AND w.ranking > l.ranking "
    "ORDER BY w.ranking - l.ranking DESC, m.match_id"
)

WINS_OVER_TOP_TEN_DESCRIPTION = (
    "Elena Rybakina's 2026 singles wins against players now in the WTA top 10, joining "
    "player_matches to current_rankings on the WTA opponent id."
)
WINS_OVER_TOP_TEN_SQL = (
    "SELECT p.opponent_name, r.ranking AS current_rank, count(*) AS wins "
    "FROM tennis.main.player_matches('WTA', 324166, season := 2026) AS p "
    "JOIN tennis.main.current_rankings AS r ON r.tour = 'WTA' AND r.player_id = p.opponent_id "
    "WHERE p.draw_type = 'singles' AND p.result = 'W' AND r.ranking <= 10 "
    "GROUP BY ALL ORDER BY current_rank"
)

LOSSES_AND_CHAMPIONS_DESCRIPTION = (
    "Every 2026 tournament where Elena Rybakina lost a singles match: the round, who beat her, "
    "and the event's eventual singles champion from the WTA calendar."
)
LOSSES_AND_CHAMPIONS_SQL = (
    "SELECT p.tournament_name, p.round, p.opponent_name AS lost_to, t.singles_champion "
    "FROM tennis.main.player_matches('WTA', 324166, season := 2026) AS p "
    "JOIN tennis.main.tournaments('WTA', season := 2026) AS t ON t.tournament_id = p.tournament_id "
    "WHERE p.draw_type = 'singles' AND p.result = 'L' "
    "ORDER BY p.tournament_start_date"
)

SLAM_CHAMPIONS_RANKED_DESCRIPTION = (
    "Each 2026 Grand Slam singles champion with their current ranking and points, joining "
    "grand_slam_results to current_rankings by tour and player name (Slam finals carry no player ids)."
)
SLAM_CHAMPIONS_RANKED_SQL = (
    "SELECT g.tournament_name, g.draw, g.champion, r.ranking AS current_rank, r.points "
    "FROM tennis.main.grand_slam_results(2026) AS g "
    "LEFT JOIN tennis.main.current_rankings AS r ON r.tour = g.tour AND r.player_name = g.champion "
    "WHERE g.draw IN ('Men''s Singles', 'Women''s Singles') "
    "ORDER BY g.final_date, g.draw"
)

COMPOSED_EXAMPLES: list[tuple[str, str]] = [
    (ATP_RANKING_UPSETS_DESCRIPTION, ATP_RANKING_UPSETS_SQL),
    (WINS_OVER_TOP_TEN_DESCRIPTION, WINS_OVER_TOP_TEN_SQL),
    (LOSSES_AND_CHAMPIONS_DESCRIPTION, LOSSES_AND_CHAMPIONS_SQL),
    (SLAM_CHAMPIONS_RANKED_DESCRIPTION, SLAM_CHAMPIONS_RANKED_SQL),
]
