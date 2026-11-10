# Changelog

All notable changes to this project are documented here.

## [Unreleased]

## [0.1.0]

- Initial release on VGI protocol 3.2 (vgi-python 0.50) with vgi-lint-check 2.2.0.
- `current_rankings` and `current_matches` tables for both tours.
- `rankings()`, `matches_on()`, `tournaments()`, `tournament_matches()`,
  `grand_slam_results()`, `player()` and `player_matches()` table functions.
- WTA data from the official WTA API; ATP data and cross-tour scoreboards from
  ESPN, with a `source` column naming each row's id space.
- Typed Arrow schemas with column comments, per-set scores as a
  `LIST(STRUCT)` column, and primary-key constraints on the current tables.
- Timeouts, bounded retries honoring `Retry-After`, an in-process response
  cache, and base-URL overrides for hermetic tests.
