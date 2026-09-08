# Paper Figure 2: Flush and Compaction Cost

**Current validated campaign:** `paper_ch23_common_260907_f2_completion1`. Numerical entries and source-DB mappings below from earlier runs are historical. Use [PAPER_CHAPTER23_COMMON_RESULTS.md](PAPER_CHAPTER23_COMMON_RESULTS.md) and the current promoted TSVs for Figures 2(b), 4 and 5. All 24 included Figure 5 cells were rerun with the common clean release readrandom driver; historical YCSB values are not mixed. 

**Historical campaign, superseded on 2026-09-07:** `paper_ch23_common_260905_approved_run3`. Numerical entries and source-DB mappings below from earlier runs are historical. Use [PAPER_CHAPTER23_COMMON_RESULTS.md](PAPER_CHAPTER23_COMMON_RESULTS.md) and the current promoted TSVs for Figures 2(b), 4 and 5. All 20 included Figure 5 cells were rerun with the common clean release readrandom driver; historical YCSB values are not mixed. F2Load is deferred and omitted from the current common comparison.

**Alignment review, 2026-09-05:** The author requested the Figure 4 release
baseline and 16-buffer settings as the common Chapter 2/3 reference. See
[PAPER_CHAPTER23_COMMON_BASELINE.md](PAPER_CHAPTER23_COMMON_BASELINE.md) for the
canonical executable, shared-data rule, instrumentation choice, and rerun
plan. The measurements and two-buffer profiler workflow below remain the
historical source of the currently plotted values; they are not the new
common configuration.

## Claim and figure logic

Figure 2 characterizes why conventional LSM-tree loading becomes a bottleneck:

1. panel (a) shows loading time as DB size increases;
2. panel (b) uses no-compaction loading as a counterfactual and compares both
   loading time and write amplification; and
3. panel (c) attributes cumulative flush and compaction work to their internal
   operations.

The three panels are independent TikZ-backed `subfigure`s sharing one row in a
two-column `figure*`. Panels (a) and (b) each use 25% of the text width, while
the horizontal stacks in panel (c) use 46%.

Panels (b) and (c) now show the validated 1,000 GiB four-cell measurements.
The earlier 100 GiB smoke remains documented below as a functional/scale gate
and must not be mixed with the final paper data.

## Four-load experiment plan

- **Status: completed and validated.** The accepted 1 KB Conventional cell is
  in `figure2_1tb_fourcell_260903_run1`; the accepted 91 B Flush-only, 91 B
  Conventional, and 1 KB Flush-only cells are in
  `figure2_1tb_fourcell_260903_resume1`. The first attempt at the 91 B
  Flush-only cell was interrupted with no completion record and is excluded.
  Its partial DB and logs remain preserved in the first series.
- Exactly four loads: one run for every cell in
  `{Conventional, No comp.} x {1 KB, 91 B}`. There are no repetitions in this
  plan, so the result must be labeled single-run and must not show error bars.
- Fixed target for panels (b) and (c): the existing Figure 2 `1 TB` point,
  represented in the experiment harness as `TARGET_DB_GB=1000`. Because the
  harness multiplies this value by `1024^3`, the exact logical target is
  1,000 GiB. This preserves comparability with panel (a)'s 1000 GB run; do not
  silently change it to `TARGET_DB_GB=1024`.
- KV configurations: 24 B key + 1000 B value (1 KB), and 48 B key + 43 B
  value (91 B).
- Operation counts:
  - 1 KB: 1,048,576,000 records, exactly 1,073,741,824,000 logical bytes;
  - 91 B: 11,799,360,703 records, 1,073,741,823,973 logical bytes (27 bytes
    below the target due to integer division).
- Key generator, seed, logical input bytes, write buffer size, target SST size,
  Bloom filter, compression, direct-I/O settings, and background-job settings
  must match between the two loading modes.
- Loading modes:
  - `Conventional`: normal flush and leveled compaction, settled with
    `waitforcompaction`.
  - `No comp.`: ingestion and flush with automatic compaction disabled; L0
    slowdown/stop triggers must be raised so the intended counterfactual is not
    confounded by an artificial L0 write stall.
- End the conventional run after compactions drain; end the no-compaction run
  after the final immutable memtable flush completes. Run `sync` before the end
  device-counter snapshot for both modes.
- Use the same profiling binary for all four cells. Common settings are one
  foreground thread, VectorRep, seed 12345678, WAL disabled, no compression,
  10 Bloom bits, index compression disabled, 48 background jobs, one
  subcompaction, a 64 MiB write buffer, two write buffers, and direct I/O for
  reads/flush/compaction.
- Preserve panel (a)'s completion rule: Conventional uses
  `fillrandom,flush,compact0,waitforcompaction,stats,levelstats`; No comp. uses
  `fillrandom,flush,stats,levelstats` with automatic compaction disabled and
  all L0/pending-byte stall limits lifted.

Use this balanced single-run order:

| Order | KV | Mode | Main use |
| ---: | --- | --- | --- |
| 1 | 1 KB | Conventional | panel (b), compaction bar in (c) |
| 2 | 91 B | No comp. | panel (b), flush bar in (c) |
| 3 | 91 B | Conventional | panel (b), compaction bar in (c) |
| 4 | 1 KB | No comp. | panel (b), flush bar in (c) |

The modes alternate and the KV order is mirrored so neither mode is always
early or late. Before each run, require no other `db_bench`, check device idle
writes, drop page cache, use a fresh DB directory, and preserve binary/source
provenance and the exact command. The current `load.sh` captures its end device
counter immediately after `db_bench`; before execution, its wrapper must place
`sync` before that end snapshot so device WAF is comparable.

The existing `run_paper_background_breakdown.sh` is not the four-load runner:
it defaults to 500 GB and schedules six conventional runs. Before execution,
prepare a dedicated wrapper around `scripts/load/load.sh` that follows the
four rows above. Its common invocation template is:

```bash
DB_BENCH=/home/smrc/virtual_compaction/vcomp-prof/db_bench \
MODE=<baseline-or-l0only> TARGET_DB_GB=1000 \
KEY_SIZE=<24-or-48> VALUE_SIZE=<1000-or-43> \
BG_JOBS=48 SUBCOMPACTIONS=1 COMPRESSION_TYPE=none \
MEMTABLE_REP=vector DB_ROOT=<fresh-db-root> LOG_DIR=<fresh-log-dir> \
  bash scripts/load/load.sh
```

Here `MODE=baseline` is Conventional and `MODE=l0only` is No comp. The wrapper
must preserve the four exact expanded commands, run them sequentially, add the
pre/end device snapshots with `sync`, and extract both flush and compaction
records from all `LOG*` files.

## Panel (b): end-to-end loading and standard WAF

Plot grouped bars for `(No comp., Conventional) x (1 KB, 91 B)`. Bar height is
end-to-end loading time. Annotate each bar with its write amplification.

Use the standard input-normalized definition consistently:

```text
loading WAF = flush SST bytes written + compaction SST bytes written
              ------------------------------------------------------
                    application logical input bytes
```

Here, application logical input bytes are
`num_insert_operations * (key_size + value_size)`. WAL is disabled, so WAL
writes are not part of this metric. The main figure should use RocksDB's
attributable flush and compaction byte counters. Host-visible `/dev/md0` writes
are a validation metric and must be labeled device WAF if reported; they must
not be silently substituted for RocksDB SST-write bytes.

Derive the numerator from successful profiler records:

```text
flush SST bytes       = sum(VCOMP_PERF_FLUSH_BREAKDOWN.output_bytes)
compaction SST bytes  = sum(VCOMP_PERF_COMPACTION_BREAKDOWN.bytes_written)
```

No comp. must have zero compaction records and therefore zero compaction SST
bytes. Cross-check both sums against RocksDB's final cumulative flush and
compaction statistics before accepting WAF.

Because the no-compaction state retains duplicate records in overlapping L0
files, do not normalize either mode by its own final directory size.

Panel (b) supports a counterfactual statement: enabling compaction increases
end-to-end loading time and WAF. It does not make the elapsed-time difference
an additive measurement of compaction wall time, because flush and compaction
overlap and contend for resources.

## Panel (c): cumulative phase breakdown

Use four horizontal stacked bars grouped by KV size:

```text
1 KB   Flush
       Compaction
91 B   Flush
       Compaction
```

Use one shared six-entry legend. A category absent from a phase has zero width:

| Category | Flush | Compaction | Meaning |
| --- | --- | --- | --- |
| Sort | yes | no | VectorRep's flush-time sort |
| Merge | no | yes | Sorted-stream merge, key processing, and deduplication |
| SST Build | yes | yes | Exclusive build residual after nested phase timers |
| Read | no | yes | Input-SST read time |
| Write | yes | yes | Output-SST write time |
| Other | yes | yes | Remaining non-overlapping tracked phase time |

Use the No comp. run for each main Flush bar; this isolates flush work from
concurrent compaction I/O. Use the corresponding Conventional run for each
Compaction bar. Also retain the Conventional run's flush breakdown as a
sensitivity check: if it differs materially from the No comp. flush breakdown,
report the difference in text rather than silently mixing the two meanings.
Keep compaction read and write separate; combining them would hide the
mechanism behind write amplification.

The timers are non-overlapping elapsed wall-clock intervals, not thread CPU
time. If timers are summed across concurrent jobs, label the x-axis
`Cumulative phase time`, not `Loading time breakdown`; the sum can exceed
end-to-end loading time because background jobs overlap.

The background FlushJob does not include foreground memtable insertion. If the
paper later claims that panel (c) completely decomposes the no-compaction
loading bar, add a separately instrumented `Memtable Insert` category. Until
then, describe panel (c) narrowly as the flush/compaction job breakdown.

## Validation gates

- All expected operations and bytes parse as non-negative values.
- The category sum equals the tracked phase total for every successful job.
- No failed or incomplete job is included.
- All four cells use the same profiler binary. With only four loads, this plan
  does not estimate profiler overhead or run-to-run variance; state both limits
  explicitly.
- Conventional runs finish with no pending flush or compaction.
- No-compaction runs finish with no pending flush, no unintended stall, and
  zero `VCOMP_PERF_COMPACTION_BREAKDOWN` records.
- Logical input bytes and executed operation counts agree with the command.
- The successful flush records' `output_bytes` sum and successful compaction
  records' `bytes_written` sum agree with the corresponding RocksDB cumulative
  byte statistics.
- The figure caption states dataset size, repetitions, WAF denominator, and
  that phase times are cumulative rather than end-to-end wall time.

## Flush profiler implementation

The profiling binary is built from `vcomp-prof` with `make.sh` and emits one
`VCOMP_PERF_FLUSH_BREAKDOWN` record per non-empty flush job. The record includes
the job ID, flush reason, memtable/input/output sizes, status, compression type,
and these non-overlapping timers:

```text
sort_us + sst_build_us + write_us + compress_us + other_us
    = total_tracked_us
```

`sort_us` measures the actual `VectorRep` sort. `sst_build_us` covers the
remaining `BuildTable` work after nested sort, compression, and write time are
removed. `write_us` measures instrumented SST `Append`/`PositionedAppend` and
`Sync`/`Fsync` calls; `Close` and `Truncate` remain in the SST Build residual.
`other_us` is the rest of `FlushJob::Run`. The complete placement rationale and
call-path audit are in `FLUSH_PROFILER_INSTRUMENTATION.md`.

Current profiler binary:

- path: `/home/smrc/virtual_compaction/vcomp-prof/db_bench`;
- SHA-256: `4a807ef1113420b3029812b497f1bb5c7fb1f11b3f79a9838a0045da84709321`;
- base commit: `33f5de2110084a37adc07fdc9928fd15db87ee6f`;
- source state: dirty profiling tree; every full run must preserve the tracked
  diff, untracked profiler header, status, binary hash, and exact command.

The matrix runner now writes case-level `raw/flush_breakdown.tsv` files and a
run-level `flush_breakdown_all.tsv`. Validate and summarize either RocksDB LOG
files or the generated TSV with:

```bash
python3 experiments/analysis/summarize_flush_breakdown.py \
  <LOG-or-flush-breakdown.tsv> --output <summary.tsv>
```

A 30,000-operation Snappy smoke test produced 31 flush and 8 compaction
records. Every successful job satisfied its corresponding component-sum
invariant; Sort, SST Build, Write, and Compress were all observed as non-zero.
This is a functional validation only, not paper data.

### 100 GiB four-cell smoke (2026-09-03)

All four cells in the planned design were exercised at `TARGET_DB_GB=100`
(100 GiB in the harness). They used the profiling binary/hash recorded above,
no compression, one foreground thread, VectorRep, two 64 MiB write buffers,
48 background jobs, one subcompaction, and direct I/O. The 1 KB Conventional
cell ran first; after an unrelated isolated 1,000 GiB run completed, the other
three cells ran sequentially in the planned order. No two `db_bench` processes
overlapped.

| KV | Mode | End-to-end | Flush jobs | Compaction jobs | Standard WAF | Peak RSS |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 KB | No comp. | 132 s | 1,624 | 0 | 1.0160 | 0.820 GiB |
| 1 KB | Conventional | 360 s | 1,624 | 2,599 | 7.1154 | 1.209 GiB |
| 91 B | No comp. | 1,019 s | 1,935 | 0 | 1.0834 | 2.216 GiB |
| 91 B | Conventional | 1,420 s | 1,935 | 2,938 | 8.7220 | 2.890 GiB |

At this scale Conventional was 2.73x slower than No comp. for 1 KB and 1.39x
slower for 91 B using end-to-end time. Its standard SST-write WAF was 7.00x
and 8.05x the corresponding No-comp WAF. These ratios are smoke observations,
not the final 1,000 GiB paper result.

The main panel-(c) source choices give the following phase distributions. The
No-comp cells supply Flush and the Conventional cells supply Compaction:

| KV / phase | Sort | Read | Merge | SST Build | Write | Other | Cumulative time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 KB Flush | 28.67% | 0 | 0 | 46.23% | 24.47% | 0.63% | 162.077 s |
| 1 KB Compaction | 0 | 11.78% | 36.97% | 28.09% | 19.49% | 3.67% | 1,669.323 s |
| 91 B Flush | 56.89% | 0 | 0 | 39.26% | 3.71% | 0.14% | 1,150.091 s |
| 91 B Compaction | 0 | 3.26% | 39.49% | 39.03% | 6.14% | 12.07% | 5,806.438 s |

Cumulative phase time exceeds elapsed loading time because background jobs
overlap. The Conventional flush sensitivity check was close but not identical:
1 KB Conventional flush totaled 185.102 s versus 162.077 s without compaction;
91 B Conventional flush totaled 1,202.046 s versus 1,150.091 s without
compaction.

Every flush and compaction row had `status=ok`, a unique job ID, and an exact
component-sum invariant. Compression/decompression time was zero. Both
Conventional cells ended with zero pending compaction bytes and zero L0 files;
both No-comp cells emitted exactly zero compaction records. The raised L0 and
pending-byte limits worked: both corresponding delay/stop counts were zero.
No-comp nevertheless incurred memtable-limit stops (13.6% cumulative stall
for 91 B and 22.6% for 1 KB), which is expected flush backpressure with two
write buffers rather than an artificial L0-limit stall. This observation is
additional evidence that flush throughput can bound no-compaction loading.

Both Conventional runs observed one initial explicit-`compact0` race with an
already-running automatic compaction. Each recovered, drained successfully,
and a second `compact0` found zero L0 files. Device WAF after `sync` was 7.5068
for 1 KB Conventional, 1.0166 for 1 KB No comp., 9.1967 for 91 B
Conventional, and 1.0840 for 91 B No comp.; these values remain validation
metrics rather than substitutes for the standard profiler-derived WAF.

The combined machine-readable results are
`experiments/artifacts/log_loads/figure2_100gb_fourcell_smoke_260903/four_cell_summary.tsv`
and `four_cell_phase_summary.tsv`. Per-run commands, resource measurements,
post-`sync` device snapshots for the final three cells, and extracted profiler
rows are preserved in their run directories. The first cell remains under
`figure2_100gb_1kb_conventional_smoke_260903/`. This smoke is not paper data
and does not replace any of the four planned 1,000 GiB cells.

## Placeholder and final outputs

- TeX/TikZ figure sources: `paper/figs/bg_loading_scale.tex`,
  `paper/figs/bg_loading_flush_only.tex`, and
  `paper/figs/bg_loading_phase_breakdown.tex`;
- panel (a) embeds the measured values from
  `experiments/paper_evidence/current/fig_bg_loading/displayed_values.tsv`;
- panels (b) and (c) render the final single-run 1,000 GiB values, and their
  TeX provenance comments identify the accepted source series;
- final machine-readable summaries:
  `experiments/results/paper_figure2_loading_waf.tsv` and
  `experiments/results/paper_figure2_phase_breakdown.tsv`;
- temporary 500 GiB compaction summary:
  `experiments/results/paper_figure2_compaction_breakdown_preliminary_500gib.tsv`;
- the 1 TB values, scales, provenance comments, and paper caption were updated
  on 2026-09-04; the figure remains native TeX/TikZ rather than a raster image.
