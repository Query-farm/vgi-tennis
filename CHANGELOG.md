# Changelog

All notable changes to this project are documented here.

## 0.3.0 (2026-11-25)

- Speaks VGI protocol 3.4: require vgi-python `>=0.55.1,<0.56`; uv.lock re-locked. This release
  needs Haybarn 2.0.1 (or stock DuckDB 2.0 with `INSTALL vgi FROM query_farm`): Haybarn 2.0.0
  loads a 3.3 `vgi` extension that cannot attach it.
- SQL tests run on haybarn-unittest 2.0.1rc3 (CI and Makefile).
- New `serve` extra (`vgi-python[http,oauth]`); the Docker image installs it, so the image runs in
  Orchard hosted mode (`VGI_ORCHARD_HOSTED=1` with VGI_JWT_ISSUER, VGI_JWT_AUDIENCE,
  VGI_JWT_JWKS_URI and VGI_SIGNING_KEY). The Docker HEALTHCHECK uses `HEAD /health`, which
  stays anonymous in hosted mode.

## [Unreleased]

## 0.2.2 (2026-11-16)

- Bump vgi-python to 0.52.2.
- Requires Python 3.14 (CI, Docker image and tooling moved to 3.14).
- CI runs the SQL tests over stdio, launch and HTTP transports.

## [0.2.1]

- Docs link to upstream sources (VGI183): the catalog documentation now links the data providers it reads.

## [0.2.0]

- Speak VGI protocol 3.3 (vgi-python 0.51); requires the current VGI DuckDB extension, 3.2 extensions are incompatible.
- Lint with vgi-lint-check 2.5.0.

## [0.1.0]

- Initial release on VGI protocol 3.2 (vgi-python 0.50) with vgi-lint-check 2.2.0.
- `current_rankings` and `current_matches` tables for both tours.
- `rankings()`, `matches_on()`, `tournaments()`, `tournament_matches()`,
  `grand_slam_results()`, `player()` and `player_matches()` table functions.
- WTA data from the official WTA API; ATP data and cross-tour scoreboards from
  ESPN, with a `source` column naming each row's id space.
- Typed Arrow schemas with column comments, per-set scores as a
  `LIST(STRUCT)` column, and primary-key constraints on the current tables.
- Deciding match tiebreaks render as `[10-8]`; ESPN tournament status is
  derived from dates and match states because ESPN's event-level status
  reads final mid-week.
- Timeouts, bounded retries honoring `Retry-After`, an in-process response
  cache, and base-URL overrides for hermetic tests.
