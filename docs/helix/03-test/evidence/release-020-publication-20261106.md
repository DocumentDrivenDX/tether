# 0.2.0 publication receipt (recorded 2026-11-06)

Machine-readable form: [release-020-publication-20261106.json](release-020-publication-20261106.json).

| Item | Value |
|---|---|
| Tag | `v0.2.0` |
| Source commit | `05ba0d9865f6c50dd1a169b896241bea3a42cf5e` |
| GitHub release | [v0.2.0](https://github.com/DocumentDrivenDX/tether/releases/tag/v0.2.0), published 2026-10-29T22:38:00Z |
| Wheel | `novirhiza_tether-0.2.0-py3-none-any.whl`, sha256 `d275bfe8ec54b893077277aee4cddd4a59ff20c9bc2f800a13e32b2de6a61c99`, PyPI upload 2026-10-29T22:38:51Z |
| Source archive | `novirhiza_tether-0.2.0.tar.gz`, sha256 `1cc317cbe746b71e64bf880daae3cbf9f7914f0fafa2af0aaab0acd4e3a3018e`, PyPI upload 2026-10-29T22:38:53Z |
| Release manifest asset | `release-manifest.json`, sha256 `fdabec156fad5726fe5de4a38594c2529e4f3e37fcc0c09c219c7dfedf12fd84` |
| Source qualification | [release-020-source-qualification-20261029.md](release-020-source-qualification-20261029.md) |

The PyPI SHA-256 values for both distribution files equal the GitHub release asset
digests.

## Fresh installs from the public index

`scripts/index_install_smoke.py 0.2.0` installed the base, `postgres`, `openai` and `tui`
profiles from `https://pypi.org/simple`, each into a new virtual environment with an empty
uv cache, and imported the package. All four profiles passed in each environment:

| Environment | Result |
|---|---|
| macOS arm64, Python 3.11 | pass |
| Linux aarch64, `python:3.11-bookworm` container, Python 3.11.18 | pass |
| Linux x86_64, `python:3.11-bookworm` container under emulation, Python 3.11.18 | pass |

Resolved runtime versions in every environment: `pydantic` 2.14.1, `pydantic-ai-slim`
2.59.0, `anyio` 4.16.0, plus `psycopg` 3.3.6, `openai` 3.37.0 or `textual` 8.2.8 for the
matching extra.

## Not covered here

The complete `make check` for 0.2.0 ran on macOS arm64 only. Linux full-suite evidence is
recorded for the 0.3.1 candidate instead, through the `Release gate` workflow and a Linux
container run; 0.2.0 is superseded and was not re-run.
