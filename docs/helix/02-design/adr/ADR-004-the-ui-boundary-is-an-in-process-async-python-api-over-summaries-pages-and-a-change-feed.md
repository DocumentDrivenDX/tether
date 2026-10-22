---
ddx:
  id: ADR-004
  type: adr
  activity: design
  status: proposed
  owner: Erik LaBianca
  updated: 2026-10-21
  authoring:
    home: repo
  links:
    - id: product-vision
      kind: informed_by
    - id: ADR-001
      kind: informed_by
    - id: ADR-005
      kind: informed_by
    - id: CONTRACT-003
      kind: informs
    - id: architecture
      kind: informs
---

# ADR-004: The UI Boundary Is an In-Process Async Python API Over Summaries, Pages, and a Change Feed

## Problem

A user interface needs three things from a harness: a list of sessions, the content of one session, and a way to watch a conversation change. The interface may be a terminal UI, a web server rendering for a browser, or a notebook. If the list is computed from transcripts, it slows as sessions grow. If a viewer must re-read a whole session to see one new message, live conversations cost quadratic work. If the interface reaches into the store, it is coupled to its schema. The operator's direction is that this boundary be a high-performance Python API, not a server.

## Decision

We will expose the session list and session data to a UI through one in-process, asynchronous Python API, the client API, built from three reads and a set of commands. The library contains no HTTP, WebSocket, or server-sent-events surface; a server is an adapter written over this API.

- **List.** Sessions for an owner, from summaries the store keeps current as part of each write, filtered and ordered, in pages addressed by an opaque keyset cursor. A list never reads a transcript and does not compute a total by default.
- **Read.** One session's view (its summary, settings, tools, pending confirmation, and turn index) and its entries in pages addressed by position, forward or backward.
- **Follow.** A change feed for an owner or one session: session updates, turn events, reply text deltas, and pending confirmations, each carrying a monotonically increasing sequence number. A follower resumes from the last sequence it saw. A position older than the retained window yields a resync signal, and the follower lists again. Adjacent text deltas are coalesced, each subscriber has a bounded queue, and a slow subscriber receives a lagged marker instead of slowing the loop.
- **Commands.** Create, send (returning a handle to the running turn), confirm, cancel, close, and settings, through the same API, so an interface needs one object.
- **Performance is designed in and measured.** Summaries are projections maintained in the same commit as each entry (ADR-005); lists and pages use keyset cursors, never offset scans; the feed carries deltas, not snapshots; models are validated when written, and the read path constructs them without re-validating store output; all I/O is asynchronous or offloaded (ADR-001). A benchmark suite with ratchets protects latency and memory. The numeric budgets are set by the owner after a baseline exists.

## Why We Chose This

### Over an HTTP and server-sent-events server in the library
It would make tether usable from any language and from a browser directly, at the cost of a server dependency, a security surface (authentication, cross-origin rules), and a deployment opinion. The vision excludes it; an adapter over the client API serves a browser.

### Keeping the existing application server
The consuming application exposes the client API through its existing authenticated routes and streaming connection. This boundary does not require a new service, a second agent runtime or an AgentKit integration. TypeScript browser code uses the application's wire contract; Python continues to host the library. Any shared schema generation belongs to that application until a reusable client package is explicitly selected. ADR-003 records the language and runtime-adoption decision.

### Over interfaces reading the store directly
It couples every interface to the store's schema, bypasses ownership checks, and gives no change feed. A store would no longer be replaceable behind a port.

### Over an event stream only, with the interface rebuilding state
A list needs the state of every session, so a new interface would replay every event of every session. Summaries and pages avoid that cost.

### Over a query language such as GraphQL
It adds a schema and an execution layer for three reads. The Python API keeps the embedded surface small; a query layer can be supplied by the application. Relative performance requires measurement.

## Consequences

**Good**: A UI can list thousands of sessions without reading transcripts, follow a live turn with deltas, and resume after a disconnect. One object serves a terminal UI and a web server. The store's schema stays private.

**Trade-offs**: Every store must maintain summaries and a change feed transactionally (the conformance suite checks it). Non-Python consumers need an adapter. Keyset cursors do not support jumping to page N. A retained window bounds how far back a follower can resume.

**Drift signals to watch**: An interface importing a store module. A list that reads entries. An offset or a total count on the hot path. A feed event carrying a whole transcript. A subscriber queue without a bound. A server framework imported by the core.

## Concern Impact

- **No concern impact**: The decision applies `hexagonal-architecture` (the client API is the driving port for interfaces) and the benchmark ratchets of the `testing` concern.
