# Alternatives: YCSB A-F with cached-zero metadata and online compaction

## Current continuation: exclude Flush-only (2026-09-08)

The user requested continuing without Flush-only after the first queue stopped.
The current scope is **five systems x A-F = 30 full cells**, each300s, with the
same executable, configuration, source DBs, and isolation policy below.
Baseline A from `paper_alternatives_ycsb_cached0_260908_run1` is valid and reused
without changing its measured values, timestamps, or original evidence paths.
Its raw log is reparsed and exact command, binary hash, source identity, and
validation record are compared before reuse. The remaining **29 new cells**
need145minutes of nominal measured time, plus staging/open/shutdown overhead.
The new run ID is `paper_alternatives_ycsb_cached0_260908_no_flush_run2`.

Run1 is retained unchanged, including the Flush-only failure and its disposable
clone. Its LOG reports writes stopped because16237L0files exist and one
compaction merging all16237intoL1. Foreground progress then stops, and the
watchdog expires after903.6s. The prior runner detected a remaining process
after signaling its wrapper and stopped safely. The continuation excludes
Flush-only from both pilots and full runs, and verifies termination of all
live members of its own process group; exited zombie processes do not count
as active storage workloads. No source DB or old artifact is removed.

The retained full ordering is the original six-system rotation with Flush-only
filtered out; Baseline A is skipped because it is already complete. The new
runner pipeline is qualified using five3s C pilots plus Baseline A/E5s pilots
on fresh clones before launching the29new full cells. These pilots are not
used as results. Only the five selected source DBs are locked and staged.
The inherited equal-duration and timeout validation rules still apply.
New summaries explicitly identify reused cells and excluded systems, with
both runs' provenance preserved. No paper edits or new build are included.

```bash
python3 experiments/scripts/read/run_ycsb_alternatives.py \
  --run-id paper_alternatives_ycsb_cached0_260908_no_flush_run2 --duration 300 \
  --exclude-flush-only --reuse-run paper_alternatives_ycsb_cached0_260908_run1
```

The sections below preserve the original six-system plan for provenance.

## Approved question and scope

User approval on 2026-09-08: run all six current read-comparison DBs through
YCSB A-F, each for **300 seconds** (the final five-minute request supersedes an
interrupted one-minute request), cached 0 GB, with automatic compaction enabled.
Evaluate the first five minutes of online use after each loading alternative,
including catch-up compaction and write stalls. This is not a steady-state or
fixed-layout read-only experiment, and is not directly interchangeable with the
older clean-binary uniform readrandom results.

36 sequential cells, one repetition: Baseline, Flush-only, Last compaction,
Fillseq, Fillseq+10% overwrite, F2Load x A, B, C, D, E, F. No ADOC/BlobDB, no new
loading, no paper/figure changes in this task. Nominal workload time is 3 hours;
opening, staging, shutdown, and blocked operations can extend wall time.

## Sources and executable

- Source of truth: `results/paper_ch23_common_260907_f2_completion1/loads.json`.
- The six `*_1kb` DBs remain on `/work/vcomp/exp`; five are from the September 5
  common campaign and F2Load is the completed September 7 DB.
- Logical input: 1000 GiB, 1,048,576,000 key IDs, key 24 B + value 1000 B.
- Existing membership differs: sequential states contain all domain keys,
  random loading states roughly 63%, and F2Load approximately 62%. Missing
  initial keys are reported, not treated as an execution failure. Updates may
  create previously absent keys because the port is a whole-value KV adapter.
- Run all cells with the same corrected `vcomp-prof/db_bench`, Release GCC11,
  source commit `dbb0a44a65344f263356c507ec09762c8ed81a71`, SHA-256
  `20d67c38612cee9a34d9ef7114c81bc8366b2d4cf99c598b642e63f21ab74266`.
  A private binary copy is frozen in the raw campaign directory before running.
- Profiler RocksDB 10.10.1 supports SST formats 6 and 7 in source; actual reopen
  and workload compatibility with the existing clean-11.1-generated DBs must
  pass the pilot. New SSTs use format 7 uniformly; existing F2Load SSTs stay 6.

## Frozen online configuration

- 48 foreground threads; 48 background jobs; one subcompaction.
- Every DB is opened read-write, including read-only workload C, so automatic
  compaction can run in every cell. `disable_auto_compactions=false`.
- Standard level compaction, priority 3, seven levels; static level byte limits.
  L0 file thresholds 4/20/36; pending-compaction soft/hard limits 64/128 GiB.
- Online memtable: `skip_list`, 64 MiB write buffer, max 16 buffers, merge min1,
  batch1, concurrent memtable writes enabled. The sources were loaded with a
  vector memtable; it is deliberately not reused for mixed online workloads
  because that implementation copies/sorts mutable contents for Gets and has
  unsupported iterator validation paths. This does not reload/change source SSTs.
- Cached-zero: `cache_size=1` byte, `cache_index_and_filter_blocks=true`, L0 and
  top-level metadata pinning disabled; the prior block-cache type is retained.
- Bloom10, no index compression, no value compression; direct reads and direct
  flush/compaction I/O; unlimited open files; target SST64 MiB, base level256 MiB.
- WAL disabled, matching the approved comparison setup; this is not a durability
  comparison. The disposable online DB is not needed after a successful cell.
- A 50R/50U; B 95R/5U; C100R; D95R/5I; E95Scan/5I; F50R/50RMW.
  A/B/C/E/F use Zipfian, D uses Latest. E scan length uniform1..100.
- No warmup or pre-compaction settling. Time starts in db_bench after Open;
  opening and shutdown wall times are recorded separately from measured time.
  The existing final-operation boundary can overrun300s under write stalls.
- Same seed87654321, statistics level3, histogram enabled, perf level3.
  Per-second throughput reporting and ten-second resource monitoring; cache
  reset is checked between cells. No competing storage benchmark is allowed.

## Source preservation and storage

Acquire and hold POSIX record locks on the six existing source LOCK files.
Compare source identity against the previous campaign's archived identity.
For each cell create a fresh directory on the source filesystem: hardlink only
immutable `.sst`/`.blob` files, copy CURRENT/MANIFEST/OPTIONS/WAL and other mutable
files, and exclude LOCK/LOG. Never open the source DB with RocksDB.

Verify copied metadata and shared SST identities, and recheck original metadata
hashes plus SST inode/size/mtime after each run. Successful disposable copies are
removed only after validation and log preservation; failed copies are retained
for diagnosis. Removal only unlinks the experimental references: source files
remain. Run-specific synthetic writes are disposable and not preserved as DBs.
Stop if `/work` free space falls below2 TiB or a source identity changes.

## Qualification, ordering, and failures

Run the complete staging/measurement/parsing/cleanup pipeline first on six
3-second C pilots (all source DBs), then 5-second Baseline A and E pilots to
exercise online writes and scans. Pilots use fresh links and never feed a full
cell's starting state. Full phase requires successful pilot validation.

Execute full workloads in A-F order, rotating the six-system order once per
workload so each system occupies every order position once. Archive that exact
order. No concurrent storage runs. A watchdog bounds each full process to900s
and each pilot to180s; a killed process is an explicit timeout, never a valid
five-minute measurement. Infrastructure errors stop the queue. Timeouts retain
the clone/evidence and permit the remaining independent cells to be attempted.
Runs whose measured duration differs materially from300s are flagged overrun,
not silently compared as equal-duration runs.

Record aggregate throughput/latency and per-operation histograms, engine Get
attempts/hits (including memtable hits), writes, cache filter/index/data accesses,
engine/OS I/O, compaction/flush bytes, stall time, RSS, elapsed time and final
levels. The db_bench text message retains only one worker's counters; do not use
those as run-wide totals or infer aggregate scan row counts from them.

## Execution and outputs

Runner: `scripts/read/run_ycsb_alternatives.py`.
Parser: `analysis/parse_ycsb_alternatives.py`.
Raw: `artifacts/log_runs/<run-id>/`; disposable DBs:
`/work/vcomp/exp/<run-id>/{pilot,full}/`.
Machine-readable status, exact commands, options, identities, binary/source
hashes and raw resource logs are saved incrementally. Validated summaries are
promoted to a new `results/<run-id>/` bundle on queue completion, without touching
historical results or the manuscript. No commit/push is implicit in launching.

```bash
python3 experiments/scripts/read/run_ycsb_alternatives.py \
  --run-id paper_alternatives_ycsb_cached0_260908_run1 --duration 300
```
