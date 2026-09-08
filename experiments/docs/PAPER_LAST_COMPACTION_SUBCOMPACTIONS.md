# Last-Compaction Subcompaction Pilot

## Question

Does key-range subcompaction make the naive last-compaction loading method
faster than a settled natural RocksDB load when both build the same 100 GB
logical dataset?

This pilot supports the background comparison that motivates removing
intermediate SST materialization. It does not establish that last-compaction
reproduces the natural leveled layout.

## Configuration

- Binary: `vcomp/db_bench`, RocksDB 10.10.1
- Logical input: 100 GiB submitted bytes
- Key/value size: 24 B / 1000 B
- Generator: `fillrandom`, seed 12345678
- Threads: one writer
- Memtable: vector
- Compression: none
- WAL: disabled
- Direct reads and direct flush/compaction I/O: enabled
- Background jobs: 48
- SST/memtable settings: the binary defaults used by the existing
  load-challenge runs

The two runs are:

1. `baseline`: normal leveled compaction with `subcompactions=1`, followed by
   `flush,compact0,waitforcompaction` so the measurement ends only after the
   tree settles.
2. `lastcomp_sub48`: auto compaction disabled during loading, followed by one
   full `CompactRange` with `subcompactions=48`.

The baseline intentionally retains its normal subcompaction setting. The
treatment changes the final full compaction only. This answers whether a
parallel final compaction can beat normal loading; it is not a controlled
`subcompactions=1` versus `subcompactions=48` sweep.

## Execution and interference control

The active 8 TB true-91 B baseline has priority. The pilot runner waits until
no `db_bench` process exists, waits an additional settling interval, and checks
again before starting. Only one pilot run executes at a time. If another
`db_bench` appears, the load runner fails rather than overlap measurements.

Run:

```bash
nohup bash experiments/scripts/load/run_lastcomp_subcompaction_100gb.sh \
  > experiments/artifacts/log_loads/paper_lastcomp_subcomp_100gb_queue.log \
  2>&1 &
```

## Metrics and validation

For each run, retain:

- end-to-end and `fillrandom` time;
- final forced-compaction time where applicable;
- actual scheduled subcompaction count;
- compaction read/write bytes and host-visible device writes;
- final DB size, level layout, and estimated pending compaction bytes;
- executable hash, repository state, full command, CPU time, and peak RSS.

The treatment is considered active only if the RocksDB statistics report more
than one scheduled subcompaction. A run is invalid if it overlaps another
`db_bench`, exits nonzero, leaves unreported logs, or the baseline does not
reach its explicit settling boundary.

## Outputs

- Raw runs:
  `experiments/artifacts/log_loads/paper_lastcomp_subcomp_100gb_<run-id>/`
- Databases:
  `/work/vcomp/exp/paper_lastcomp_subcomp_100gb/<run-id>/`
- Machine-readable provisional summary:
  `experiments/artifacts/log_loads/paper_lastcomp_subcomp_100gb_<run-id>/summary.tsv`

Results remain provisional until the two commands, completion boundaries,
actual subcompaction count, and final layouts have been checked together.

## Concurrent exploratory run

At the author's direction, an additional 100 GB run may execute while the
8 TB true-91 B baseline is active by setting
`ALLOW_CONCURRENT_DB_BENCH=1`. Such a run disables page-cache dropping to
avoid disrupting the active load. Its elapsed times are exploratory: CPU and
RAID interference are uncontrolled, and device-write deltas include traffic
from both experiments. Do not promote its absolute times, normalized speedup,
or device-write measurements as paper evidence. The actual scheduled
subcompaction count remains valid for checking feature activation.

## 2026-08-23 concurrent exploratory result

The author explicitly requested that the pilot run concurrently with the
active 8 TB true-91 B baseline. Run ID: `260823_0540_concurrent`.

| System | Configured / actual subcompactions | Fill time | Final compaction | End-to-end | Pending bytes | Peak RSS | Final layout |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| baseline | 1 / 0 | 328.921 s | included in settle | 343 s | 0 | 1.241 GiB | leveled, L1--L4 |
| last-comp | 48 / 35 | 133.251 s | 33.714 s | 168 s | 0 | 113.065 GiB | 1,039 files, all in L1 |

Under this concurrent run, last-comp completed 2.04x faster than baseline and
reduced elapsed time by 51.0%. The full compaction scheduled 35
subcompactions, so the requested feature was active. Its compaction statistics
reported 3,237.8 MB/s read and 2,046.3 MB/s write, compared with 332.6 MB/s
read and 210.1 MB/s write in the historical 500 GB serial last-comp run.
This throughput contrast is suggestive, not a controlled speedup, because the
dataset sizes and interference differ.

The 113 GiB peak RSS is a material cost. Every key-range subcompaction creates
iterators over the wide L0 fan-in, so aggressive subcompaction multiplies
iterator and table-reader state even though it does not multiply the data
bytes read. The output also confirms that last-comp creates a single L1 run,
not the natural leveled layout.

Raw logs and the provisional summary are under:

```text
experiments/artifacts/log_loads/paper_lastcomp_subcomp_100gb_260823_0540_concurrent/
```

This run demonstrates that RocksDB can split this L0 full compaction and that
parallel execution can make last-comp faster than the concurrent baseline. It
does not isolate the causal speedup from subcompaction. A controlled claim
requires an idle-machine sweep with caps `1, 4, 8, 16, 32`, preferably
alternating order. Cap 48 remains diagnostic because it consumed excessive
memory. Select the smallest cap within 5% of the fastest safe last-compaction
point, while requiring the paired natural baseline to remain within 5% of its
best safe point and peak RSS to stay below 256 GiB. Validate that cap at
500 GB and apply it symmetrically to the final natural-baseline and
last-compaction pair. ADOC/ADOC-off and DiffKV/Titan independently qualify the
same cap within each artifact family; no-compaction reports the setting as
`N/A` because it schedules no compaction.

## 2026-08-24 idle 100 GB sweep

The isolated sweep used an immutable runner snapshot and alternated pair order
across caps `1, 4, 8, 16, 32`. Every run completed with zero swap activity,
reached its required completion boundary, reopened successfully, and returned
10,000/10,000 sampled reads. No-compaction scheduled zero subcompactions as
required.

| Cap | Baseline elapsed | Baseline scheduled sum | Last-comp elapsed | Final compaction | Last-comp ranges | Last-comp peak RSS |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 306 s | 0 | 313 s | 186.314 s | 0 | 3.95 GiB |
| 4 | 188 s | 120 | 207 s | 83.217 s | 3 | 10.39 GiB |
| 8 | 161 s | 249 | 203 s | 75.852 s | 6 | 20.01 GiB |
| 16 | 158 s | 476 | 177 s | 50.619 s | 12 | 39.31 GiB |
| 32 | 154 s | 899 | 165 s | 38.193 s | 24 | 76.19 GiB |

No-compaction completed its load and flush in 124 s with all files left in L0;
its nonzero pending-compaction estimate is intentional. The fastest baseline
and last-compaction points were both cap 32. The 5% thresholds are 161.7 s for
baseline and 173.25 s for last-compaction. Cap 16 satisfies the baseline
threshold but misses the last-compaction threshold (177 s), so the 100 GB rule
selects cap 32.

This sweep also changes the earlier interpretation of last-compaction speed.
Once subcompactions are enabled symmetrically, natural baseline loading remains
faster at every cap in this sweep: at cap 32 it takes 154 s versus 165 s for
last-compaction. Removing intermediate compactions alone therefore does not
guarantee lower end-to-end time because one very wide final L0 compaction still
reads and merges the entire run. No-compaction is fastest only because it omits
the work needed to produce a realistic searchable layout.

Last-compaction memory grows almost linearly with scheduled ranges: 20.01 GiB
at six ranges, 39.31 GiB at 12, and 76.19 GiB at 24. A 500 GB gate therefore
uses an active 240 GiB abort threshold below the 256 GiB hard qualification
limit. If cap 32 reaches the abort threshold, retain the diagnostic and repeat
with cap 16 and then cap 8 until a safe paired configuration is found.

Raw data:

```text
experiments/artifacts/log_loads/paper_subcomp_sweep_100gb_260824_idle_full/
experiments/results/paper_subcompaction_100gb_260824.tsv
```

## 2026-08-24 500 GB memory gate

The 100 GB-selected cap 32 was attempted first with a 240 GiB active-abort
threshold, below the 256 GiB hard gate. It reached 254,971,324 KB (243.2 GiB)
immediately after the final compaction began and was terminated with `SIGTERM`
before swap or OOM. Logs and the incomplete database are retained as a failed
memory qualification; no timing result from that run is usable.

The fallback cap 16 completed successfully:

| System | Cap | Elapsed | Fill | Final compaction | Scheduled sum/ranges | Peak RSS | Pending | Reopen reads |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 16 | 796 s | 782.162 s | included in settle | 2,467 | 3.34 GiB | 0 | 10,000/10,000 |
| last-comp | 16 | 982 s | 637.898 s | 343.511 s | 11 | 179.67 GiB | 0 | 10,000/10,000 |

Both completed runs had zero swap delta. Cap 16 is therefore the largest
qualified setting at 500 GB and replaces cap 32 for larger paired experiments.
Even after removing intermediate compactions, last-comp is 186 s (23.4%)
slower than the matched natural baseline and uses roughly 54x its peak RSS.
This reinforces the 100 GB result: a wide final merge remains expensive, while
natural compaction overlaps its work with ingestion and exploits many smaller
subcompactions.

Raw data:

```text
experiments/artifacts/log_loads/paper_subcomp_sweep_500gb_260824_gate500_sub32/
experiments/artifacts/log_loads/paper_subcomp_sweep_500gb_260824_gate500_sub16/
experiments/artifacts/log_loads/paper_subcomp_sweep_500gb_260824_gate500_baseline_sub16/
experiments/results/paper_subcompaction_500gb_260824.tsv
```

## 2026-08-25 clean RocksDB 500 GB baseline validation

The earlier qualification used `vcomp/db_bench` with virtual compaction
disabled. To isolate whether the natural-baseline speedup also occurs in an
unmodified engine, a matched `max_subcompactions=1` versus `16` pair was run
with clean RocksDB 11.1.0 at commit
`cae42cd895f3bdeaaed1e58c0d27e2755b6ad308`. Both runs used the same binary,
500 GiB logical input, 24 B keys, 1000 B values, vector memtables, one writer,
no compression, no WAL, direct compaction/flush I/O, 48 background jobs, and
the same `fillrandom,flush,compact0,waitforcompaction` completion boundary.

| Cap | Elapsed | Fill | Throughput | Scheduled subcompactions | Write stall | Average CPU | Device writes | DWA | Peak RSS |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 2,314 s | 2,289.146 s | 229,032 ops/s | 0 | 1,575.48 s | 614% | 5,847.40 GiB | 11.695 | 3.02 GiB |
| 16 | 1,107 s | 1,096.732 s | 478,045 ops/s | 2,386 | 397.22 s | 1,328% | 5,890.60 GiB | 11.781 | 3.33 GiB |

Cap 16 reduced end-to-end time by 52.2% (2.09x speedup) and cumulative
foreground write-stall time by 74.8%. Device writes increased by only 0.74%
and total CPU time by 3.4%, while average CPU utilization more than doubled.
The result therefore supports parallel execution of essentially the same
physical compaction work, rather than work elimination, as the cause. Both
runs completed with zero pending compaction bytes, zero benchmark-local and
system swap activity, and 10,000/10,000 sampled reopen reads.

This is one ordered pair (`cap 1` followed by `cap 16`), so it establishes a
strong qualification result rather than a paper-ready confidence interval.
Use alternating repetitions if reporting error bars or an exact 2.09x value.

Raw and promoted data:

```text
experiments/artifacts/log_loads/paper_subcomp_sweep_500gb_260825_cleanrocksdb_subcomp_500gb_pair1/
experiments/results/paper_clean_rocksdb_subcompaction_500gb_260825.tsv
```
