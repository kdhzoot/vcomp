# Common baseline for paper Chapters 2 and 3

**Current validated campaign:** `paper_ch23_common_260907_f2_completion1`. Numerical entries and source-DB mappings below from earlier runs are historical. Use [PAPER_CHAPTER23_COMMON_RESULTS.md](PAPER_CHAPTER23_COMMON_RESULTS.md) and the current promoted TSVs for Figures 2(b), 4 and 5. All 24 included Figure 5 cells were rerun with the common clean release readrandom driver; historical YCSB values are not mixed. 

**Historical campaign, superseded on 2026-09-07:** `paper_ch23_common_260905_approved_run3`. Numerical entries and source-DB mappings below from earlier runs are historical. Use [PAPER_CHAPTER23_COMMON_RESULTS.md](PAPER_CHAPTER23_COMMON_RESULTS.md) and the current promoted TSVs for Figures 2(b), 4 and 5. All 20 included Figure 5 cells were rerun with the common clean release readrandom driver; historical YCSB values are not mixed. F2Load is deferred and omitted from the current common comparison.

**Detailed approval proposal, 2026-09-05:**
[PAPER_CHAPTER23_OVERNIGHT_PLAN.md](PAPER_CHAPTER23_OVERNIGHT_PLAN.md)
specifies the proposed first-night cases, settings, reuse, time/space budget
and later packages. Source inspection found that native `readrandom` can run
the required uniform point reads on the exact Figure 4 release executable.
The new proposal therefore replaces the reader-build exception below with
that driver and a full 24-cell read rerun. It also proposes a fresh shared
1,000-GiB baseline and reproducible F2Load load. These are proposals awaiting
approval, not completed experiments; the earlier YCSB-driver policy below is
retained as planning history.

## Decision and scope

On 2026-09-05 the author requested alignment of the Chapter 2/3 baseline
experiments with Figure 4, including their connection to the subsequent read
experiment. Use the **Figure 4 clean RocksDB release baseline with 16 write
buffers** as the reference. The same 1,000-GiB, 1-KB baseline measurement must
be used by Figure 2(a), Figure 2(b), and Figure 4; do not independently choose
a faster or slower run for each panel. Figure 5 must identify the actual
source DB built by the corresponding Figure 4 loading run.

This document is the alignment plan, not a report of new measurements. The
existing plotted data remain historical until each replacement is validated.
No multi-TB experiment was started as part of the configuration/layout review.

The recommended build policy is to retain the exact Figure 4 release binary
for **absolute loading times and SST-byte WAF**, and port instrumentation onto
the same source revision for the **separate Figure 2(c) job breakdown**. The
instrumented executable is a declared exception, not the same binary. An
alternative, if literal binary identity across all measurements is required,
is a new common release executable containing both instrumentation and the
read workload driver; that requires new baseline load/read measurements and
requalification of the instrumented code. The author was asked to choose
between these two policies; do not interpret silence as approval of the
literal-identity alternative or launch dependent experiments on that basis.

## Canonical loading configuration

| Item | Required value |
| --- | --- |
| Source | `rocksdb-f455-release/`, clean RocksDB 11.1.0 |
| Commit | `f455ab7bd6a8c67f00d48075bb310f131d9fae5f` |
| Executable | `/home/smrc/virtual_compaction/rocksdb-f455-release/db_bench` |
| SHA-256 | `8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b` |
| Build | release; assertions disabled; `INFO_LEVEL` logging |
| Input size | logical bytes; 1,000 GiB = 1,073,741,824,000 bytes |
| 1-KB KV | 24-B key + 1,000-B value; 1,048,576,000 operations at 1,000 GiB |
| 91-B KV | 48-B key + 43-B value; 11,799,360,703 operations at 1,000 GiB |
| Generator | `fillrandom`, random with replacement, seed 12345678 |
| Foreground | one writer, batch size one |
| Memtable | Vector; concurrent-memtable-write option true |
| Write buffers | 64 MiB each; maximum 16; minimum merge count 1 |
| Target SST | 64 MiB; block-based format version 7 |
| Compaction | leveled, `kMinOverlappingRatio`, seven configured levels |
| Level sizing | static (`level_compaction_dynamic_level_bytes=false`); base 256 MiB, multiplier 10 |
| Background | 48 jobs; one subcompaction |
| L0 thresholds | compaction/slowdown/stop = 4/20/36 |
| Pending bytes | soft/hard limits = 64/128 GiB |
| WAL/compression | disabled/none |
| I/O | direct reads and direct flush/compaction I/O |
| Filter/index | 10 Bloom bits; index compression disabled |
| Completion | `fillrandom,flush,compact0,waitforcompaction,stats,levelstats` |
| Reporting | statistics enabled; 60-s interval stats; 1-s throughput report |

The reference DB's `OPTIONS-000007` and `OPTIONS-206456` were checked on
2026-09-05. Both record the settings above, including 16 buffers, format 7,
static level sizing, and the pending-byte limits. The release checkout is
clean and its executable hash still matches the recorded Figure 4 hash.
The 91-B operation count rounds down by 27 logical bytes at 1,000 GiB.
Use `GiB` explicitly in future axes, commands, and summaries; never label
1,000 GiB as exactly 1 TiB or 1 decimal TB.

## Data reuse and rerun matrix

| Panel/measurement | Required action |
| --- | --- |
| Figure 2(a), 1-KB scaling | Replace the historical assertion-enabled SkipList series with the canonical release/Vector/16-buffer series at 500, 1,000, 2,000, 4,000, 8,000 GiB. |
| Figure 2(a), 91-B scaling | Replace the historical assertion-enabled series with the same canonical binary/options, changing only KV size and the operation count. |
| Shared 1,000-GiB, 1-KB conventional point | Reuse the validated Figure 4 3,320-s run initially. If it is rerun, update all three panels and the read-source mapping together. |
| Figure 2(b), conventional | Reuse the corresponding 1,000-GiB scaling runs. No independent conventional run is needed for this panel. |
| Figure 2(b), flush-only | Measure with the same canonical release executable and 16 buffers. Disable compaction and lift L0/pending-byte stall limits as the explicit treatment. |
| Figure 2(c), breakdown | Port/qualify timers on `f455ab7b`; use identical workload/options. Report cumulative job time, not loading wall time or CPU time. Quantify instrumentation overhead in paired pilots. |
| Figure 4 Baseline/BlobDB | Existing runs use the same canonical release executable. Preserve them unless the common executable itself changes. BlobDB's GC-off/value-separation settings remain explicit treatment differences. |
| Figure 4 Flush-only/Last-comp | Existing results use a vcomp executable. For stronger comparability, regenerate both with the canonical clean executable, deriving Last-comp from the corresponding Flush-only checkpoint. |
| Figure 4 Fillseq/Fillseq+OW | Existing results use clean commit `cae42cd8`, not `f455ab7b`. Requalify these modes on the canonical executable before replacing measurements. |
| Figure 4 ADOC/F2Load | Method-specific implementations cannot be the same executable as clean RocksDB. Preserve source/hash, release mode, and common exposed options; disclose algorithm-specific exceptions. |
| Figure 5 reads | Reuse existing read results only for unchanged source DBs. Read each replaced Figure 4 state again under all four cache configurations. If the read executable changes, repeat the entire 24-run matrix. |

The common build policy must not turn method-specific source revisions into
an unqualified claim that every Figure 4 bar uses an identical engine.

## Load/read identity

The Figure 4 Baseline is the validated 3,320-s run under:

```text
experiments/artifacts/log_loads/paper_clean_vector_wb16_1000gib_260903_run1/
/work/vcomp/exp/paper_clean_vector_wb16_1000gib_260903_run1/baseline_bg48
```

The four baseline rows in
`experiments/results/paper_figure4_uniform_read_cache_5m_single.tsv` already
point to that exact source DB. Preserve it; run readers on staged copies
with immutable SST hard links and copied mutable metadata.

The existing read matrix uses one common `vcomp-prof` 10.10.1 executable
(SHA-256 `4a807ef1113420b3029812b497f1bb5c7fb1f11b3f79a9838a0045da84709321`)
for all six states. This is **read-phase consistency**, not load/read binary
identity: the clean reference binary lacks `workloadc` and its YCSB flags.
Retaining these read measurements requires stating that distinction. Literal
load/read binary unification needs the workload driver/counters ported to the
common 11.1.0 release source and a full read rerun.

Read controls remain uniform YCSB-C, 48 threads, 300 seconds, seed 87654321,
direct reads, and no automatic compaction. Use the four combinations of
cached/pinned metadata and a 1-byte/50-GiB LRU cache. Pinned metadata consumes
additional memory; record RSS and do not claim equal total-memory budgets.

## Execution and qualification sequence

1. Preserve old results, source DBs, commands, OPTIONS, logs, and binary hashes.
   Create a fresh run directory; never overwrite a plotted run or change an
   existing DB to fit the proposed settings.
2. Select the build policy above. Reuse the canonical release executable;
   create isolated source/build directories for instrumentation or driver
   ports rather than modifying the baseline checkout or profiler worktree.
3. Implement the shared configuration in a runner using `experiments/lib/`
   and the existing load/read helpers. The old Figure 2 four-cell runner is
   fixed to the 10.10.1 profiler and two buffers: it is not the new runner.
4. Dry-run all cases. Validate executable hash, effective OPTIONS, record
   count, format version, completion commands, and output paths. Explicitly
   check the absence of assertion/disabled-optimization warnings.
5. Run 1-GiB functional pilots for both KV sizes and conventional/flush-only
   modes. Check settling, SST-byte counters, no unintended compaction or L0
   stalls in flush-only, and reopen/read validation. Do not include pilot
   results in paper figures.
6. For instrumentation, run paired release/instrumented 100-GiB pilots in
   alternating order, ideally three pairs per KV size. Validate timer sums,
   counter agreement, final state, and overhead before any full breakdown.
   If instrumentation changes layout or material loading behavior, investigate
   before promoting the breakdown.
7. Complete 1,000-GiB conventional/flush-only cells and the paired source-DB
   read workloads. Reuse the shared conventional runs across figures.
8. Extend size scaling serially. Retain the current single-run convention
   unless repetitions are explicitly expanded; label each cell single-run
   and do not invent uncertainty estimates. Log time, CPU, I/O, stalls,
   peak RSS, final live bytes/SST counts/level occupancy, and all failures.
9. Promote machine-readable summaries only after validation, then regenerate
   the plots and update claims together. Compare WAF using total SST writes
   divided by logical input bytes; report device WAF separately.

Use fresh paths under `experiments/artifacts/log_loads/paper_ch23_common_*`
and `/work/vcomp/exp/paper_ch23_common_*`. Put read results under
`experiments/artifacts/log_runs/paper_ch23_common_*`. Promote validated
summaries under `experiments/results/paper_ch23_common_*`.

The existing 1-KB reference runner is
`scripts/artifact_baselines/run_clean_vector_baseline_wb16_1tb.sh`; its
`raw/load_cmd.sh` is the canonical executed command. That runner hardcodes
1-KB keys/values in validation and must be generalized before the 91-B series.

## Environment and failure gates

The reference host reports 48 CPUs of Intel Xeon Gold 6336Y. Use the same
host and `/work` NVMe RAID-0 (`/dev/md0`), preserve hardware/mount/topology,
and record compiler/linker flags and dependencies for new builds. Drop page
cache before cases, serialize storage benchmarks, check device idle state,
and capture swap activity and known interference. No warm-up load is mixed
into timing; readers follow the preserved page-cache policy.

On 2026-09-05 the read-only preflight found no running `db_bench` and about
19 TiB available on `/work`. This is not authorization or sufficient proof
of capacity for retaining two complete new scaling series plus temporary
compaction outputs. Plan retained DBs and peak temporary space before the
4,000/8,000-GiB cases. Do not remove old DBs to make room without explicit
authorization; preserve the Figure 4/5 source DBs in all cases.

Reject on executable/configuration drift, nonzero exit, corruption, OOM,
unexpected swap/interference, missing operations/counters, nonsettled
conventional state, failed reopen, or unexpected compaction in flush-only.
An explicit `compact0` race is acceptable only if the recorded recovery and
final successful drain establish completion; retain the original log.

## Figure layout revision (2026-09-05)

Figure 4 now uses vertical loading-time bars. Following the author's further
Figure 5 feedback, panel (a) uses a 0--5 left axis with a marked/clipped
Flush-only bar and Filter positive percentage diamonds on a separate right
axis. Panel (b) retains vertical membership bars. Panel (c) normalizes all 24
throughputs to the same-configuration Baseline, uses a linear 0--3 axis, and
marks 1 with a red line. See the current-display section in
`PAPER_FIGURE4_UNIFORM_READ_CACHE_MATRIX.md` for formulas and validation. The
data were not changed. The plotting script generates both native TikZ and
PDF/PNG previews; previous figures and prose are preserved as TeX comments.

Validation: all eight loading values and 24 read cells passed the plotting
input checks. Native figures were inspected in the compiled paper, and the
final `latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex` build
completed successfully (12 pages, resolved references/citations). The
remaining 1.22-pt overfull box is in the existing bibliography. Stable PDF/PNG
previews were refreshed under
`experiments/results/paper_figure4_uniform_read_cache_figures/`.

Reproduce with:

```bash
python3 experiments/analysis/plot_paper_figure4_uniform_read_cache.py \
  --loading-tsv experiments/results/paper_figure4_loading_time_1tb_single.tsv \
  --read-tsv experiments/results/paper_figure4_uniform_read_cache_5m_single.tsv \
  --output-dir ../paper/figs
```
