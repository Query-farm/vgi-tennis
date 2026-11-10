"""Hermetic end-to-end coverage through Haybarn and the VGI transport."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from fixture_server import FixtureServer


def test_haybarn_attaches_and_queries_every_current_table() -> None:
    project = Path(__file__).resolve().parent.parent
    location = f"uv run --project {project} vgi-tennis"
    sql = (
        f"INSTALL vgi FROM community; LOAD vgi; ATTACH 'tennis' (TYPE vgi, LOCATION '{location}'); "
        "SELECT "
        "(SELECT count(*) FROM tennis.main.current_rankings) AS rankings, "
        "(SELECT count(*) FROM tennis.main.current_matches) AS matches, "
        "(SELECT max(winner_name) FROM tennis.main.tournament_matches('WTA', 901, 2026) "
        "WHERE round = 'Final' AND draw_type = 'singles') AS ao_champion, "
        "(SELECT max(champion) FROM tennis.main.grand_slam_results(2026, tour := 'ATP') "
        "WHERE draw = 'Men''s Singles') AS slam_champion, "
        "(SELECT max(plays) FROM tennis.main.player('ATP', 3782)) AS plays;"
    )
    with FixtureServer() as server:
        result = subprocess.run(
            ["uvx", "haybarn-cli", ":memory:", "-csv", "-c", sql],
            cwd=project,
            env={**os.environ, **server.environment},
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )

    assert "rankings,matches,ao_champion,slam_champion,plays" in result.stdout
    assert "10,12,Elena Rybakina,Jannik Sinner,Right" in result.stdout
