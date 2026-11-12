<p align="center">
  <a href="https://query.farm/vgi/">
    <img src="https://raw.githubusercontent.com/Query-farm/vgi-kalshi/main/docs/vgi-logo.png" alt="Vector Gateway Interface logo" width="320">
  </a>
</p>

<h1 align="center">vgi-tennis</h1>

<p align="center">
  ATP and WTA rankings, tournaments, Grand Slam finals and set-by-set match results as ordinary DuckDB tables.<br>
  A <strong>read-only</strong> <a href="https://query.farm/vgi/">VGI</a> worker, built by <a href="https://query.farm">🚜 Query.Farm</a>
</p>

<p align="center">
  <a href="https://github.com/Query-farm/vgi-tennis/actions/workflows/ci.yml"><img src="https://github.com/Query-farm/vgi-tennis/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/python-3.13%2B-3776AB.svg" alt="Python 3.13+">
  <a href="https://query.farm/vgi/"><img src="https://img.shields.io/badge/VGI-read--only-6f42c1.svg" alt="Read-only VGI worker"></a>
</p>

---

`vgi-tennis` fetches professional tennis data on demand and reformats the
responses as typed Arrow data for Haybarn SQL. WTA data comes from the WTA's
own public API; ATP data and the cross-tour scoreboards come from ESPN. It
requires no credentials and does not rehost or persist any dataset. Read
[Data sources and terms](#data-sources-and-terms) before you use it.

## Quick start

The worker is not published to PyPI. `uvx` runs the tagged release straight from
GitHub and caches it after the first use:

```bash
uvx --from git+https://github.com/Query-farm/vgi-tennis@v0.2.0 vgi-tennis --help
```

Launch Haybarn with the catalog pre-attached:

```bash
uvx haybarn-cli \
  -cmd "INSTALL vgi FROM community; LOAD vgi; ATTACH 'tennis' (TYPE vgi, LOCATION 'uvx --from git+https://github.com/Query-farm/vgi-tennis@v0.2.0 vgi-tennis');" \
  :memory:
```

Or attach it in an existing Haybarn session:

```sql
INSTALL vgi FROM community;
LOAD vgi;
ATTACH 'tennis' (
  TYPE vgi,
  LOCATION 'uvx --from git+https://github.com/Query-farm/vgi-tennis@v0.2.0 vgi-tennis'
);
```

For source development, run `uv lock --check --no-sources`, then
`uv sync --no-sources` and `uv run --no-sources vgi-tennis`.
The lockfile resolves all dependencies from package registries and does not
require a sibling checkout.

## Catalog surface

The catalog exposes two current tables and seven parameterized table functions:

| Kind | Objects | Source |
| --- | --- | --- |
| Current | `current_rankings`, `current_matches` | WTA API + ESPN; ESPN |
| Rankings | `rankings(tour, ranking_type, max_rank)` | WTA API (WTA), ESPN (ATP singles) |
| Matches | `matches_on(tour, match_date)`, `tournament_matches(tour, tournament_id, season)` | ESPN; WTA API or ESPN |
| Tournaments | `tournaments(tour, season)`, `grand_slam_results(season, tour)` | WTA API or ESPN; ESPN |
| Players | `player(tour, player_id)`, `player_matches(tour, player_id, season)` | WTA API or ESPN |

`tour` is `'ATP'` or `'WTA'` (`matches_on` and `grand_slam_results` also take
`'ALL'`, which adds mixed doubles). Leading required arguments are
positional, for example `tournament_matches('WTA', 901, 2026)`; optional
arguments are named, for example `season := 2026`.

### Ids and the `source` column

Player and tournament ids belong to the source that issued them. WTA rows use
the official WTA ids by default (Elena Rybakina is `324166`; the Australian
Open is tournament `901`). ATP rows, and every scoreboard row, use ESPN ids
(Carlos Alcaraz is `3782`; Wimbledon is tournament `188`). Every row carries a
`source` column (`wta` or `espn`) naming its id space. Pass
`source := 'espn'` to a WTA function when you hold an ESPN id, for example one
taken from `current_matches`.

### Scores

`score` reads from the winner's side, with the tiebreak loser's points in
parentheses: `6-7(7) 7-6(2) 6-3 6-4`. A deciding match tiebreak (doubles)
renders in square brackets, `4-6 6-3 [10-8]`, and is stored in `sets` as a
1-0 set with the loser's points. The `sets` column holds one struct per set
(`set_number`, `side1_games`, `side2_games`, `tiebreak_loser_points`) for
set-level analysis with `UNNEST`. `status` is one of `scheduled`,
`in_progress`, `completed`, `retired`, `walkover` or `other`.

## Usage

### Rankings

```sql
-- Top ten of each tour
SELECT tour, ranking, player_name, country, points
FROM tennis.main.current_rankings
WHERE ranking <= 10
ORDER BY tour, ranking;

-- WTA doubles (ATP doubles are not published by ESPN)
SELECT ranking, player_name, country, points
FROM tennis.main.rankings('WTA', ranking_type := 'doubles', max_rank := 20);
```

`current_rankings` holds the ATP top 150 (ESPN's depth) and the WTA top 200.
`rankings('WTA', max_rank := 1000)` goes deeper. Tied players share a
position.

### This week's matches

```sql
SELECT tournament_name, draw, count(*) AS matches,
       count(*) FILTER (WHERE status = 'completed') AS completed
FROM tennis.main.current_matches
GROUP BY ALL
ORDER BY tournament_name, draw;
```

`current_matches` covers every match (qualifying and main draw, singles and
doubles) of the tournaments on ESPN's current ATP and WTA scoreboards.

### A given day

```sql
SELECT draw, round, winner_name, loser_name, score
FROM tennis.main.matches_on('ATP', DATE '2026-07-12')
ORDER BY scheduled_at;
```

`matches_on` keeps the matches whose start time falls on that UTC day.

### Tournaments and full draws

```sql
-- The 2026 WTA premier calendar with champions
SELECT name, level, surface, start_date, singles_champion
FROM tennis.main.tournaments('WTA', season := 2026)
WHERE level IN ('Grand Slam', 'WTA 1000')
ORDER BY start_date;

-- Australian Open 2026, women's singles final rounds
SELECT round, winner_name, loser_name, score
FROM tennis.main.tournament_matches('WTA', 901, 2026)
WHERE draw_type = 'singles' AND NOT qualifying
  AND round IN ('Semifinal', 'Final');

-- Tiebreak sets in the Wimbledon 2026 men's singles main draw
SELECT count(*) AS tiebreak_sets
FROM tennis.main.tournament_matches('ATP', 188, 2026), UNNEST(sets) AS t(s)
WHERE draw = 'Men''s Singles' AND NOT qualifying
  AND s.tiebreak_loser_points IS NOT NULL;
```

WTA tournaments from the WTA API add level (WTA 125 to Grand Slam), surface,
draw sizes and prize money. ESPN, the only ATP source, marks only Grand Slams
in `level`. The first ATP call for a season reads twelve monthly scoreboards
and is then cached.

### Grand Slam finals

```sql
SELECT tournament_name, draw, champion, champion_seed, runner_up, score
FROM tennis.main.grand_slam_results(2026)
ORDER BY final_date, draw;
```

One row per Slam draw: men's and women's singles and doubles, plus mixed
doubles when `tour` is `'ALL'` (the default).

### Players

```sql
SELECT player_name, birth_date, height_inches, plays, debut_year
FROM tennis.main.player('ATP', 3782);

SELECT result, count(*) AS matches
FROM tennis.main.player_matches('WTA', 324166, season := 2026)
WHERE draw_type = 'singles'
GROUP BY result;
```

WTA results add entry rankings, ranking points and prize money earned at each
tournament. Byes are omitted.

## Data sources and terms

`vgi-tennis` is **not affiliated with, endorsed by, or sponsored by** the ATP,
the WTA, ESPN, The Walt Disney Company, or any Grand Slam tournament or
governing body. All names are used only to describe the data. The worker
fetches data on demand for the person running the query, caches it briefly in
memory, and never rehosts, redistributes or persists it. **You are responsible
for complying with each upstream's terms for your use of the data.**

| Data | Source | Status |
| --- | --- | --- |
| WTA rankings, tournaments, draws, players, player results | `api.wtatennis.com` | The WTA's own backend for wtatennis.com. Keyless, undocumented, no published licence. Data remains the WTA's. |
| ATP rankings, tournaments, draws, players; cross-tour scoreboards; Grand Slam finals | ESPN `site.api.espn.com`, `site.web.api.espn.com` | Unofficial and undocumented. Use is subject to ESPN's (Disney's) Terms of Use, which restrict automated access. |

ESPN's endpoints carry no service guarantee and can change or disappear
without notice. ATP data has no official alternative: atptour.com and the ATP
app gateway block non-browser clients. The worker deliberately does not use
the Jeff Sackmann tennis datasets, which carry a non-commercial licence.

To keep load low, the worker sends an honest User-Agent
(`vgi-tennis/<version> (+https://github.com/Query-farm/vgi-tennis)`), bounds
concurrency, and serves repeated queries from its cache.

## Production HTTP deployment

Stdio is the recommended transport when Haybarn can launch the worker locally.
Use HTTP when the worker must be shared across hosts. The production HTTP entry
point fails closed unless it has a stable signing key and either authentication
or an explicit anonymous-access decision:

```bash
export VGI_SIGNING_KEY='a-long-random-secret-shared-by-every-replica'
export VGI_BEARER_TOKENS='replace-with-a-random-token=haybarn'

uvx --from git+https://github.com/Query-farm/vgi-tennis@v0.2.0 vgi-tennis-http \
  --host 0.0.0.0 \
  --port 8000 \
  --cors-origins 'https://sql.example.com' \
  --log-format json
```

`vgi-tennis-http` disables the description pages and cross-origin access unless
they are explicitly configured. Terminate TLS at a trusted proxy or load
balancer. All replicas must receive the same `VGI_SIGNING_KEY`; rotate it as a
coordinated deployment because outstanding state tokens are signed with it.

Supported authentication options are:

- `VGI_BEARER_TOKENS`: comma-separated `token=principal` pairs.
- `VGI_JWT_ISSUER` and `VGI_JWT_AUDIENCE`: JWT/JWKS authentication.
- `VGI_PROXY_PROOF_MODE=require` with the corresponding VGI proxy-proof
  settings when a trusted proxy authenticates users.

An intentionally public service can set `VGI_ALLOW_ANONYMOUS=1`. It should
still be protected by gateway rate limits, both for your own capacity and out
of respect for the upstreams. The health probe is `GET /health`.

### Container

```bash
docker build -t vgi-tennis .
docker run --rm -p 8000:8000 \
  -e VGI_SIGNING_KEY='replace-with-a-stable-random-secret' \
  -e VGI_BEARER_TOKENS='replace-with-a-random-token=haybarn' \
  vgi-tennis
```

The image runs as an unprivileged user, emits JSON logs, disables interactive
description pages, and includes a health check.

## Reliability and operations

Upstream calls use a 30-second request timeout, a 120-second total query
deadline, at most four attempts, and at most four simultaneous upstream
requests per process. Redirects are followed. HTTP 429 and transient 5xx
responses are retried with exponential backoff and jitter; `Retry-After` is
honored with a 30-second per-retry cap. ESPN bodies that arrive gzipped
without a `Content-Encoding` header are decompressed.

Responses are cached in process (96 MB LRU) for the longer of the origin's
`max-age` and a floor: 60 seconds for live data, 10 minutes for the current
season, and 6 hours for past seasons. The same freshness is forwarded to VGI
clients as cache control. Success timings are logged at debug level; retries
and transport failures are warnings.

For hermetic tests, `TENNIS_WTA_API_BASE_URL`, `TENNIS_ESPN_SITE_BASE_URL` and
`TENNIS_ESPN_COMMON_BASE_URL` may point at alternate roots. Do not set them
from untrusted user input.

## Development and release checks

```bash
uv lock --check --no-sources
uv sync --no-sources
uv run --no-sources ruff check .
uv run --no-sources ruff format --check .
uv run --no-sources pytest --cov=vgi_tennis --cov-fail-under=80
uv build
uvx twine check dist/*
uvx --no-sources --from vgi-lint-check==2.5.0 vgi-lint lint \
  'uv run --no-sources tennis_worker.py' \
  --no-execute --no-check-links --audit-waivers --fail-on warning
```

Offline tests replay small recorded WTA and ESPN responses from
`tests/fixtures/`, including a Haybarn end-to-end test against a local fixture
server. Live upstream smoke tests are opt-in with `uv run pytest -m live` and
also run in a serialized scheduled workflow alongside the executing
`vgi-lint` pass. Publishing is driven by releases. Create a GitHub release named `vX.Y.Z` that
matches the version in `pyproject.toml`. CI then builds, validates and attests
the wheel and sdist, attaches them to the release, and publishes a
multi-platform container image to `ghcr.io/query-farm/vgi-tennis`. The package is
not published to PyPI; install it from the release tag as shown in the quick
start.

## License

MIT. Copyright 2026 Query Farm LLC - https://query.farm
