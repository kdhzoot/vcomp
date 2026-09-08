# Chapter 2/3 experiment approval package

**2026-09-07 current Chapter 2/3 results:** F2Load loading and all four reads completed and were reflected in Figures 4/5 and dependent prose. The combined comparison has eight validated load states and twenty-four reads, reusing the previous seven load controls and twenty reads unchanged. F2Load is 134.731 s including final physical completion (pending bytes zero). Use [PAPER_CHAPTER23_COMMON_RESULTS.md](PAPER_CHAPTER23_COMMON_RESULTS.md). Only the previously deferred size-scaling and instrumentation follow-ups remain outside this campaign.

## Completed and reflected in the paper: 2026-09-06

The non-F2Load continuation finished at **11:09:18 UTC** with seven validated
load states and twenty validated read cells. There were no failed measurements
in this continuation. The 91-B Flush-only and Conventional cases took
8,711.426 and 11,426.155 seconds, respectively, and passed clean read-only
reopen checks. F2Load recovery and all four F2Load reads remain deferred.

The scope-aware promoter (`promote_paper_ch23_common.py --exclude-f2load`)
validated all source identities and read counters, then replaced Figure 2(b),
Figures 4/5, and dependent prose. Figure 2(a) retains its historical series
with distinct common-baseline diamonds; Figure 2(c) retains its historical
profiling data. F2Load is omitted from the new common figures, and its old
headline scaling claims are explicitly labeled historical. Prior prose and
plots are preserved as comments; pre-promotion files are backed up under
the campaign's `full/promotion_backup/` directory. See
[PAPER_CHAPTER23_COMMON_RESULTS.md](PAPER_CHAPTER23_COMMON_RESULTS.md).

Validation includes exact shared Figure 2/4 timing equality, all twenty
Figure 5 source-DB mappings, same-configuration baseline normalization,
LaTeX compilation, and visual inspection of the paper pages containing
Figures 2, 4 and 5. Standalone PDFs/PNGs are exported from the native TikZ
panels so those files match the paper's plotted data.

## Continuation on 2026-09-06: F2Load deferred by the author

The full campaign stopped at 2026-09-05 17:19:27 UTC, after five validated
1-KB load states. F2Load exited successfully after 102.067 seconds, but its
final estimated pending compaction bytes were 17,558,185,945; the strict
zero-pending validation gate rejected it. Its raw output and original DB are
preserved and remain unvalidated. No full read cell or 91-B load had started,
and no new paper data had been promoted. `FAILED.txt` records this original
failure and must not be overwritten by continuation failures.

The author's new instruction is to exclude F2Load and complete the other
experiments first. `scripts/paper/resume_paper_ch23_without_f2.py` therefore
reuses the five validated 1-KB DBs, runs all **20** non-F2Load read cells with
the already qualified options and original relative order, then runs 91-B
Flush-only and Conventional. No F2Load qualification, recovery, extra
compaction, or read is included. The original runner, library, binaries and
measurement parameters remain frozen and are hash checked; the continuation
script is recorded separately. Before resuming, all five source DB identities
matched their validation snapshots, both binary hashes matched, and `/work`
had about 14 TiB free with no active db_bench.

The continuation's completion marker is `NON_F2_COMPLETED.json` (seven load
states and twenty reads), distinct from the full campaign's `COMPLETED`.
The existing eight-load/twenty-four-read promotion pipeline must not be run on
this reduced scope or populated with old F2Load results. Figure/prose updates
require a separate scope-aware validation and review after these runs finish.
This scope change does not approve the deferred scaling/breakdown packages.

The continuation launched at 03:51:51 UTC with host controller PID 195837 and
log `paper_ch23_common_260905_approved_run3/resume_without_f2_controller.log`.
An initial status check in the isolated environment could not see host PIDs;
this was mistakenly interpreted as process termination. A direct host check
confirmed the original controller and reader were still running normally.
The attempted second launch via user systemd was rejected by the campaign
lock before starting any measurement, so there was no concurrent benchmark
or first-reader interruption. Check host processes in the host context.
The running continuation uses the script snapshot saved under
`resume_without_f2_260906/`; subsequent source edits add separate reader/staging
paths for future retries and do not modify its already loaded Python code.

Prepared 2026-09-05 UTC. **P/A/B/C approved by the author on 2026-09-05;
D/E (size scaling and breakdown) explicitly deferred.** The author also
authorized replacing validated figure data and corresponding prose, with
shared measurements and source DBs across figures. No
benchmark was launched while preparing the original proposal. Source inspection,
binary/help checks, and the existing canonical load runner's dry run passed.
The implementation is `scripts/paper/run_paper_ch23_common.py` with shared
helpers in `lib/ch23_common.py`. All load/read flag interfaces and 12 dry-run
templates passed validation; execution pilots are the remaining launch gate.

## Recommendation and build identity

Keep the exact Figure 4 clean release executable for all conventional
loading alternatives **and all Figure 5 readers**:

- Source: `/home/smrc/virtual_compaction/rocksdb-f455-release`.
- RocksDB 11.1.0, commit `f455ab7bd6a8c67f00d48075bb310f131d9fae5f`.
- Executable: the source directory's `db_bench`.
- SHA-256: `8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b`.
- Release, assertions disabled, INFO logging; do not rebuild or replace it
  during the campaign. The checkout was clean and the hash matched at planning.

The executable lacks `workloadc`, but its native `readrandom` supports a
uniform, read-only, single-Get workload. Use `read_random_exp_range=0`, the
same domain, key encoding, concurrency and cache matrix for every reader.
Source inspection confirmed the uniform key selection in `GetRandomKey`,
the Get path in `ReadRandom`, and availability of all required reader flags.
This removes the earlier proposed 10.10.1 reader exception without porting
YCSB or rebuilding the baseline. It changes the request generator and Get
API path relative to the previous YCSB driver: rerun all 24 cells, retain old
results separately, and describe the new workload as **uniform point reads**.
Do not imply the old and new drivers generate identical requests or timing.

Two method-specific loading builds remain explicit exceptions:

- ADOC: retain the validated author-artifact result, commit
  `5ed60f50d6cd8259b94e7f842ff06c6ab4df40a1`, SHA-256
  `68eb52fb2c6db555f47fa877c015015a98ee7ecc879fe3574004c9ec47f47a05`.
- F2Load: rerun the reproducible `vcomp/db_bench`, commit
  `f9e281caa65943140385aa99fff1066da4117be8`, SHA-256
  `c5964e54fb0afc4779fd17b82f7366bdd8d19be9bd2acae33dd26c77e55019cd`.
  The binary hash was rechecked. Preserve source status and diff with the run;
  a commit alone does not describe a dirty source tree. The legacy 102-second
  bar and reproduced 103-second DB will remain historical evidence. The new
  F2Load loading bar and read rows will share the new, validated source DB.

Figure 2(c) needs a separate instrumented build at the canonical source
revision. Its port and qualification are a **later package**, not a silently
different binary used for the overnight loading times.

## Common load conditions

| Item | Frozen setting |
| --- | --- |
| Host | Existing reference host, 48 logical CPUs, Intel Xeon Gold 6336Y |
| Storage | `/work`, `/dev/md0` NVMe RAID-0; serial storage experiments |
| Input | Logical submitted key + value bytes; not final live DB size |
| Main scale | 1,000 GiB = 1,073,741,824,000 bytes |
| 1-KB case | 24-byte key + 1,000-byte value; N = 1,048,576,000 writes |
| 91-B case | 48-byte key + 43-byte value; N = 11,799,360,703 writes; 27-byte rounding remainder |
| Distribution | `fillrandom`, uniform with replacement on `[0,N)` unless the treatment is Fillseq |
| Seed/concurrency | Seed 12345678; one writer; batch size one |
| Memtable | Vector, `allow_concurrent_memtable_write=true` |
| Buffers | 64 MiB each, maximum 16, minimum merge count 1 |
| SST | 64 MiB target, block-based format version 7 |
| LSM | Leveled; kMinOverlappingRatio; seven configured levels |
| Level sizes | Static; base 256 MiB; multiplier 10 |
| Parallelism | 48 background jobs, one subcompaction |
| L0 thresholds | Compaction/slowdown/stop = 4/20/36 |
| Pending bytes | Soft/hard = 64/128 GiB |
| WAL/compression | WAL disabled; no compression; index compression disabled |
| Filter | Bloom filter, 10 bits/key; display terminology remains Filter positive |
| I/O | Direct reads; direct flush/compaction I/O |
| Statistics | Enabled; interval stats every 60 s, throughput reports every 1 s |
| Load timing | Process wall time through terminal flush/compaction/drain; reopen checks outside timing |
| Repetitions | One full run per case in this first pass; no error bars or variance claim |

No warm-up load is included. Record initial device state, CPU topology,
memory, mount details and kernel. Clear page cache before cases on the idle
benchmark host and serialize all measured jobs. Record total DB size, live
SST bytes, per-level file counts, compaction pending state, CPU time, peak
RSS, swap-in/out, device I/O, stalls, SST writes and operation counts.
SST WAF = (flush + compaction SST bytes written) / actual logical input bytes;
device-write amplification is a separate metric.

## P: runner preparation and pilot gate

Pilot run `paper_ch23_common_260905_approved_run1` correctly stopped at the
Last-comp reopen gate: RocksDB's single-level `CompactedDBImpl` fast path
reported zero aggregate statistics despite completing 1,000 successful Gets.
All pilot evidence and DBs were retained. Source inspection established that
`DB::OpenForReadOnly` selects this path when possible, and an explicitly
configured merge operator disables it. The corrected common reader uses
`merge_operator=put` for **every state**, selecting `DBImplReadOnly` while
retaining `readonly=true` and `open_files=-1`. None of these workloads creates
Merge operands, so this operator is unused and does not change stored values.
The correction preserves the exact executable and is qualified in a fresh
pilot. It is a read-engine path control, not a method-specific optimization.

Pilot run 2 passed all eight loading/reopen cases, including clean-reader
compatibility with F2Load. Its first timed read exposed a monitor race when
the child exited between PID enumeration and process-group inspection. The
monitor now tolerates only an already-exited PID; the unrelated-process check
remains enforced. Evidence was retained and a fresh complete pilot is run.

Generalize the canonical load runner for both KV sizes and treatment modes;
freeze explicit effective options rather than relying on different defaults.
Adapt read staging and analysis to `readrandom`, preserving existing YCSB
results and their parser. Store reusable code under `experiments/scripts/`
and `experiments/analysis/`, using `experiments/lib/` for shared operations.

After implementation, dry-run every case and run these pilots:

1. 1-GiB conventional and flush-only cases at both KV sizes (four cases).
2. 1-GiB Last-comp, Fillseq, full Fillseq+10% OW and F2Load paths. Last-comp
   derives from the pilot flush-only source; the sequential cases are separate.
3. 30-second read pilots across all four cache configurations on the small
   baseline, plus Flush-only and F2Load source compatibility checks. These
   establish driver, counters, staging and duration handling, not 1-TB speed.

Require exact operation counts, expected OPTIONS, no assertion/optimization
warnings, successful reopen, valid Get results and counters, and correctly
settled boundaries. Flush-only must have zero compaction bytes and no
non-L0 SSTs. It is not subjected to a compaction drain that changes its state.
Validate known inserted keys separately; a uniform sample of `[0,N)` after
random-with-replacement loading is expected to contain misses. F2Load's
approximate membership must be measured and disclosed, not treated as exact
baseline membership or repaired by changing the read domain.

`readrandom`'s textual found/read message retains only one worker's message
(`Stats::Merge`), so it is unsuitable as an all-thread membership count.
For these read-only, flushed, fixed-1,000-byte-value DBs, obtain successful
Gets from aggregate `rocksdb.bytes.read / 1000`, cross-check against
`GET_HIT_L0 + GET_HIT_L1 + GET_HIT_L2_AND_UP`, and divide by aggregate
`NUMBER_KEYS_READ`. Reject discrepancies; validate the calculation on pilots.
Use aggregate filter/cache counters for the other panels as well.

No full-scale launch occurs if a required pilot fails. Preserve failed logs
and report the failure rather than proceeding with a different configuration.

## A: common 1,000-GiB load cells (Figures 2(a), 2(b), 4)

| ID | KV | Mode | Completion | Figure reuse |
| --- | --- | --- | --- | --- |
| A1 | 1 KB | Conventional | `fillrandom,flush,compact0,waitforcompaction,stats,levelstats` | Figure 2(a) 1,000-GiB point, Figure 2(b) Conventional, Figure 4 Baseline, Figure 5 source |
| A2 | 91 B | Conventional | Same as A1 | Figure 2(a) 1,000-GiB point and Figure 2(b) Conventional |
| A3 | 1 KB | Flush-only | `fillrandom,flush,stats,levelstats` | Figure 2(b) Flush-only, Figure 4 Flush-only, Figure 5 source and B1 prefix |
| A4 | 91 B | Flush-only | Same as A3 | Figure 2(b) Flush-only |

A3/A4 explicitly disable automatic compaction, set all three L0 thresholds
to 1,073,741,824 and set both pending-byte limits to zero. Those are the
treatment differences; binary, input and remaining shared options match.
The new A1 replaces the shared historical 3,320-second value everywhere
only after validation. No separate conventional point is selected per panel.

## B: 1,000-GiB alternatives (Figure 4 and Figure 5 sources)

| ID | Case | Exact treatment and timing |
| --- | --- | --- |
| B1 | Last-comp | Preserve A3; stage a checkpoint with hard-linked immutable SSTs and copied mutable metadata. Run exactly one full-range manual `compact`, one subcompaction, auto-compaction disabled. Loading time = measured A3 prefix + measured final compaction process; checkpoint preparation excluded and disclosed. No second full compaction during validation. |
| B2 | Fillseq | New standalone full-domain `fillseq`, then flush/compact0/drain, with canonical shared settings. |
| B3 | Fillseq + 10% overwrite | Independently load all N sequential keys, flush/compact0/drain; reopen and issue 104,857,600 random-with-replacement overwrites over `[0,N)`, then flush/compact0/drain. Report both phases and their sum. 1,100 GiB logical writes against the 1,000-GiB domain; not 10% distinct-key replacement. |
| B4 | F2Load | New reproducible `fillvirtual` load: PLR error 8, virtual flush 64 MiB, register batch max 256, phase-1 shards 8, materialization workers 48. Preserve command/hash, materialization/drain boundary and resulting DB. |
| Reuse | BlobDB | Keep validated 1,712-second result from the exact canonical release. min_blob_size 128 B, blob-file limit 1 GiB, start level 0, GC off, no blob compression, compaction readahead 0. |
| Reuse | ADOC | Keep validated 4,122-second result: FEA/TEA enabled; initial/max memtable 64/512 MiB; initial/max jobs 48/48; write-buffer count 16; pending limits 64/128 GiB; one subcompaction. |

Vector/concurrent-write policy for B2/B3 now follows A1, unlike the older
Fillseq runner's concurrent-write=false setting. This is a declared
configuration alignment and a new measurement, not a buffer-only sensitivity.
F2Load's custom ingest path does not consume the conventional write-buffer
count. ADOC/F2Load remain method-specific implementations; matching exposed
settings cannot make them identical engines.

## C: uniform read matrix (Figure 5)

Read exactly the new six source states: A1, A3, B1, B2, B3 and B4. All readers
use the **same canonical clean release SHA-256**, including the F2Load DB
after pilot-confirmed compatibility. ADOC and BlobDB do not enter this figure.

| Setting | Value |
| --- | --- |
| Benchmark | `readrandom,stats,levelstats`; 100% single-key Get |
| Domain | `[0,1048576000)`, 24-byte keys; uniform with replacement |
| Distribution control | `read_random_exp_range=0`, batch size 1 |
| Seed | 87654321, identical across states/configurations |
| Threads/duration | 48 threads, 300 measured seconds per cell, duration check after every operation |
| Read count flag | 10,000,000,000; duration controls stopping |
| Open | Read-only staged DB, use-existing=true, auto-compaction disabled, open_files=-1 |
| I/O | Direct reads, fresh process and page-cache reset per cell |
| Statistics | statistics=true, stats_level=3, histogram=true, perf_level=3, interval stats 30 s, reports 10 s |
| Warm-up | No separate read warm-up; measure 300 s from workload start after DB open; record open/prefetch time separately |
| Repetitions | 1; 6 states x 4 configurations = 24 cells, 120 measured minutes |

| Cache ID | Index/filter placement | LRU capacity |
| --- | --- | ---: |
| A | In block cache | 1 byte |
| B | In block cache | 50 GiB = 53,687,091,200 bytes |
| C | Retained by open table readers | 1 byte |
| D | Retained by open table readers | 50 GiB |

One byte represents nominal zero capacity while keeping the cache object.
Pinned metadata occupies memory outside the LRU; report RSS and do not claim
equal total-memory budgets. Stage on the same filesystem using immutable SST
hard links and copied mutable metadata; never open the preserved source DB
directly. Remove only this campaign's disposable reader staging directories
after each run; retain source DBs and all evidence.

Predeclare case order: configurations A, B, C, D. Within them use respectively
`[Baseline, Flush, Last, Seq, SeqOW, F2]`, its reverse,
`[Last, Seq, SeqOW, F2, Baseline, Flush]`, and that third order's reverse.
This spreads position effects without selecting order after observing results.
All actual timestamps and exclusions remain in the manifest.

Figure 5(a)/(b) use configuration D. Filter positive (%) is 100 times full
positive filter checks divided by useful-negative plus full-positive checks.
The left axis remains capped at 5 with a marked, actual-value-labeled clipped
Flush-only bar; percentages use right-axis diamonds. Figure 5(c) divides each
throughput by the Baseline measured under the same cache ID and marks 1 red.
Keep absolute throughput, successful-lookup fractions and RSS in the TSV.

## Overnight order, budget and stop boundary

Execution started: `paper_ch23_common_260905_approved_run3`.
Its 1-GiB pilot completed at 2026-09-05 14:14:58 UTC with all eight load/reopen
cases and seven 30-second reader pilots validated. The full campaign's first
1,000-GiB baseline began at 14:15:37 UTC. Controller PID at launch: 161796.
The durable controller is `scripts/paper/run_paper_ch23_overnight.sh`; its log
is `artifacts/log_loads/paper_ch23_common_260905_approved_run3/controller.log`.
After all full measurements validate, it runs
`analysis/promote_paper_ch23_common.py`, which builds an isolated paper copy
before installing shared TSVs, figures and generated prose numbers. It retains
backups, build logs and page previews. The actual paper remains unchanged
until full validation and the staging build pass.

The common time markers are overlaid as distinct, unconnected diamonds in
Figure 2(a), while its old bars/WAF lines retain their complete historical
series. This resolves the earlier plan's potential mixed-series ambiguity
without rerunning the deferred size matrix. Figure 2(b) and Figure 4 share
the new Conventional/Flush-only values. Historical breakdown and scaling
captions explicitly state their separate builds and buffer policies.

Recommended approval covers P, A, B and C only:

1. P: preparation and pilots; do not quote an unmeasured port/build time.
2. A1 -> A3 -> B1 -> B2 -> B3 -> B4: establish all six new 1-KB source DBs.
3. C: all 24 reads, so Figures 4/5 have a complete matched set first.
4. A4 -> A2: run the two longer 91-B cells, reversing the earlier
   conventional/flush-only ordering. Stop the queue after A2.

Time estimates are scheduling estimates from historical runs, not results:

| Work | Planning range |
| --- | ---: |
| 1-KB load states, including the shared Last-comp prefix | about 3-4 h |
| Full read matrix | 2 measured h; reserve 2-3 h including opening/staging |
| 91-B Conventional | about 3-5 h (historical profiler/two-buffer run: 3.82 h) |
| 91-B Flush-only | about 2-3.5 h (historical profiler/two-buffer run: 2.81 h) |
| Main overnight campaign | about 10-16 h, plus preparation/pilots |

This is not a guarantee of completion by morning or permission to extend the
queue to multi-TB scaling. If execution is slower, finish the current valid
case and report updated remaining time; do not silently shorten read duration,
change thread counts, or mix failed/partial cases into results.

At planning, `/work` had 18.55 TiB available and no active db_bench was found.
Plan roughly 8 TiB for the eight new final states, with a **10-TiB campaign
allocation plus 2-TiB free-space safety margin** for transient writes/metadata.
These estimates are not a filesystem reservation. Recheck before launch and
before each case; stop scheduling if the remaining allocation plus safety
margin is unavailable. Do not delete any previous DB or raw result for space.
Capture prior swap usage; reject new benchmark swap activity rather than
mistaking preexisting swap allocation for an observed run failure.

## D: later size-scaling package (Figure 2(a))

Canonical release and all A settings; vary only logical size and KV size.
One run per point. Reuse A1/A2 at 1,000 GiB; eight additional runs remain.

| GiB | 1-KB operations | 91-B operations | Action |
| ---: | ---: | ---: | --- |
| 500 | 524,288,000 | 5,899,680,351 | New, both KV sizes |
| 1,000 | 1,048,576,000 | 11,799,360,703 | Reuse A1/A2 |
| 2,000 | 2,097,152,000 | 23,598,721,406 | New, both KV sizes |
| 4,000 | 4,194,304,000 | 47,197,442,813 | New, both KV sizes |
| 8,000 | 8,388,608,000 | 94,394,885,626 | New, both KV sizes |

Schedule increasing size, alternating KV order. Calibrate duration and peak
space using A1/A2, then the 500/2,000-GiB cases. Historical 8,000-GiB loading
alone took about 13 h at 1 KB and about 47 h at 91 B under the older build;
do not promise this full matrix overnight or substitute linear extrapolation
for measurements. Retaining both entire series is approximately 24 TiB before
extra alternative DBs and transient compaction output, exceeding present free
space. This package requires a concrete extra-space/retention decision before
launch. The overnight approval does not authorize deleting old or new source
DBs to fit this package.

## E: later breakdown package (Figure 2(c))

Port the existing flush/compaction timers to an isolated build of f455ab7b,
keeping release flags and all A conditions. Record the patch and new SHA.
No such qualified build exists yet. A new binary must never be described as
the exact canonical executable.

Qualification: 1-GiB functional conventional/flush-only pilots for both KVs;
then three release/instrumented pairs of 100-GiB conventional loading for
each KV (12 measured pilot loads), alternating pair order. Compare wall
time, logical operations, output structure, SST-write totals and timer
accounting. Use 5% median wall-time difference as a review trigger, not proof
of equivalence; investigate structure/byte disagreement and quantify all
observed overhead before promoting a breakdown.

After qualification, run four instrumented 1,000-GiB cells: Conventional and
Flush-only at each KV. Flush-only supplies Sort/SST Build/Write/Other flush
times; Conventional supplies Merge/SST Build/Read/Write/Other compaction times.
Also retain Conventional flush timers for the contention comparison.
Report cumulative elapsed time across background jobs, separately from
end-to-end loading time and CPU time. Only canonical uninstrumented A runs
populate Figure 2(b), Figure 4 and the shared Figure 2(a) points.
Reserve roughly another overnight for the four full cells, plus port and
qualification time; refine using the new A measurements and pilots.

## Evidence and promotion

Use fresh run IDs under `paper_ch23_common_<UTC timestamp>`:

- Source DBs: `/work/vcomp/exp/<run_id>/<case_id>`.
- Load logs: `experiments/artifacts/log_loads/<run_id>/<case_id>`.
- Reader logs: `experiments/artifacts/log_runs/<run_id>/<cache_id>/<state>`.
- Validated summaries: `experiments/results/paper_ch23_common_*.tsv`.

Record every exact command, source commit/status/diff, binary hash, effective
OPTIONS, environment and resource snapshots, timing boundaries and source-DB
mapping. Never edit raw logs. Nonzero exits, corruption/OOM, configuration
drift, missing counters/operations, unexpected compaction, failed reopening
or host interference stop promotion and dependent cases. A compact0 race
requires preserved failure evidence and explicitly timed, successful recovery;
never drop recovery time from loading totals.

After successful validation, update shared figures and dependent prose
together, preserving old TeX prose as comments. Keep historical series and
unfinished Figure 2(c)/scaling replacements visibly identified until their
separate packages finish. Compile and inspect the resulting paper figures.
