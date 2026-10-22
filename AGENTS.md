# AGENTS.md

**Canonical guidance for coding agents working in this repository.** [`CLAUDE.md`](CLAUDE.md)
points here and carries no rules of its own.

## What this repository is

**tether** is an open-source Python library for building agent harnesses: an asynchronous agent
loop, a typed tool-call interface, durable session management, telemetry, and a fast in-process
API that exposes the session list and session data to a user interface. It also ships a simple
CLI and terminal UI as reference clients. It is dual licensed MIT OR Apache-2.0, copyright
Synaptiq.

The repository is governed by HELIX. `.helix.yml` scopes the `helix` flow to `docs/helix/`.
Project artifacts (vision, PRD, concerns, ADRs, contracts) govern; the research under
`docs/helix/00-discover/` informs and never overrides them.

## Handling

This repository is built to be public. Hard floors:

- **No credentials, tokens, keys, or connection strings**, anywhere, ever. Build a
  credential-shaped value from parts at runtime if a test needs one; never write a literal.
- **No private or customer data, and no reference to any private repository, client, or
  organization.** Examples, fixtures, and recorded model conversations use fabricated data
  only. Recording a conversation is allowed only on fabricated prompts.
- **Clean history.** This repository's history starts here. Do not import history, files, or
  documents by copy from another repository without reading and scrubbing them first.

## Working in this repository

- Follow the HELIX activation discipline: read `.helix.yml` and bind the catalog before any
  write under `docs/helix/`; author artifacts from the catalog's template, prompt, and meta
  files, never from memory. Preserve frontmatter except the fields you change deliberately.
- Artifact identifiers are this repository's own (`ADR-001`, `CONTRACT-001`, ...). They never
  link to another repository's identifiers.
- Voice: lead with the finding; never invent numbers, name the owner who can supply a missing
  one; US spelling.
- Stack decisions live in `docs/helix/01-frame/concerns.md` and the ADRs. Do not add a
  framework, runtime, or dependency that no selected concern or ADR covers.
- The library is asynchronous and has no synchronous interface (ADR-001). Do not add a
  `run_sync` helper, a blocking call inside an `async def`, or `time.sleep` in a test.
- The core package reads no environment variable. A settings object is built at the composition
  root and passed in.
- Every port has a dummy implementation and a conformance suite. A new port needs both.
- Telemetry and audit entries carry identifiers, sizes, and reasons, never message or tool
  content.
- Add regression coverage for changed behavior. Do not lower checks, weaken assertions, or add
  a skip or an ignore to get a change past a gate.
- Commit only when asked. Commits carry a `Co-authored-by:` trailer for the person directing the
  session.
