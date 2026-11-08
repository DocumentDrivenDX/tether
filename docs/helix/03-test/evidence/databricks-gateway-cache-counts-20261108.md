# Gateway cache-count live receipt (recorded 2026-11-08)

Machine-readable form:
[databricks-gateway-cache-counts-20261108.json](databricks-gateway-cache-counts-20261108.json).

With the `databricks-ai-gateway-stream-v1` profile selected (ADR-013, CONTRACT-001), the deep
and balanced tiers' top-level cache counts now reach `TokenUsage` as `cache_read_tokens` and
`cache_write_tokens`. Before this change the profile forwarded them and the strict usage
reader dropped them, so both were missing. The quick tier's nested OpenAI counts were already
mapped and are unchanged.

- Tether commit: see the JSON `tether_commit` and its note
- Route: `POST <workspace>/ai-gateway/mlflow/v1/chat/completions`; services by tier alias
- Caller identity mode; real HTTP (`retries=0`) wrapped by a call counter; `max_attempts=1`
- Recorded: statuses, content types, outcomes, finish reasons, booleans and token counts only

| Tier | Call | Outcome | Input | Output | Cache read | Cache write |
|---|---|---|---|---|---|---|
| quick | plain | response, `stop` | 37 | 29 | 0 | 0 |
| balanced | plain | response, `stop` | 48 | 34 | 0 | 0 |
| deep | plain | response, `stop` | 48 | 27 | 0 | 0 |
| deep | cache marker, 1st | response, `stop` | 10420 | 5 | 0 | 10403 |
| deep | cache marker, 2nd | response, `stop` | 10420 | 5 | 10403 | 0 |
| balanced | cache marker, 1st | response, `stop` | 10420 | 6 | 0 | 10403 |
| balanced | cache marker, 2nd | response, `stop` | 10420 | 6 | 10403 | 0 |

Every row is one streaming physical call with status 200, `text/event-stream`, nonempty
content and a receipt whose `schema_profile` is the gateway profile.

## Cache-count semantics

Direct HTTP posts outside Tether (structure and integer counts only) settled what the counts
mean before the mapping was chosen:

- deep and balanced report `cache_read_input_tokens` and `cache_creation_input_tokens` at the
  top level of `usage`, plus a `cache_creation` breakdown
  (`ephemeral_5m_input_tokens`, `ephemeral_1h_input_tokens`) on a write. Without a cache marker
  a 10,420-token prompt reports zero for both, so they do not cache automatically.
- With a cache marker on the system part, the same prompt reports `prompt_tokens` 10,420 on the
  write (10,403 created), on the read (10,403 read) and uncached; `total_tokens` stays prompt
  plus completion. The counts are therefore subsets of `prompt_tokens`, the same meaning as
  OpenAI's nested `cached_tokens` and the port's cache subcounts.
- quick caches automatically and reports the nested OpenAI fields: `prompt_tokens` 6,020 with
  6,017 written, then 6,017 `cached_tokens`.

Fabricated-fixture tests for the mapping and its negative controls live in
`tests/test_gateway_cache_counts.py`.

## Limitations

- Cache rows add the cache marker in the test transport, below Tether, after Tether records
  the request. Tether's request mapping sends no cache markers, so in ordinary use deep and
  balanced report zero unless a deployment caches on its own.
- Nonstreaming bodies, including the strict fallback when a server ignores `stream: true`, are
  not covered: they keep the strict validator and still drop top-level cache counts. Their
  observed structure is recorded in the JSON for scoping only.
- One signed-in human user; caller-identity checks are covered by the earlier
  [stream dialect receipt](databricks-gateway-stream-dialect-20261108.md).
