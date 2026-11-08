# Gateway stream dialect live receipt (recorded 2026-11-08)

Machine-readable form:
[databricks-gateway-stream-dialect-20261108.json](databricks-gateway-stream-dialect-20261108.json).

With the `databricks-ai-gateway-stream-v1` stream profile selected (ADR-013, CONTRACT-001),
all three gateway tiers of a development workspace complete through `OpenAICompatModel` in
caller identity mode, and every on-behalf-of check passes. Before this change only the
balanced tier completed: the strict profile refused the quick tier's prompt-filter preamble
and filter metadata, and the deep tier's reasoning-summary content parts.

- Tether commit: `9469fa7ca31227487dbe4e2552fb963a42f4a1ae`; Python 3.11
- Route: `POST <workspace>/ai-gateway/mlflow/v1/chat/completions`; services by tier alias
  (`quick`, `balanced`, `deep`)
- Transport: real HTTP (`retries=0`) wrapped by a header observer; `max_attempts=1`
- Physical HTTP calls: 19; application credential reads: 0

| Tier | P1 caller auth | P2 five concurrent | P3 tags and traceparent | P5 request id on receipt | Status | Latency ms | Outcome |
|---|---|---|---|---|---|---|---|
| quick | pass | pass | pass | pass | 200 | 1222 | response |
| balanced | pass | pass | pass | pass | 200 | 1011 | response |
| deep | pass | pass | pass | pass | 200 | 1251 | response |

P4 error path (nonexistent service): status 404, reason `http 404`, error text free of the
response body, gateway request id carried on the error and its receipt: pass.

## Dialect coverage

The eval prompt does not always make each backend emit every frame kind, so three more calls
on the same commit used a prompt that invites reasoning and recorded structure only (keys,
types and enum labels, never values):

| Tier | Frames observed | Outcome |
|---|---|---|
| deep (two calls) | two frames of `reasoning` / `summary_text` parts before answer text; interim usage with null completion counts | response, `stop` |
| quick | prompt-filter preamble (choice filter results and the trailing latency/routing usage chunk appeared in every quick stream inspected) | response, `stop` |

Fabricated-fixture regression tests for both shapes and their negative controls live in
`tests/test_gateway_stream_dialect.py`.

## Limitations

- A single signed-in human user: the five concurrent callers share one user token but carry
  distinct fabricated actor, session, turn and call ids and traceparents. Per-user token
  isolation across different humans is covered only by the mock-transport tests.
- Token equality and request-id correlation are checked in-process; only booleans are
  recorded. No token, workspace host, service name or gateway request id is stored.
- Streaming only. Nonstreaming gateway responses still use the strict body validator and are
  not qualified here.
- Dropped filter metadata is not interpreted beyond the `filtered` flags described in ADR-013.
