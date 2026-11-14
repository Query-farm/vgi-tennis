# Tennis notebooks

[How do games and sets tell different match stories?](01_reconcile_games_and_sets.ipynb)
reconstructs set outcomes and game totals, then tests a descriptive close-set
margin for the 2026 Australian Open women's main singles draw.

## Setup

Install uv and Git. The entire directory runs independently of the worker checkout.

```sh
cd notebooks
uv sync --locked
uv run python execute.py
uv run python -m ipykernel install --user --name vgi-tennis-notebooks --display-name 'Python (Tennis notebooks)'
uv run --with jupyterlab jupyter lab
```

Select that kernel interactively. The runner uses a temporary kernel spec and
notebook-directory cwd. Only successful execution atomically replaces saved
outputs. Published Haybarn **2.0.0rc11** is separately locked. Literal SQL uses
public Git tag **v0.2.0**, Python 3.13, vgi-python 0.51.0, vgi-rpc 0.53.1,
PyArrow 25.0.1 and httpx 0.28.1 through uvx. First launch needs internet for
packages/Git and the extension. No local worker environment is discovered.
Update pins explicitly and revalidate before upgrading.

## Provenance and modes

The default replays an **unchanged actual public WTA response**, captured at
2026-11-14 10:24:03 UTC from
`https://api.wtatennis.com/tennis/tournaments/901/2026/matches`.
`recordings/provenance.json` stores the exact URL, UTC capture time and SHA-256.
The 356,195-byte body contains 302 tournament records; it is not a trimmed test
fixture or synthetic match dataset. The source owns its data; its API is
undocumented and has no published dataset license. The worker's MIT license
covers code, not upstream data. See the root README's source and terms notes.
No WTA, tournament or player endorsement is implied.

Replay verifies the checksum and serves only the exact recorded path on loopback
through `TENNIS_WTA_API_BASE_URL`; unexpected requests fail. All three source
base-URL hooks are cleared first and restored at cleanup. Live creates no replay
server and makes a direct request to the same event, with no fallback. The worker
may cache the single materialized response in process; charts issue no API reads.
The replay audit requires exactly one tournament GET.

SQL reports all draws/statuses, selects completed main singles, and excludes
missing/unsupported set scores before analysis. Standard scores here are 6–0 to
6–4, 7–5 or 7–6 in either direction; doubles match tiebreaks are outside scope.
Tiebreak points are not games. Source timestamps are UTC and distinct from capture
time. This is a descriptive score analysis, not independent officiating evidence
or an estimate of point-by-point competitiveness.

## Experiments

```sh
uv run python execute.py --parameters '{"CLOSE_SET_MARGIN":4}' --output-dir artifacts/margin-4
uv run python execute.py --parameters '{"DATA_MODE":"live"}' --output-dir artifacts/live
```

CLOSE_SET_MARGIN accepts integer 0–6 games inclusive; DATA_MODE is replay/live.
Overrides need separate destinations. Copies include branding, recordings,
helpers, runner, docs and environment. The event and scores stay fixed for the
margin experiment; restore 2 afterward. Live source changes are a separate factor.

## Validation

Validated with published Haybarn 2.0.0rc11: 16 code cells, 7 visible SQL cells,
2 charts, 12 SQL invariants and an exact single-request replay audit. Default
passed in 17.2 seconds including first tagged worker launch; a notebooks-only
copy passed the margin-4 experiment in 15.1 seconds and direct live in 6.1 seconds.
All six charts were reviewed. Three rejected-run guards preserve saved defaults.

Of 127 completed main singles records, 124 have supported terminal set scores;
three are explicitly excluded. Those matches contain 285 sets and four match
winners with fewer total games. Margin 2→4 changes close sets **109→231**
(**38.2%→81.1%**), while score-pair and winner/check outputs are identical and
the first chart is byte-identical. The live run returned identical analytical
counts and winner/check outputs to the saved default.

Source gates passed: 90 offline pytest tests including hermetic Haybarn E2E,
98.30% coverage (80% gate), Ruff lint/format including notebooks, dependency lock
check/frozen synchronization, standalone entrypoint help, wheel/sdist build,
Twine, isolated wheel smoke, and runtime audit with no known vulnerabilities.
Nine full live tests were deselected; the notebook's live WTA endpoint was tested
separately. CI-pinned vgi-lint-check 2.5.0 structural gate passed **98/100 L1,
zero findings**. Executable catalog examples were not run by that gate.

Container validation is blocked: Docker Desktop reports that it is unable to
start. No container success is claimed. Worker source and root lock hashes are
unchanged.
