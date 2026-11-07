# PyPI release path

The first `novirhiza-tether` publication is held until every required P0 gate in [TP-001](helix/03-test/test-plan.md) passes. The [deployment checklist](helix/05-deploy/deployment-checklist.md) records the exact local Python 3.11 build, installed-profile checks, immutable GitHub release identity, fresh asset verification, and PyPI upload order. Do not create a stable tag or upload a package while a required gate remains open.

Release staging stays local and PyPI upload is a manual trusted-publisher workflow. The release operator runs the complete `make check` gate, builds one wheel and source distribution from the annotated stable tag, creates `release-manifest.json` with `scripts/verify_release_artifacts.py`, and attaches those three files to a draft GitHub release. After publishing the immutable release, the operator downloads its assets into a fresh directory and runs the same verifier against GitHub's reported asset digests. Pull requests retain one small Python 3.11 `make pr-check` Actions gate; branch pushes do not duplicate it. The PR gate does not qualify a release: complete test, coverage, integration and installed-profile checks remain mandatory at the explicit release gate.

## PyPI upload

PyPI upload uses a trusted publisher, so no token is stored, pasted or handled. The project's PyPI publisher is bound to `DocumentDrivenDX/tether`, workflow `.github/workflows/publish.yml` and the GitHub environment `pypi`. That environment has no required reviewer and is limited to `main` and stable-tag refs.

Run the workflow manually (`workflow_dispatch`) with the tag of an already published, immutable release. It never builds. It downloads that release's assets, refuses a release that is not immutable, runs `verify-release` against GitHub's reported digests and the tag's source commit, and uploads only the verified wheel and source archive. No tag, release or push event can start it. After the upload, compare PyPI's reported SHA-256 values with `release-manifest.json` and run the fresh Python 3.11 install smoke before claiming support.

PyPI will not replace an existing filename. If a published release is broken, yank it and publish a corrected new version; keep the original tag, release assets, and manifest unchanged.
