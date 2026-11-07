# Dependency support matrix (recorded 2026-11-06)

This matrix qualifies the base-install ranges published from 0.3.1 under FR-63. For each
runtime dependency it names the lowest and highest versions that passed the complete suite,
with the exact versions run. The lock (`uv.lock`) still holds one exact qualified set and
remains the release gate.

| Distribution | Published range | Lowest run | Highest run | Locked |
|---|---|---|---|---|
| `pydantic` | `>=2.12,<2.15` | 2.12.0 (core 2.41.1) | 2.14.3 (core 2.50.4) | 2.14.1 (core 2.50.1) |
| `pydantic-ai-slim` | `>=2.59.0,<2.63` | 2.59.0 | 2.62.0 | 2.59.0 |
| `anyio` | `>=4.15.1,<5` | 4.15.1 | 4.16.0 | 4.15.1 |
| `psycopg[binary,pool]` (`postgres`, `embedded-postgres` extras) | `>=3.3.6,<3.4` | 3.3.6 (pool 3.3.3) | 3.3.8 (pool 3.3.3) | 3.3.6 |

Receipts: [lowest](dependency-matrix-lowest-20261106.json),
[highest](dependency-matrix-highest-20261106.json).

## How it ran

`scripts/dependency_matrix.py {lowest|highest}` exports the locked environment, removes the
ranged distributions and the distributions that move with them (`pydantic-core`,
`pydantic-graph`, `psycopg-binary`, `psycopg-pool`), and installs into a fresh Python 3.11
virtual environment. The lowest profile requests each range's floor exactly. The highest
profile resolves each range to its newest release and also unpins `openai`, `genai-prices`,
`griffe`, `opentelemetry-api` and `httpx` so the newer Agent release can resolve; the receipt
records each resolved value. The script then runs `scripts/test_suite.py`, the same complete
inventory `make check` runs: bounded groups and the 85% branch-coverage floor.

| Profile | Platform | Python | Result | Groups | Tests passed | Branch coverage |
|---|---|---|---|---|---|---|
| lowest | macOS arm64 | 3.11.17 | pass | 144 | 3,632 | 87% |
| highest | macOS arm64 | 3.11.17 | pass | 144 | 3,632 | 87% |

Both runs used the 0.3.1 candidate source (`5a4c0b5` plus the uncommitted 0.3.1 changes).
`tests/test_replay_version_corpus.py` accepts the locked set and these two sets only; any
other installed set fails it until a new matrix run adds it.

## Consumer constraints

Checked against a consumer that needs `pydantic>=2.11`, `psycopg>=3.3.6` and Python
`>=3.11,<3.12`:

- `pydantic>=2.11`: compatible, but the effective floor is 2.12.0. `pydantic-ai-slim` 2.59.0
  declares `pydantic>=2.12`, so a resolver cannot select 2.11 with any supported Agent
  version.
- `psycopg>=3.3.6`: inside the range; 3.3.6 through 3.3.8 passed.
- Python 3.11: inside the matrix. Both runs and the locked gate use 3.11.17.

## Excluded upper versions

`pydantic-ai-slim` 2.63.0 and later (2.66.0 was current on 2026-11-06) are excluded. From
2.63.0 the Agent appends built-in instruction text about `<system>` tags in user turns to the
configured instructions. Tether compares the request's system text with the pinned
specification before every physical model attempt and refuses the mismatch as
`derived_history_changed`, so turns stop as `model_denied`. 2.62.0 passes
`tests/client/test_remote_client.py`; 2.63.0 fails five of its tests. The suite stops at the
first failing group, so the full extent at 2.63 or later is not measured. Supporting 2.63 or later needs a
design decision on whether and how that SDK text may reach the model; it is not a patch change.

## Extras

`openai`, `tiktoken` and `textual` stay exact pins in their extras. In the highest run
`openai` was unpinned and resolved to 3.46.0. An installation through Tether's `openai`
extra keeps `openai==3.37.0`; a fresh resolve of `novirhiza-tether[openai]` from this source
selects `openai` 3.37.0 with `pydantic-ai-slim` 2.62.0 and `pydantic` 2.14.3. That exact
combination was not run as a separate profile.
