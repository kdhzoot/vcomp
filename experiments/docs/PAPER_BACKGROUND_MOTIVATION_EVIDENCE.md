# Paper Background Motivation Experiments

## Purpose

This document plans the measurements needed to resolve three evidence gaps in
`paper/tex/02_Background.tex`:

1. measure, rather than project, the baseline loading time of an 8 TB dataset
   with true 91 B KV pairs;
2. obtain a valid compaction-phase breakdown for true 91 B KV pairs and compare
   it with the 1 KB configuration; and
3. quantify scale-amplified compaction cost using host-visible device write
   amplification.

No value from these experiments may replace a `\TBC{}` or be presented as a
measurement until its run passes the validation and acceptance criteria below.
Historical results are sanity-check references only and must not be mixed with
the new series when their binary, commit, or configuration differs.

## Paper questions and intended claims

| ID | Question | Intended evidence |
| --- | --- | --- |
| E1 | How long does baseline RocksDB take to load 8 TB with 48 B keys and 43 B values? | Direct elapsed-time measurement, with the final tree fully settled |
| E2 | Why does true-91 B loading take longer than 1 KB loading at the same logical DB size? | Cumulative compaction time split into Merge, SST Build, I/O, and Other |
| E3 | Does host-visible device write amplification increase with dataset scale under a fixed configuration? | Device-write-amplification curve from 500 GB through 8 TB |

## Common experimental controls

### Hardware and isolation

- DB filesystem: `/work` on `/dev/md0`, an ext4 RAID0 array over NVMe SSDs.
- Raw logs: `experiments/artifacts/log_loads/`.
- DB outputs: a unique directory under `/work/vcomp/exp/` for every run.
- Run only one `db_bench` process at a time.
- Reserve `/dev/md0` for the experiment. Before every run, sample its written
  sectors for 60 seconds. Start only if there is no sustained unrelated writer;
  record the observed idle delta in the run metadata.
- Record `df -h /work`, `/proc/mdstat`, `lsblk`, CPU/memory information, kernel
  version, mount options, and relevant device information before the series.
- Require at least 15 TB free before starting E1. Recheck available space before
  every 4 TB or 8 TB point.
- Do not delete a completed DB until its logs, final `stats`/`levelstats`, command,
  and summary row have been validated. Raw logs are never deleted as cleanup.

### Software provenance

Before a pilot or full run, record for every repository used:

- absolute binary path;
- Git commit and `git status --short`;
- SHA-256 of the actual `db_bench` binary;
- compiler and build type;
- complete generated `load_cmd.sh`;
- runner commit and any uncommitted runner diff.

The current worktrees are dirty, so a Git commit alone is not sufficient
provenance. A full-scale run must either use a clean, named build or archive the
exact diff and binary hash with its artifacts.

### Shared RocksDB configuration

- workload: `fillrandom`, followed by `flush`, `compact0`,
  `waitforcompaction`, `stats`, and `levelstats`;
- one application write thread;
- random seed: `12345678`;
- WAL disabled;
- compression: `none`;
- memtable representation: `vector`;
- background jobs: `48`;
- Bloom filter: 10 bits;
- index compression disabled;
- direct reads and direct I/O for flush and compaction enabled;
- no simultaneous benchmark, trace generation, or DB cleanup on `/dev/md0`.

These settings must be identical across all points of a comparison. Any change
starts a new series rather than being appended to an existing summary.

### Repetition policy

- Pilot points: one complete pipeline run.
- 500 GB and 1 TB points: three independent runs when used for error bars.
- 2 TB and 4 TB points: one primary run; repeat once if the result departs from
  the established trend by more than 10% or if interference is suspected.
- 8 TB points: one complete primary run because each run is multi-day or
  write-intensive. A failed or interrupted run is not a measurement.
- Preserve individual values. Use median and min/max for points with three
  repetitions; show a single marker without an error bar for one-run points.

## E1: Direct 8 TB true-91 B baseline loading

### Dataset

- target logical size: 8,000 GiB, matching the runner's existing convention;
- key size: 48 B;
- value size: 43 B;
- KV size: 91 B total;
- key count: computed by the runner as
  `8000 * 1024^3 / (48 + 43)`;
- distribution: the deterministic `fillrandom` generator with seed 12345678.

The 48 B + 43 B configuration represents the average key and value sizes
reported for the ZippyDB workload. It is a fixed-size synthetic approximation,
not a reproduction of the full production size distribution.

### Runner and command

Use the clean baseline RocksDB binary through the existing scaling runner:

```bash
cd /home/smrc/virtual_compaction/vcomp/experiments
RUN_ID=paper_bg_8tb91_<timestamp> \
SIZES_GB="8000" \
SYSTEMS="baseline" \
KEY_SIZE=48 VALUE_SIZE=43 COMPRESSION_TYPE=none \
BG_JOBS=48 DISKSTAT_DEV=md0 \
EXP_DB_ROOT=/work/vcomp/exp/paper_bg_8tb91 \
scripts/load/run_exp_91b_scaling.sh
```

Before the 8 TB run, execute a 100 GB pilot with the same binary and options.
The pilot must exercise loading, final settling, summary extraction, device
write measurement, and validation of the resulting tree.

### Metrics

- wall-clock elapsed time;
- `fillrandom` benchmark time;
- operations per second;
- final DB size and per-level SST count/bytes;
- RocksDB ingest and compaction-written bytes;
- host-visible bytes written to `/dev/md0`;
- peak RSS;
- stalls and compaction errors from RocksDB statistics.

### Acceptance criteria

- `db_bench` exits with status 0;
- `waitforcompaction` completes and the final log reports no pending background
  compaction or flush;
- the executed key count and key/value sizes match the generated command;
- final `stats` and `levelstats` are present;
- start/end diskstats and elapsed-time files are present and parseable;
- no concurrent `db_bench` or material unrelated `/dev/md0` writer is observed;
- enough space remained throughout the run and no out-of-space recovery occurred.

If any criterion fails, retain the current projected wording and label the run
as failed or incomplete. Do not derive a measured 8 TB value by doubling the
4 TB result.

### Outputs

- raw: `artifacts/log_loads/exp_paper_bg_8tb91_<timestamp>/`;
- stable row: `results/paper_background_loading_scale.tsv`;
- figure input: the measured 8 TB point in
  `results/paper_background_loading_scale.tsv`;
- figure assets: `paper/figs/bg_loading_scale.pdf` and `.png` after the full
  scale series is validated.

## E2: True-91 B compaction breakdown

### Experimental comparison

Use a fixed 500 GB logical dataset and no compression for both configurations:

| Configuration | Key | Value | Total KV |
| --- | ---: | ---: | ---: |
| 1 KB | 24 B | 1000 B | 1024 B |
| true 91 B | 48 B | 43 B | 91 B |

Both configurations must be run with the same `vcomp-prof` commit, binary,
profiler implementation, RocksDB options, and machine state. Do not combine the
historical incorrect `24 B + 91 B` run with this result.

### Pilot and full run

1. Run 10 GB pilots for both configurations and verify that
   `VCOMP_PERF_COMPACTION_BREAKDOWN` records are emitted and parsed.
2. Validate that, for each completed compaction, the configured phase categories
   have the intended interpretation and do not produce impossible totals.
3. Run three independent 500 GB repetitions per configuration, alternating the
   order: `1KB, 91B, 91B, 1KB, 1KB, 91B`.
4. Use unique `RUN_ID`, log, and DB directories for every repetition.

The existing matrix runner accepts one key size per invocation. Until a shared
paper wrapper is added, invoke it separately for the two configurations:

```bash
cd /home/smrc/virtual_compaction/vcomp/experiments

CASES_SPEC='kv1024_nocompress 1000 none' \
KEY_SIZE=24 TARGET_DB_GB=500 BG_JOBS=48 DISKSTAT_DEV=md0 \
DB_ROOT=/work/vcomp/exp/paper_bg_breakdown \
RUN_ID=paper_bg_breakdown_1kb_r<rep> \
scripts/load/run_motivation_kv_compression_matrix.sh

CASES_SPEC='kv91_nocompress 43 none' \
KEY_SIZE=48 TARGET_DB_GB=500 BG_JOBS=48 DISKSTAT_DEV=md0 \
DB_ROOT=/work/vcomp/exp/paper_bg_breakdown \
RUN_ID=paper_bg_breakdown_91b_r<rep> \
scripts/load/run_motivation_kv_compression_matrix.sh
```

### Aggregation

The paper claim concerns cumulative loading cost, so aggregate **all successful
compactions** in `compaction_breakdown_all.tsv`. Do not use only the largest
representative compaction.

For each run, sum the following non-overlapping categories as validated by the
pilot:

- Merge;
- SST Build;
- I/O = Read + Write;
- Other = tracked compaction time not assigned above.

Compression and decompression must be zero or negligible because compression
is disabled. Report each category in seconds and as a fraction of the summed
tracked compaction time. Then aggregate the three run-level results using the
median and retain min/max variation.

Because subcompactions may execute concurrently, summed component timers must
be described as cumulative tracked compaction time, not end-to-end wall time.

### Acceptance and failure criteria

- every successful compaction has exactly one deduplicated breakdown record;
- all expected fields parse as non-negative integers;
- no failed compaction is silently included;
- the category definition is identical for 1 KB and 91 B;
- the number of parsed jobs agrees with the raw log after deduplication;
- the profiler does not change key/value sizes or loading options;
- missing records, overlapping category accounting, or timer overflow blocks
  the figure until the instrumentation is corrected and rerun.

### Outputs

- raw: `artifacts/log_loads/motivation_kv_compression_paper_bg_breakdown_*/`;
- machine-readable run summary:
  `results/paper_background_compaction_breakdown.tsv`;
- aggregation script:
  `analysis/summarize_paper_background_breakdown.py`;
- figure: `paper/figs/bg_loading_breakdown.pdf` and `.png`;
- intended plot: two stacked bars, 1 KB and true 91 B, with cumulative seconds
  on the y-axis and Merge, SST Build, I/O, and Other as the stack categories.

## E3: Device write amplification versus dataset scale

### Metric definition

For this paper, device write amplification is defined as:

```text
device write amplification =
    host-visible bytes written to /dev/md0 during the complete load
    ---------------------------------------------------------------
    application logical key bytes + value bytes submitted by fillrandom
```

The numerator is calculated from the change in `/proc/diskstats` field 10 for
`md0`, whose sector count is converted using 512-byte sectors. The denominator
is `num_keys * (key_size + value_size)`. RocksDB's reported ingest bytes are
stored as a cross-check but are not silently substituted for the denominator.

This metric includes filesystem/RAID writes visible at the md block device. It
does **not** measure SSD-internal NAND write amplification and must not be
described as such.

Immediately before the start snapshot and immediately before the end snapshot,
run `sync`. The end snapshot must be taken only after `waitforcompaction` and
the final sync complete. The current runners already record diskstats, but the
end-sync behavior must be verified or added before the pilot.

### Primary scale series

Hold all configuration variables fixed and vary only logical DB size:

- KV: 24 B key + 1000 B value;
- sizes: 500 GB, 1 TB, 2 TB, 4 TB, and 8 TB;
- compression: none;
- baseline RocksDB, one write thread, vector memtable, 48 background jobs;
- repetitions: three at 500 GB and 1 TB; one at 2, 4, and 8 TB, with a repeat
  triggered by the common repetition policy.

Use a new clean series rather than the historical scaling artifact, which used
a skip-list memtable despite earlier documentation stating `vector`.

Run the repeated small points as three independent series:

```bash
cd /home/smrc/virtual_compaction/vcomp/experiments
for rep in 1 2 3; do
  RUN_ID=paper_bg_dwa_1kb_small_r${rep}_<timestamp> \
  SIZES_GB="500 1000" \
  BG_JOBS=48 DISKSTAT_DEV=md0 \
  DB_ROOT=/work/vcomp/exp/paper_bg_dwa_1kb \
  scripts/load/run_motivation_load_scaling.sh
done
```

Then run the large points once each in one sequential series:

```bash
cd /home/smrc/virtual_compaction/vcomp/experiments
RUN_ID=paper_bg_dwa_1kb_large_<timestamp> \
SIZES_GB="2000 4000 8000" \
BG_JOBS=48 DISKSTAT_DEV=md0 \
DB_ROOT=/work/vcomp/exp/paper_bg_dwa_1kb \
scripts/load/run_motivation_load_scaling.sh
```

E1 supplies an additional true-91 B 8 TB point but is not part of the primary
fixed-1 KB DWA trend. It may be shown as a separately labeled sensitivity point;
it must not be connected to the 1 KB line.

### Controls and validation

- record diskstats for `md0` and all member NVMe devices for diagnosis;
- reject a run if the md array resets, wraps, degrades, or changes membership;
- record an idle-write delta immediately before the run;
- verify numerator, denominator, and unit conversion in a 10 GB pilot;
- require `total_write_bytes >= logical_input_bytes` for a conventional load;
- compare the extracted total with RocksDB ingest and compaction bytes as a
  consistency check, while allowing filesystem and RAID overhead;
- do not subtract estimated background writes. If isolation fails, rerun.

### Analysis and figure

Create `analysis/summarize_paper_background_dwa.py` to produce:

```text
size_gb,rep,total_write_bytes,logical_input_bytes,device_write_amp,
elapsed_sec,status,run_dir
```

Promote the validated summary to
`results/paper_background_device_write_amp.tsv`. Plot DB size on the x-axis
and device write amplification on the y-axis. Use a log2-spaced x-axis with
explicit labels `500 GB`, `1 TB`, `2 TB`, `4 TB`, and `8 TB`; do not imply a
continuous functional law from the connecting line. Error bars show min/max
only where three repetitions exist.

## Execution order

1. Freeze and record the exact baseline and profiler builds.
2. Add/verify end-of-run `sync`, idle-write recording, provenance capture, and
   analysis scripts.
3. Run 10 GB pilots for E2 and E3.
4. Run the 100 GB E1 pilot.
5. Run the E2 500 GB alternating matrix and validate the breakdown figure.
6. Run E3 from smallest to largest, validating every summary row before moving
   to the next scale.
7. Run E1's 8 TB true-91 B load only after the runner has already completed the
   smaller true-91 B controls and at least 15 TB remains free.
8. Promote stable TSV/PDF/PNG outputs and update this document with exact
   commits, commands, exclusions, and final interpretation.

## Paper update gate

After all required runs pass:

- replace the projected 8 TB·91 B wording with a measured value and remove the
  projection styling from that point;
- activate the true-91 B breakdown claim only from the new cumulative summary;
- replace the `XX` scale values with the measured device write amplification
  values and explicitly call the metric host-visible device write amplification;
- update the figure caption with dataset size, KV size, repetitions, metric
  definition, and whether each point is measured;
- keep failed and excluded runs documented in this file and in the raw artifact
  tree.

## E2 execution status (2026-08-31)

The 10 GiB pilot completed for both uncompressed configurations using the same
profiler binary:

- binary: `/home/smrc/virtual_compaction/vcomp-prof/db_bench`;
- binary SHA-256:
  `e12faf195eee0ca5b1346776936cbc159bab6861daaa6380938223ef989f9bac`;
- `vcomp-prof` commit: `33f5de2110084a37adc07fdc9928fd15db87ee6f` with the
  dirty profiler source state recorded for the full series;
- 1 KB pilot: 24 B key + 1000 B value, 10 GiB, 34 seconds, 93 breakdown jobs;
- true-91 B pilot: 48 B key + 43 B value, 10 GiB, 130 seconds, 144 breakdown
  jobs.

Both pilots exited successfully, completed `waitforcompaction`, ended with L0
empty and zero estimated pending compaction bytes, and reported zero background
errors. Every deduplicated breakdown row had non-negative fields, a component
sum equal to `total_tracked_us`, and zero compression/decompression time.

Pilot artifacts:

- `artifacts/log_loads/motivation_kv_compression_paper_bg_breakdown_pilot_260831_033439_1kb/`;
- `artifacts/log_loads/motivation_kv_compression_paper_bg_breakdown_pilot_260831_033439_91b/`.

The 500 GiB alternating series uses
`scripts/load/run_paper_background_breakdown.sh` and started as
`paper_bg_breakdown_260831_034107`. The planned order is
`1KB, 91B, 91B, 1KB, 1KB, 91B`. It preserves each completed DB and captures the
binary hash, source commits/status/diff, hardware state, exact command, idle
device-write delta, raw logs, and per-run breakdown records. An earlier start,
`paper_bg_breakdown_260831_034027`, stopped before any `db_bench` invocation
because the host `lsblk` lacked the `MOUNTPOINTS` column; its failure note is
retained in the artifact directory.

### 2026-08-31 single-run paper result

Per the author's decision, the full series was stopped after one completed run
per configuration. The usable runs are `o1_r1_1kb` and `o2_r1_91b`; the
already-started `o3_r2_91b` run was interrupted and excluded, and the remaining
runs were not started. This result therefore has no run-to-run uncertainty.

All 22,020 1 KB jobs and 24,624 true-91 B jobs passed the breakdown validation:
the non-overlapping component sum equals `total_tracked_us` for every job and
compression/decompression time is zero. The cumulative tracked times are:

| Category | 1 KB (hour) | true 91 B (hour) |
| --- | ---: | ---: |
| SST Build | 0.7925 | 4.2856 |
| Merge | 1.8742 | 4.4046 |
| I/O | 0.7530 | 0.7689 |
| Other | 0.1521 | 1.4955 |
| **Total** | **3.5718** | **10.9546** |

The validated summary is
`experiments/results/paper_background_compaction_breakdown.tsv`. The paper
assets are generated with `experiments/analysis/plot_figure2_redraw.py`, and the
auditable figure bundle is
`experiments/artifacts/figure2_true91_single_run_260831/`.
