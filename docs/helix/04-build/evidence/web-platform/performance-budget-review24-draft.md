# NFR-7 first-baseline budget review — pending measurements

This execution worksheet supports [WP-36](../../web-platform-implementation-plan.md)
and [PRD NFR-7](../../../01-frame/prd.md). It adopts no numeric budget and
closes no acceptance task. Current formal receipts for all seven profiles remain
required. The PRD authorizes the first baseline to validate or replace its
proposals; subsequent independently measured runs enforce committed ratchets.
Initial baseline establishment is not a historical regression pass.

The observations recorded here belong to source24's frozen 697-file inventory,
canonical SHA-256 `d89c61ab673ac9506ae26ee1dc4d31ba09bfd6442a9bc97d1be6d777cc3309b3`.
They cannot qualify a later implementation without new matching measurements.

## Evidence identity

Record the exact source inventory, successful aggregate, runtime versions,
client hardware profile, workload, limits, actual sample counts, operator code
hashes and immutable artifact bindings for every accepted receipt. Apps also
requires current deployment and export equality before and after measurement;
managed server hardware remains unknown. Refuse stale or mismatched identities.

| Profile | Required formal workload and scope | Current formal receipt |
|---|---|---|
| Local POSIX JSONL store | 10,000 × 200 entries; one 100,000-entry session; 768 fact bytes; 100 samples; explicit 4 GiB index capacity | Completed source24 observation archived; budget decision pending |
| MemoryFiles JSONL reference-capacity store | Same dataset, positions and explicit capacity; volatile durability; does not change shipped defaults | Completed source24 observation archived; budget decision pending |
| Local POSIX public client | 1,000 followers; 100 turns; 200 paced deltas; every consumer verifies text and committed terminal; operational CLI and cursor reconnect | Completed source24 observation archived; budget decision pending |
| VolatileSettings public client | Same in-process follower and turn workload; original required configuration; cross-process delivery inapplicable | Completed source24 observation archived; budget decision pending |
| Public engine exclusive wall | 100 fresh-history, one-model, no-tool, Full-context steps; complete adapter-boundary coverage and callback counts | Completed source24 observation archived; budget decision pending |
| Public follower retained Python | 100 single-follower trials; configured queues 32; verified persisted/live occupancy; separate 32 KiB coalescing and 1,000-follower process envelope | Completed source24 observation archived; budget decision pending |
| Apps public-client reads | 100 actual capability handshakes and 100 list calls; stable measured page cardinality and observed HTTP protocols; accepted preacquired credential | Pending |

## Proposed versus observed disposition

All latency proposals below are p99. Enter actual observations and the final
scoped budget only from accepted formal receipts. A miss requires a reasoned
cause, optimization assessment and explicit acceptance or replacement decision;
do not choose a threshold merely because it exceeds the measurement.

| PRD operation | Original memory / local-durable proposal | Formal observation | Final scoped budget and rationale |
|---|---|---|---|
| List 50 summaries | 3 / 10 ms | MemoryFiles 20.232375 / POSIX 15.585875 ms; both proposals missed | Pending; static assessment below |
| Session view | 2 / 5 ms | MemoryFiles 0.018083 / POSIX 0.017125 ms | Pending |
| Page 100 entries at any position | 5 / 15 ms | Largest measured distribution p99: MemoryFiles 1.017125 / POSIX 0.652167 ms | Pending; measured beginning/middle/end and 100 spread positions, not every possible window |
| Append up to four entries in one step | 2 / 25 ms | MemoryFiles 1.288875 / POSIX 12.345083 ms | Pending; retain declared durability |
| Commit to in-process follower | 5 / 10 ms | POSIX command-to-delivery upper boundary 252.522750 ms; volatile 163.074458 ms | Pending; an upper boundary above the proposal does not isolate or prove a miss by the commit-to-delivery segment |
| Commit to another process | Inapplicable / 300 ms | POSIX separate-process request-to-delivery upper boundary 109.626500 ms | Pending; one separate-process follower, including local HTTP and storage |
| Engine overhead excluding external work | 1 / 1 ms | Fresh-step exclusive-wall upper residual 19.590378 ms | Pending; operational public-boundary exclusive-wall residual, not pure module CPU |
| Follower memory at default queue | 1 / 1 MiB | Full live whole-trial net Python growth p99 1,285,260 bytes; not isolated per-follower memory | Pending scope amendment: required configured queue 32; observed net whole-trial Python growth and separate process envelope |
| Event-loop lag, 1,000 followers and one streamed turn | 20 / 20 ms | POSIX streaming 6.751334 ms; volatile 340.260458 ms, missing the 20 ms proposal | Pending; all 1,000 consumers reached exact text and terminal with zero missed events |
| Core import | 300 ms | Cold-process import/exit 577.014583 ms, above the numeric proposal | Pending boundary disposition; includes interpreter startup and exit, not isolated in-process import time |
| CLI start to first output | 600 ms | Actual list first byte 1011.838792 ms; proposal missed | Pending; CLI help/exit 618.512167 ms is a separate measurement |
| Terminal first paint | 1.5 s | Actual PTY first paint 1643.992458 ms; proposal missed | Pending; 100 samples, separate from the one-sample functional installed journey |

Apps latency is an additional separately scoped observation. Local measurements
cannot establish managed ingress latency. Record capability handshake and list
p99 values independently; keep server hardware and service-level guarantees
unknown. Page cardinality and transport context must match future comparisons.

## Completed POSIX client observations

The completed client workload has 1,000 configured followers, queue size 32,
100 metadata commits and 100 sequential scripted turns. Streaming uses 200 paced
text deltas; every consumer verified the exact ordered reply and the matching
committed terminal, with zero missed events. Streaming loop-lag p99 was
6.751334 ms. The persisted metadata-update workload separately observed loop-lag
p99 77.452500 ms, and its second fresh-subscription pass observed 72.725041 ms.
The streaming result does not establish that every workload stays below 20 ms.

Persisted delivery p99 was 252.522750 ms from public update-command start to
delivery. It includes authorization, command processing, durable update and
fanout, and pools deliveries across 100 commits and 1,000 followers. It is not a
timestamp at accepted storage commit. Its value above a 10 ms proposal therefore
does not by itself prove the isolated commit-to-delivery segment missed that
proposal. The second fresh-subscription pass measured 209.062792 ms at the same
upper boundary; despite its legacy `reconnect` label, it is not retained-cursor
replay.

The independent actual cursor fixture uses retention eight. Replaying three
missed commits measured p99 3.173750 ms across 100 reconnects, with 300 exact
ordered final commit markers. Below-floor reconnect through resync and current
snapshot measured 1.429334 ms; all 100 resync/snapshot checks passed. The exact
floor boundary also replayed while one older cursor resynced. These are local
public-client observations, not an interrupted network transport or default
retention claim.

The separate-process observation used one follower and 100 commits. Its p99
109.626500 ms runs from writer HTTP request start to last-in-commit delivery in
the other process. It conservatively includes local HTTP and storage, and does
not isolate Apps ingress or a pure commit segment.

The 100-sample operational startup distributions were cold-process core import
577.014583 ms, CLI help 618.512167 ms, actual nonempty list first stdout byte
1011.838792 ms, and actual Textual PTY first paint 1643.992458 ms. Import and
help time process spawn through clean exit. First byte and paint use their
explicit observation markers; provenance setup checks occur outside those
samples. The numeric import proposal is exceeded by the conservative process
boundary; isolated in-process import remains unmeasured. Actual CLI first byte
and PTY paint miss their corresponding 600/1500 ms proposals. No separate
startup segments may be inferred by subtracting independent p99 distributions.

Whole scripted-turn p99 was 17215.833250 ms. It includes model adapter, policy,
context/history and storage work over a growing 100-turn session. It is neither
exclusive engine overhead nor a substitute for the separate fresh-history
engine profile. The 425443328-byte process peak includes the host/runtime and
does not establish retained per-follower allocation. This client profile uses a
small fabricated session, not the store profile's 10,000-session dataset, and
cannot establish managed service latency.

## Completed six-profile observation set

[The exact six-receipt archive](reference-performance/source24-observations/README.md)
retains completed source24 measurements, their hashes and original boundaries.
The serial owner completed all six workloads with unchanged source and verified
process-group settlement. Apps remains a separate pending seventh profile.
This archive adopts no budget and cannot qualify a later source revision.

The volatile public-client profile completed all 1,000 exact replies and
committed terminals with zero missed events. Its streaming loop-lag p99 was
340.260458 ms (median 9.352166 ms, 1,197 observations), whereas POSIX measured
6.751334 ms. The volatile result genuinely exceeds the proposed 20 ms wall-lag
target; the durable result must not stand in for it. Volatile retained-cursor
replay and resync/snapshot p99 were 1.238583 and 0.439250 ms, respectively.

The public engine's 100 fresh steps measured exclusive-wall residual p99
19.590378 ms (median 6.904169 ms), with total-step p99 22.852667 ms. This residual
retains wrapper bookkeeping and untimed getters, excludes specified adapter
body intervals and includes nested recorder callback work except actual storage
I/O. It is an operational boundary metric, not pure engine CPU; a 1 ms pure
CPU claim cannot be inferred from it.

The 100-trial memory profile measured net whole-trial traced-Python growth p99
144,345 bytes for the full persisted queue, 1,285,260 bytes for the full live
buffer and 914,557 bytes for the coalesced single stream. Fixture, producer and
model allocations remain included and unrelated allocations may be freed. The
live value above 1 MiB therefore neither proves nor disproves an isolated
per-follower total-memory limit. The separate 1,000-ready-follower envelope
observed 18,169,619 traced-Python growth bytes and process peak RSS 132,628,480
bytes; neither is divided by follower count.

The PRD permits explicit first-baseline replacement of proposed numeric targets.
It does not require the original proposal to pass before a justified replacement
can be adopted. Any final replacement must retain the original proposal and
miss, identify the measured scope and engineering rationale, and establish the
future committed ratchet. No threshold is selected merely to exceed an observed
p99, and initial establishment is not an independent regression pass.

Two localized changes are now authorized for the next cohort: eliminate
identifiable list-query allocation/key work while preserving cursor semantics,
and add accepted-publication generation notifications to remove the demonstrated
100 ms same-store delivery floor while retaining the existing timed polling
fallback and custom-port behavior. Required final measurements will
qualify their actual behavior. No speculative streaming optimization or GC-time
subtraction follows from the source24 observations.

## List-page implementation assessment

The two completed store workloads each measured 100 list calls after one warmup.
POSIX list p99/median were 15.585875/10.887583 ms; MemoryFiles values were
20.232375/15.123479 ms. These are workload observations, not adopted budgets,
regression comparisons or completion of the seven-profile baseline.

Both profiles use `SessionReads.list` and the same shared `stores.queries.page`.
The list call holds the store lock and reads materialized committed summaries;
it performs no journal-file read or SSD flush. The benchmark creates 10,000
ordinary sessions plus one large session, so the default owner-only first-page
query visits 10,001 summaries. Its 2.1 million journal entries are not traversed
by this operation. The large session contributes one summary.

The current query scans all summaries, evaluates owner/filter predicates,
constructs UTC ISO-string ordering keys for every match, sorts the complete
matching collection and then selects the first page. `matches` allocates two tag
sets for every summary even when `tags_all` is empty. The operation deep-copies
the returned 50 summaries, including their nested values; it does not deep-copy
all 10,001 summaries. Copy isolation must remain intact.

The generic algorithm has O(N) predicate/key work, O(M log M) worst-case sorting
and O(M) temporary selection/key storage for M matching rows. That complexity is
not a measured phase attribution. Seeded timestamps follow insertion order, so
Python's adaptive sort may exploit an already ordered run; a claim that sorting
comparisons dominate this particular sample would require profiling. Cursor
filtering occurs after sorting and recomputes ordering positions, although the
measured first-page query supplies no cursor.

MemoryFiles also retains copied raw journal `FileRecord` values in addition to
the common materialized projections. This gives a plausible difference in
resident-object/cache/garbage-collection conditions. The measurements do not
identify that as the reason its list calls were slower. They also do not prove
SSD access caused the POSIX result: no storage access occurs in this read path.

Practical optimization options require measurement and semantic controls:

- Avoid empty-tag set construction and precompute query-invariant predicates.
  Preserve owner, status, date, casefolded-text and tag behavior. This removes
  identifiable allocation work but has no demonstrated latency benefit yet.
- Select only the leading `limit + 1` matches instead of sorting every match.
  This can reduce worst-case selection work and temporary storage, but still
  scans all rows and computes ordering keys. For this naturally ordered seed,
  a heap may be slower than the existing adaptive sort; no gain is assumed.
- Maintain a derived owner/order metadata index or bounded invalidated catalog
  view. This shifts cost and complexity to create, append, update, restore and
  reopen paths. It must reflect only accepted commits under the existing lock,
  preserve UTC/id ordering and cursor-query fingerprints, retain detached page
  copies and current caller checks, and never become cached authority. Arbitrary
  user query caches cannot grow without bounds. Write latency and reconstruction
  cost would need requalification, not only a faster repeated first page.

The present access path favors a fresh metadata projection over incremental
ordering machinery. Its observed tens-of-milliseconds cost is specific to the
10,001-summary first page; it establishes no service-level or end-to-end UX
budget, arbitrary-filter maximum, concurrent-writer behavior or larger-catalog
limit. A reasoned first-baseline decision must weigh the original proposals,
actual access path, measured optimization benefit if any, write/rebuild costs
and all remaining profiles. Merely setting a threshold above these p99 values
does not resolve the missed proposals.

## Required scope resolutions

- Preserve the original numeric proposals and every miss in the final review.
  Explain each replacement with the actual workload, hardware, measurement
  boundary, investigation and chosen engineering margin.
- Store pages cover beginning, middle, end and 100 deterministic spread positions
  with exact index checks. Avoid an exhaustive worst-window timing claim.
- Engine exclusion uses the union of physical model spans minus nested recorder
  callback spans, then unions all measured store/history spans. Untimed getters
  and bookkeeping remain included. Setup, close and deferred observer drain are
  outside the timed step; no CPU or all-background-work claim follows.
- Memory results are observed net whole-trial traced-Python allocation growth,
  including fixture, model, producer and observation allocations. They establish
  neither isolated per-follower total memory nor a mathematical upper bound.
  Retain p99 and maximum observations, serialized payload sizes, causal occupancy
  and overflow checks. Never divide process RSS by follower count.
- Structural boundedness also requires terminal attempt-marker retirement,
  sealed publication scopes and current-identity checks; a finite allocation
  trial alone cannot prove lifetime bounds.
- The single-stream coalesced case uses multiple later deltas totaling 32 KiB;
  it does not stand in for 32 occupied independent live-buffer slots.
- True retained-cursor replay and below-floor resync/snapshot have separate
  distributions and exact ordering controls. Reopening a reader is not a cursor
  reconnect measurement.
- Shared import/CLI/PTY startup is measured once. Provenance setup timeout is
  outside timed samples and contributes no product-latency claim.
- Paired private fanout experiments establish diagnostic loop-lag comparisons
  with complete consumers. Their total runner elapsed times are not throughput
  measurements. Final formal profiles confirm the selected implementation.

## Final adoption checklist

1. Verify all seven current receipts and their complete sample/occupancy/count
   controls, source/runtime/workload identities and resource/process closure.
2. Resolve every retained PRD row with an accepted scoped measured budget or an
   explicit justified replacement. An unmeasured row or follow-up-only owner
   assignment cannot close the retained requirement.
3. Amend PRD wording, TP-002 and WP-36 evidence consistently; preserve artifact
   identifiers, frontmatter and traceability. Root owns final budget decisions;
   Sol applies the authorized governed reconciliation.
4. Establish the initial baseline with exact metric/profile identities and
   committed ratchets. Record this as establishment, not comparison success.
5. Claim regression protection only when a later independent matching run is
   actually compared against those committed ratchets. Keep publication and
   production deployment disposition separate from local acceptance.
