# VComp Measurements

**2026-09-07 current Chapter 2/3 results:** F2Load loading and all four reads completed and were reflected in Figures 4/5 and dependent prose. The combined comparison has eight validated load states and twenty-four reads, reusing the previous seven load controls and twenty reads unchanged. F2Load is 134.731 s including final physical completion (pending bytes zero). Use [PAPER_CHAPTER23_COMMON_RESULTS.md](PAPER_CHAPTER23_COMMON_RESULTS.md). Only the previously deferred size-scaling and instrumentation follow-ups remain outside this campaign.

**2026-09-06 common Chapter 2/3 update:** Seven load states and twenty reads completed and were reflected in Figures 2(b), 4 and 5 and their prose. Use [PAPER_CHAPTER23_COMMON_RESULTS.md](PAPER_CHAPTER23_COMMON_RESULTS.md) for the current shared baseline. Earlier paper values below are historical for these comparisons. F2Load recovery/reads and the scaling/breakdown follow-ups remain deferred.

This document is the measurement notebook for vcomp vs. baseline. The
[published result index](../results/README.md) identifies the latest validated
campaign and its Git-tracked data. Dated measurements below retain their
original configurations and caveats; do not combine them with newer controls
without checking comparability. Implementation history and the algorithm
specification live in [../../README.md](../../README.md).

Every measurement section labels:

- **When** it was collected
- **Which DBs / which build** were used
- **Caveats** that affect interpretation

If a section's caveat says "stale disk state" or "unfair", treat the
numbers as a sanity bound, not a clean comparison.

---

## Test environment

- **CPU**: 48-core Intel Xeon Gold 6336Y @ 2.40 GHz (HT off, 96 logical offline)
- **Storage**: RAID0 over 20+ NVMe SSDs, 108 TB ext4, mounted at `/work`
- **RocksDB config**: `bloom_bits=10`, `disable_wal=true`, direct I/O,
  `compression=none`
- **VComp config**: `plr_error_bound=8`, `memtable_flush_size=64 MB`
- **Key/Value**: `key_size=24`, `value_size=1000`
- **Build**: `vcomp/db_bench` produced by `make.sh` (gcc-11, static_lib)

---

## 1. Loading performance

### 1.1 250 GB — 30-run batch

`fillrandom,flush,compact0,waitforcompaction` for baseline,
`fillvirtual,flush,compact0,waitforcompaction` for vcomp. Loaded via
`load_batch.sh`.

| Mode | n | DB size | Mean | Stddev | Min | Max |
|------|---|---------|------|--------|-----|-----|
| baseline | 30 | 177 GB | **903.4 s** | 12.8 s | 883 s | 926 s |
| vcomp    | 30 | 177 GB | **18.0 s**  | 0.7 s  | 17 s  | 21 s  |

Speedup: **~50×**, vcomp loading is over an order of magnitude more
consistent (stddev/mean ≈ 4% vs 1.4%).

Batch directories on disk:
- baseline: `/work/vcomp/260410_0339_250gb_x30/`
- vcomp:    `/work/vcomp/260414_1710_250gb_x30/`
- summary CSVs in
  [vcomp/experiments/artifacts/log_batch/260410_0339_250gb_x30/](../artifacts/log_batch/260410_0339_250gb_x30/)
  and [vcomp/experiments/artifacts/log_batch/260414_1710_250gb_x30/](../artifacts/log_batch/260414_1710_250gb_x30/)

### 1.2 Single-load scaling: 250 GB → 1 TB → 5 TB → 10 TB

Single-shot fillrandom vs fillvirtual at four size points. Same RocksDB
config and KV size as §1.1. **No variance estimate** — single sample per
cell. Disk-write column = `ingest_bytes + compaction_write_bytes`
(baseline) or `final DB size` (vcomp Phase 2 writes once, no
ingest/compaction stats).

| Scale | Mode | DB size | Loading time | Disk writes | Time speedup | Write reduction |
|-------|------|---------|--------------|-------------|---------------|------------------|
| 250 GB | baseline | 176 GB  | 781 s    | 2.6 TB    | — | — |
| 250 GB | vcomp    | 177 GB  | 18 s     | 0.18 TB   | **43×** | **15×** |
| 1 TB   | baseline | 768 GB  | 3,891 s  | 13.5 TB   | — | — |
| 1 TB   | vcomp    | 766 GB  | 109 s    | 0.75 TB   | **36×** | **18×** |
| 5 TB   | baseline | 3.50 TB | 20,465 s (5.7 h) | 101.4 TB | — | — |
| 5 TB   | vcomp    | 3.47 TB | 1,862 s (31 m)   | 3.5 TB   | **11×** | **29×** |
| 10 TB  | baseline | 7.65 TB | 50,818 s (14.1 h) | 225.4 TB | — | — |
| 10 TB  | vcomp    | 7.65 TB | 8,469 s (2.4 h)   | 7.7 TB   | **6×**  | **29×** |

**Trend across scale**

- **Time speedup shrinks as scale grows** (43× → 36× → 11× → 6×).
  vcomp's Phase 2 (parallel materialization + SST write) becomes the
  dominant cost at large scale; per-byte cost is bounded by NVMe
  bandwidth × 48 worker threads, while baseline's per-byte cost is
  bounded by *write amplification* × NVMe bandwidth, which itself
  saturates earlier. Net result: gap closes but vcomp still wins.
- **Disk-write reduction grows then plateaus** (15× → 18× → 29× → 29×).
  vcomp writes each byte exactly once (W-Amp = 1.0×). Baseline's W-Amp
  rises with depth: 9.6× at 250 GB, 12.6× at 1 TB, 19× at 5 TB, 21× at
  10 TB. Reduction ratio plateaus once baseline's W-Amp grows
  proportionally to the new level depth.

The historical plot is not retained. Regenerate it with
[plot_load_comparison.py](../analysis/plot_load_comparison.py).

### 1.3 250 GB pre-KV-preservation vcomp breakdown

This is the original synthetic `fillvirtual` path before `vcomp-w/kv`.
It does **not** preserve the input key/value set. Phase 1 builds and
compacts only virtual metadata; Phase 2 materializes generated SSTs from
the virtual model. This is the cleanest measurement of the old
"shape-only" vcomp speed.

The primary result at 250 GB is **60.6x faster** than baseline
`fillrandom` (`763.40s / 12.59s`). The figure intentionally shows only
the vcomp-side breakdown; the baseline comparison is kept in the table.

| Component | Time |
|-----------|------|
| Key generation | 3.11 s |
| Sort | 2.72 s |
| PLR fit | 0.79 s |
| VSST registration | 1.62 s |
| BG wait | 0.10 s |
| Materialize | 3.47 s |
| VersionEdit | 0.78 s |
| **Total vcomp** | **12.59 s** |
| **Baseline fillrandom** | **763.40 s** |
| **Speedup** | **60.6x** |

The historical breakdown plot is not retained. Regenerate it with
[plot_nokv_vcomp_breakdown.py](../analysis/plot_nokv_vcomp_breakdown.py).

**Interpretation**

- At 250 GB, the old no-KV path spent 8.24 s in virtual metadata work
  and 4.25 s in synthetic SST materialization.
- The speedup comes from avoiding memtable insertion, flush write
  amplification, and real compaction write amplification.
- This number is a shape-only upper bound: it does not include preserving
  and replaying the original input key/value set.

Raw load logs:
- [artifacts/log_loads/baseline_260414_1305_250gb/](../artifacts/log_loads/baseline_260414_1305_250gb/)
- `artifacts/log_loads/vcomp_260414_1648_250gb/` (not retained)

### 1.4 Paper design alternatives: 1-TB uniform point reads

**When**: primary matrix 2026-09-04, conventional-Baseline supplement
2026-09-05. **Workload**: one 300-second uniform YCSB-C run per cell, 48
threads, direct reads, read-only open, automatic compaction disabled. The four
cells per DB cross cached/pinned filter-index metadata with a one-byte/50-GiB
LRU data cache. Pinned cells use additional memory outside the cache.

The central result is structural, not a read-speed ranking. Conventional
Baseline checks 3.62 SST filters per lookup. Flush-only checks 10,253 and is
178--1,517x slower than Baseline across the four cache settings; even the
pinned/50-GiB case is only 3,883 ops/s versus Baseline's 813,716. Last-comp
checks one filter and can run faster than Baseline because its single-level
state is artificially easy to search. Fillseq and Fillseq+OW have 100%
successful lookups, unlike the approximately 63.2% random-load states.

The shape-only F2Load DB checks 3.57 filters per lookup, close to Baseline, but
its successful-lookup ratio is 61.761% versus Baseline's 63.214%. Both use the
same nominal operation count and key domain; the gap comes from approximate
descriptor-based key-set reconstruction and is not sampling noise. Therefore
its throughput is contextual evidence, not a controlled layout-only
comparison.

Promoted data and full caveats:

- [paper_figure4_uniform_read_cache_5m_single.tsv](../results/paper_figure4_uniform_read_cache_5m_single.tsv)
- [PAPER_FIGURE4_UNIFORM_READ_CACHE_MATRIX.md](PAPER_FIGURE4_UNIFORM_READ_CACHE_MATRIX.md)

---

## 2. Tree shape — 30×30 coverage comparison

The single most informative measurement of structural fidelity.
30 baseline loads vs 30 vcomp loads, measured with the `coverage`
benchmark (per-level file count, total size, union-of-key-ranges as a
fraction of each level's overall span).

**When**: 2026-05-12. **Build**: vcomp with the L0→L1 size gate
(4 GB) + end-of-load L0 drain, plus all earlier fixes
(see [README.md §"2026-05-12"](../../README.md)).
**DBs**: baseline `260415_0635_250gb_x30/`, vcomp `260512_0555_250gb_x30/`.

| Level | Metric | baseline (n=30) | vcomp (n=30) | Δ |
|-------|--------|------------------|---------------|---|
| L1 | files       | 3.6 ± 0.9        | **3.9 ± 0.6** ✓  | +0.3 |
| L1 | cov mean    | 34.59%           | 19.63%           | −14.96 pp |
| L1 | cov std     | 28.96%           | 11.93%           | −17.03 pp |
| L1 | cov range   | 7.7 – 100.0%     | 8.6 – 63.9%      | inside |
| L2 | files       | **45.7 ± 3.1**   | **48.5 ± 2.6** ✓ | +2.8 |
| L2 | cov mean    | 65.81%           | 61.42%           | −4.39 pp |
| L2 | cov std     | 9.06%            | 2.58%            | −6.48 pp |
| L2 | cov range   | 44.40 – 80.88%   | 55.74 – 65.31%   | inside |
| L3 | files       | **439.7 ± 5.1**  | **427.6 ± 3.9** ✓ | −12.1 |
| L3 | cov mean    | **88.79%**       | **87.89%** ✓     | **−0.90 pp** |
| L3 | cov std     | 0.96%            | 0.62%            | −0.34 pp |
| L4 | files       | 2809.2 ± 11.8    | 2622.3 ± 11.4    | −186.9 |
| L4 | cov mean    | 100%             | 100% ✓           | 0 |

Box plot: [coverage_box_sg_final.png](../results/coverage/coverage_box_sg_final.png).

**Observations**
- **L2/L3 file counts match baseline within 3 %** — primary structural
  fidelity target met. (The 2026-04-15 snapshot had L2 cov mean +15.9 pp
  off and a much tighter variance than baseline; that gap is closed.)
- **L3 cov essentially matches** (88.79 vs 87.89 %, Δ 0.9 pp). L4 cov
  identical at 100 %.
- L1 and L2 cov means now sit in the **lower half** of baseline's
  range (L1: 20 vs 35; L2: 61 vs 66). Both are inside baseline's
  range and chasing them further has diminishing returns vs the
  file-count + L3/L4 match already achieved.
- L4 file count: vcomp 2622 vs baseline 2809 (−6.7 %). vcomp's L4
  files are individually larger, reflecting the simpler
  grandparent-split heuristic in `SplitIntoSSTs`. Not pursued further.

**Earlier snapshot (2026-04-15, pre-fix), kept here only as a
reference point for the gap that has since been closed**:

| Level | baseline (cov mean) | vcomp pre-fix (cov mean) | Δ pre-fix |
|---|---|---|---|
| L1 | 29.30 % | 65.17 % | +35.86 pp |
| L2 | 69.92 % | 85.82 % | +15.89 pp |
| L3 | 89.12 % | 92.53 % | +3.41 pp |
| L4 | 100 % | 100 % | 0 |

Full narrative for that gap and its resolution lives in
[README.md "2026-04-14" → "2026-05-12"](../../README.md).

---

## 3. Read performance

### 3.1 250 GB — 30×30 readrandom (1M reads, 1 thread, cache=0)

**When**: 2026-05-12 (vcomp), 2026-04-16 (baseline original); see §3.3
for the 2026-05-13 same-day fair comparison via deep-copy.
**DBs**: baseline `260415_0635_250gb_x30/baseline_run{1..30}` (loaded
2026-04-15), vcomp `260512_0555_250gb_x30/vcomp_run{1..30}` (loaded
2026-05-12 with the post-fix size-gate build — see
[README.md §"2026-05-12"](../../README.md)).
**Build**: vcomp-prof for both (`$VCOMP_PROF_DB_BENCH`).

⚠️ **Important**: the baseline DBs are ~1 month older on disk than the
vcomp DBs. §3.3 quantifies the resulting NVMe physical-state effect on
read latency (large) and shows how to separate it from genuine
tree-shape effects. The disk-state-sensitive numbers below are NOT a
fair structural comparison — they reflect the combined "fresh write
vs. aged write" + tree-shape effects.

**Plot**: [results/read/batch/readrandom_260512_30x30_comparison.png](../results/read/batch/readrandom_260512_30x30_comparison.png),
3-way [results/read/batch/readrandom_3way_30x30_comparison.png](../results/read/batch/readrandom_3way_30x30_comparison.png).

**Disk-state-sensitive metrics** (baseline 1 month aged on disk):

| Metric | baseline (n=30) | vcomp_new (n=30) | Δ% | CoV_b | CoV_v |
|---|---|---|---|---|---|
| elapsed_s | 493.91 ± 58.76 | 403.78 ± 3.10 | -18.2% | 11.9% | 0.8% |
| ops_per_s | 2,052 ± 233 | 2,476 ± 19 | +20.7% | 11.3% | 0.8% |
| get_p50 (µs) | 495.0 ± 61.1 | 410.1 ± 2.3 | -17.2% | 12.3% | 0.6% |
| get_p99 (µs) | 929.0 ± 118.9 | 769.4 ± 31.4 | -17.2% | 12.8% | 4.1% |
| sst_p50 (µs) | 113.5 ± 15.1 | 93.86 ± 0.10 | -17.3% | 13.3% | 0.1% |
| sst_p99 (µs) | 226.2 ± 22.7 | 167.4 ± 0.2 | -26.0% | 10.0% | 0.1% |
| sst_reads_per_get | 3.92 ± 0.26 | 3.77 ± 0.03 | -3.9% | 6.7% | 0.8% |
| bytes_per_sst_read | 105,569 ± 2,370 | 107,343 ± 866 | +1.7% | 2.2% | 0.8% |

**Structural metrics** (disk-state independent — trustworthy):

| Metric | baseline (n=30) | vcomp_new (n=30) | Δ% | CoV_b | CoV_v |
|---|---|---|---|---|---|
| filter_per_get | 2.6153 ± 0.2580 | 2.4617 ± 0.0301 | **-5.9%** | 9.9% | 1.2% |
| index_per_get | 0.6512 ± 0.0026 | 0.6507 ± 0.0005 | -0.1% | 0.4% | 0.1% |
| data_per_get | 0.6517 ± 0.0027 | 0.6511 ± 0.0006 | -0.1% | 0.4% | 0.1% |
| bloom_fpr (%) | 2.90 ± 0.38 | 2.69 ± 0.07 | -7.2% | 13.2% | 2.4% |
| bloom_useful (M) | 1.96 ± 0.26 | 1.81 ± 0.03 | -7.8% | 13.0% | 1.6% |
| found / 1M | 632,270 ± 0 | 633,210 ± 416 | +0.1% | 0.0% | 0.1% |

**Per-level hit** (structural):

| Metric | baseline (n=30) | vcomp_new (n=30) | Δ% |
|---|---|---|---|
| l1_hit | 884 ± 94 | 874 ± 77 | -1.1% |
| l2_hit | 9,737 ± 107 | 9,910 ± 132 | +1.8% |
| l3_hit | 97,622 ± 259 | 98,706 ± 303 | +1.1% |
| l4_hit | 524,027 ± 276 | 523,720 ± 524 | -0.1% |

**Structural observations**

- `filter_per_get -5.9%` (vs old vcomp's +17%): tree-shape parity goal
  achieved. vcomp_new now actually does *fewer* filter block reads per
  get than baseline. Maps to the L1/L2/L3 coverage parity in §2.
- `sst_reads_per_get -3.9%`: direct consequence of slightly tighter
  L1-L3 distribution.
- `bloom_fpr -7.2%`, `bloom_useful -7.8%`: also slightly better than
  baseline. (vcomp_old's +22% gap was driven by its over-compacted
  L1-L2; closed when L0→L1 picks match baseline's input size.)
- `data_per_get` and `index_per_get` near-identical — same data volume
  per get.
- Per-level hit distribution within 2%, L4 within 0.1%.

**Stacked block-reads-per-get** comparison (baseline / vcomp_old /
vcomp_new): [results/read/block_reads_stacked.png](../results/read/block_reads_stacked.png).
vcomp_old's +13.4% total block-reads regression is fully reversed in
vcomp_new (-3.8% total) — entirely from the filter row.

**Disk-state-sensitive readings** — see §3.3 for full decomposition.
Short version: of the -18.2% elapsed gap, ~13% is NVMe fresh-write
effect (vcomp DBs are 1 month younger on disk than baseline), ~5% is
the genuine sst_reads_per_get -3.9% tree effect. The sst_p50 difference
(94 vs 113 µs) is NOT a vcomp tree-shape benefit — same baseline DBs
deep-copied today read at the same 94 µs as vcomp.

### 3.2 1 TB — single readrandom run

Earlier 1 TB readrandom comparisons are not preserved here because the
disk state at the time was strongly skewed (vcomp DB freshly loaded,
baseline DB days old). A fair 1 TB comparison requires both DBs loaded
within a short window of each other. This is not currently scheduled.

### 3.3 NVMe physical-state effect — 2026-05-13 isolation experiment

**Question** (carried over from §3.1's disk-state caveat): how much of
the elapsed gap comes from real DB structure vs. NVMe physical layout
of the SSTs?

**Method**:

1. Remeasure all 30 baseline DBs (`260415_0635_250gb_x30`) today
   (2026-05-13) using the same binary + parameters as §3.1. Same DB
   content, ~1 month older on disk.
   Logs: [artifacts/log_batch/260415_0635_250gb_x30/readrandom_1t_0p_remeasure_260513/](../artifacts/log_batch/260415_0635_250gb_x30/readrandom_1t_0p_remeasure_260513/)
2. Deep-copy 5 baseline DBs to new LBAs on `/work/vcomp/copy_test/`
   and readrandom each copy + 1 vcomp control. Same DB content, fresh
   physical location.
   Logs: [artifacts/log_batch/copy_test_260513/](../artifacts/log_batch/copy_test_260513/)

**Result table** (per-block latency, identical DB content):

| Measurement (n) | us/blk | sst_p50 | elapsed | NVMe state |
|---|---|---|---|---|
| baseline orig (n=30, 2026-04-16) | 125.9 ± 12.03 | 113.5 ± 11.0 | 493.9 ± 58.8s | mixed (per-DB variance) |
| baseline now (n=30, 2026-05-13) | 141.9 ± 1.62 | 132.6 ± 1.5 | 556.7 ± 33.8s | aged 1 month, **uniformly slow** |
| baseline copy (n=5, 2026-05-13) | 106.4 ± 0.37 | 93.8 ± 0.1 | 440.5 ± 39.7s | **fresh** (re-written) |
| vcomp_new (n=30, 2026-05-12) | 107.1 ± 0.34 | 93.9 ± 0.2 | 403.8 ± 3.1s | **fresh** (loaded 1 day ago) |

**Key findings**:

1. **NVMe layout effect quantified**: per-block latency 141 µs (aged
   data) → 106 µs (fresh write) = **−35 µs**, regardless of DB content
   or tree shape. Same baseline_run4 SSTs read at 141.7 µs from their
   April LBAs vs 106.5 µs from fresh-copy LBAs.

2. **Same DB, different LBA, very different speed**:
   - baseline_run4 (originally FAST, 107 µs in April): aged 141.7 µs →
     copy 106.5 µs
   - baseline_run12 (originally SLOWEST, 142 µs in April): aged 135.6
     µs → copy 107.1 µs
   The "fast/slow" identity in April was about *where the DB happened
   to land on NVMe*, not about the DB itself.

3. **Originally fast baseline DBs lost the most over time**: split the
   30 April measurements at 120 µs/blk:
   - originally FAST (n=12, ~112 µs): now 141.5 µs (Δ +28.8 µs)
   - originally slow (n=18, ~135 µs): now 142.2 µs (Δ +7.5 µs)
   All 30 converge to ~142 µs after 1 month. Consistent with NVMe
   internal data migration (SLC eviction / fresh-tier displacement /
   read-disturb refresh).

4. **Decomposition of original §3.1 −18.2% elapsed gap**:
   - ~13 pp from NVMe fresh-write state (vcomp DBs 1 month younger)
   - ~5 pp from genuine tree shape (sst_reads_per_get 3.92 → 3.77,
     filter_per_get −5.9%)
   When both baseline copy and vcomp are fresh, baseline_run4_copy
   takes 425s and vcomp_run1 takes 402s — a ~5% structural advantage,
   not 18%.

5. **vcomp's "fast NVMe state" is not a vcomp property**: it's a
   consequence of "data written 1 day ago" vs. "data written 1 month
   ago". A baseline DB freshly re-copied today reads at the same
   speed as vcomp_new. Predicts vcomp_new will degrade to ~141 µs
   if remeasured in 1 month (untested).

**Implications for fair comparison**:

- Only metrics independent of NVMe physical state are trustworthy for
  structural comparison: filter_per_get, sst_reads_per_get, bloom_fpr,
  per-level hit counts, bytes_per_sst_read.
- For elapsed / throughput / latency comparisons, both DBs must be
  measured at the same NVMe freshness. The cleanest way is to
  deep-copy both groups immediately before measurement.
- §3.4 below: full 30×30 deep-copy fair comparison (TBD).

**Resolves**: the "fragmentation isolation" experiment queued in
[EXPERIMENTS_PLANNED.md](EXPERIMENTS_PLANNED.md) at 2026-04-15.

### 3.4 30×30 fair-comparison deep-copy experiment — 2026-05-13

**Method**: deep-copy all 30 baseline DBs from `260415_0635_250gb_x30`
to `/work/vcomp/copy_test/baseline_run{i}_copy/` and readrandom each
(same params: 1M reads, 1 thread, cache=0%, vcomp-prof binary).
Compare 4 groups, all n=30:

- **baseline_orig** — April 16 measurement (mixed NVMe state)
- **baseline_now** — May 13 remeasure (aged 1 month, same LBAs)
- **baseline_copy** — May 13 cp + read (fresh LBAs, same DB content)
- **vcomp_new** — May 12 vcomp load (fresh LBAs, vcomp tree)

Logs: [artifacts/log_batch/copy_test_260513/](../artifacts/log_batch/copy_test_260513/)

**Per-group means (n=30 each)**:

| group | elapsed (s) | ops/s | us/blk | sst_p50 (µs) | sst_per_get | filter_per_get |
|---|---|---|---|---|---|---|
| baseline_orig (4/16) | 493.91 | 2,052 | 125.86 | 113.54 | 3.92 | 2.62 |
| baseline_now (5/13) | **556.68** | 1,802 | **141.93** | 132.58 | 3.92 | 2.62 |
| baseline_copy (5/13) | **419.41** | 2,393 | **106.89** | 93.93 | 3.92 | 2.62 |
| **vcomp_new** (5/12) | **403.78** | 2,476 | 107.13 | 93.86 | **3.77** | **2.46** |

**Per-group std (variance shrinks with NVMe state convergence)**:

| group | elapsed_std | ops/s_std | us/blk_std | sst_p50_std |
|---|---|---|---|---|
| baseline_orig | 58.76 | 232.8 | **12.03** | 15.11 |
| baseline_now | 33.81 | 102.2 | 1.62 | 1.97 |
| baseline_copy | 27.56 | 144.4 | 0.68 | 0.30 |
| vcomp_new | 3.10 | 18.9 | 0.34 | 0.10 |

**Box plot**: [results/read/batch/readrandom_4way_30x30_comparison.png](../results/read/batch/readrandom_4way_30x30_comparison.png)

**Decomposition (clean isolation now possible)**:

1. **NVMe physical-state effect** (compare `baseline_now` vs `baseline_copy`,
   same DB content, only LBA freshness differs):
   - us/blk: 141.93 → 106.89 = **−35.0 µs (−24.7%)**
   - elapsed: 556.68 → 419.41 = **−137.3 s (−24.7%)**
   - DB content metrics (sst_per_get 3.92, filter_per_get 2.62) **identical**.

2. **Time-on-disk effect alone** (compare `baseline_orig` vs `baseline_now`,
   same DBs aged 1 month):
   - us/blk: 125.86 → 141.93 = **+16.1 µs (+12.8%)**
   - Aging effect ~half the size of fresh-write effect — consistent with
     "originally fast" DBs losing ~29 µs while "originally slow" lost
     ~7 µs (per §3.3 split).

3. **vcomp's genuine tree-shape advantage** (compare `baseline_copy` vs
   `vcomp_new`, both fresh NVMe state):
   - elapsed: 419.41 → 403.78 = **−15.6 s (−3.7%)**
   - sst_per_get: 3.92 → 3.77 = **−3.9%**
   - filter_per_get: 2.62 → 2.46 = **−5.9%**
   - us/blk: 106.89 → 107.13 = **+0.2%** (essentially same NVMe layer)
   - **The −3.7% elapsed matches the −3.9% sst_per_get** — tree
     structure alone, no NVMe state contamination.

**Variance decomposition**:

| variance source | std reduction | mechanism |
|---|---|---|
| 4/16 → 5/13 baseline aging | 58.8s → 33.8s | per-DB NVMe state converges |
| baseline_now → baseline_copy | 33.8s → 27.6s | fresh write removes most state variance |
| baseline_copy → vcomp_new | 27.6s → 3.1s | vcomp loads in one tight 17-min window → uniform fresh state; the 27.6s residual in baseline_copy is per-DB tree differences (different fillrandom seeds produce different L1-L3 distributions) |

**The vcomp_new 3.1s std vs baseline_copy 27.6s std is itself
diagnostic**: vcomp's tree-shape uniformity (deterministic PLR
materialization from the same key set) is much tighter than
fillrandom's per-DB variance. This is a small but real
*reproducibility* advantage of vcomp loading, independent of any
read-side speedup.

**Conclusion**: The honest single-line summary of vcomp's read-side
result is "−3.7% elapsed at same NVMe freshness, all from
sst_per_get/filter_per_get reduction." The headline −18.2% in §3.1
was ~80% NVMe state artifact, ~20% real tree advantage.

---

## 4. Per-load resource accounting

Baseline write-amplification grows with level depth. vcomp's W-Amp
stays at 1.0× because Phase 2 materializes each SST exactly once, with
zero compaction read/write.

| Scale | Mode | Compaction bytes read | Compaction bytes written | Ingest | Write amplification |
|-------|------|------------------------|---------------------------|--------|----------------------|
| 250 GB | baseline | 2,268 GB  | 2,444 GB  | 254 GB    | **9.6×** |
| 250 GB | vcomp    | **0**     | **0**     | 0         | **1.0×** |
| 1 TB   | baseline | 12,084 GB | 12,851 GB | 1,016 GB  | **12.6×** |
| 1 TB   | vcomp    | **0**     | **0**     | 0         | **1.0×** |
| 5 TB   | baseline | 95,087 GB | 98,666 GB | 5,200 GB  | **19.0×** |
| 5 TB   | vcomp    | **0**     | **0**     | 0         | **1.0×** |
| 10 TB  | baseline | 212,583 GB | 220,417 GB | 10,400 GB | **21.2×** |
| 10 TB  | vcomp    | **0**      | **0**      | 0         | **1.0×** |

(W-Amp here is RocksDB's reported `W-Amp` from compaction stats, defined
as `total writes / ingest`.)

---

## 5. Twitter trace load + tree-shape sanity

**When**: baseline 2026-04-15, vcomp 2026-05-17. **Workload**:
Twitter cache trace cluster012, write-only Put replay, converted to
`VCMPTRC1` binary traces. Baseline uses `twitterload`; vcomp uses the
real fast path: `fillvirtual --twitter_trace_file=...`.

**Caveats**: single run per scale, not 30-run statistics. The vcomp runs
use `.vcomptrace.new` files with the 40 B header fields needed to avoid
pre-scanning; payload records are intended to match the earlier baseline
traces. Baseline `compact0` reported "input file currently being
compacted" in these logs, but final `levelstats` shows L0 empty after
`waitforcompaction`, so the table is a tree-shape sanity result, not a
full coverage/read-performance parity result.

### 5.1 Load time

| Scale | baseline elapsed | vcomp elapsed | Speedup | baseline ingest | vcomp materialized keys |
|-------|------------------|---------------|---------|-----------------|--------------------------|
| 10M Puts  | 40 s    | 8 s   | **5.0x**  | 10.10 GB  | 9.89M |
| 100M Puts | 490 s   | 33 s  | **14.8x** | 104.53 GB | 99.03M |
| 500M Puts | 3,776 s | 156 s | **24.2x** | 518.46 GB | 495.85M |

### 5.2 Final levelstats

| Scale | Metric | baseline | vcomp | Δ |
|-------|--------|----------|-------|---|
| 10M | total files | 158 | 154 | −2.5% |
| 10M | total size | 10.05 GB | 10.01 GB | −0.4% |
| 10M | non-empty levels | L1:4, L2:24, L3:130 | L1:3, L2:23, L3:128 | close |
| 100M | total files | 1,954 | 1,785 | −8.6% |
| 100M | total size | 103.84 GB | 103.78 GB | −0.1% |
| 100M | non-empty levels | L1:4, L2:48, L3:448, L4:1454 | L1:5, L2:48, L3:406, L4:1326 | L3/L4 fewer files |
| 500M | total files | 8,960 | 8,601 | −4.0% |
| 500M | total size | 514.37 GB | 515.35 GB | +0.2% |
| 500M | non-empty levels | L1:3, L2:47, L3:447, L4:4397, L5:4066 | L1:4, L2:44, L3:437, L4:4294, L5:3822 | close |

**Interpretation**

- The trace path now exercises the intended vcomp design: memtable bypass,
  prefix8 PLR domain, virtual BG compactions, and final materialization.
- Tree size parity is strong at all three scales (≤0.4% size delta).
- File-count parity is good enough for a first trace sanity pass: 500M is
  within 4.0%; 100M is the worst at −8.6%, mostly from fewer L3/L4 files.
- This does not replace the synthetic 30×30 coverage result in §2. The
  next trace-specific validation should be a coverage/readrandom pass on
  freshly copied baseline/vcomp DBs if trace read-side parity matters.

### 5.3 Exact KV materialization sanity (2026-05-25)

Branch: `vcomp-w/kv`. Current design:

- Phase 1 is still metadata-only virtual compaction.
- L0 virtual SSTs carry source-run lineage; virtual compaction unions it.
- Phase 2 re-scans the append-only trace source, partitions raw keys plus
  value offsets in memory by final virtual range, deduplicates by newest
  seqno, and writes real SSTs once.
- Conservative naive entry counts are used for output splitting; this avoids
  oversized materialized SSTs when PLR dedup estimation undershoots.
- Gap records outside final virtual ranges are materialized through a small
  L0 patch path, then background work is resumed.

Run-phase lookup uses raw trace keys (`twitterrun_encode_for_vcomp=false`).

| Load | Read trace window | baseline misses | vcomp misses | Result |
|------|-------------------|-----------------|--------------|--------|
| 1M Puts | first 1M records of `cluster012_100M.vcomptrace.with_reads` | 10,941 / 198,797 gets | 10,941 / 198,797 gets | exact |
| 10M Puts | first 5M records of `cluster012_100M.vcomptrace.with_reads` | 15,045 / 1,006,713 gets | 15,045 / 1,006,713 gets | exact |
| 100M Puts | first 5M records of `cluster012_100M.vcomptrace.with_reads` | 15,040 / 1,006,713 gets | 15,040 / 1,006,713 gets | exact |

Exact-KV load comparison:

| Scale | Mode | full db_bench elapsed | primary benchmark | non-primary remainder | final files | levelstats | DB size |
|-------|------|-----------------------|-------------------|-----------------------|-------------|------------|---------|
| 10M | baseline | 40 s | `twitterload` 22.45 s | 17.55 s | 158 | L1:4, L2:24, L3:130 | 11 GB |
| 10M | vcomp-w/kv | 28 s | `fillvirtual` 18.16 s | 9.84 s | 167 | L1:1, L2:39, L3:127 | 11 GB |
| 100M | baseline | 490 s | `twitterload` 463.44 s | 26.56 s | 1,954 | L1:4, L2:48, L3:448, L4:1454 | 104 GB |
| 100M | vcomp-w/kv | 337 s | `fillvirtual` 232.31 s | 104.69 s | 1,703 | L2:48, L3:411, L4:1244 | 104 GB |

10M vcomp breakdown: Phase 1 2.56 s, Phase 2 15.58 s, total 18.16 s,
9,854,975 raw keys materialized. Phase 2 wrote 19,966 gap-patch keys and
then resumed background work; final levelstats have L0 empty.

100M vcomp breakdown: Phase 1 25.76 s, Phase 2 206.48 s, total 232.31 s,
98,734,869 raw keys materialized. Phase 2 wrote 61,731 gap-patch keys and
then resumed background work; final levelstats have L0 empty.

10M read comparison using `run.sh`-style no-cache settings
(`cache_size=1`, `cache_index_and_filter_blocks=true`,
`cache_type=hyper_clock_cache`, direct reads):

| Scale | Mode | ops/s | elapsed | index miss/hit | filter miss/hit | data miss/hit | block-cache bytes written |
|-------|------|-------|---------|----------------|-----------------|---------------|---------------------------|
| 10M | baseline | 2,402 | 418.98 s | 996,013 / 1,133 | 1,553,663 / 355 | 997,242 / 0 | 283.99 GB |
| 10M | vcomp-w/kv | 3,524 | 285.67 s | 991,801 / 38 | 1,011,590 / 105 | 991,845 / 0 | 206.74 GB |
| 100M | baseline | 1,793 | 561.28 s | 1,010,858 / 2 | 2,965,168 / 64 | 1,011,464 / 0 | 349.79 GB |
| 100M | vcomp-w/kv | 2,621 | 384.04 s | 1,001,557 / 5 | 1,999,022 / 1,127 | 1,001,758 / 0 | 268.63 GB |

Read throughput speedup: **1.47x** at 10M and **1.46x** at 100M.
`twitterrun_miss_log_file` output was also compared with `cmp`; baseline
and vcomp miss-key sequences were byte-identical at both scales.

Raw logs:
- 1M vcomp load:
  `artifacts/log_loads/vcomp_tl_cluster012_1M_tracesrc_patch/`
- 10M vcomp load:
  `artifacts/log_loads/vcomp_tl_cluster012_10M_valueoffset/`
- 10M run.sh-style baseline read:
  `artifacts/log_runs/twitterrun_tracesrc_patch_10M_runsh/baseline/`
- 10M run.sh-style vcomp read:
  `artifacts/log_runs/twitterrun_valueoffset_10M_runsh/vcomp/`
- 100M vcomp load:
  `artifacts/log_loads/vcomp_tl_cluster012_100M_valueoffset/`
- 100M run.sh-style baseline/vcomp read:
  `artifacts/log_runs/twitterrun_valueoffset_100M_runsh/{baseline,vcomp}/`

Raw logs:
- baseline 10M:
  `artifacts/log_loads/baseline_tl_cluster012_10M_260415_1516_stage1/` (not retained)
- vcomp 10M:
  `artifacts/log_loads/vcomp_tl_cluster012_10M.vcomptrace.new_260517_1252_newhdr_sanity/` (not retained)
- baseline 100M:
  `artifacts/log_loads/baseline_tl_cluster012_100M_260415_1523_stage2/` (not retained)
- vcomp 100M:
  `artifacts/log_loads/vcomp_tl_cluster012_100M.vcomptrace.new_260517_1259_newhdr_sanity/` (not retained)
- baseline 500M:
  `artifacts/log_loads/baseline_tl_cluster012_500M_260415_1602_stage3/` (not retained)
- vcomp 500M:
  `artifacts/log_loads/vcomp_tl_cluster012_500M.vcomptrace.new_260517_1325_newhdr_sanity/` (not retained)

---

## 6. Where to find the raw data

| Item | Location |
|------|----------|
| 30× baseline 250 GB DBs | `/work/vcomp/260410_0339_250gb_x30/baseline_run{1..30}/` |
| 30× vcomp 250 GB DBs    | `/work/vcomp/260414_1710_250gb_x30/vcomp_run{1..30}/` |
| baseline batch load logs | [artifacts/log_batch/260410_0339_250gb_x30/](../artifacts/log_batch/260410_0339_250gb_x30/) |
| vcomp batch load logs    | [artifacts/log_batch/260414_1710_250gb_x30/](../artifacts/log_batch/260414_1710_250gb_x30/) |
| baseline batch readrandom (1M) | [artifacts/log_batch/260410_0339_250gb_x30/readrandom_1t_0p/](../artifacts/log_batch/260410_0339_250gb_x30/readrandom_1t_0p/) |
| vcomp batch readrandom (1M)    | [artifacts/log_batch/260414_1710_250gb_x30/readrandom_1t_0p/](../artifacts/log_batch/260414_1710_250gb_x30/readrandom_1t_0p/) |
| Per-run stats parser     | [parse_runs.py](../analysis/parse_runs.py) |
| Batch readrandom analyzer | [analyze_batch_readrandom.py](../analysis/analyze_batch_readrandom.py) |
| 30×30 comparison CSV     | [results/read/batch/readrandom_30x30_comparison.csv](../results/read/batch/readrandom_30x30_comparison.csv) |
| Per-run wide CSV         | [results/read/runs_summary.csv](../results/read/runs_summary.csv) |

---

## 7. Open structural questions

These are tracked here so they don't get lost between sessions.

- **Tree-shape gap — closed (2026-05-12).** End-state L2/L3/L4 file
  counts now match baseline within 3 %, L3 / L4 coverage match within
  ≤1 pp, L1 / L2 coverage sit inside baseline's range (lower half).
  The structural variable was the per-event L0→L1 *input data size*,
  not intra-L0 cascade or write stall. Replaced earlier hypotheses
  with a vcomp-only size gate at the L0→L1 picker (4 GB ≈ baseline's
  measured 4876 MB per-event input) plus an end-of-load L0 drain so
  Phase 2 sees no L0 leftover and `compact0` is a no-op. Full chain
  of investigation and fixes in
  [README.md §"2026-05-12"](../../README.md).
  Measurements added below as §2 update.
- ~~**bloom FPR gap** (§3.1)~~ — **closed (2026-05-12)**. vcomp_new
  bloom_fpr 2.69% vs baseline 2.90% (−7.2%); now slightly *better*
  than baseline. Old vcomp's +22% gap was driven by over-compacted
  L1-L2, which the size-gate fix corrected.
- ~~**disk fragmentation** (§3.1)~~ — **resolved (2026-05-13, §3.3)**.
  Was an NVMe physical-state effect, not file-system fragmentation.
  ~13 pp of the §3.1 −18.2% elapsed gap is "fresh write vs 1-month-old
  write" per-block latency (141 → 106 µs); the remaining ~5 pp is
  genuine tree-shape advantage (sst_reads_per_get −3.9%). Fair
  apples-to-apples comparison requires both groups measured at same
  NVMe freshness.

---

## 8. Section 3 alternatives and the 91 B scale-up (2026-09-11)

### 8.1 YCSB A-D on three 1 TB states, 50 GiB block cache

`results/ch3_ycsb_cache50_260911_run2`, 300 s per cell, 48 threads. Baseline is
reused from `results/ycsb_band_n01_260910`, the independently loaded arm whose
A-D throughput sits closest to the median of ten such loadings (within 0.42%).

| state | A | B | C | D |
|---|---|---|---|---|
| incremental construction | 695,604 | 1,037,150 | 1,594,123 | 2,658,994 |
| fillseq | 747,594 | 976,981 | 1,231,296 | 2,075,100 |
| flush-only | timeout | timeout | 1,581 | timeout |

Flush-only serves only workload C. A, B and D carry writes, and the DB stops
writes 0.6 s after open with 16,237 $L_0$ files against a stop trigger of 36;
the watchdog ends each of those cells at 903.6 s. On C it runs at 1/1008 of
baseline throughput.

Per-lookup work on C, which is what produces that: SSTs consulted 3.65 /
1.00 / 10,398.93, of which positive 0.63 / 1.00 / 101.92 and true positive 0.60
/ 1.00 / 0.60. Flush-only's positives are 99.41% false, so a single lookup pays
for about 101 wasted data-block reads. Fillseq checks exactly one SST because
trivial move leaves no inter-level overlap, yet it is still 23% slower than
baseline on C: every key exists there (100% successful lookups against 60.3%),
so every operation reaches a data block.

Collected numbers: `results/paper_ch3_state_comparison.tsv`. Final-state shape
across all six loaded states: `results/paper_ch3_final_state_shape.tsv`.
Figures: `analysis/plot_ch3_alternatives.py`.

### 8.2 Run-to-run band across independent loadings

Ten independent baseline loadings and six F2Load loadings, YCSB A-F, 1 TB,
50 GiB block cache (`results/ycsb_band_*`, `results/ycsb_f2band_*`). The
baseline band is 98.5-103.5% (A), 97.9-102.3% (B), 94.4-102.6% (C),
98.3-101.2% (D), 98.9-100.5% (E) and 99.2-101.0% (F) of its own mean. F2Load
lands inside it on A, B, D and E; C sits at 0.926 and F at 0.988. One F2Load C
run (f01) reads 71.4% and pulls that mean down; without it C is 96.8%, inside
the band. The band is a loading band, not a measurement band: the ten source
databases hold 13,460 to 13,549 SSTs, so they are distinct loads.

### 8.3 F2Load 91 B scale-up

Full record in `results/f2load_91b_scaling_260911/RESULTS.md`. 1.33 / 2.64 /
5.11 / 12.99 / 49.84 min at 500 GiB to 8 TB, linear to 2 TB and superlinear
after, with peak RSS turning harder than time (4.4x and 4.6x per doubling past
2 TB). The break coincides with the tree reaching $L_6$ at 4 TB.

The comparable baseline series was loaded with the db_bench default of two
memtables rather than the frozen 16, so the two cannot be divided directly; see
that file for the stall evidence.
