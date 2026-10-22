---
ddx:
  id: ADR-007
  type: adr
  activity: design
  status: accepted
  owner: Erik LaBianca
  updated: 2026-10-22
  authoring:
    home: repo
  links:
    - id: ADR-006
      kind: informed_by
    - id: prd
      kind: informs
---

# ADR-007: Contributions Are Welcome Directly With No CLA, and Are Accepted as Prompts

## Problem

The project wants outside help without putting a legal hurdle in front of it. A contributor license agreement (CLA, a signed grant of rights to the project) is friction and needs legal infrastructure. A developer certificate of origin (DCO, a sign-off asserting the right to submit) still merges code of uncertain provenance, which is a real question when contributors use coding agents trained on unknown sources. The maintainers also build the project largely with coding agents under the repository's gates, so a diff written elsewhere must be reviewed and reshaped to fit anyway.

## Decision

We will accept contributions directly from anyone, through issues and pull requests, with no CLA and no sign-off requirement, and we will accept the content of a contribution as a prompt: a precise statement of the change wanted, its reason, and its acceptance criteria, optionally with reference code as illustration. A maintainer implements the change with the project's agents, under the repository's gates, and credits the contributor in the commit.

- **Direct.** Opening an issue or a pull request needs no agreement and no account beyond the hosting service's.
- **Prompt is the contribution.** A contribution is merged as the maintainers' implementation of the prompt, not as the submitted diff. A pull request that holds code and no prompt is asked to state the prompt; the maintainers MAY extract it.
- **Credit.** The commit carries a `Co-authored-by:` trailer naming the contributor.
- **Inbound license.** Unless the contributor explicitly states otherwise, a contribution intentionally submitted for inclusion is licensed under the project's terms (MIT OR Apache-2.0), without additional terms, as Apache-2.0 section 5 provides. The README and `CONTRIBUTING.md` say so.
- **Public record.** A prompt lives in the issue or pull request, so the reasoning behind a change stays visible.

## Why We Chose This

### Over a contributor license agreement
It gives the project broad rights, including to relicense, at the cost of a signing step that turns away casual contributors and needs a process and a record.

### Over a developer certificate of origin
It is light, but it asks the contributor to assert provenance of code the maintainers still cannot verify, and it still merges that code.

### Over accepting pull requests as code
It is the usual practice. Here it would merge code whose provenance is unverified and whose style the gates would then rewrite; the prompt carries the intent, which is the valuable part.

## Consequences

**Good**: No legal step for contributors. Little provenance risk in the merged code. Every change is implemented once, in the repository's own voice, under its gates. The reasoning behind each change is public.

**Trade-offs**: Acceptance throughput is bounded by maintainer capacity. Contributors may expect their code to merge as written and must be told the policy up front. A contribution is only as good as its prompt, so the maintainers ask clarifying questions in the issue.

**Drift signals to watch**: Outside code merged without review or without a prompt on record. A CLA or a sign-off check appearing in CI. A contributor's credit missing from the commit. A policy statement in the README that disagrees with `CONTRIBUTING.md`.

## Concern Impact

- **No concern impact**: Contribution policy is not a concern practice. `security-owasp` still applies to the code that is merged.
