# 0.3.1 candidate qualification (recorded 2026-11-06)

Source: `5a4c0b5` (main, 0.3.0 cohort) plus the uncommitted 0.3.1 changes. Record the
release commit here when it exists; the tag must point at a commit whose tree matches the
checked tree.

## Complete gate

| Platform | Python | Result | Groups | Tests passed | Branch coverage | Duration |
|---|---|---|---|---|---|---|
| macOS arm64 | 3.11.17 | **pass** (`make check`, exit 0) | 145 | 3,636 | 87% | 699 s |
| Linux aarch64, `python:3.11-bookworm` container | 3.11.18 | **incomplete**, see below | — | — | — | — |
| Linux x86_64 | 3.11 | not run; `Release gate` workflow | — | — | — | — |

`make check` ran lint, format, strict typing, file-size and ceiling checks, layer and dependency
audits, then the full inventory in bounded groups with the 85% branch floor. Browser assets
were built first with `npm run build`; the suite serves them, and `make check` fails at
`examples/embed` without them.

## Linux aarch64 runs

The container runs found one product defect and two environment requirements; none of them
finished the complete suite.

1. Root, no init process: `tests/test_common_tools.py::test_bash_cancellation_stops_descendant`
   failed. After cancellation the tool's orphaned grandchild was still running (state `R`,
   parent 1) and exited about 200 ms later. **Fixed in 0.3.1**: `stop_process` now waits until
   the process group is gone; regression tests in `tests/test_common_tools_group_exit.py`.
2. Root, with `--init`: 45 groups (1,365 tests) passed, then `tests/test_embedded_lifetime.py`
   refused with `embedded_platform_unsupported`. The embedded PostgreSQL profile is non-root
   only by design, so this is the expected refusal, not a defect.
3. Non-root with `--init`: started, but the local container runtime stopped responding
   before the suite began. No result.

GitHub-hosted Ubuntu runners execute as a non-root user with a reaping init, which is what the
`Release gate` workflow (`.github/workflows/release-gate.yml`) uses for Linux x86_64 and arm64.
A Linux pass from that workflow, or from a non-root container run with `--init`, is still
required before Linux support is claimed.

## Dependency ranges

See [dependency-matrix-20261106.md](dependency-matrix-20261106.md). The lowest and highest
bounds each passed the complete suite on macOS arm64 (144 groups, 3,632 tests, 87%) before the
Bash-tool fix and its four new tests were added. The fix touches only the reference CLI tool.

## Fresh installs

Published 0.2.0 base and extras installs from PyPI pass on macOS arm64, Linux aarch64 and Linux
x86_64 ([release-020-publication-20261106.md](release-020-publication-20261106.md)). The same
check for 0.3.1 runs after upload: dispatch `Release gate` with `published_version: 0.3.1`.

## Not covered

- Live Databricks AI Gateway receipts per tier (R3-3) need a signed-in development workspace
  and an operator; the wire properties are covered by mock-transport tests only.
- `pydantic-ai-slim` 2.63.0 and later are excluded, not qualified.
