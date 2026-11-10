"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixture_server import FixtureServer  # noqa: E402

from vgi_tennis import api  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    """Keep the in-process response cache from leaking between tests."""
    api.CACHE.clear()
    yield
    api.CACHE.clear()


@pytest.fixture(scope="session")
def fixture_server() -> Iterator[FixtureServer]:
    """A replay server for recorded upstream responses."""
    with FixtureServer() as server:
        yield server


@pytest.fixture
def replay(fixture_server: FixtureServer, monkeypatch: pytest.MonkeyPatch) -> FixtureServer:
    """Point the worker's HTTP client at the replay server."""
    for name, value in fixture_server.environment.items():
        monkeypatch.setenv(name, value)
    return fixture_server
