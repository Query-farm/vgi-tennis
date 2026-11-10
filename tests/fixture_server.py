"""A local HTTP server that replays recorded WTA and ESPN responses.

Paths mirror the upstream APIs under three prefixes, matching the worker's
base-URL environment overrides (see ``environment``).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name: str) -> object:
    """Return a decoded fixture."""
    return json.loads((FIXTURES / name).read_text())


def route(path: str, query: dict[str, list[str]]) -> object | None:
    """Map a request to a fixture payload; ``None`` means 404."""
    dates = query.get("dates", [""])[0]
    if path == "/wta/players/ranked":
        if query.get("page", ["0"])[0] != "0":
            return []
        doubles = query.get("metric", [""])[0] == "DOUBLES"
        return load("wta_rankings_doubles.json" if doubles else "wta_rankings_singles.json")
    if path == "/wta/tournaments/":
        return load("wta_tournaments_2026.json")
    if path == "/wta/tournaments/901/2026/matches":
        return load("wta_tournament_901_2026.json")
    if path == "/wta/tournaments/806/2026/matches":
        return load("wta_tournament_806_2026.json")
    if path.startswith("/wta/tournaments/") and path.endswith("/matches"):
        return {"matches": []}
    if path == "/wta/players/324166":
        return load("wta_player_324166.json")
    if path == "/wta/players/324166/matches/":
        return (
            load("wta_player_324166_matches_2026.json") if query.get("year") == ["2026"] else {"matches": []}
        )
    if path in ("/espn-site/atp/scoreboard", "/espn-site/wta/scoreboard"):
        tour = path.split("/")[2]
        if not dates:
            return load(f"espn_{tour}_scoreboard.json")
        if tour == "atp" and dates.startswith("202607"):
            return load("espn_atp_scoreboard_202607.json")
        return {"leagues": [], "events": []}
    if path == "/espn-site/atp/rankings":
        return load("espn_atp_rankings.json")
    if path == "/espn-common/atp/athletes/3782":
        return load("espn_atp_athlete_3782.json")
    return None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        payload = route(parsed.path, parse_qs(parsed.query))
        if payload is None:
            self.send_error(404)
            return
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "max-age=30")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FixtureServer:
    """Context manager running the replay server on an ephemeral port."""

    def __enter__(self) -> FixtureServer:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def environment(self) -> dict[str, str]:
        """Base-URL overrides pointing the worker at this server."""
        root = f"http://127.0.0.1:{self.server.server_port}"
        return {
            "TENNIS_WTA_API_BASE_URL": f"{root}/wta",
            "TENNIS_ESPN_SITE_BASE_URL": f"{root}/espn-site",
            "TENNIS_ESPN_COMMON_BASE_URL": f"{root}/espn-common",
        }
