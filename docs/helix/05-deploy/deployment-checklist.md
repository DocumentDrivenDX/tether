---
ddx:
  id: deployment-checklist
  type: deployment-checklist
  activity: deploy
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-28
  authoring:
    home: repo
  links:
    - id: prd
      kind: informed_by
    - id: SD-001
      kind: informed_by
    - id: TP-001
      kind: informed_by
---

# Tether 0.1 deployment checklist

## Release

- **Component**: `novirhiza-tether` distribution; `tether` import.
- **Release version**: `v0.1.0` is a candidate, not an existing tag or an authorized publication.
- **Source of truth**: one reviewed main commit, an annotated stable tag at that commit, and one immutable GitHub release holding the exact wheel, source archive, and `release-manifest.json`.
- **Publication route**: local Python 3.11 qualification and build, then direct PyPI upload. No GitHub Actions release job.

## Before Deploy

- **Plan reviewed**: the complete TP-001 P0 gate and every selected release profile have passing evidence at the chosen source commit. Record remaining limits and versions; a passing subset does not close the release gate. Do not create a tag while any required gate is open.
- **Order of operations**: merge the no-Actions release workflow change before pushing any tag or publishing any GitHub release. `.github/workflows/checks.yml` then runs on pull requests only; the former tag-triggered candidate and release-triggered PyPI workflows are removed. Confirm the default branch has those exact trigger changes before proceeding.
- **Config is complete**: `uv.lock` is committed; Python 3.11 and the selected `uv` version are available. The GitHub repository and package name are `DocumentDrivenDX/tether` and `novirhiza-tether`. Check the PyPI name again before creating the tag; a pending trusted publisher does not reserve it.
- **Embedded binary prerequisite**: the published `embedded-postgres` extra installs the driver. Its native `pgserver-pg18==18.6.1` wheel is an explicit operator prerequisite; the checkout's hash-pinned `embedded-binaries` group supplies it for qualification. Published package metadata must contain no direct URL dependencies.
- **Access verified**: the operator can create the tag and draft GitHub release. GitHub release immutability must report `enabled: true`. Local publication uses the operator-provisioned PyPI account token in macOS Keychain, service `https://upload.pypi.org/legacy/`, account `__token__`. The local uploader reads it into memory and supplies it to Twine without printing it or placing it in a command argument or checkout file. The pending GitHub trusted-publisher binding does not authenticate this local upload. A project-scoped token can replace the account token after the project exists.

## Validation Gates

| Gate | Must Pass Before | Check |
|------|------------------|-------|
| Full P0 qualification and source identity | tag | `make check CHECK_BASE=HEAD` on the clean selected commit, plus TP-001's required installed and real-profile evidence; verify `git status --porcelain` is empty |
| Artifact identity | draft release | From the annotated `v0.1.0` tag, `uv build --out-dir "$release_stage/dist"`; run `scripts/check_distribution.py` against the exact directory for base, `openai`, `tui`, `postgres`, and `embedded-postgres`; `verify_release_artifacts.py create-manifest` must bind tag, full commit, package, version, and both file digests |
| GitHub immutability | published release | `gh api repos/DocumentDrivenDX/tether/immutable-releases` returns `enabled: true`; publish the draft only after its three assets match the local manifest |
| Released asset identity | PyPI upload | Download the published release assets into a fresh directory and run `verify_release_artifacts.py verify-release` with the same tag, commit, repository, and package; upload **those verified wheel and source archive files**, never a rebuild |
| PyPI result | support claim | Compare PyPI's reported SHA-256 for both files to the manifest, then install the published distribution in a fresh Python 3.11 environment and run the selected smoke journeys |

## Rollout Stages

| Stage | Deploy Action | Exit Condition |
|-------|---------------|----------------|
| Local candidate | Qualify the selected commit, create its annotated stable tag locally, build once, check all selected extras, and generate the manifest | Every P0 gate passes and wheel/source archive metadata says `novirhiza-tether` at the tag's version |
| GitHub release | Push the tag only after automatic tag/release workflows are absent from main; create a draft release with the exact three assets, inspect it, then publish it | The release is immutable, non-prerelease, and `verify-release` accepts fresh downloads and GitHub asset digests |
| PyPI | Read the account token securely from Keychain and run local Twine against only the verified wheel and source archive | Both published file hashes match the manifest and a clean Python 3.11 install passes the release smoke |

After the P0 decision and workflow-trigger change are complete, the operator uses this sequence from a clean checkout of the selected main commit. Stop on any failed command; use a fresh staging directory for each candidate.

```sh
release_tag=v0.1.0
source_commit=$(git rev-parse HEAD)
release_stage=$(mktemp -d)
test -z "$(git status --porcelain)"
make check CHECK_BASE=HEAD
git tag -a "$release_tag" "$source_commit" -m "Tether $release_tag"
uv build --out-dir "$release_stage/dist"
for profile in base openai tui postgres embedded-postgres; do
  if [ "$profile" = base ]; then
    uv run --locked python scripts/check_distribution.py "$release_stage/dist"
  else
    uv run --locked python scripts/check_distribution.py "$release_stage/dist" "$profile"
  fi
done
uv run --locked python scripts/verify_release_artifacts.py create-manifest \
  --dist-dir "$release_stage/dist" --output "$release_stage/dist/release-manifest.json" \
  --tag "$release_tag" --repository DocumentDrivenDX/tether \
  --source-commit "$source_commit" --package novirhiza-tether
```

Push the tag only after confirming the main branch has no automatic tag or release workflow. Create a draft GitHub release with the three staged assets, inspect its tag and files, then publish it. The following commands continue the same shell session and variables used above:

```sh
git push origin "$release_tag"
gh release create "$release_tag" --verify-tag --draft \
  --title "Tether $release_tag" "$release_stage/dist/"*
# Inspect the draft's tag and three asset names before publishing.
gh release edit "$release_tag" --draft=false
verified_stage=$(mktemp -d)
mkdir "$verified_stage/dist"
gh release download "$release_tag" --dir "$verified_stage/dist"
gh api "repos/DocumentDrivenDX/tether/releases/tags/$release_tag" > "$verified_stage/release.json"
uv run --locked python scripts/verify_release_artifacts.py verify-release \
  --release-json "$verified_stage/release.json" --dist-dir "$verified_stage/dist" \
  --tag "$release_tag" --repository DocumentDrivenDX/tether \
  --source-commit "$source_commit" --package novirhiza-tether
# The local Keychain-backed uploader passes only these two verified files to Twine.
```

Compare both PyPI file SHA-256 values with `release-manifest.json` and perform the fresh Python 3.11 install smoke before claiming support. Do not put the token in a shell command, transcript, or file in the checkout.

## Rollback

**Before PyPI upload**: stop, retain the candidate evidence, and leave the release unpublished or record why an immutable published release has no PyPI upload. Correct the source and choose a new version if the tagged contents must change.

**After PyPI upload**: PyPI will not accept replacement files under the same filename. If a released version is broken, yank it with a reason and release a corrected new version; never overwrite the tag, GitHub assets, or PyPI files. If only one distribution file uploaded, verify its PyPI hash against the manifest before retrying the missing file from the same verified release assets.
