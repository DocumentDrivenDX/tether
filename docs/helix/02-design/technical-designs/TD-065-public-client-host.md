---
ddx:
  id: TD-065
  type: technical-design
  activity: design
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: US-065
      kind: informed_by
    - id: FEAT-013
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

# Technical Design: TD-065-public-client-host

**User Story**: [[US-065-public-client-host]] | **Feature**: [[FEAT-013]] | **Solution Design**: [[SD-002-embeddable-client-and-web-interfaces]]

## Scope

Realize US-065 through an installed public embedding facade and an injected
terminal consumer. CONTRACT-006 owns all exact constructors, types, ownership
and operation signatures; CONTRACT-003 governs session authority.

## Technical Approach

Keep the existing session service as execution owner. Add public facade
construction over internal assembly, then change the TUI to accept a client.
No consumer constructs Engine, SessionService or a concrete store.
The compatibility path preserves existing Tether construction while the public
facade becomes the documented ordinary path.

The injected terminal captures CONTRACT-006's atomic snapshot watermark before
its initial list/history presentation and starts the owner feed from that cursor.
A commit during presentation is replayed rather than lost between the reads and
subscription. Gap recovery closes the old subscription before snapshot/history
reads, so a borrowed transport with one connection can recover.

Rebuild terminal diagnostics from current authorized public snapshot/entry reads
whenever history is rendered.
Pin metadata and displayed history to the selected session's captured prefix.
Use a finite reverse-read budget and fixed unavailable presentation for exhausted
or unrecognized status facts. A new lifecycle, changed session prefix, selection
or render generation must suppress an older terminal notice. Clear the previous
transcript before rendering; a refused current read must never append a cached
terminal notice. This does not revoke history already authorized and rendered
earlier in the same operation.

Caller-provided ports are borrowed. The selected built-in local resource profile
acquires journal/history resources once and closes them only through its owning
host. Construction rollback tracks acquired resources in reverse ownership order;
no failure closes a borrowed provider. The owning event loop remains explicit.

## Component Changes

| Proposed or existing path | Change | AC realization |
|---|---|---|
| src/tether/client/__init__.py | Export documented client/host/protocol/value/error surface; avoid optional transport imports | US-065-AC1, US-065-AC8 |
| src/tether/client/protocol.py (new) | Define public protocol and typed capabilities over existing values | US-065-AC2, US-065-AC7 |
| src/tether/client/host.py (new) | Own explicit factory/resource lifetime and internal assembly | US-065-AC1, US-065-AC4, US-065-AC5 |
| src/tether/client/configuration.py (new) | Validate public local/provided resource profiles and finite limits | US-065-AC1, US-065-AC2 |
| src/tether/client/_composition.py (new private) | Assemble current engine/store/history internally without environment discovery | US-065-AC2, US-065-AC8 |
| src/tether/tui/__init__.py, src/tether/tui/feed.py, src/tether/cli/demo.py | Separate client-injected UI from explicitly owned launcher; retain snapshot/replay and owned subscription-stop state | US-065-AC3, US-065-AC4, US-065-AC7 |
| scripts/boundaries/ and scripts/check_layers.py | Enforce consumer public imports and private composition ownership with negative controls | US-065-AC1–AC8 |

The client package owns its public protocol/configuration. Internal assembly may
import core adapters; UI modules may import only the public boundary for Tether
behavior. Private composition is not part of the embedder's extension contract.
New paths are planned, not implementation evidence.

## API/Interface Design

| Surface | Governing contract | Usage |
|---|---|---|
| Host/configuration/resources | CONTRACT-006 | Ordinary facade assembles existing core under explicit lifetime |
| Client reads/commands/follow | CONTRACT-003/006 | Pass actor-bound local or remote implementation to terminal |
| Advanced ports | CONTRACT-001–006 | Public reexports and conformance-tested substitution |

## Integration Points

The standalone launcher owns model/profile configuration, a host or remote
transport and its close. An embedded TUI borrows a client. A failure to acquire
storage or bind identity refuses explicitly; it never starts an implicit demo.
Existing history adaptation remains internal to host construction.

The terminal owns only its follower task and subscription. Set its stop state
before cancellation and recheck after awaited presentation work; a completed
borrowed read may consume cancellation without authorizing another receive or
late presentation callback. Close the subscription without canceling accepted
work or closing the borrowed client/host. During an owned stop, a close error
does not announce a new connection failure in the disappearing view.

## Security

Retained clients repeat current authorization and source-disclosure checks.
Factories accept explicit values; imports and core do not inspect environment.
No public diagnostic serializes credentials, tool content or model messages.
The TUI cannot widen capabilities by inspecting a concrete client implementation.

## Performance

Use existing PRD client/subscriber limits rather than invent a new load target.
Facade creation must not scan transcripts. Bound close waits while retaining
actual-worker ownership; a timed-out close cannot mark cleanup complete.

## Testing

STP-001 owns exact AC/test mapping; proposed test modules include
`tests/client/test_public_host.py`, `tests/client/test_public_client_conformance.py`
and `tests/tui/test_injected_client.py`. Use clean installed artifacts for
US-065-AC1/AC8; dummy public ports for AC2; two borrowed views for AC3/AC4;
a barrier-held real worker for AC5; revocation for AC6; and the remote conformance
profile for AC7. Test actual import-checker negative controls and construction
failure cleanup. No test doubles establish native durability support.

The current terminal regressions are
`tests/test_tui_feed_presentation.py::test_initial_snapshot_cursor_replays_commit_during_initial_render`
and the normal-close/close-error variants of
`tests/test_tui_feed_recovery.py::test_shutdown_after_swallowed_read_cancellation_closes_without_next_receive`.
Keep `tests/test_public_host_ownership.py` as the real held-model/borrowed-view
check: view shutdown must leave accepted work running and then allow its final
result through another client. Focused passes do not replace the current full
and installed terminal gates.
Keep `test_terminal_ui_keeps_context_failure_visible` unchanged. Add deterministic
late-feed/second-render reconstruction, new-turn and session-switch invalidation,
current-read refusal, finite-budget/unknown-status presentation and a same-session
commit during history hydration. Force another terminal refresh and assert the
diagnostic is reconstructed from committed facts.

## Migration & Rollback

Retain supported Tether imports and characterize their existing defaults before
changing the launcher. Introduce the facade before migrating TUI call sites.
No journal migration is introduced by the facade. Roll back UI adoption by
selecting the existing launcher composition while preserving journal/history
identity; never reopen a namespace with another owner while workers remain.

## Implementation Sequence

1. IP-002 baseline/boundary tasks establish characterization and public exports.
2. Implement host/configuration and dummy/conformance suite, then owned/borrowed
   lifetime tests before changing TUI wiring.
3. Migrate the launcher/TUI and prove embedded behavior.
4. Integrate the separately qualified remote client and installed examples.

## Risks

Resource lifetime leaks, accidental optional dependencies and duplicated engine
construction are controlled by ownership tests, clean installs and import rules.
Unknown exact compatibility pins block their selected profile, not independent
facade work.

## Diagnostic Wiring

Host construction/close emits existing content-free lifecycle diagnostics only.
CONTRACT-003/005 retain correlation/source authority. Test captured stdout/span
sinks for bounded identifiers/reasons and absence of generated sensitive markers.
