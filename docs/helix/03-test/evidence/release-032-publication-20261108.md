# 0.3.2 publication receipt (recorded 2026-11-08)

Machine-readable form: [release-032-publication-20261108.json](release-032-publication-20261108.json).

All four 0.3.2 packages come from one source commit,
`236c56956d8e3a11e5212568e06088f6a90a4198` (merge of pull request 91, which includes pull
request 89). Each annotated tag points at it. Each GitHub release is immutable, and its
downloaded assets passed `verify_release_artifacts.py verify-release` against GitHub's reported
digests before upload. PyPI received the files through the trusted-publisher workflow
(`publish.yml`), and PyPI's SHA-256 for every file equals the GitHub asset digest and the
release manifest. Timestamps are UTC; the GitHub releases were published on 2026-11-09 UTC,
the evening of 2026-11-08 local time.

| Package | Tag | GitHub release published | Publish run |
|---|---|---|---|
| `novirhiza-tether` | [v0.3.2](https://github.com/DocumentDrivenDX/tether/releases/tag/v0.3.2) | 2026-11-09T02:25:52Z | [42550973337](https://github.com/DocumentDrivenDX/tether/actions/runs/42550973337) |
| `novirhiza-tether-web-api` | [web-api-v0.3.2](https://github.com/DocumentDrivenDX/tether/releases/tag/web-api-v0.3.2) | 2026-11-09T02:25:58Z | [42551052682](https://github.com/DocumentDrivenDX/tether/actions/runs/42551052682) |
| `novirhiza-tether-web-ui` | [web-ui-v0.3.2](https://github.com/DocumentDrivenDX/tether/releases/tag/web-ui-v0.3.2) | 2026-11-09T02:26:04Z | [42551107578](https://github.com/DocumentDrivenDX/tether/actions/runs/42551107578) |
| `novirhiza-tether-databricks` | [databricks-v0.3.2](https://github.com/DocumentDrivenDX/tether/releases/tag/databricks-v0.3.2) | 2026-11-09T02:26:09Z | [42551208093](https://github.com/DocumentDrivenDX/tether/actions/runs/42551208093) |

## Files

| File | SHA-256 | PyPI upload |
|---|---|---|
| `novirhiza_tether-0.3.2-py3-none-any.whl` | `3b2ed3e4a0be1ffb073173e3502e5146c1c4e725ccb71cf074956242e05972e5` | 2026-11-09T02:27:12Z |
| `novirhiza_tether-0.3.2.tar.gz` | `8ba2db8ab23ff7db2b8c2a82e7ae844b53fe3be28d153d08daa08bab08c42fb3` | 2026-11-09T02:27:13Z |
| `novirhiza_tether_web_api-0.3.2-py3-none-any.whl` | `bf0312f3dbcdf48ec857cb33ec88148785104e277ca4c8240d650bda1faa536a` | 2026-11-09T02:28:01Z |
| `novirhiza_tether_web_api-0.3.2.tar.gz` | `c5782af367e9a537b90ab364db0c4a440815e99582ba4c559eec3afc91f321bc` | 2026-11-09T02:28:02Z |
| `novirhiza_tether_web_ui-0.3.2-py3-none-any.whl` | `fefa8f634e4058986ce19d3139571ae5ab4a9c8015b5f40f0c41a77210bb5371` | 2026-11-09T02:28:54Z |
| `novirhiza_tether_web_ui-0.3.2.tar.gz` | `786f23b544d2e4177094726a81098d8aff14ad7167e85314e1b7ca24e5d85414` | 2026-11-09T02:28:56Z |
| `novirhiza_tether_databricks-0.3.2-py3-none-any.whl` | `f3a1d3febc83537644d2ab222269c2db7c8ff6ca4df1db85712ec3aa9ee7d41e` | 2026-11-09T02:29:43Z |
| `novirhiza_tether_databricks-0.3.2.tar.gz` | `3677cb308a22be67b5858c4ea09bce0a69cfdbaa67c744ff5bca59fb694021c9` | 2026-11-09T02:29:44Z |

Release manifest assets, in package order: `e0872c92…5ece`, `247262be…b5c5`, `56961ef0…b134`,
`f74cb89a…2000`; full digests are in the JSON form.

Before upload, the core wheel passed `scripts/check_distribution.py` for the base, `openai`,
`tui`, `tui-openai`, `postgres` and `embedded-postgres` profiles. All four wheels were then
installed together from the local files into a fresh Python 3.11 environment and imported.
The web-ui wheel carries the built browser assets.

## Complete gate

Source qualification: [release-032-qualification-20261108.md](release-032-qualification-20261108.md).

| Run | Ref | Platforms | Result |
|---|---|---|---|
| [Release gate 42528857029](https://github.com/DocumentDrivenDX/tether/actions/runs/42528857029) | `299117e` (version bump, before pull request 89) | Linux x86_64 and aarch64, Python 3.11.17 | pass on both: 147 groups, 3,689 tests, 87% |
| [Release gate 42533091832](https://github.com/DocumentDrivenDX/tether/actions/runs/42533091832) | `236c569` | Linux x86_64 and aarch64, Python 3.11.17 | pass on both: 147 groups, 3,693 tests, 87% |
| [Release gate 42551268464](https://github.com/DocumentDrivenDX/tether/actions/runs/42551268464) | `v0.3.2` | Linux x86_64 and aarch64, Python 3.11.17 | pass on both: 147 groups, 3,693 tests, 87%, plus fresh public-index installs |

## Fresh installs from the public index

`scripts/index_install_smoke.py 0.3.2`, run by Release gate 42551268464, installed the base,
`postgres`, `openai` and `tui` profiles of `novirhiza-tether` from `https://pypi.org/simple`
into new environments and imported the package. All four profiles passed on GitHub-hosted
Linux x86_64 and aarch64. Resolved runtime versions: `pydantic` 2.14.3, `pydantic-ai-slim`
2.62.0, `anyio` 4.16.0, plus `psycopg` 3.3.8, `openai` 3.37.0 or `textual` 8.2.8 for the
matching extra.

On macOS arm64 with Python 3.11.17, `novirhiza-tether[postgres]`, `-web-api`, `-web-ui` and
`-databricks` 0.3.2 installed together from `https://pypi.org/simple` into a fresh environment
with no cache. All four imported, and the installed core lists both
`openai-chat-text-tools-v1` and `databricks-ai-gateway-stream-v1` stream profiles. Resolved
versions match the Linux runs.

## Not covered here

- No public-index install of the companion packages ran on Linux; they were installed from
  the public index on macOS only.
- The macOS complete gate for this candidate is incomplete (one timing-sensitive case under
  heavy host load); see the source qualification.
- Nonstreaming gateway responses and gateway cache-count mapping remain unqualified.
