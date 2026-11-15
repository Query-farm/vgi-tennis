# Rollout checkpoint — work paused

Paused at the user's request on 15 November 2026. This working branch contains
unfinished notebook revisions; the existing published main branch is unchanged.

Completed:

- Preserved the original tournament recording and score-analysis SQL.
- Drafted the standardized score notebook and a second ranking-points notebook.
- Captured seven additional complete public WTA/ESPN responses with provenance.
- Verified the short public v0.2.0 worker launch and actual SQL calls against the recordings.
- Published eleven frozen companions at fd006d9cf6a3c555dd4052a10b2b4a474e3e3925,
  registered in shared tooling v0.3.55.
- Captured the full released catalog: nine functions and two distinct tables.

Still required before publication to main:

- Resolve the existing notebooks/ruff.toml overriding the new pyproject.toml settings.
  The last lint command fixed six issues but stopped on ten line-length findings;
  neither new default notebook has been executed yet.
- Finish the third story about repeated tournament-level points in player match rows.
  Actual release probes returned one profile, 69 player rows, 19 represented events,
  and 124 calendar rows. Every player row joins to its source-qualified event;
  tournament points are constant within each event but repeated on its match rows.
  Proposed experiment: minimum returned rows per event, 1 to 4. Keep full source
  records fixed and distinguish event totals from official current ranking points.
- Execute defaults, experiments and bounded live paths; review charts and test a
  fresh preloaded notebook-only setup.
- Complete hash-bound coverage evidence: target six of eleven distinct surfaces;
  plan tournament_matches, rankings, current_rankings, player, player_matches,
  tournaments. Current source differs from v0.2.0 in metadata/examples only.
- Replace the old notebooks README with documentation of the final set of stories.
  Its old execution commands and validation claims describe the previous published
  notebook, not these unfinished drafts.
- Verify SVG rendering after final publication.

Source CI at ae4ded6267523b2184aed8020abda39985ef1540 is green (43585497762),
as is its separate live run (43600691371). No source changes or worker builds are
part of this branch. All 60 files in the original clean checkout remain unchanged.

The successful ranking and season runtime probes ran in separate Python processes.
An initial combined probe reused VGI's process-level worker pool after closing a
loopback server and failed the second scenario with connection refused; fresh
processes passed. Published notebooks must use fresh kernels as designed.
