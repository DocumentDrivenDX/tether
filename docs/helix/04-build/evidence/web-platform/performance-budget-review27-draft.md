# NFR-7 source27 accepted first-baseline review

This review supports [WP-36](../../web-platform-implementation-plan.md) and
[PRD NFR-7](../../../01-frame/prd.md). On 2026-11-02 Root accepted the engineering
disposition under the PRD's explicit first-baseline validate-or-replace authority,
after all seven actual measurements were independently schema/source/hash checked.
The existing establishment command completed with tolerance 1.2 and seven absent
baseline destinations; no existing baseline was overwritten. The seven exact
[establishment nodes](performance/source27-first-establishment/first-baseline-establishment.json)
passed. This adopts the scoped reference ratchets, not acceptance closure,
a historical regression pass, an SLA or enforcement of stricter original caps.
The filename retains its preparation history; the decision in this document is
now accepted. Exact [measurement/baseline joins](performance/first-baseline-joins27.json)
identify every committed selected metric and its observed × 1.2 maximum.

The current source27 inventory has 700 files, canonical SHA-256
`4eb2d3e8f38e08604eb617ec6de70b50c48838be00aafc1c7aba1238e923c986`.
The successful aggregate is `b630d80f4a9a24d4e4d75ce7ad54b98b4c6397a72a34a4819fe43c67cd53a860`.
The completed workload observations below belong only to this cohort.
[Source24 observations](reference-performance/source24-observations/README.md)
remain historical; the numbers are not a controlled comparative speedup result.

## Current observation set

| Profile | Current state |
|---|---|
| POSIX JSONL reference store | Actual observation verified; scoped first baseline established |
| MemoryFiles JSONL reference-capacity store | Actual observation verified; scoped first baseline established |
| POSIX public client, 1,000 followers / 100 samples | Actual observation verified; scoped first baseline established |
| VolatileSettings public client, 1,000 followers / 100 samples | Actual observation verified; scoped first baseline established |
| Public engine exclusive wall, 100 fresh steps | Actual observation verified; scoped first baseline established |
| Follower Python allocation, 100 trials / 1,000 aggregate followers | Actual observation verified; scoped first baseline established |
| Current selected Apps public-client reads, 100 opens / 100 list calls | Actual observation verified; exact deployment/export bound; scoped first baseline established |

All six exact measurements and the original serial ledger are retained in the
[source27 observation archive](reference-performance/source27-observations/README.md).
The [manifest](reference-performance/source27-observations/manifest.json) binds
all raw hashes, the current aggregate, reviewed operator and qualified machine
profile. The [original serial ledger](reference-performance/source27-observations/serial-measurement-execution.json)
has SHA-256 `8c44fd41628a0acb3ffb5237b9b6e72c4f211489ff8034cc8316be6782ba18ed`.
All six schemas, source maps, hashes and serial completion metadata were
independently rechecked without another workload run. The original six-only manifest remains historical observation metadata. The
[seven-profile manifest](reference-performance/source27-observations/seven-profile-manifest.json)
adds the separately completed Apps receipt and operator provenance. All seven
first baselines are now established; WP-36 dependency/manual acceptance remains pending.

## Original proposals and current observations

All latency figures below are p99 milliseconds. The original memory/local-durable
proposals remain visible; accepted operational definitions and exact committed
ratchets appear below and in the baseline JSON files.

| PRD operation | Original memory / local-durable proposal | Current source27 observation | Disposition |
|---|---|---|---|
| List 50 summaries | 3 / 10 | MemoryFiles 10.140583 / POSIX 9.010375 | Memory proposal missed; durable proposal met. Adopt separate measured ratchets. |
| Session view | 2 / 5 | MemoryFiles 0.018417 / POSIX 0.017208 | Adopt measured profile/view ratchets. |
| Page 100 entries at any position | 5 / 15 | Largest measured distribution p99: MemoryFiles 0.655542 / POSIX 0.718666 | Beginning/middle/end plus 100 deterministic positions; no exhaustive every-window claim |
| Append up to four entries in one step | 2 / 25 | MemoryFiles 1.317584 / POSIX 11.677292 | Adopt separate measured ratchets; preserve non-durable versus durable semantics |
| Commit to in-process follower | 5 / 10 | 1,000-follower command-to-delivery upper p99: POSIX 147.925000 / volatile 137.948583 | This upper boundary does not isolate accepted commit; above-target upper value alone does not prove the inner segment missed |
| Commit to another process | Inapplicable / 300 | POSIX one-follower/100-commit request-to-delivery upper p99 19.822083 | Conservative upper below proposal for this selected subset; includes HTTP/storage, not Apps |
| Engine overhead excluding external work | 1 / 1 | Public-port exclusive-wall upper p99 20.537122 / median 7.1375815 | Original 1 ms target not validated at this conservative boundary; no isolated CPU miss inferred |
| Follower memory at default queue | 1 / 1 MiB | Full-live whole-trial net Python growth p99 1,401,006 bytes | Broad isolated-total proposal not validated; configured queue 32 and fixture-inclusive net scope require explicit disposition |
| Event-loop lag, 1,000 followers / one streamed turn | 20 / 20 | POSIX p99 7.550167 / volatile 392.050375 | Durable streaming proposal met; volatile 20 ms proposal missed. Both verify 1,000 exact text/terminal checks and zero misses; adopt separate measured wall-lag ratchets |
| Core import | 300 | Cold-process import/exit p99 466.760125 | Numeric proposal exceeded at this boundary; isolated in-process import unmeasured |
| CLI first output | 600 | Actual nonempty-list first-byte p99 370.450375 | Proposal met at actual first-byte boundary; adopt measured ratchet |
| Terminal first paint | 1500 | Actual Textual PTY first-paint p99 1516.746000 | Proposal missed; 100 samples, distinct from one functional journey |

## Store workload and limits

Both completed workloads seeded 10,000 ordinary sessions with 200 entries each,
plus a separate 100,000-entry session, 768 fact bytes per seeded entry, and 100
samples. Each explicitly used a 4 GiB index capacity. The MemoryFiles-backed
JSONL reference-capacity profile is non-durable and is not the shipped default
VolatileSettings capacity or a substitute dummy store. POSIX journal bytes were
3,932,658,515. The list query visits the committed summaries rather than scanning
all journal entries or reading SSD files in the timed operation.

POSIX list median/p99 were 7.387125/9.010375 ms; MemoryFiles values were
8.2546665/10.140583 ms. The localized query change avoids empty-tag allocation and
uses native UTC ordering while retaining encoded cursor bytes and legacy
noncanonical-cursor behavior. Native query/SQL parity and deterministic checks
passed; this does not establish an isolated attribution or controlled speedup.
The remaining MemoryFiles 3 ms proposal miss must remain explicit. No further
optional implementation cycle is inferred from it; final scoped disposition
waits for the complete observations and engineering review.

Measured page p99 values were POSIX beginning 0.699375, middle 0.622042,
end 0.655042, spread 0.718666; MemoryFiles beginning 0.654166, middle 0.634375,
end 0.628542, spread 0.655542 ms. Every sampled page had exact index checks.
Append medians were POSIX 2.932104 and MemoryFiles 0.664062 ms. Independent p99
values cannot be subtracted to infer an unmeasured component cost.

## Completed POSIX public-client observation

The current POSIX public client used unchanged LocalJsonlSettings defaults with
OS durability and resume reporting, 1,000 followers, explicitly configured queue
32, 100 metadata commits and 100 scripted turns. Its receipt belongs to the
small fabricated client session, not the 10,000-session store dataset or Apps.
All timing figures here are p99 milliseconds unless stated otherwise.

The 200-chunk streamed turn produced 3,906 loop-lag observations. Every one of
1,000 consumers verified the exact ordered reply and matching committed terminal;
missed events were zero. Streaming loop lag was 7.550166929 ms (median
0.393916629 ms), below the proposed 20 ms for this durable streaming workload.
Do not generalize this result to volatile storage or every client workload.
The ordinary metadata-command workload separately observed command-to-delivery
147.924999939 ms and loop lag 93.605833137 ms. The legacy `reconnect` field is a
second fresh-subscription pass: command-to-delivery 167.603125097 ms and loop lag
102.791249994 ms. It is not retained-cursor replay. These command boundaries
include the update and fanout; they are not accepted-commit timestamps.

The independent retention-eight cursor fixture measured three missed commits
per sample over 100 samples. Retained-cursor replay to completion was 1.931708 ms;
below-floor reconnect through resync and current snapshot was 1.024375 ms. It
verified 300 exact ordered final commit markers and all 100 resync/snapshot
checks, plus replay exactly at the retained boundary and resync one older. This
is not a main-host default-retention or interrupted network-transport claim.

The separate-process profile used one follower and 100 commits. Its measured
19.822083180 ms runs from writer HTTP request start to final commit delivery in
the other process. This conservative upper includes local HTTP and storage;
it is below the 300 ms proposal for the selected subset and does not isolate
Apps ingress or a pure commit segment.

Startup used 100 samples. Core import/exit was 466.760125 ms and CLI help/exit
436.643375 ms, both measured from process spawn through clean exit. The import
number exceeds the original numeric 300 ms proposal at this conservative
boundary; it does not directly measure isolated in-process import. Actual
nonempty `tether list` first stdout byte was 370.450375 ms, with all 100 clean
exits, and meets the proposed 600 ms boundary. Actual Textual PTY first paint
was 1516.746000 ms, above the proposed 1500 ms. Independent p99 distributions
cannot be subtracted to infer an unmeasured startup segment.

Whole scripted-turn p99 was 15368.448958 ms over a growing 100-turn session.
It includes engine, model adapter, policy, history and storage work and is not
exclusive engine overhead. The separate completed fresh-history engine profile is recorded below; it
does not make whole-turn time an exclusive measurement. Process peak RSS was 427,081,728 bytes including the host/runtime;
it is not retained per-follower allocation. The separate source27 volatile observation is recorded below. All six local observations and their serial ledger are complete and archived.
All-profile first-baseline disposition is accepted below; the separate engine
and follower-memory observations remain distinct measurements. No source24 observation is treated as current or as a controlled
comparison with these results.

## Completed volatile public-client observation

The current volatile public-client profile used actual VolatileSettings with
unchanged composition defaults, no durability and resume off. It is distinct
from the separately capacity-scoped MemoryFiles JSONL store benchmark. The
client workload had 1,000 followers, explicitly configured queue 32, 100 metadata
commits and 100 scripted turns; its small fabricated session does not stand in
for the 10,000-session store dataset or Apps.

The streamed turn used 200 paced chunks and produced 1,217 loop-lag observations.
All 1,000 consumers verified exact ordered reply text and committed terminal
boundaries, with zero missed events. Loop-lag p99 was 392.050375007 ms and median
5.824083006 ms. This actual volatile wall-lag result explicitly MISSES the original
20 ms proposal. The durable 7.550166929 ms result does not qualify this profile.
Neither complete consumer delivery nor zero missed events converts the timing
miss to a pass. No optional new optimization cycle or GC subtraction is inferred. The accepted
measured wall-lag ratchet below preserves this original target miss.

Ordinary 1,000-follower metadata-command p99 was 137.948582880 ms from command start
to delivery, with loop lag 87.231208812 ms. The legacy second fresh-subscription
pass measured command upper 145.432041958 ms and lag 90.915458053 ms; it is not cursor
replay. These upper command boundaries include update and fanout and do not
isolate the accepted-commit segment.

The separate retention-eight cursor fixture replayed three missed commits per
sample over 100 samples. Replay-to-completion p99 was 1.398459 ms and below-floor
resync/current-snapshot p99 was 0.428208 ms. It verified 300 exact ordered final
commit markers, all 100 resync/snapshot observations, replay at the retained
boundary and resync one older. No default-retention or interrupted transport
claim follows. Cross-process storage is inapplicable to this process-lifetime
volatile profile. Startup was measured once in the POSIX runtime cohort; this
profile contains no second import/CLI/PTY measurement.

Whole scripted-turn p99 was 20717.560415855 ms over the growing 100-turn session.
It is not exclusive engine overhead: model adapter, policy, context/history and
storage are included. All six local observations and their serial ledger are complete and archived.
The separate Apps profile and seven-profile budget adoption are now complete below. Source24 observations remain historical
and are not rebound to source27 or treated as a controlled comparison.

## Completed public-engine exclusive-wall observation

The current profile measured 100 fresh-history Full-context public Engine.run_turn
steps, with one model call per step and no tools. Exclusive-wall upper p99 was
20.537122 ms and median 7.1375815 ms; total-step wall p99 was 24.472125 ms and median
10.678771 ms. Exactly 100 model streams and 100 each of attempt reserve/start/observe
were recorded. The original 1 ms proposal is not validated by this conservative
public-port interval. It is not pure engine CPU, so an isolated CPU miss cannot
be inferred by equating the boundaries.

Exclusion uses the union of physical model-body spans minus nested recorder
callback spans, then unions all actual measured storage/history body spans.
Recorder callback work remains included except its real storage/history I/O.
Untimed session/workers/turn_id getters and wrapper bookkeeping remain included;
setup, close and deferred observer drain occur outside the timed run_turn. This
supports no background-CPU or all-module CPU claim. The scripted model and
MemoryFiles storage do not represent remote latency. The dedicated delay and
interval controls are deterministic evidence; their existence does not adopt
a numeric budget or convert whole-turn latency into this metric.

All six local observations and their serial ledger are complete and archived.
The separately deployed Apps read profile is complete. Root accepted the
public-port exclusive-wall definition and the same predeclared variance policy
used for every selected metric, rather than inventing an exception immediately
above 20.537122 ms.

## Completed follower allocation observation

The exact follower-memory receipt SHA-256 is
`72566a777f6d0d2a483eab79ede1aebb3470085c7ffb96abc411b4e5f342e816`.
It reports 100 causal single-follower trials with explicit queue/live capacity 32
and a separate 1,000-follower envelope. Queue 32 is required benchmark
configuration, not a library default.

Observed traced-Python values were:

| State | p99 bytes | Maximum bytes | Boundary |
|---|---|---|---|
| Empty active persisted queue | 19,930 | 21,454 | Current traced allocations in the selected empty-state fixture |
| Full persisted queue | 144,353 | 144,407 | Whole-trial net growth |
| Full live buffer | 1,401,006 | 1,420,550 | Whole-trial net growth |
| One coalesced live event | 921,772 | 921,772 | Whole-trial net growth |

Full-state growth includes fixture, producer/model and observation allocations;
unrelated allocations may also be freed. These numbers are neither isolated
per-follower total memory nor mathematical upper bounds. The full-live number
above 1 MiB does not establish an isolated per-follower limit violation, and it
cannot validate the broad original 1 MiB total-memory proposal either. The accepted first-baseline disposition below replaces that broad meaning
explicitly. Native allocator memory and
RSS are not measured by tracemalloc.

The causal persisted fixture preseeded 35 journal commits before baseline,
consumed one ready event, proved 32 enqueued changes at snapshot and held an
overflow probe pending. The live fixture proved 32 occupied slots with exact
reply/identity checks. Its events used 760 text bytes and 935 serialized bytes in
every trial. The separate coalesced fixture used 32 later deltas totaling 32 KiB
including the consumed first character, with one remaining event containing
32,767 text bytes and 32,942 serialized bytes. These exact checks do not imply
worst-case retention for the configured 1 MiB max_read_bytes or all event mixes.
Structural admission/terminal retirement and cancellation/race controls remain
separate supporting boundedness evidence; finite allocation samples alone do
not establish lifetime bounds.

The aggregate fixture actually registered and readied 1,000 followers. It
observed 18,708,563 bytes of traced-Python retained growth and whole-process peak
RSS 161,693,696 bytes (prior peak 140,378,112). The peak includes the preceding 100
trials and host/runtime state. Do not divide either process value by follower
count or present a maximum per-follower allocation. The complete local set is
now archived; the Apps observation and accepted adoption are recorded below.

## Completed Apps observation and operator correction

The seventh exact receipt is [apps-measurement.json](reference-performance/source27-observations/apps-measurement.json),
SHA-256 `3acc508dc86fe1c3e9819aa20adfffc4cc0b463a6b4696ef176a08139f266505`.
It records 100 real public-client opens and 100 list calls, stable 50-item pages,
HTTP/2, persistent sequential HTTPX, default verified TLS trust and the same
preacquired RAM credential accepted on all requests. Exact deployment38 and
immutable export/source bindings were verified before and after. Handshake p99
was 364.316125 ms (median 309.9395625); list p99 was 716.556208 ms (median 631.7680415).
Client hardware hash is the same qualified `28da12e7…a4961`; server hardware is
unknown managed. This selected read observation is not a large-catalog dataset,
cold-start, server CPU or Apps SLA claim. The timing includes the credential
provider call, network and typed decode; credential acquisition was preperformed
in RAM, so it does not time one CLI invocation per sample.

The actual fixed operator has SHA-256
`a5bf66c9ebb768b58f8daca446eb5e39e4250c2f8a0a361b3693e808f592ac9e` and
[archived exact source](apps-performance/run_apps_performance27_fixed.py.txt).
The [failed original attempt](apps-performance/attempt27-01-failed.json) used
`3d50bd31…14efc`, read only the first 65,536-byte stdout chunk and failed JSON
decoding after the child exited. It produced no valid measurement. The corrected
bounded EOF reader preserved the 2 MiB output cap, 900-second parent deadline,
exact workload and process cleanup; it has a new operator hash and independent
actual attempt. Neither compile nor failed-attempt metadata is promoted as a
measurement.

## Accepted operational budgets and rationale

Root adopted the source27 disposition under PRD first-baseline authority, not a
claim that all original proposals passed. Every selected metric uses the same
predeclared 20% future-variance factor: `maximum = observed × 1.2`. The seven
baseline JSON files are the exact numeric authority, including metrics beyond
the representative values below. There is no per-miss threshold fitting or
retained claim that a higher ratchet enforces a stricter original cap. Initial
establishment does not perform or claim historical regression comparison.

| Accepted boundary | Observed p99 | Committed maximum | Resolution of original meaning |
|---|---|---|---|
| MemoryFiles / POSIX first page of 50 | 10.140583 / 9.010375 ms | 12.1686996 / 10.81245 ms | Preserve original memory 3 ms miss and durable 10 ms met. Replace with exact dataset/reference-capacity profile ratchets. |
| MemoryFiles / POSIX session view | .018417 / .017208 ms | .0221004 / .0206496 ms | Original 2/5 ms subset met; protect measured profile rather than retain a looser old cap. |
| MemoryFiles / POSIX spread page100 | .655542 / .718666 ms | .7866504 / .8623992 ms | Named beginning/middle/end and 100 deterministic spread positions each have separate ratchets and exact-index checks; no exhaustive all-window measured claim. |
| MemoryFiles / POSIX append4 | 1.317584 / 11.677292 ms | 1.5811008 / 14.0127504 ms | Original 2/25 ms subset met; non-durable/OS durability remain separate. |
| Volatile / POSIX command→last follower | 137.948583 / 147.925000 ms | 165.538299456 / 177.509999927 ms | Explicitly replace isolated commit→delivery with caller-visible command upper boundary at 1,000 fanout/queue32; isolated 5/10 ms segment is unvalidated, not inferred failed or passed. |
| POSIX separate-process request→delivery | 19.822083 ms | 23.786499817 ms | Selected one-follower/100-commit upper includes transport/storage and is below original300 ms; not Apps evidence. |
| Actual public-engine exclusive-wall upper | 20.537122 ms | 24.6445464 ms | Replace unvalidated isolated CPU/1 ms meaning with measured interval contract; no pure-engine CPU claim. |
| Empty active follower traced state | 19,930 bytes | 23,916 bytes | Explicit queue32/~1KiB selected state, not default-queue native total. |
| Full persisted/live/coalesced net trial growth | 144,353 / 1,401,006 / 921,772 bytes | 173,223.6 / 1,681,207.2 / 1,106,126.4 bytes | Replace unvalidated broad1MiB isolated total with fixture-inclusive net traced-Python observations. Per-trial maxima also ratcheted, separate from p99. |
| 1,000-ready-follower process envelope | 18,708,563 traced growth / 161,693,696 peak RSS bytes | 22,450,275.6 / 194,032,435.2 bytes | Actual aggregate state, never divided into per-follower memory; includes prior trials/runtime. |
| Volatile / POSIX streaming loop lag | 392.050375 / 7.550167 ms | 470.460450009 / 9.060200315 ms | Preserve volatile20 ms miss and durable20 ms met. Accept scoped fractional-second volatile wall-lag limitation; no universal20 ms claim. |
| Cold-process core import/exit | 466.760125 ms | 560.112150013 ms | Original300 ms exceeded at conservative boundary; isolated import remains unmeasured. |
| Operational CLI first byte | 370.450375 ms | 444.540450163 ms | Original600 ms proposal met for nonempty list; help/exit remains separate. |
| Actual PTY first paint | 1516.746000 ms | 1820.095199905 ms | Preserve original1500 ms miss; no special just-over-observation exception. |
| Selected Apps handshake / list50 | 364.316125 / 716.556208 ms | 437.17935 / 859.8674496 ms | New separately deployment-bound read profile; no inherited local budget or managed-server SLA. |

The exact baseline files also protect fresh-host open, follower loop lag,
second fresh-subscription timing, true retained-cursor replay and floor-resnapshot,
whole scripted-turn timing, help/exit and whole-process memory observations.
Whole-turn labels remain upper boundaries including model/store, never engine CPU.
True retained-cursor/floor metrics retain 300 exact missed commits, 100 resync
snapshots and floor-boundary controls; fresh subscriptions are not cursor recovery.

Repeated full-journal copying/index allocation, unbounded terminal markers,
fanout scheduling, list allocations and the demonstrated 100 ms polling floor
were investigated and finite fixes verified before adoption. A new write-maintained
list index or representation/scheduler redesign would add authority/invalidation/
reopen complexity without a correctness requirement. Remaining GC/retained-host
explanations are unproven. No further optional implementation cycle, forced GC,
subtracted startup percentiles or fixture-only speed claim is used to hide misses.
The public boundary definitions are reproducible and conservatively include
bookkeeping; choosing them explicitly is preferable to claiming unmeasured pure
CPU, isolated commit latency or total native per-follower memory.

All selected metric maxima and exact workload/runtime identities must protect
independent future observations. Hardware qualification remains an operator
precondition; a different runner does not inherit this machine's budgets. Apps
comparisons additionally bind actual page cardinality and HTTP versions. The
initial baseline uses the existing shipped establishment path and seven exact
`establish-*-baseline` nodes; it is not a comparison against itself or a reason
to run the costly workload a second time. Reference ratchets are not SLAs.

Current selected identity/expiry/log acceptance, managed fencing/recovery, Root's
final manual support-claim review, resource cleanup and overall acceptance remain
separate pending outcomes. The already observed two live browser cases do not
substitute for those proofs. Source24/26 observations remain historical.
