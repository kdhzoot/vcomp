# Baseline loading repeats: five-minute YCSB C

User request on 2026-09-08 follows the three-baseline layout comparison:
measure whether the two newly loaded baselines change the previous YCSB-C
throughput/read-cost conclusion. Scope is the two new DBs, C only, sequential
300-second runs after a 3-second qualification of each. Historical original
baseline and F2Load C results are reference measurements, not new repetitions.
No loading, binary rebuild, paper changes, or commit/push is included.

## Frozen conditions and protection

Sources: full repeat_01 and repeat_02 in
`artifacts/log_loads/baseline_coverage_260908_repeat1/results.json`; both loaded
1000 GiB of 24+1000-byte KV using the same clean binary and seed as the original.
Validate completed status and archived identities, then hold original POSIX
LOCKs throughout. Never open an original DB in RocksDB. Every pilot/full cell
uses fresh immutable SST hardlinks and copied mutable metadata in its own
`/work/vcomp/exp/<run-id>/<phase>/workloadc/<system>` directory. Allow automatic
compaction only in this disposable DB. Revalidate originals after each cell;
retain failures, remove only verified successful private clones.

YCSB executable: the same Release vcomp-prof build, commit
`dbb0a44a65344f263356c507ec09762c8ed81a71`, SHA-256
`20d67c38612cee9a34d9ef7114c81bc8366b2d4cf99c598b642e63f21ab74266`.
Use a frozen private binary copy. Compare full argv to the original baseline C
from `paper_alternatives_ycsb_cached0_260908_no_flush_run2`; only executable,
DB and report locations may differ. This is NOT the new SST-size-model binary.

Same 48 threads, 300 seconds, seed 87654321, Zipfian distribution, cached-zero
metadata (1-byte block cache, no metadata pinning), direct I/O, read-write Open
and automatic compaction enabled, skip-list memtable 64 MiB x16, 48 background
jobs, format 7, no compression, Bloom 10. C issues only Gets. Reset page cache
before each cell, no warmup, one storage workload at a time. Same 180-second
pilot / 900-second full watchdog, 3-TiB initial and 2-TiB running free-space
thresholds, Release/options/error/swap/interference validation as the original.

## Metrics and interpretation

Capture aggregate throughput and read latency, L1 file-read histogram count
and average latency, filter/index/data cache misses per Get, successful-Get
ratio, compaction I/O and final level layout. Verify C has zero engine writes
and aggregate operations match engine Get attempts. A source identity change,
failed pilot, timeout or material duration overrun stops this narrow queue.

Same loading seed does not force identical asynchronous compaction layouts.
Do not discard the original high-coverage result; distinguish DB-load
repetitions from repeated reads on the same DB. One five-minute read per new
DB can test the layout hypothesis, not establish universal F2Load superiority.

## Execution and evidence

```bash
python3 experiments/scripts/read/run_baseline_repeat_ycsb_c.py \
  --run-id baseline_repeat_ycsb_c_260908_run1 --dry-run
python3 -u experiments/scripts/read/run_baseline_repeat_ycsb_c.py \
  --run-id baseline_repeat_ycsb_c_260908_run1
```

Runner reuses the existing campaign's measured options, staging, safety checks,
parser, and monitoring. Raw evidence is under `artifacts/log_runs/<run-id>/`;
validated summaries, exact commands, original reference C logs and provenance
are copied to `results/<run-id>/` after both cells finish. Original measurement
values and paths are preserved. The run ID must be new; no automatic retry or
overwrite is permitted. Expected wall time is about 11-13 minutes.
