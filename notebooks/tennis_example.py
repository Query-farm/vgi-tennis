"""Verify and replay complete public tennis responses; calculations stay in SQL."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlsplit

ENVIRONMENT = ("TENNIS_WTA_API_BASE_URL", "TENNIS_ESPN_SITE_BASE_URL", "TENNIS_ESPN_COMMON_BASE_URL")


class TennisExample:
    """Own one bounded recording scenario and restore all inherited endpoints."""

    def __init__(self, mode: str, scenario: str):
        if mode not in {"replay", "live"}:
            raise ValueError("DATA_MODE must be replay or live")
        if scenario not in {"sets", "rankings", "season"}:
            raise ValueError("Unknown tennis example scenario")
        root = Path(__file__).resolve().parent / "recordings"
        self.mode = mode
        self.scenario = scenario
        self.started_at = datetime.now(UTC).isoformat()
        self.requests: list[str] = []
        self.unexpected: list[str] = []
        self.server = self.thread = None
        if scenario == "sets":
            self.provenance = json.loads((root / "provenance.json").read_text())
            entries = [
                dict(
                    self.provenance,
                    file="australian-open-2026.json",
                    route="/wta/tournaments/901/2026/matches",
                    query={},
                )
            ]
        else:
            self.provenance = json.loads((root / "extended-provenance.json").read_text())
            entries = [
                x
                for x in self.provenance["responses"]
                if ("rankings" in x["file"]) == (scenario == "rankings")
            ]
        self.capture_times = [x["captured_utc"] for x in entries]
        self.expected = [x["file"] for x in entries]
        responses = {}
        for item in entries:
            body = (root / item["file"]).read_bytes()
            if hashlib.sha256(body).hexdigest() != item["sha256"]:
                raise ValueError("Recorded response checksum mismatch: " + item["file"])
            key = (item["route"], tuple(sorted(item["query"].items())))
            if key in responses:
                raise ValueError("Duplicate recorded request")
            responses[key] = (item["file"], body)
        self.previous = {key: os.environ.pop(key, None) for key in ENVIRONMENT}
        if mode == "live":
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                route = urlsplit(self.path)
                query = parse_qs(route.query, keep_blank_values=True)
                if any(len(v) != 1 for v in query.values()):
                    owner.unexpected.append(self.path)
                    self.send_error(400)
                    return
                key = (route.path, tuple(sorted((k, v[0]) for k, v in query.items())))
                if key not in responses:
                    owner.unexpected.append(self.path)
                    self.send_error(404, "Unrecorded request; no forwarding")
                    return
                name, body = responses[key]
                owner.requests.append(name)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        base = f"http://127.0.0.1:{self.server.server_port}"
        os.environ.update(
            TENNIS_WTA_API_BASE_URL=base + "/wta",
            TENNIS_ESPN_SITE_BASE_URL=base + "/espn-site",
            TENNIS_ESPN_COMMON_BASE_URL=base + "/unrecorded-common",
        )

    def audit(self) -> str:
        """Require every scenario response exactly once; live retries are not measured."""
        if self.mode == "live":
            return "Direct public reads; no replay fallback. HTTP retry counts are not instrumented."
        assert not self.unexpected, self.unexpected
        assert Counter(self.requests) == Counter(self.expected), (self.requests, self.expected)
        return f"Exactly {len(self.expected)} recorded responses requested once each; no unexpected requests."

    def close(self):
        """Stop the local server and restore prior environment values."""
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
