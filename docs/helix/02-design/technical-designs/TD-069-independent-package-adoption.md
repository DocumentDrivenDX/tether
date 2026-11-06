---
ddx:
  id: TD-069
  type: technical-design
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: US-069
      kind: informed_by
    - id: FEAT-017
      kind: informed_by
    - id: SD-002
      kind: informed_by
    - id: CONTRACT-006
      kind: informed_by
    - id: STP-001
      kind: informs
    - id: IP-002
      kind: informs
---

# Technical Design: TD-069-independent-package-adoption

**User Story**: [[US-069-independent-package-adoption]] | **Feature**: [[FEAT-017]] | **Solution Design**: [[SD-002-embeddable-client-and-web-interfaces]], [[SD-003-web-and-databricks-apps-carrier]]

## Scope

Realize independent installed-artifact adoption for US-069. ADR-022 owns
repository/distribution boundaries; CONTRACT-006/007 own public and carrier
compatibility surfaces. This slice does not publish or deploy artifacts.

## Technical Approach

Build Python wheels/sdists and npm tarballs under pinned toolchains, then move
them to fresh fixture directories without source checkout imports. Exercise
public core/API/UI/carrier composition and replacement there. A source test pass
cannot substitute for the installed cohort.

## Component Changes

| Proposed or existing path | Change | AC realization |
|---|---|---|
| pyproject.toml, uv.lock | Keep existing core identity and add isolated remote extra; preserve TUI extra and base closure | US-069-AC1, US-069-AC7 |
| packages/web-api/pyproject.toml | Own API namespace/dependencies and typing metadata | US-069-AC2, US-069-AC7 |
| packages/web-ui/pyproject.toml | Own UI asset namespace and manifest assembly | US-069-AC2, US-069-AC5, US-069-AC6 |
| packages/databricks/pyproject.toml | Own platform extras and published carrier helpers | US-069-AC2, US-069-AC7 |
| package.json, package-lock.json, packages/{client-ts,react,ui} | npm workspaces, public exports, peer React and packed type declarations | US-069-AC3, US-069-AC5 |
| tests/packaging/test_external_embedder.py, tests/packaging/fixtures/ (new) | Generate isolated external consumer using artifacts, no private imports | US-069-AC1–AC4 |
| scripts/quality.py and package build/check helpers | Aggregate per-package installed checks without weakening existing core gate | US-069-AC5–AC7 |
| docs/public guide source and release evidence paths | Record exact profile compatibility and honest unsupported limits | US-069-AC8 |

The external fixture's behavior comes from public entrypoints. The fixture cannot
import private core, Python adapter internals, private npm paths or monorepo
aliases. Runtime-generated fixture credentials contain fabricated markers only.

## API/Interface Design

The installed fixture consumes CONTRACT-006 factories, generated HTTP/event
schemas, React exports and style opt-in rules; carrier composition follows
CONTRACT-007. No packaging helper defines another public runtime API.

## Integration Points

Build browser assets before the UI wheel and verify manifest/asset consistency.
Build core before optional artifacts and install declared dependency constraints
in clean environments. This unreleased Python `0.2.0.dev0` candidate requires
exact internal cohort versions: hash-verified public core 0.2.0 still exposes the
older client surface, so a broad range would admit an unqualified facade. Prove
real solver refusal for hash-pinned public cores 0.1.0 and 0.2.0. Broader future
ranges require their own evidence. Pack and install npm packages with workspace references
resolved to supported artifact versions. Test default UI, headless hooks and a
custom replacement without installing unnecessary higher layers.

## Security

Never read real credentials to create the artifact fixture. Scan package inputs
and captured diagnostics for fabricated sensitive markers, private source paths
and unintended files. Package index credentials/signing identity are outside this
slice; release preparation cannot silently publish. UI assets contain no secret
runtime settings. Installed carrier uses explicit identity injection.

## Performance

Use ADR-022's proposed 250 KiB gzip reference initial-JS budget and measured
asset manifests. Record package sizes and dependency closures; do not fabricate
artifact-install timing promises. Existing client performance gates remain.

## Testing

STP-001 owns the exact per-AC case mapping. Clean core installation proves
US-069-AC1; namespace ownership and sdist-to-wheel/type/asset tests prove AC2;
packed external npm consumers prove AC3; injected alternative provider/UI prove
AC4; repeated pinned schema/assets builds prove AC5; missing asset/protocol/extra
negatives prove AC6; range-boundary/extras resolution and complete regression
cohort prove AC7; documentation/support-claim audit proves AC8.

Installed gates must test built artifacts, not add src to PYTHONPATH or use
editable installs. Negative controls prove a source-only dependency cannot sneak
through. Browser proof drives the Python-served production assets. Local results
never qualify Apps, Lakebase or the app/user authorization profile.

## Migration & Rollback

Keep src/tether and its existing package name. New top-level namespaces avoid
file ownership overlap. A compatibility table declares supported combinations;
coordinated releases are allowed without forcing identical version numbers.
Rollback selects the preceding tested artifact cohort and preserves session
schema/profile compatibility; unsupported schema downgrades refuse. No package
operation performs database migrations during import or startup.

## Implementation Sequence

1. Establish dependency/export rules and registry/name/version checks.
2. Add deterministic schema and asset manifests with source build ownership.
3. Build artifacts and isolated Python/npm consumers; implement failure controls.
4. Exercise replacement composition and the default carrier packet.
5. Run the complete source-specific release gate, documentation and evidence
   audit; prepare checksums/rollback notes without publication.

## Risks

Workspace imports, missing wheel assets, React duplication and drift between
Python schemas and TypeScript types are explicit installed negative controls.
Registry name availability and exact range pins remain bounded IP-002 tasks.

## Diagnostic Wiring

Build and install receipts record artifact identities, versions, checks and
bounded reasons. Existing metadata-only telemetry rules apply when the fixture
executes sessions; no message/tool payload is captured as operational telemetry.
