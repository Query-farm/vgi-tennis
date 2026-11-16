"""Serve the recorded upstream fixtures over HTTP for the SQL tests.

``ci/run-integration.sh`` starts this server, points the worker's base-URL
environment variables at it, and runs ``test/sql`` through DuckDB, so the
suite is hermetic. Prints ``PORT:<n>`` once listening.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))

from fixture_server import FixtureServer  # noqa: E402


def main() -> None:
    """Serve until killed."""
    with FixtureServer() as fixtures:
        print(f"PORT:{fixtures.server.server_port}", flush=True)
        threading.Event().wait()


if __name__ == "__main__":
    main()
