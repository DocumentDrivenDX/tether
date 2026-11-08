# 0.3.1 publication receipt (recorded 2026-11-08)

Machine-readable form: [release-031-publication-20261108.json](release-031-publication-20261108.json).

All four 0.3.1 packages come from one source commit,
`ee7810504889ba9286e821b37cd9d12ef8b9ee5e` (merge of pull request 86). Each annotated tag
points at it. Each GitHub release is immutable, and its downloaded assets passed
`verify_release_artifacts.py verify-release` against GitHub's reported digests before upload.
PyPI received the files through the trusted-publisher workflow (`publish.yml`), and PyPI's
SHA-256 for every file equals the GitHub asset digest and the release manifest.

| Package | Tag | GitHub release published | Publish run |
|---|---|---|---|
| `novirhiza-tether` | [v0.3.1](https://github.com/DocumentDrivenDX/tether/releases/tag/v0.3.1) | 2026-11-08T00:02:35Z | [42511863317](https://github.com/DocumentDrivenDX/tether/actions/runs/42511863317) |
| `novirhiza-tether-web-api` | [web-api-v0.3.1](https://github.com/DocumentDrivenDX/tether/releases/tag/web-api-v0.3.1) | 2026-11-08T00:02:40Z | [42511927480](https://github.com/DocumentDrivenDX/tether/actions/runs/42511927480) |
| `novirhiza-tether-web-ui` | [web-ui-v0.3.1](https://github.com/DocumentDrivenDX/tether/releases/tag/web-ui-v0.3.1) | 2026-11-08T00:02:45Z | [42511976845](https://github.com/DocumentDrivenDX/tether/actions/runs/42511976845) |
| `novirhiza-tether-databricks` | [databricks-v0.3.1](https://github.com/DocumentDrivenDX/tether/releases/tag/databricks-v0.3.1) | 2026-11-08T00:02:49Z | [42512043400](https://github.com/DocumentDrivenDX/tether/actions/runs/42512043400) |

## Files

| File | SHA-256 | PyPI upload |
|---|---|---|
| `novirhiza_tether-0.3.1-py3-none-any.whl` | `f59b8281f6d01239a8f4319cb9e7b61ae1b28086d3db43068f540588431b5782` | 2026-11-08T18:49:33Z |
| `novirhiza_tether-0.3.1.tar.gz` | `4714d0bef0996887370fef3d7d4211728ee30e870d24d6362ee02f07d7894d55` | 2026-11-08T18:49:34Z |
| `novirhiza_tether_web_api-0.3.1-py3-none-any.whl` | `f842970ff03b874f06ed3df2c65c8fd5fb707ff77c811b351e69184c7c12767b` | 2026-11-08T18:50:07Z |
| `novirhiza_tether_web_api-0.3.1.tar.gz` | `89ba51824fe2b9a733e0fa79ef611c0e08eef5958d4b56650071c7db4a6f06d5` | 2026-11-08T18:50:09Z |
| `novirhiza_tether_web_ui-0.3.1-py3-none-any.whl` | `01de29144ade01565365ccd00916dba8cdf17906c8ae698a01071c56414c9ac4` | 2026-11-08T18:50:47Z |
| `novirhiza_tether_web_ui-0.3.1.tar.gz` | `558aec5971e5c30419807a376ba23412fea98c943204bd2ca63fe5d298b665e3` | 2026-11-08T18:50:48Z |
| `novirhiza_tether_databricks-0.3.1-py3-none-any.whl` | `f23c207e0481d97c97e1f12c8e8c357865d42db42865e693bea9f32a68403ceb` | 2026-11-08T18:51:35Z |
| `novirhiza_tether_databricks-0.3.1.tar.gz` | `c497ce4df13e66a2d8abb25bea1f128ee4fecbbb4837e99f1aaf573826ae813f` | 2026-11-08T18:51:36Z |

Release manifest assets, in package order: `a95dbed9…f272`, `15a1b05b…a659`, `48c88fb5…7df9`,
`9f390520…3265`; full digests are in the JSON form.

Before upload, the core wheel passed `scripts/check_distribution.py` for the base, `openai`,
`tui`, `tui-openai`, `postgres` and `embedded-postgres` profiles. Each companion wheel was
installed with its local dependencies into a fresh Python 3.11 environment and imported. The
web-ui wheel carries the built browser assets.

## Complete gate

Source qualification: [release-031-qualification-20261106.md](release-031-qualification-20261106.md).

| Run | Ref | Platforms | Result |
|---|---|---|---|
| Local, clean checkout | `ee78105` | macOS arm64, Python 3.11.17 | pass: 145 groups, 3,638 tests, 87% branch coverage |
| [Release gate 42426485753](https://github.com/DocumentDrivenDX/tether/actions/runs/42426485753) | `ee78105` | Linux x86_64 and aarch64, Python 3.11.17 | pass on both: 145 groups, 3,638 tests, 87% |
| [Release gate 42512116775](https://github.com/DocumentDrivenDX/tether/actions/runs/42512116775) | `v0.3.1` | Linux x86_64 and aarch64, Python 3.11.17 | pass on both, plus fresh public-index installs |

## Fresh installs from the public index

`scripts/index_install_smoke.py 0.3.1`, run by Release gate 42512116775, installed the base,
`postgres`, `openai` and `tui` profiles of `novirhiza-tether` from `https://pypi.org/simple`
into new environments and imported the package. All four profiles passed on GitHub-hosted
Linux x86_64 and aarch64.

Resolved runtime versions in both environments: `pydantic` 2.14.3, `pydantic-ai-slim` 2.62.0,
`anyio` 4.16.0, plus `psycopg` 3.3.8, `openai` 3.37.0 or `textual` 8.2.8 for the matching
extra. These are the upper bounds the
[dependency matrix](dependency-matrix-20261106.md) qualified with the complete suite.

## Not covered here

- No public-index install smoke ran for the companion packages or on macOS; the companions
  were install-checked from the local wheels before upload.
- Live Databricks AI Gateway receipts per tier need a signed-in development workspace and an
  operator.
