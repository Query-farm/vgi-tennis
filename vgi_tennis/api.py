"""Small, read-only client for the WTA's public API and ESPN's tennis endpoints.

Every outbound request in the worker passes through :func:`get_json`. It applies
timeouts, bounded retries with ``Retry-After`` support, an honest User-Agent, and
an in-process response cache so repeated SQL queries do not re-fetch the same
upstream document while it is still fresh.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import random
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from vgi_tennis import __version__

USER_AGENT = f"vgi-tennis/{__version__} (+https://github.com/Query-farm/vgi-tennis)"

DEFAULT_WTA_BASE_URL = "https://api.wtatennis.com/tennis"
DEFAULT_ESPN_SITE_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/tennis"
DEFAULT_ESPN_COMMON_BASE_URL = "https://site.web.api.espn.com/apis/common/v3/sports/tennis"

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
MAX_RETRY_DELAY = 30.0
QUERY_TIMEOUT = 120.0
MAX_CONCURRENT_REQUESTS = 4

# The WTA API caps pageSize at 100 rows.
WTA_PAGE_SIZE = 100
WTA_MAX_PAGES = 20

# Cache policy. Upstream max-age values are short (WTA 30 s, ESPN 9 s), so a
# floor keeps a burst of SQL queries from re-fetching the same document. Past
# seasons are effectively immutable and are kept longer.
LIVE_TTL = 60
SEASON_TTL = 600
HISTORICAL_TTL = 6 * 3600
CACHE_MAX_BYTES = 96 * 1024 * 1024

TOURS = ("ATP", "WTA")

logger = logging.getLogger(__name__)
_request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)


class TennisApiError(RuntimeError):
    """A failed response from an upstream tennis endpoint."""

    def __init__(self, source: str, status: int, body: str) -> None:
        super().__init__(f"{source} returned HTTP {status}: {body[:400]}")
        self.source = source
        self.status = status


@dataclass(frozen=True, slots=True)
class Fetched:
    """A decoded upstream document plus the freshness the worker may advertise."""

    payload: Any
    ttl: int


class _ResponseCache:
    """A byte-bounded LRU of raw upstream bodies with per-entry expiry."""

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._entries: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def lookup(self, key: str) -> tuple[bytes, int] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires, body = entry
            remaining = expires - time.monotonic()
            if remaining <= 0:
                self._drop(key)
                return None
            self._entries.move_to_end(key)
            return body, max(1, int(remaining))

    def store(self, key: str, body: bytes, ttl: int) -> None:
        if ttl <= 0 or len(body) > self._max_bytes:
            return
        with self._lock:
            if key in self._entries:
                self._drop(key)
            self._entries[key] = (time.monotonic() + ttl, body)
            self._bytes += len(body)
            while self._bytes > self._max_bytes and self._entries:
                self._drop(next(iter(self._entries)))

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def _drop(self, key: str) -> None:
        _expires, body = self._entries.pop(key)
        self._bytes -= len(body)


CACHE = _ResponseCache(CACHE_MAX_BYTES)


def wta_base_url() -> str:
    """Return the WTA API root, optionally overridden for hermetic tests."""
    return os.environ.get("TENNIS_WTA_API_BASE_URL", DEFAULT_WTA_BASE_URL).rstrip("/")


def espn_site_base_url() -> str:
    """Return ESPN's tennis site-API root, optionally overridden for tests."""
    return os.environ.get("TENNIS_ESPN_SITE_BASE_URL", DEFAULT_ESPN_SITE_BASE_URL).rstrip("/")


def espn_common_base_url() -> str:
    """Return ESPN's tennis common-v3 root, optionally overridden for tests."""
    return os.environ.get("TENNIS_ESPN_COMMON_BASE_URL", DEFAULT_ESPN_COMMON_BASE_URL).rstrip("/")


def current_season() -> int:
    """Tennis seasons follow the calendar year."""
    return datetime.now(UTC).year


def season_ttl(season: int) -> int:
    """Cache lifetime for a season-scoped document."""
    return SEASON_TTL if season >= current_season() else HISTORICAL_TTL


@contextmanager
def _client(client: httpx.Client | None):
    """Reuse one connection pool for every request in a logical fetch."""
    if client is not None:
        yield client
        return
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as owned:
        yield owned


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    """Return a bounded Retry-After delay or exponential backoff with jitter."""
    if response is not None:
        raw = response.headers.get("retry-after")
        if raw:
            try:
                delay = float(raw)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(raw)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=UTC)
                    delay = (retry_at - datetime.now(UTC)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    delay = -1.0
            if delay >= 0:
                return min(delay, MAX_RETRY_DELAY)
    base = min(0.25 * 2**attempt, MAX_RETRY_DELAY)
    return min(base + random.uniform(0, base * 0.25), MAX_RETRY_DELAY)


def _request(
    url: str,
    params: Mapping[str, str | int],
    *,
    client: httpx.Client,
    deadline: float,
) -> httpx.Response:
    """Issue one GET, retrying transport failures, 429 and transient 5xx."""
    for attempt in range(MAX_ATTEMPTS):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"tennis query exceeded {QUERY_TIMEOUT:g} seconds")
        started = time.monotonic()
        try:
            if not _request_slots.acquire(timeout=remaining):
                raise TimeoutError(f"tennis query exceeded {QUERY_TIMEOUT:g} seconds")
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"tennis query exceeded {QUERY_TIMEOUT:g} seconds")
                response = client.get(
                    url,
                    params=params,
                    headers={"Accept": "application/json", "User-Agent": USER_AGENT},
                    timeout=httpx.Timeout(min(30.0, remaining), connect=min(10.0, remaining)),
                    follow_redirects=True,
                )
            finally:
                _request_slots.release()
        except httpx.TransportError:
            logger.warning(
                "tennis_api_transport_error url=%s attempt=%d duration_ms=%d",
                url,
                attempt + 1,
                round((time.monotonic() - started) * 1_000),
            )
            if attempt + 1 == MAX_ATTEMPTS:
                raise
            time.sleep(min(_retry_delay(None, attempt), max(0.0, deadline - time.monotonic())))
            continue
        logger.debug(
            "tennis_api_response url=%s status=%d attempt=%d duration_ms=%d",
            url,
            response.status_code,
            attempt + 1,
            round((time.monotonic() - started) * 1_000),
        )
        if response.status_code not in RETRYABLE_STATUSES or attempt + 1 == MAX_ATTEMPTS:
            return response
        delay = min(_retry_delay(response, attempt), max(0.0, deadline - time.monotonic()))
        logger.warning(
            "tennis_api_retry url=%s status=%d attempt=%d delay_seconds=%.3f",
            url,
            response.status_code,
            attempt + 1,
            delay,
        )
        time.sleep(delay)
    raise AssertionError("retry loop exited without a response")


def _max_age(response: httpx.Response) -> int | None:
    for part in response.headers.get("cache-control", "").split(","):
        name, _, value = part.strip().partition("=")
        if name.lower() == "max-age":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def get_json(
    source: str,
    url: str,
    params: Mapping[str, str | int] | None = None,
    *,
    ttl: int = LIVE_TTL,
    missing_ok: bool = False,
    client: httpx.Client | None = None,
    deadline: float | None = None,
) -> Fetched:
    """GET one JSON document through the response cache.

    The cache lifetime is the longer of the origin's ``max-age`` and ``ttl``.
    With ``missing_ok``, an HTTP 404 decodes to ``None`` instead of raising.
    """
    query = dict(params or {})
    key = f"{url}?{sorted(query.items())!r}"
    cached = CACHE.lookup(key)
    if cached is not None:
        body, remaining = cached
        return Fetched(payload=json.loads(body) if body else None, ttl=remaining)

    deadline = deadline if deadline is not None else time.monotonic() + QUERY_TIMEOUT
    with _client(client) as http:
        response = _request(url, query, client=http, deadline=deadline)
    if missing_ok and response.status_code == 404:
        CACHE.store(key, b"", ttl)
        return Fetched(payload=None, ttl=ttl)
    if not response.is_success:
        raise TennisApiError(source, response.status_code, response.text)
    body = response.content
    if body[:2] == b"\x1f\x8b":
        # ESPN sometimes gzips bodies without a Content-Encoding header.
        body = gzip.decompress(body)
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise ValueError(f"{source} returned a non-JSON body for {url}") from exc
    effective = max(ttl, _max_age(response) or 0)
    CACHE.store(key, body, effective)
    return Fetched(payload=payload, ttl=effective)


def _require_tour(tour: str) -> str:
    value = tour.upper()
    if value not in TOURS:
        raise ValueError("tour must be 'ATP' or 'WTA'")
    return value.lower()


# --------------------------------------------------------------------------- WTA


def fetch_wta_rankings(
    ranking_type: str = "singles", max_rank: int = 200, *, client: httpx.Client | None = None
) -> Fetched:
    """Fetch the official WTA singles or doubles ranking down to ``max_rank``."""
    if ranking_type not in {"singles", "doubles"}:
        raise ValueError("ranking_type must be 'singles' or 'doubles'")
    if max_rank < 1:
        raise ValueError("max_rank must be positive")
    metric = ranking_type.upper()
    rows: list[dict[str, Any]] = []
    ttls: list[int] = []
    deadline = time.monotonic() + QUERY_TIMEOUT
    with _client(client) as http:
        for page in range(WTA_MAX_PAGES):
            fetched = get_json(
                "WTA API",
                f"{wta_base_url()}/players/ranked",
                {
                    "page": page,
                    "pageSize": WTA_PAGE_SIZE,
                    "type": f"rank{ranking_type.capitalize()}",
                    "sort": "asc",
                    "metric": metric,
                },
                ttl=LIVE_TTL,
                client=http,
                deadline=deadline,
            )
            if not isinstance(fetched.payload, list):
                raise ValueError("WTA API rankings response is not an array")
            ttls.append(fetched.ttl)
            page_rows = [row for row in fetched.payload if isinstance(row, dict)]
            rows.extend(page_rows)
            if len(page_rows) < WTA_PAGE_SIZE or len(rows) >= max_rank:
                break
    return Fetched(payload=[row for row in rows if (row.get("ranking") or 0) <= max_rank], ttl=min(ttls))


def fetch_wta_tournaments(season: int, *, client: httpx.Client | None = None) -> Fetched:
    """Fetch every non-ITF WTA tournament in a calendar year."""
    content: list[dict[str, Any]] = []
    ttls: list[int] = []
    deadline = time.monotonic() + QUERY_TIMEOUT
    with _client(client) as http:
        for page in range(WTA_MAX_PAGES):
            fetched = get_json(
                "WTA API",
                f"{wta_base_url()}/tournaments/",
                {
                    "page": page,
                    "pageSize": WTA_PAGE_SIZE,
                    "excludeLevels": "ITF",
                    "from": f"{season}-01-01",
                    "to": f"{season}-12-31",
                },
                ttl=season_ttl(season),
                client=http,
                deadline=deadline,
            )
            payload = fetched.payload
            if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
                raise ValueError("WTA API tournaments response has no content array")
            ttls.append(fetched.ttl)
            page_rows = [row for row in payload["content"] if isinstance(row, dict)]
            content.extend(page_rows)
            total = (payload.get("pageInfo") or {}).get("numEntries")
            if len(page_rows) < WTA_PAGE_SIZE or (isinstance(total, int) and len(content) >= total):
                break
    return Fetched(payload=content, ttl=min(ttls))


def fetch_wta_tournament_matches(
    tournament_id: int, season: int, *, client: httpx.Client | None = None
) -> Fetched:
    """Fetch every draw's matches for one WTA tournament edition."""
    fetched = get_json(
        "WTA API",
        f"{wta_base_url()}/tournaments/{tournament_id}/{season}/matches",
        ttl=season_ttl(season),
        missing_ok=True,
        client=client,
    )
    payload = fetched.payload or {"matches": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
        raise ValueError("WTA API tournament response has no matches array")
    return Fetched(payload=payload, ttl=fetched.ttl)


def fetch_wta_player(player_id: int, *, client: httpx.Client | None = None) -> Fetched:
    """Fetch one WTA player profile; an unknown id yields ``None``."""
    return get_json(
        "WTA API",
        f"{wta_base_url()}/players/{player_id}",
        ttl=HISTORICAL_TTL,
        missing_ok=True,
        client=client,
    )


def fetch_wta_player_matches(player_id: int, season: int, *, client: httpx.Client | None = None) -> Fetched:
    """Fetch one WTA player's results for a calendar year, all pages."""
    matches: list[dict[str, Any]] = []
    ttls: list[int] = []
    deadline = time.monotonic() + QUERY_TIMEOUT
    with _client(client) as http:
        for page in range(WTA_MAX_PAGES):
            fetched = get_json(
                "WTA API",
                f"{wta_base_url()}/players/{player_id}/matches/",
                {"page": page, "pageSize": WTA_PAGE_SIZE, "year": season},
                ttl=season_ttl(season),
                missing_ok=True,
                client=http,
                deadline=deadline,
            )
            ttls.append(fetched.ttl)
            payload = fetched.payload
            if payload is None:
                break
            if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
                raise ValueError("WTA API player matches response has no matches array")
            page_rows = [row for row in payload["matches"] if isinstance(row, dict)]
            matches.extend(page_rows)
            if len(page_rows) < WTA_PAGE_SIZE:
                break
    return Fetched(payload=matches, ttl=min(ttls))


# -------------------------------------------------------------------------- ESPN


def fetch_espn_scoreboard(
    tour: str, day: date | None = None, *, client: httpx.Client | None = None
) -> Fetched:
    """Fetch ESPN's scoreboard: today's tournaments, or those in play on ``day``.

    ESPN returns every match of each tournament in progress on that day, not
    only the matches played that day.
    """
    params: dict[str, str | int] = {}
    ttl = LIVE_TTL
    if day is not None:
        params["dates"] = day.strftime("%Y%m%d")
        ttl = season_ttl(day.year) if day < datetime.now(UTC).date() else LIVE_TTL
    fetched = get_json(
        "ESPN",
        f"{espn_site_base_url()}/{_require_tour(tour)}/scoreboard",
        params,
        ttl=ttl,
        client=client,
    )
    if not isinstance(fetched.payload, dict) or not isinstance(fetched.payload.get("events", []), list):
        raise ValueError("ESPN scoreboard response has no events array")
    return fetched


def fetch_espn_season(tour: str, season: int, *, client: httpx.Client | None = None) -> Fetched:
    """Fetch every ESPN tournament of a season with all its matches.

    A whole-year scoreboard is about 20 MB, so the season is assembled from
    twelve monthly scoreboards (each lists the full draws of every tournament
    in play that month) and de-duplicated by event id.
    """
    events: dict[str, dict[str, Any]] = {}
    ttls: list[int] = []
    deadline = time.monotonic() + QUERY_TIMEOUT * 2
    with _client(client) as http:
        for month in range(1, 13):
            fetched = get_json(
                "ESPN",
                f"{espn_site_base_url()}/{_require_tour(tour)}/scoreboard",
                {"dates": f"{season}{month:02d}"},
                ttl=season_ttl(season),
                client=http,
                deadline=deadline,
            )
            payload = fetched.payload
            if not isinstance(payload, dict) or not isinstance(payload.get("events", []), list):
                raise ValueError("ESPN scoreboard response has no events array")
            ttls.append(fetched.ttl)
            for event in payload.get("events", []):
                if isinstance(event, dict) and event.get("id") and event["id"] not in events:
                    season_year = (event.get("season") or {}).get("year")
                    if season_year in (None, season):
                        events[event["id"]] = event
    return Fetched(payload=list(events.values()), ttl=min(ttls) if ttls else SEASON_TTL)


def fetch_espn_rankings(tour: str, *, client: httpx.Client | None = None) -> Fetched:
    """Fetch ESPN's singles ranking (top 150) for a tour."""
    fetched = get_json(
        "ESPN",
        f"{espn_site_base_url()}/{_require_tour(tour)}/rankings",
        ttl=LIVE_TTL,
        client=client,
    )
    if not isinstance(fetched.payload, dict) or not isinstance(fetched.payload.get("rankings"), list):
        raise ValueError("ESPN rankings response has no rankings array")
    return fetched


def fetch_espn_athlete(tour: str, athlete_id: int, *, client: httpx.Client | None = None) -> Fetched:
    """Fetch one ESPN athlete profile; an unknown id yields ``None``."""
    return get_json(
        "ESPN",
        f"{espn_common_base_url()}/{_require_tour(tour)}/athletes/{athlete_id}",
        ttl=HISTORICAL_TTL,
        missing_ok=True,
        client=client,
    )
