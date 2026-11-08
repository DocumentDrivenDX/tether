# 0.3.2 candidate qualification (recorded 2026-11-08)

Source: `b1a7d57` (main, merge of pull request 90) plus the 0.3.2 version bump
`299117e5741bf2840e1ca8f7a1bcdf42f51f7c2d`. Release commit: not yet merged; the tags must point
at the merge of the release pull request, and that commit needs its own `Release gate` run
before tagging.

## Changes since 0.3.1

- The explicitly selected `databricks-ai-gateway-stream-v1` stream profile (ADR-013,
  CONTRACT-001), plus request ids kept on stream-validation errors. See the
  [release notes](../../../releases/0.3.2.md).
- No dependency range changed, so the 0.3.1
  [dependency matrix](dependency-matrix-20261106.md) still applies. `uv.lock` changes only the
  four cohort versions.

## Complete gate

| Platform | Ref | Python | Result | Groups | Tests passed | Branch coverage |
|---|---|---|---|---|---|---|
| macOS arm64, local, load average 20–48 | `299117e` | 3.11.17 | **incomplete**, see below | — | — | — |
| Linux x86_64, GitHub-hosted `ubuntu-24.04` ([run](https://github.com/DocumentDrivenDX/tether/actions/runs/42528857029)) | `299117e` | 3.11.17 | **pass** (`Release gate`) | 147 | 3,689 | 87% |
| Linux aarch64, GitHub-hosted `ubuntu-24.04-arm` ([run](https://github.com/DocumentDrivenDX/tether/actions/runs/42528857029)) | `299117e` | 3.11.17 | **pass** (`Release gate`) | 147 | 3,689 | 87% |

`make check` runs lint, format, strict typing, file-size and ceiling checks, layer and
dependency audits, then the full inventory in bounded groups with the 85% branch floor.
Browser assets were built first with `npm run build`. The 51 added tests are the gateway
stream dialect cases from pull request 90 (0.3.1 ran 3,638).

The local macOS run (621 s) failed one timing-sensitive case,
`tests/test_embedded_lifetime.py::test_cancelled_real_initialization_finishes_before_directory_release`
(`TimeoutError`), on a host with load average between 20 and 48. That file passed on its own
right after (20 tests). Pull request 90 saw the same case flake under similar load. The
release change touches no embedded-PostgreSQL code, so the Linux runs above are the clean
complete gate for this candidate.

## Live evidence

The [gateway stream dialect live receipt](databricks-gateway-stream-dialect-20261108.md)
covers quick, balanced and deep tiers on commit `9469fa7`, which this release contains
unchanged: P1, P2 (five concurrent), P3 and P5 pass on every tier, P4 passes.

## Not covered

- Nonstreaming gateway responses and gateway cache-count mapping (see the release notes).
- `pydantic-ai-slim` 2.63.0 and later remain excluded, not qualified.
- Publication: no tag, GitHub release or PyPI upload exists yet for 0.3.2.
