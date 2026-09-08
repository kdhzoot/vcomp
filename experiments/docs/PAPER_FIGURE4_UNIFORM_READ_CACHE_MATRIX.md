# Figure 4 DB Uniform-Read Cache Matrix

**Current validated campaign:** `paper_ch23_common_260907_f2_completion1`. Numerical entries and source-DB mappings below from earlier runs are historical. Use [PAPER_CHAPTER23_COMMON_RESULTS.md](PAPER_CHAPTER23_COMMON_RESULTS.md) and the current promoted TSVs for Figures 2(b), 4 and 5. All 24 included Figure 5 cells were rerun with the common clean release readrandom driver; historical YCSB values are not mixed. 

**Historical campaign, superseded on 2026-09-07:** `paper_ch23_common_260905_approved_run3`. Numerical entries and source-DB mappings below from earlier runs are historical. Use [PAPER_CHAPTER23_COMMON_RESULTS.md](PAPER_CHAPTER23_COMMON_RESULTS.md) and the current promoted TSVs for Figures 2(b), 4 and 5. All 20 included Figure 5 cells were rerun with the common clean release readrandom driver; historical YCSB values are not mixed. F2Load is deferred and omitted from the current common comparison.

## Current Figure 5 display (author feedback, 2026-09-05)

The input measurements are unchanged. Panel (a) now has a linear left axis
from 0 to 5 filter checks per lookup. The Flush-only bar is clipped, marked
with break strokes, and labeled with its actual value (10.3K). Diamonds use
the separate 0--100% right axis and show **Filter positive**:

```text
Filter positive (%) = 100 * positive filter checks / all filter checks
                    = 100 * bloom_full_positive / filter_probes
```

This includes false positives and is not the successful-lookup percentage in
panel (b). Baseline, Flush-only, Last-comp, Fillseq, Fillseq+OW, and F2Load are
18.2581%, 0.9729%, 63.5711%, 100%, 30.1824%, and 18.0691%, respectively.
Raw counter/TSV names retain `bloom` for traceability; the graph and active
paper prose use `Filter positive`.

Panel (c) shows each state's throughput divided by Baseline throughput in the
**same metadata/cache configuration**, on a linear 0--3 axis. All four
baseline bars equal 1, marked by a red horizontal line. Flush-only's four
ratios range from 0.000659 to 0.005610 and are annotated `<0.006` because their
bars are very short. Normalization does not remove the recorded differences
in source key membership or turn F2Load ratios into a controlled speedup.

All 24 ratios were checked against the existing `throughput_vs_baseline`
column. All six positive rates were checked against the raw aggregate counter
ratios. Previous captions, prose, and plotted TikZ versions are preserved as
TeX comments. The plotting command documented below reproduces this display.

## Measurement status

**Status:** completed and validated. The primary 20-run matrix finished on
2026-09-04 UTC, and the separately logged four-run conventional-Baseline
supplement finished on 2026-09-05 UTC. All 24 runs passed command, duration,
status, and required-counter validation.

## Question

Measure how the five selected 1,000-GiB Figure 4 alternative layouts and the
matching conventional Baseline affect point-read performance and logical
lookup work under a uniform YCSB-C workload, separating metadata placement
from the data-block-cache budget.

## Source databases

The immutable source directories are the conventional Baseline, Flush-only,
Last compaction, Fillseq, Fillseq plus 10% overwrite, and reproduced F2Load
entries recorded in `PAPER_FIGURE4_DB_INVENTORY.md`. Each run uses a staged
database: immutable SSTs are hard-linked while mutable RocksDB metadata is
copied. The source directories must not be opened directly by the workload.

All commands use the logical key space from the fixed Figure 4 configuration:
1,048,576,000 records, 24-byte keys, and 1,000-byte values.

## Binary

- Path: `/home/smrc/virtual_compaction/vcomp-prof/db_bench`
- Git commit: `33f5de211`
- SHA-256: `4a807ef1113420b3029812b497f1bb5c7fb1f11b3f79a9838a0045da84709321`

This single binary reads all five layouts. The current clean RocksDB
`db_bench` does not implement `workloadc` or
`--ycsb_requestdistribution`; the profiler binary supplies that workload
driver and the statistics used here. The exact command for every run is
preserved as `raw/run_cmd.sh`.

## Matrix

The nominal zero-cache cases use a one-byte LRU cache. A literal zero disables
the block cache and is incompatible with routing index/filter blocks through
it.

| ID | Index/filter placement | Block-cache capacity |
| --- | --- | ---: |
| A-cache-zero | block cache | 1 byte |
| B-cache-5pct | block cache | 50 GiB (53,687,091,200 bytes) |
| C-pinned-zero | pinned by table readers | 1 byte |
| D-pinned-5pct | pinned by table readers | 50 GiB (53,687,091,200 bytes) |

The requested primary matrix contains five database layouts times four cache
configurations: 20 runs per repetition. Runs are serial, in A/B/C/D
configuration order and inventory order within each configuration.

Analysis showed that the section's final-state-fidelity claim needs the
conventional Baseline under the same read configurations. A separately logged
supplement therefore adds Baseline times four cache configurations (four
runs), without altering or rerunning the primary 20 measurements. Together,
the figure input contains 24 measured runs.

## Workload and controls

- YCSB-C: 100% point reads
- Request distribution: uniform, passed explicitly
- Duration: 300 seconds
- Duration checked after every operation (`ops_between_duration_checks=1`)
- Threads: 48
- Repetitions: one initial full pass
- Seed: 87654321
- Read-only DB open; automatic compaction disabled by the preserved DB state
- `open_files=-1`, direct reads, no compression, 10 Bloom bits
- LRU block cache
- Linux page cache dropped before each measured run
- No other `db_bench` may run concurrently

Before the full matrix, run a 30-second end-to-end pilot using Flush-only and
A-cache-zero. The pilot is validation evidence and is excluded from the
20-run result matrix.

The first pilot (`260904_uniform_cache_pilot1`) exposed the benchmark's
default 1,000-operation duration-check granularity and was intentionally
interrupted after 337 seconds; its failed raw output is preserved. After
setting `ops_between_duration_checks=1`, the replacement pilot
(`260904_uniform_cache_pilot2`) completed successfully in 33 wall-clock
seconds and produced all requested cache counters. The full matrix run ID is
`260904_1115_uniform_cache_5m`; the conventional-Baseline supplement run ID is
`260905_baseline_uniform_cache_5m`.

## Measurements

Preserve throughput, average and percentile Get latency, filter/index/data
block-cache hits and misses, bytes inserted into each cache category,
RocksDB bytes read/written, device read/write counts and bytes, elapsed time,
and peak RSS. Report block activity per completed operation as well as totals.

Pinned index/filter blocks bypass the block cache, so their cache hit/miss
tickers are expected to be zero and must not be interpreted as zero logical
metadata accesses. Also, B and D have the same block-cache capacity but not
the same total memory budget: D additionally retains pinned metadata. Peak RSS
must accompany that comparison.

## Result and paper use

Panels (a) and (b) use the pinned-metadata, 50-GiB-cache configuration. The
logical Bloom counters were effectively invariant across the four cache
configurations; the largest relative spread in filter checks per operation was
0.242% (Flush-only).

| State | Filter checks/op | Bloom positives/op | Successful lookups |
| --- | ---: | ---: | ---: |
| Baseline | 3.619 | 0.661 | 63.214% |
| Flush-only | 10,253.244 | 99.757 | 63.314% |
| Last-comp | 1.000 | 0.636 | 63.214% |
| Fillseq | 1.000 | 1.000 | 100.000% |
| Fillseq+OW | 3.388 | 1.022 | 100.000% |
| F2Load | 3.571 | 0.645 | 61.761% |

Panel (c) reports absolute point-read throughput. `≈0` below is the one-byte
LRU cache, not a disabled cache.

| Metadata/cache | Baseline | Flush-only | Last-comp | Fillseq | Fillseq+OW | F2Load |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Cached, ≈0 | 59,153 | 39 | 156,517 | 120,833 | 68,606 | 76,548 |
| Pinned, ≈0 | 669,385 | 3,755 | 713,364 | 452,421 | 420,139 | 694,457 |
| Cached, 50 GiB | 752,969 | 1,579 | 910,711 | 569,202 | 538,646 | 832,889 |
| Pinned, 50 GiB | 813,716 | 3,883 | 949,618 | 589,071 | 571,893 | 906,821 |

The paper uses these results as the missing link between the final LSM state
and subsequent lookup cost:

- Flush-only makes loading fast but checks about 10.3K filters and still
  produces about 100 Bloom-positive SST candidates per lookup. Even with
  pinned metadata and a 50-GiB cache, it is 210x slower than Baseline.
- Last-comp's one-SST lookup path can outperform Baseline, but that is an
  artificially simplified state rather than evidence of final-state fidelity.
- Fillseq and Fillseq+OW have a 100% successful-lookup ratio because they
  materialize the complete key domain. They do not reproduce the random-load
  membership even when overwrites restore some cross-level overlap.
- F2Load closely follows Baseline's logical lookup shape, but its 61.761%
  successful-lookup ratio is 1.453 percentage points below Baseline. The
  current approximate synthetic-key materialization therefore does not
  preserve exact membership; F2Load-versus-Baseline throughput is not a strict
  apples-to-apples speed comparison.

This experiment directly measures point-read consequences only. It does not
measure subsequent-write or compaction behavior.

## Analysis and plotting commands

Run from the `vcomp` repository:

```bash
python3 experiments/analysis/summarize_paper_figure4_uniform_read_cache.py \
  --run-root experiments/artifacts/log_runs/paper_figure4_uniform_read_cache_260904_1115_uniform_cache_5m \
  --run-root experiments/artifacts/log_runs/paper_figure4_uniform_read_baseline_260905_5m \
  --output experiments/results/paper_figure4_uniform_read_cache_5m_single.tsv

python3 experiments/analysis/plot_paper_figure4_uniform_read_cache.py \
  --loading-tsv experiments/results/paper_figure4_loading_time_1tb_single.tsv \
  --read-tsv experiments/results/paper_figure4_uniform_read_cache_5m_single.tsv \
  --output-dir ../paper/figs
```

The promoted summary is
`experiments/results/paper_figure4_uniform_read_cache_5m_single.tsv`. Stable
plot previews are under
`experiments/results/paper_figure4_uniform_read_cache_figures/`. The active
publication assets are the native TikZ sources
`paper/figs/bg_alternative_*.tex`; generated PDF/PNG files are retained only
as diagnostic previews and are not referenced by the manuscript.

On 2026-09-05 the author requested vertical bars in paper Figures 4 and 5.
The plotting command above now generates the active TikZ files as well as
PDF/PNG previews. Lookup work and membership use vertical bars, and the
throughput heatmap is replaced by grouped vertical bars with a log y-axis.
All measured values remain unchanged; previous TikZ sources are preserved as
comments. Chapter 2/3 baseline alignment and load/read build distinctions are
recorded in [PAPER_CHAPTER23_COMMON_BASELINE.md](PAPER_CHAPTER23_COMMON_BASELINE.md).

## Analysis limitations

- There is one run per state/configuration, with a fixed A/B/C/D order. Do not
  interpret small throughput differences as causal.
- Pinned configurations consume an additional 4.15--7.16 GiB for metadata;
  the 50-GiB cached and pinned rows do not have equal total memory.
- The source DBs have different physical sizes, so 50 GiB is exactly 5% of the
  logical input but not of every physical DB.
- Last-comp's global block-cache filter/index/data tickers are zero despite
  nonzero perf-context and device reads. Those tickers are invalid for that
  state and are intentionally not plotted.
- `read_amp_bytes_per_op` in the raw Q3 summary is returned value bytes, not
  physical read amplification, and is intentionally not used.
- The primary matrix's recorded `unique_ratio=1.0` is incorrect for the
  random-with-replacement Flush-only, Last-comp, and F2Load sources. Raw input
  provenance is preserved unchanged; analysis uses measured true-positive
  counters instead.
- The loading-time F2Load bar uses a retained 102-second run, whereas the read
  panels use an independently reproduced, same-option DB that loaded in 103
  seconds.

## Failure and exclusion policy

A run fails if `db_bench` exits nonzero, the staged DB cannot reopen, Uniform
is absent from its recorded command, the duration terminates early because of
the operation limit, or required logs/statistics are missing. Preserve failed
raw outputs and stop the serial matrix rather than silently retrying or
excluding them. Record any concurrent machine activity or page-cache-drop
failure as a limitation.

## Evidence locations

- Matrix input: `experiments/artifacts/log_loads/paper_figure4_read_db_matrix.tsv`
- Pilot: `experiments/artifacts/log_runs/paper_figure4_uniform_read_cache_pilot_*`
- Full run root: `experiments/artifacts/log_runs/paper_figure4_uniform_read_cache_*`
- Baseline supplement matrix:
  `experiments/artifacts/log_loads/paper_figure4_read_baseline_matrix.tsv`
- Baseline supplement run root:
  `experiments/artifacts/log_runs/paper_figure4_uniform_read_baseline_*`
- Staging root: `/work/vcomp/exp/paper_figure4_uniform_read_tmp`
