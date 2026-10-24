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
retention gaps reset beyond a captured owner watermark. Client send/cancel now use bounded
owned tasks, shielded completion waits, canonical request retries with a lease-protected recheck,
current-source result materialization and supervised lease heartbeats. Finish in-flight heartbeats
before lease release; cleanup retains ownership even after bounded/canceled shutdown. Explicit
command/session/default spec ids must resolve to a supplied unambiguous pinned spec. Confirmation uses authoritative pending facts before cached runners, atomically commits the decision
and running projection before fallible continuation, and refreshes current principal/credential.
Optional approval notes use bounded immutable history outside executable inputs and audit facts.
Retained successful handles follow accepted continuations; late answers cannot repeat effects.
Create/update use bounded copied owner-authorized requests; the store serializes updates.
Omitted title preserves it, explicit None clears it; public settings refuse managed held context.
Close is owner/read-authorized and lease-serialized; it refuses actual workers and unresolved
effects, prepares incomplete-group results before atomic closure, and keeps closed sessions readable.
Explicit cancellation is idle/nonresumable under ADR-010; interruption remains distinct.
Manual resolve requires current RESOLVE_EFFECT and a lease, refuses live workers and conflicting
decisions, and commits unknown-result closure/decision/barrier atomically without new execution.
Reused partial-group refs need scope/profile/call correlation; malformed immutable groups refuse.
Volatile MemoryHistory refs carry and verify owner/session/message scope.
Pure plan_resume uses scoped dense journal facts and immutable digest/replay declarations;
no schema hook, content hydration or dispatch runs inside the planner. Session identity comes
from the leased log. Canceled/superseded ancestry and malformed source identity refuse planning.
Explicit Client/Engine resume now uses public Agent deferred results, current READ_SESSION/RESUME,
scoped original sources, bounded resume ancestry, same-intent safe retries and cumulative model budgets.
Pending approvals move to the accepted phase without approval; changed bindings close invalid.
Invocation intents/results retain original proposal attribution; recovery attempts use the new phase.
TurnLog, SessionStore and JournalFiles share a concrete WorkerLifetime per storage namespace.
All sync offloads, including pending inspection/reconstruction, retain executor futures until actual
exit; lease acquisition refuses outstanding workers across Engine replacement. Shutdown seals new
submissions and keeps root ownership until physical completion. Independent local roots do not couple.
SessionStore contract4 requires explicit for_actor scopes for every caller operation, including
retained outcomes, followers, archive transfers and turn acquisition. Owner predicates remain actor.id;
model principal is never the storage owner. Bind a fresh isolated actor copy per callback while retaining
the live Credential object. Borrowed scopes cannot perform global recovery or close root/pool ownership;
factory, I/O, rebind and close enforce the owning event loop before mutation. Acquisition refusal never
falls back to an unbound or broader-role connection. Postgres acquisition/pool/role/RLS/maintenance
composition remains application-owned and requires separate database qualification.
Optional ArchiveSource/ArchiveSink now compose independently. InMemoryArchive is bounded and volatile,
verifies full bundle closure/projections, preserves caller scope and rejects stale/conflicting snapshots.
Client archive tiers/history use present policy, explicit pending/denied/unavailable failures, opaque
owner/query/tier cursors and final aggregate payload checks. Derived history keeps partial status even
on empty pages. Full restore requires raw-payload authority, isolated actor copies at provider writes,
original address/codec compatibility and exact reconstructed identity on retry. It never dispatches,
approves or reopens closure. Explicit Tether.archive_session runs after idle/close outside Agent turns;
application retention scheduling and eviction remain external. No automatic archive_on driver exists.
Provisional stream production, broader process/storage fault qualification, context and telemetry remain later work. `AllowAll` is the documented permissive default. Engine's
default journal attempt recorder checks current reads/model policy and commits preparation
before each physical model attempt; supplying a test recorder does not establish durability.
The new-input seam adopts the lease turn id, prepares content before atomic supersession/start/
input acceptance, and notifies only after the known commit. Result content uses invocation plus
payload-digest keys; the journal retains one result key. Orphan content is never outcome proof.
Accepted supersession invalidates derived Run caches before fallible SDK setup.
Development dependencies include the optional
provider SDK for tests; it must stay out of the runtime base closure. Do not promote fabricated
transport checks to live server/profile qualification.


Postgres slice5 now has an optional `psycopg` acquisition boundary, explicit fresh-schema SQL
migrations and immutable `PostgresHistory`, composing independently with the JSONL journal.
Only PostgreSQL18 logged ordinary tables with fsync/full_page_writes/synchronous_commit enabled
are selected by this checkpoint. Verify schema columns/primary keys/persistence, revision/checksum
and namespace on acquisition. Never stamp unmanaged tables or implicitly repair drift. Preserve
canonical bytes and charge failed-envelope bytes against aggregate reads. Public I/O uses finite
caller waits with actual acquisition/transaction/source-release tasks retained through cancellation;
post-commit release failures are unknown. Closing a root seals only its admission, never an external
pool or sibling root. Old repeated close cannot remove a replacement namespace registry entry.
PostgresSessionStore now composes the selected revision2 SQL profile. Its dedicated process and
Agent suites qualify that profile; do not infer guarantees for arbitrary roles, downstream physical
effects or OS power loss. Embedded convenience and broader release/profile coverage remain work.


Revision2 now adds internal indexed session/journal/projection/feed primitives and shared pure
metadata/lookup/ledger/usage reducers. Explicit migrate upgrades verified revision1 while preserving
namespace/history identity; ordinary opening refuses old revisions. Equal journal keys retain original
positions and owner sequences. Every accepted batch, including existing-key retries, must obey total
byte limits; stored envelopes must remain canonical. SQL projection/ledger/usage/feed changes share
the fact transaction. Pending insertion order and exact restore identity match reconstruction. These
internal primitives do not qualify public session-store leases, takeover or deployment RLS.


Internal SQL metadata, snapshot, entry/group, effect and persisted-feed reads now use bounded indexes.
Keep canonical summary/projection/entry checks and aware timestamps/nonnegative positions. Compare
redundant cached columns in SQL without transferring them. Pending metadata requires original
requested state, position, invocation, binding owner/session and source-reference scope. Preserve
opaque provider/codec metadata until later history hydration. Owner-head shared locks hold the
snapshot watermark through pending lookup. Feed filtering advances across unrelated owner changes;
retention/sequence gaps and oversized first events fail explicitly. No public SQL lease or complete
SessionStore capability follows from those read primitives.


Internal SQL lease/capture primitives use server time after row locks and again after final
publication. Renewals atomically require an unexpired matching holder/generation and derive expiry
from database time. Lease holder identity is distinct from the authoritative logical phase;
recovery ends that phase without clearing pending approvals or unknown effects. Expired current
cleanup is metadata-only; stale cleanup cannot touch a replacement. Bounded canonical capture
releases database resources before retained executor reconstruction and validates the lease again
before returning. Reconstruction uses linear group/usage updates and staged clones isolate groups.
These primitives alone do not confer public capabilities; use the full SessionStore process,
Agent and caller-acquisition qualification below when assessing the selected profile.


Public PostgresSessionStore requires explicit caller facades; root caller I/O refuses. Global
recovery retains the original supplied maintenance actor/credential while row ownership comes
from verified candidates. Bound recover_owner is optional and cannot widen to global authority.
Recovery candidate count is bounded by page_limit, transfer stops at its first byte-overflow
sentinel, and returned recovery bytes are bounded; any failure rolls back the whole operation.
Root-local WorkerLifetime admission gates share the namespace's concrete active-worker registry.
Close seals only that root's submissions; already-running futures remain tracked across roots.
Preflight preparation admission before executor submission, so failed registration cannot orphan
work. Leased handles enforce cumulative row plus final-frame bounds in the append transaction,
including lease-holder bytes; stable keys consume no extra prefix budget. Post-commit staging
and final heartbeat failures poison the old handle as CommitUnknown, except terminal LeaseLost.
Cache publication follows retained executor staging and a current lease check. Metadata-only
release never clears actual worker ownership; cleanup preserves an existing primary exception.


Optional EmbeddedPostgres owns only the selected binary subprocess and canonical data-directory
lock. Keep its marker inside PGDATA; refuse unmanaged/partial/orphan servers without adoption,
repair or PID-based termination. Explicit config supplies persistent/runtime paths, bootstrap user
and finite waits. TCP is disabled; Unix socket trust lives inside the owned private runtime folder.
No pool, role selection, implicit migration or data-deletion API belongs here. Root openings race
shutdown safely: register, seal and refuse publication. Retain canceled initdb through normal actual
completion, plus unreturned acquisitions and physical workers, before native shutdown and lock
release. Runtime cleanup errors after process exit still release the data lock. Base/postgres-only
profiles must remain independent of server binaries and the cached pgserver Python lifecycle.
