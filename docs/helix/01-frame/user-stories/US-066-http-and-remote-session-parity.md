---
ddx:
  id: US-066
  type: user-stories
  activity: frame
  status: draft
  owner: Erik LaBianca
  updated: 2026-10-29
  authoring:
    home: repo
  links:
    - id: FEAT-014
      kind: informed_by
    - id: CONTRACT-006
      kind: informed_by
    - id: SD-002
      kind: informed_by
---

# US-066: Embed an HTTP API and Recover Remote Session Outcomes

**Feature**: [FEAT-014 — HTTP API and Remote Clients](../features/FEAT-014-http-api-and-remote-clients.md)  
**Feature Requirements**: HTTP-01, HTTP-03, HTTP-04, HTTP-06, HTTP-09, HTTP-10, HTTP-12  
**PRD Requirements**: FR-65, FR-67  
**Priority**: P2

## Story

**As a** Python application developer  
**I want** to mount Tether's optional HTTP API beside my authenticated application and connect my browser to its existing client  
**So that** terminal and browser users can work with the same authorized sessions and recover accepted commands after a network interruption.

## Context

Maya already owns the Python host, identity integration, and storage profile. The remote interface must reuse that host and preserve its current authorization, approvals, disclosure, and feed behavior. A browser or API process restart must not turn a lost response into a duplicate turn.

## Walkthrough

1. Maya creates the Tether host through the public settings/factory and typed ports, then mounts the optional API with a client provider and her authentication integration.
2. A browser authenticates through Maya's application and lists the same owner-scoped sessions as the TUI.
3. The browser sends a fabricated message with request ID `web-req-1`; the API acknowledges accepted turn `turn-1`.
4. The network drops before the browser receives the acknowledgement. The browser reconnects and repeats `web-req-1`.
5. The API returns the same accepted turn identity, and the browser follows committed changes after its snapshot watermark.
6. Maya stops the host through its owner lifecycle after accepted work drains or reports outstanding work.
7. A Python worker creates a remote client with an explicit API URL and credential provider, then lists and sends through the same client protocol.

## Acceptance criteria

- [ ] **US-066-AC1** — Given a conforming client and explicit authenticator, when Maya mounts the API and calls its capabilities endpoint, then every client operation is listed as available or explicitly unsupported (CONTRACT-006).
- [ ] **US-066-AC2** — Given a valid session and no active turn, when the browser sends a request with ID `web-req-1`, then the API returns an accepted turn identity before model completion.
- [ ] **US-066-AC3** — Given `web-req-1` was accepted but its response was lost, when the browser repeats the same request, then the API returns the original turn identity without starting another turn.
- [ ] **US-066-AC4** — Given two events share one owner commit sequence, when the stream sends them, then the browser advances its durable resume position only after the final event in that commit.
- [ ] **US-066-AC5** — Given current session permission is revoked during a stream, when the API processes the next event batch, then it closes the stream without disclosing that batch.
- [ ] **US-066-AC6** — Given the API process restarts after the accepted send, when the browser reads the turn result, then it receives the journal-backed terminal outcome or an explicit pending/unavailable result without rerunning the turn.
- [ ] **US-066-AC7** — Given turn `turn-1` has finished and `turn-2` is now active in the same session, when a delayed cancellation targets `turn-1`, then `turn-2` continues without cancellation.
- [ ] **US-066-AC8** — Given Maya supplies an endpoint and credential provider to the optional Python remote client, when she uses its Client protocol methods, then it reaches the same server contract without importing FastAPI or `tether_web_api`.

## Edge cases

- A reused request ID with different text, attachments, or spec returns a conflict and does not start a turn.
- A feed cursor is older than retention; the browser obtains a fresh snapshot and discards provisional reply text.
- History is denied or unavailable; the browser shows the declared status and does not present an empty successful transcript.
- The API process stops while a turn continues in the host; reconnect reads persisted progress and never cancels the turn implicitly.

## Test scenarios

| Scenario | AC ID | Input / State | Action | Expected Result |
|---|---|---|---|---|
| Capabilities and client parity | US-066-AC1 | Dummy client supports reads and send, archive is unavailable | Read capabilities | Every method is marked supported or unsupported; archive reason is explicit |
| Accepted send | US-066-AC2 | Session `s-1`, request ID `web-req-1` | Send `hello` | Response names one accepted `turn-1` |
| Lost response retry | US-066-AC3 | `web-req-1` already accepted | Repeat identical send | Same `turn-1`; model call count stays one |
| Commit boundary | US-066-AC4 | Two persisted events at sequence `9007199254740993` | Consume first and final event | Resume position stays old after first, advances after final, exact value preserved |
| Permission revocation | US-066-AC5 | Actor may read at connect, policy denies before next batch | Deliver next batch | No protected event bytes leave API |
| Server restart | US-066-AC6 | Accepted `turn-1` persisted, API process memory cleared | Read result after restart | Journal-backed terminal result or explicit pending/unavailable; no second dispatch |
| Stale cancellation | US-066-AC7 | `turn-1` completed; `turn-2` active | Cancel by `turn-1` identity | Cancellation reports false; `turn-2` remains active |
| Python remote client | US-066-AC8 | API server, explicit URL, credential provider | Call list and send remotely | Same protocol semantics; FastAPI and API package absent from client dependencies |

## Dependencies

- **Stories**: US-065 (public client protocol and host lifecycle) must establish the consumer boundary.
- **Feature Spec**: FEAT-014.
- **Feature Requirements**: HTTP-01, HTTP-03, HTTP-04, HTTP-06, HTTP-09, HTTP-10, HTTP-12.
- **PRD Requirements**: FR-65, FR-67, FR-17–20, FR-48, NFR-6, NFR-9.
- **External**: Application authenticator, Tether host, CONTRACT-003/005/006.

## Out of scope

- Implementing Maya's authentication provider or hosting infrastructure.
- React layout and presentation customization, covered by US-067.
- Claiming arbitrary storage profiles or external history sources survive deployment interruption without separate qualification.
