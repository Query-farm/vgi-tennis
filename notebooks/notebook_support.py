"""Replay an unchanged public WTA response or configure direct live retrieval."""

import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import matplotlib.pyplot as plt
import seaborn as sns
from IPython.display import Markdown, display

ENVIRONMENT = ("TENNIS_WTA_API_BASE_URL", "TENNIS_ESPN_SITE_BASE_URL", "TENNIS_ESPN_COMMON_BASE_URL")
ROUTE = "/wta/tournaments/901/2026/matches"


class ReplayHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.server.requests.append(self.path)
        valid = self.path == ROUTE
        body = self.server.document if valid else b'{"error":"unrecorded request"}'
        self.send_response(200 if valid else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def configure(mode, margin):
    if mode not in ("replay", "live"):
        raise ValueError("DATA_MODE must be 'replay' or 'live'")
    if type(margin) is not int or not 0 <= margin <= 6:
        raise ValueError("CLOSE_SET_MARGIN must be an integer from 0 through 6 games")
    previous = {key: os.environ.pop(key, None) for key in ENVIRONMENT}
    server = thread = None
    folder = Path(__file__).parent / "recordings"
    provenance = json.loads((folder / "provenance.json").read_text())
    if mode == "replay":
        body = (folder / "australian-open-2026.json").read_bytes()
        if hashlib.sha256(body).hexdigest() != provenance["sha256"]:
            raise ValueError("Recording checksum mismatch")
        server = ThreadingHTTPServer(("127.0.0.1", 0), ReplayHandler)
        server.document = body
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        os.environ.update(
            TENNIS_WTA_API_BASE_URL=base + "/wta",
            TENNIS_ESPN_SITE_BASE_URL=base + "/unrecorded-espn-site",
            TENNIS_ESPN_COMMON_BASE_URL=base + "/unrecorded-espn-common",
        )
    return server, thread, previous, datetime.now(UTC).isoformat(), provenance


def audit(server):
    if server is not None:
        assert server.requests == [ROUTE], "Expected exactly one request matching the recorded tournament"
        return "Replay audit: exactly one WTA tournament request; no additional provider requests."
    return "Live run: one materialized WTA tournament retrieval; no replay server was created."


def cleanup(server, thread, previous):
    if server:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def prose(text: str) -> None:
    display(Markdown(text))


def theme() -> None:
    sns.set_theme(
        style="whitegrid",
        rc={
            "figure.dpi": 145,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "grid.color": "#E8EDE9",
            "text.color": "#243B2F",
            "axes.labelcolor": "#405047",
            "text.parse_math": False,
            "legend.frameon": False,
        },
    )


def chart(fig, title: str, subtitle: str, source: str) -> None:
    fig.text(0.08, 0.98, title, ha="left", va="top", fontsize=17, fontweight="semibold")
    fig.text(0.08, 0.90, subtitle, ha="left", va="top", fontsize=10, color="#58685F")
    fig.tight_layout(rect=(0.02, 0.11, 1, 0.82))
    fig.text(0.08, 0.03, source, fontsize=8, color="#6C7D73")
    plt.show()
