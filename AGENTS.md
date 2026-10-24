# AGENTS.md

**Canonical guidance for coding agents working in this repository.** [`CLAUDE.md`](CLAUDE.md)
points here and carries no rules of its own.

## What this repository is

**tether** is an open-source Python library for building agent harnesses: Pydantic AI Agent with a Tether invariant integration,
a typed tool-call interface, durable session management, telemetry, and a fast in-process
API that exposes the session list and session data to a user interface. It also ships a simple
CLI and terminal UI as reference clients. It is dual licensed MIT OR Apache-2.0, copyright
FocusedDiversity LLC.

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
- Public I/O operations are asynchronous (ADR-001). Pure planners/factories may be synchronous.
  Do not add a blocking I/O facade or `run_sync` helper, block an `async def`, or use `time.sleep` in a test.
- The core package reads no environment variable. A settings object is built at the composition
  root and passed in.
- Every port has a dummy implementation and a conformance suite. A new port needs both.
- Port before you write. When the first downstream platform already has a tested implementation of a component, port it: read it, remove what is specific to that platform, adapt it to this library's contracts, and bring its tests. Do not reinvent it. The commit that adds ported work names it by description, never by the private repository's name.
- Use Pydantic AI Agent as the single runner (ADR-003), with `pydantic-ai-slim` in the base.
  Do not implement an owned loop, default HTTPX protocol client, or private graph integration.
- `tether.runtime.pydantic_ai` owns Agent/capability/message/Conversation integration;
  `tether.adapters.pydantic_ai` owns selected provider clients. Core public types remain Tether-owned.
- Foundation-model SDKs stay out of the base dependency closure. Explicit optional adapter extras
  may use them under ADR-002/012 after profile qualification. SDK wrapping alone is not lock-in.
- Tether owns current policy and READ_SESSION checks, approval/invocation bindings, intent/result
  commits, unknown effects and recovery. Journal facts and original history are authoritative;
  SDK conversation/context/checkpoints are verified derivatives. Fixed trusted capabilities cannot
  add a dispatch or model-call bypass. SPIKE-004 is bounded evidence, not production conformance.
- Telemetry and audit entries carry identifiers, sizes, and reasons, never message or tool
  content.
- Add regression coverage for changed behavior. Do not lower checks, weaken assertions, or add
  a skip or an ignore to get a change past a gate.
- Commit only when asked. Commits carry a `Co-authored-by:` trailer for the person directing the
  session.

## Checks

Use Python 3.11 or the explicitly qualified release matrix, through uv rather than system Python.
Run `uv sync --locked`, then `make check`. CI and the committed pre-commit hook use the same
recipe. `make hooks` installs the repository-local hook path when you want it enabled.

Keep `uv.lock` current. `make check` runs lint/format, strict source typing, shape ceilings,
architectural import checks, the reachable base dependency audit and negative-control tests.
The suite is collected through pytest's public API and every node runs in bounded groups
(maximum 32 cases/64 KiB of ids per child). The existing 180-second child deadline stays in
place; branch coverage accumulates across collection/groups and the unchanged 85% global
floor is enforced once at the end. Empty/duplicate inventories and any failed stage refuse
success. Do not filter cases or lower coverage to fit one whole-suite process deadline.
Slices 2–3 add the gated `tether.engine.Engine` facade, pinned tool registry, normalized
argument/approval bindings, source verification, intent/result commits and fixed serial Agent
tools, followed by local JSONL journal/history, atomic projections, ownership/fencing and
journal-derived pending approval reconstruction. `MemoryTurnLog` and `MessageHistory` remain
limited volatile test subsets; full durable ports live in `types.storage` and `types.history`.
Local storage requires explicit `JsonlRoot` lifetime and a qualified POSIX single-writer
filesystem profile. Unknown writes poison the open writer until reopen; a canceled close must
retain its OS lock until actual I/O exits. Process-exit tests do not establish power-cut proof.
Supply the original pinned specs when reconstructing pending work; SDK caches confer no
authority. The partial `Tether.client(actor)` facade provides bounded owner/policy-checked
metadata, history, attempt detail and verified pending reads. Raw exchange payloads require
current READ_CALL_PAYLOAD at aggregate disclosure as well as individual hydration. Recovery
currently accepts immediate writable history with all record kinds and durability at least
that of the journal; external/eventual profiles require separate qualification. Async context
close retains storage ownership until actual worker exit. Persisted follow uses bounded independent replay workers and per-variant current authorization;
retention gaps reset beyond a captured owner watermark. Task-owned Client commands,
provisional stream production,
full safe-invocation recovery/manual resolution, context and telemetry remain later work. `AllowAll` is the documented permissive default. Engine's
default journal attempt recorder checks current reads/model policy and commits preparation
before each physical model attempt; supplying a test recorder does not establish durability.
The new-input seam adopts the lease turn id, prepares content before atomic supersession/start/
input acceptance, and notifies only after the known commit. Result content uses invocation plus
payload-digest keys; the journal retains one result key. Orphan content is never outcome proof.
Accepted supersession invalidates derived Run caches before fallible SDK setup.
Development dependencies include the optional
provider SDK for tests; it must stay out of the runtime base closure. Do not promote fabricated
transport checks to live server/profile qualification.
