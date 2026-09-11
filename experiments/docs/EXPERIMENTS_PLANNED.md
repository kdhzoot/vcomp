# Planned Experiments

**2026-09-10 fidelity status:** The dedup-ratio estimate, the physical SST size model and the F2Load fidelity comparison are recorded in [F2LOAD_FIDELITY_260909.md](F2LOAD_FIDELITY_260909.md). The A-F placement control is complete (three campaigns, 12/12 cells each): the read comparison must be made from a fresh single-stream copy of both DBs, and every read result before 2026-09-10 carries the loader's flash placement. Remaining: re-measure the paper's Chapter 2/3 read figures under that protocol; decide whether the materializer should write `format_version=7` to match the baselines; state the uniform-read found-fraction difference of model-generated keys in the paper; and settle the residual `dropped_live_entries` on unique100. The Chapter 2/3 status below is unchanged.

**2026-09-07 current Chapter 2/3 results:** F2Load loading and all four reads completed and were reflected in Figures 4/5 and dependent prose. The combined comparison has eight validated load states and twenty-four reads, reusing the previous seven load controls and twenty reads unchanged. F2Load is 134.731 s including final physical completion (pending bytes zero). Use [PAPER_CHAPTER23_COMMON_RESULTS.md](PAPER_CHAPTER23_COMMON_RESULTS.md). Only the previously deferred size-scaling and instrumentation follow-ups remain outside this campaign.

**2026-09-06 common Chapter 2/3 update:** Seven load states and twenty reads completed and were reflected in Figures 2(b), 4 and 5 and their prose. Use [PAPER_CHAPTER23_COMMON_RESULTS.md](PAPER_CHAPTER23_COMMON_RESULTS.md) for the current shared baseline. Earlier paper values below are historical for these comparisons. F2Load recovery/reads and the scaling/breakdown follow-ups remain deferred.

## 2026-09-11: Baseline Series Unification and Phase 1 Shard Sweep

Two items left open by the 91 B scale-up, both blocking numbers that would
otherwise go into the paper.

**Unify the baseline series before quoting any 91 B speedup.** The five-point
baseline curve (`exp_260604_exp91b_baseline`, `exp_260822_paper_bg_91b_8tb_direct`)
passes no write-buffer flags and therefore ran with two memtables; the frozen
configuration uses 16. The same 1 TB load reads 5.58 h there and 3.17 h in
`paper_ch23_common_260905_approved_run3`, and cumulative stall accounts for
about half the gap (36.6% of the run against 27.0%).

**Decision (2026-09-11): re-measure baseline under the frozen configuration.**
Re-measuring F2Load under the older one instead would be far cheaper, about 72
minutes against more than 80 hours, but it would compare F2Load against a
baseline handicapped by a setting we would not choose for it, and the speedup
would carry that handicap. The comparison has to run both systems under the
configuration the paper claims to use. Deferred for now, not dropped: the 91 B
speedups stay out of the paper until this is measured.

**Sweep `--vcomp_phase1_shards`.** Phase 1 is fixed at 8 shards while
materialization uses 48 workers, and during the 8 TB load the process held
about 9.5 cores of the 48 online. Throughput falls from 6.3 to 2.7 GiB/s
between 1 TB and 8 TB. A sweep over 8/16/32/48 at 1 TB costs four runs of
under three minutes each and would show how much of the scale-up loss is the
shard count rather than the extra level.

**Also open:** peak RSS grows faster than SST count past 2 TB (4.4x and 4.6x
per doubling against 2.0x). At that slope a 16 TB load needs roughly 190 GiB
and a 32 TB load roughly 870 GiB against 1.0 TiB of DRAM. Worth understanding
before any run above 8 TB is scheduled.

## 2026-09-03: Paired No-Comp and Last-Comp at 16 Write Buffers

**Status:** Completed and validated. No-comp was 973 seconds; its preserved
checkpoint took 4,053 seconds for one-shot compaction, producing a 5,026-second
Last-comp total.

The selected No-comp result is the completed 973-second, 16-buffer L0-only DB.
Preserve it and hard-link-checkpoint its immutable SSTs into a metadata-private
Last-comp DB. Run exactly one synchronous full-range `compact` on the
checkpoint. Report Last-comp as 973 seconds plus the separately timed one-shot
compaction, and verify that the No-comp source is unchanged.

Both states use vector memtables, 64 MiB write buffers,
`max_write_buffer_number=16`, 48 background jobs, one subcompaction, direct
I/O, no WAL, and no compression. Run the one-shot path on the retained 1 GiB
pilot before the full checkpoint and preserve both final DBs. Detailed boundaries and rejection
criteria are in `PAPER_NOCOMP_WB16_1TB.md` and
`PAPER_LASTCOMP_WB16_1TB.md`.

## 2026-09-03: Fillseq + 10% Overwrite, 16 Write Buffers

**Status:** Completed and validated. Total time was 1,745 seconds: 1,272
seconds for Fillseq and 473 seconds for overwrite.

Repeat the fixed 1,000 GiB clean-RocksDB Fillseq + 10% random-overwrite
workload with the original binary and all original settings preserved, except
for `max_write_buffer_number=16` in both phases. Run a complete 1 GiB pilot
first, execute only on an idle machine, retain the final DB, and compare both
phase-local stalls as well as combined wall time against the 2,014-second
two-buffer control. The exact configuration, boundaries, reserved paths,
metrics, and rejection gates are in `PAPER_FILLSEQ_OVERWRITE_WB16_1TB.md`.

## 2026-09-03: Figure 2 Four-Load Flush/Compaction Study

**Status:** Completed and validated. The 1 KB Conventional cell completed in
`figure2_1tb_fourcell_260903_run1`. After the first 91 B Flush-only attempt was
interrupted and excluded, fresh 91 B Flush-only, 91 B Conventional, and 1 KB
Flush-only cells completed in `figure2_1tb_fourcell_260903_resume1`. All four
accepted cells passed the profiler accounting gates. Final summaries are
`results/paper_figure2_loading_waf.tsv` and
`results/paper_figure2_phase_breakdown.tsv`; Figure 2 was updated on
2026-09-04.

The complete four-cell 100 GiB profiler smoke passed on 2026-09-03. All
operation counts and flush/compaction accounting invariants matched; both
No-comp cells emitted zero compaction records, and both Conventional cells
ended with zero pending compaction bytes. This is a functional/scale gate only
and does not count as any of the four 1,000 GiB paper loads. See
`PAPER_FIGURE2_FLUSH_COMPACTION.md` for the full record.

Run exactly four single-run cells at the existing Figure 2 `1 TB` point
(`TARGET_DB_GB=1000`, i.e., 1,000 GiB in the harness): Conventional and No
compaction for both 24 B + 1000 B and 48 B + 43 B KV pairs. Use the same
`vcomp-prof/db_bench` binary and common RocksDB configuration in all cells.

Panel (b) uses loading time and standard input-normalized WAF from all four
loads. Panel (c) uses the No-compaction runs for isolated Flush bars and the
Conventional runs for Compaction bars; Conventional flush data is retained as
a sensitivity check. This is a single-run design with no variance or profiler
overhead estimate.

The exact order, controls, operation counts, completion rules, WAF definition,
and validation gates are in `PAPER_FIGURE2_FLUSH_COMPACTION.md`. The source-level
justification and limitations of every flush timer are in
`FLUSH_PROFILER_INSTRUMENTATION.md`.

## 2026-09-03: Figure 4 16-Buffer Configuration Freeze

**Status:** Completed and fixed.

The author explicitly reopened Figure 4 and selected the complete 16-buffer
set for all conventional `db_bench` paths. The figure now includes Baseline,
ADOC, BlobDB GC-off, Flush-only, Last-comp, Fillseq, Fillseq plus 10% overwrite,
and F2Load. F2Load retains its 102-second `fillvirtual` measurement because its
custom loading path does not use RocksDB's write-buffer-count option. Figure 4
is fixed to the promoted measurements and exact per-method configurations in
`PAPER_FIGURE4_FIXED_CONFIGURATION.md`.

The historical 4,889-second SkipList and two-buffer Figure 4 results remain
preserved but are no longer plotted. Do not promote other ADOC thread,
memtable, or pending-limit sweeps without reopening the figure again.

## 2026-09-04: Figure 4 Uniform-Read Cache Matrix

**Status:** Completed and validated on 2026-09-05 UTC. The original five-state
matrix produced 20 successful runs, and a separately logged conventional
Baseline supplement produced four successful runs. The promoted 24-run summary
is `results/paper_figure4_uniform_read_cache_5m_single.tsv`.

Run 300-second, 48-thread uniform YCSB-C against the retained 1,000-GiB
Flush-only, Last-comp, Fillseq, Fillseq+OW, and same-option F2Load DBs under a
2x2 metadata-placement/block-cache matrix. Add conventional Baseline under the
same four configurations so final-state divergence is measured directly
rather than inferred. All runs are read-only, use direct reads, disable
automatic compaction, and execute serially.

The resulting paper panels separate logical filter checks, key-set fidelity,
and absolute throughput. Flush-only checks about 10.3K filters per lookup and
remains 210x slower than Baseline even with pinned metadata and a 50-GiB data
cache. Fillseq variants have 100% successful lookups, unlike random loading.
F2Load's 61.761% successful-lookup ratio is 1.453 percentage points below
Baseline because its current descriptor-based materialization reconstructs an
approximate key set; its throughput is therefore contextual rather than a
controlled layout-only comparison. Full commands, provenance, interpretation,
and limitations are in `PAPER_FIGURE4_UNIFORM_READ_CACHE_MATRIX.md`.

## 2026-08-23: Artifact Baseline Comparison for the Background Figure

**Status**: Planned. The active 8 TB true-91 B rerun was stopped on 2026-08-24
after confirming that a completed first run had unintentionally restarted.
Recoverable logs and the incident record are preserved under
`artifacts/log_loads/exp_260822_paper_bg_91b_8tb_direct/`. The incomplete DB was
deleted. Before using the 46.2-hour first-run result in the paper, decide
whether its brief overlap with the 100 GB diagnostic is acceptable or whether
an isolated rerun is required. Device write amplification requires a clean
rerun because the start counters were overwritten.

The baseline-selection matrix, reproduction gates, matched-baseline
normalization, 1 GB/10 GB/100 GB/1 TB run sequence, and intended figure are
specified in `PAPER_ARTIFACT_BASELINE_COMPARISON.md`.

- Primary pilot candidates: ADOC and DiffKV.
- Functional smoke status (2026-08-24): ADOC/ADOC-off and DiffKV/Titan passed
  1 GB load, persistence, and reopen checks. These results are not usable for
  performance; ADOC still needs a release build, and the DiffKV smoke uses the
  documented single-thread explicit-flush wrapper required by its small-load
  lifecycle. See `PAPER_ARTIFACT_BASELINE_COMPARISON.md` and
  `results/paper_artifact_smoke_260824.tsv`.
- Direct design-alternative queue: ADOC with its ADOC-off baseline, DiffKV
  with its Titan baseline, no-compaction, and last-compaction with natural
  RocksDB as the matched baseline.
- Subcompaction policy: qualify a safe setting on 100 GB before artifact
  paper runs. Apply the selected value symmetrically within every
  compaction-bearing matched pair. No-compaction reports subcompactions as
  `N/A` and an actual scheduled count of zero.
- Last-compaction qualification: compare `1, 4, 8, 16, 32`; retain 48 only as
  a diagnostic point because the concurrent 100 GB run scheduled 35 ranges
  but consumed 113.1 GiB peak RSS. Select the fastest setting that remains
  within the memory gate in `PAPER_ARTIFACT_BASELINE_COMPARISON.md`.
- Qualification result (2026-08-24): cap 32 was fastest at 100 GB but failed
  the 500 GB memory gate at 243.2 GiB under a 240 GiB active-abort threshold.
  Cap 16 passed at 500 GB (179.67 GiB for last-comp; 3.34 GiB for baseline),
  with zero swap and 10,000/10,000 reopen reads. Cap 16 is the selected safe
  setting for larger current-RocksDB pairs.
- Clean-engine validation (2026-08-25): a matched 500 GB pair on unmodified
  RocksDB 11.1.0 completed in 2,314 s at cap 1 and 1,107 s at cap 16. Cap 16
  reduced loading time by 52.2% and write-stall time by 74.8%, with only 0.74%
  more device writes. Both runs had zero benchmark-local/system swap, zero
  pending compaction bytes, and 10,000/10,000 reopen reads. This single ordered
  pair is a qualification result; alternate repetitions before reporting an
  exact speedup with error bars.
- Conditional candidate: SpanDB, currently blocked because every experiment
  NVMe device belongs to `/dev/md0` and SPDK requires a dedicated raw device.
- Citation-only due to specialized hardware: MatrixKV, ListDB, Pacman,
  DecouKV, and AegonKV.
- Citation-only due to deployment scope: HATS and Calcspar.
- Primary figure metric: treatment loading time normalized to a same-artifact,
  version-matched baseline. Do not pool absolute times from different
  RocksDB/Titan versions as a direct ranking.

## 2026-06-04: Paper Experiment Plan

**Status**: Planned.

논문 experiment part는 no-compression track으로 정리한다. Compression은
motivation에서 비용 차이를 확인하는 정도로만 남기고, 본 실험에서는
`compression_type=none`만 사용한다.

### Common Setup

- DB output root: `/work/vcomp/exp`
- Log output root: `vcomp/experiments/artifacts/log_loads/exp_<RUN_ID>/`
- Execution: sequential only. Do not run two loading/read jobs at once.
- 1KB workload: 24B key + 1000B value
- 91B workload: 48B key + 43B value
- Write threads: 1
- Memtable representation: `vector`
- WAL: disabled
- Direct I/O: enabled for reads, flushes, and compactions
- Bloom bits: 10
- Index compression: disabled
- Baseline binary: `$BASELINE_DB_BENCH`
- Vcomp binary: `$VCOMP_DB_BENCH`
- Profiling binary: `$VCOMP_PROF_DB_BENCH` only when compaction breakdown is needed

Directory convention:

```text
/work/vcomp/exp/
  reality/
    baseline_1tb_1kb_none_<RUN_ID>/
    vcomp_1tb_1kb_none_<RUN_ID>/
    baseline_1tb_91b_none_<RUN_ID>/
    vcomp_1tb_91b_none_<RUN_ID>/
  scaling/
    baseline_<size>_<kv>_none_<RUN_ID>/
    vcomp_<size>_<kv>_none_<RUN_ID>/
  sweep/
    zipf_alpha_<alpha>_<RUN_ID>/
    unique_ratio_<ratio>_<RUN_ID>/
```

## A. Reality Ground Truth

Question:

`baseline RocksDB로 만든 DB와 vcomp로 만든 DB가 read/query 관점에서 충분히 같은가?`

Matrix:

| Scale | KV | Compression | Systems |
| ---: | ---: | --- | --- |
| 1TB | 1KB value | none | baseline, vcomp |
| 1TB | 91B KV | none | baseline, vcomp |
| 10TB | 1KB value | none | baseline, vcomp |
| 10TB | 91B KV | none | baseline, vcomp |

10TB는 1TB에서 관찰한 결론이 large scale에서도 유지되는지 확인하는
comparison point다. 실행 비용이 너무 크면 10TB 1KB를 먼저 수행하고,
10TB 91B는 best-effort로 둔다.

Metrics:

- Loading time
- Final DB size
- Total device write GB
- LSM tree shape: level별 file count, level size, compaction count
- Read throughput / latency
- Level-hit distribution
- Filter/index/data read count
- Level coverage or key-range overlap stats

Expected outputs:

- `reality_summary.tsv`
- `reality_shape.tsv`
- `reality_read_summary.tsv`
- `reality_coverage.tsv`

## B. DB-Size Scaling

Question:

`dataset size가 커질 때 baseline과 vcomp의 loading time, total write, tree shape가 어떻게 증가하는가?`

Matrix:

| KV | Compression | Sizes |
| ---: | --- | --- |
| 1KB value | none | 500GB, 1TB, 2TB, 4TB, 8TB |
| 91B KV | none | 500GB, 1TB, 2TB, 4TB, 8TB |

Systems:

- baseline
- vcomp

Metrics:

- Elapsed loading time
- `fillrandom` or `fillvirtual` time
- Total device write GB
- Final DB size
- Compaction write GB
- Compaction count by level
- LSM shape by level

Important comparison points:

- 1TB: detailed realism comparison point.
- 8TB: scaling limit for the size-scaling sweep.

Expected outputs:

- `scaling_summary.tsv`
- `scaling_compactions.tsv`
- `scaling_shape.tsv`

## B2. Vcomp Motivation-Config Speedup

Question:

`motivation scaling/config에서 baseline real loading 대비 vcomp synthetic construction이 얼마나 빠른가?`

Interpretation:

- no-KV vcomp 결과는 KV-preserving loading이 아니라 LSM-tree construction
  speedup으로 해석한다.
- compression on/off matrix에서 vcomp compression 효과를 직접 비교하는 것은
  조심해야 한다. no-KV path는 실제 block compression 비용을 수행하지 않기
  때문이다.

Primary matrix:

| KV | Compression | Sizes | Systems |
| ---: | --- | --- | --- |
| 1KB value | none | 500GB, 1TB, 2TB, 4TB, 8TB | baseline, vcomp |
| 91B KV | none | 500GB, 1TB, 2TB, 4TB | baseline, vcomp |

Current baseline source:

- 1KB: `artifacts/log_loads/motivation_load_scaling_260602_cleanrocksdb_motivation/summary.tsv`
- 91B: `artifacts/log_loads/exp_260604_exp91b_baseline/scaling_91b_none/scaling_91b_summary.tsv`

Next runs:

1. Run vcomp 1KB/no-compression at 500GB, 1TB, 2TB, 4TB, 8TB.
2. Run vcomp 91B/no-compression at 500GB, 1TB, 2TB, 4TB.
3. Compare elapsed time and final LSM shape against the baseline summaries above.

Expected outputs:

- `motivation_vcomp_speedup_summary.tsv`
- `motivation_vcomp_shape.tsv`
- `motivation_vcomp_speedup.png`

Execution note:

- Use the existing clean-RocksDB 1KB baseline summary instead of rerunning
  baseline unless configuration drift is found.
- Run only one loading job at a time.
- Disable detailed hot-loop instrumentation for final speedup numbers:
  `VCOMP_LOG_APPLY_TIMING=false`, `VCOMP_SORT_DETAIL_TIMING=false`.
- If a bottleneck needs diagnosis, rerun only the target size with
  `VCOMP_SORT_DETAIL_TIMING=true` or LogAndApply timing enabled.

Vcomp run command:

```bash
cd vcomp/experiments
scripts/load/run_motivation_vcomp_speedup_1kb.sh
```

Default vcomp settings:

- Sizes: `500 1000 2000 4000 8000`
- Key/value: `key_size=24`, `value_size=1000`
- Compression: `none`
- Write threads: `1`
- Memtable: `vector`
- DB root: `/work/vcomp/exp/motivation_speedup_1kb`
- Log root: `artifacts/log_loads/motivation_vcomp_speedup_1kb_<RUN_ID>`

## B3. Motivation KV/Compression Matrix

Question:

`value size와 compression 여부가 baseline loading time과 compaction cost를 어떻게 바꾸는가?`

Completed baseline/profiling run:

`artifacts/log_loads/motivation_kv_compression_260604_kv500_matrix/summary.tsv`

Current loading-time matrix:

| Case | Target | Key size | Value size | Compression | Elapsed |
| --- | ---: | ---: | ---: | --- | ---: |
| `kv1000_nocompress` | 500GB | 24B | 1000B | none | 1,820s |
| `kv1000_snappy` | 500GB | 24B | 1000B | snappy | 2,485s |
| `kv91_nocompress` | 500GB | 48B | 43B | none | 9,842s |
| `kv91_snappy` | 500GB | 48B | 43B | snappy | 10,177s |

Note:

- `summary.tsv` has been updated to the corrected true-91B loading results.
- The old `key_size=24,value_size=91` breakdown rows were removed because they
  are not true 91B.
- Corrected true-91B breakdown still needs a profiling rerun with `vcomp-prof`.

Planned true-91B breakdown rerun:

```bash
cd vcomp/experiments
scripts/load/run_true91b_breakdown_matrix.sh
```

Expected outputs:

- `artifacts/log_loads/motivation_kv_compression_true91b_breakdown_<timestamp>/summary.tsv`
- `compaction_breakdown_all.tsv`
- `compaction_breakdown_representative.tsv`
- case-level `raw/compaction_breakdown.tsv`

First execution target:

- Start with 91B/no-compression because 1KB/no-compression already has
  motivation measurements that cover the first scaling trend.
- Script: `scripts/load/run_exp_91b_scaling.sh`
- Default sizes: `500GB, 1TB, 2TB, 4TB, 8TB`
- Default systems: `baseline` only
- Later vcomp run: pass `SYSTEMS=vcomp` with the same `SIZES_GB`
- DB output root: `/work/vcomp/exp/scaling`
- Summary output: `artifacts/log_loads/exp_<RUN_ID>/scaling_91b_none/scaling_91b_summary.tsv`

## C. Flexibility Sweep

Question:

`vcomp가 fixed fillrandom뿐 아니라 workload parameter 변화에도 적용 가능한가?`

Base setting:

- Logical DB size: 250GB
- Value size: 1000B
- Compression: none
- Systems: baseline, vcomp

Zipfian alpha sweep:

| Parameter | Candidate values |
| --- | --- |
| `zipf_alpha` | 0.0, 0.5, 0.8, 0.99, 1.2 |

Unique key ratio sweep:

| Parameter | Candidate values |
| --- | --- |
| `unique_key_ratio` | 1.0, 0.75, 0.5, 0.25 |

Metrics:

- Loading time
- Final unique key count
- Final DB size
- LSM tree shape
- Read throughput / latency
- Level-hit distribution
- Filter/index/data read count

Expected outputs:

- `zipf_alpha_sweep.tsv`
- `unique_ratio_sweep.tsv`
- Per-run `read_summary.tsv`
- Per-run `shape.tsv`

## Q3. Testbed Construction Flexibility

Question:

`F2Load가 다양한 synthetic dataset configuration을 만들 수 있고, 그 결과가 loading뿐 아니라 read/mixed workload 관점에서도 baseline과 유사한가?`

Dataset matrix:

| Factor | Values |
| --- | --- |
| DB size | 500GB, 1TB |
| KV size | 91B, 1024B |
| Distribution | uniform, Zipfian |
| Unique key ratio | 100%, 50% |

Current trace naming:

- `unique100`: 100% unique. Put-only에서는 uniform/Zipfian 구분이 없어 하나로 대표.
- `uniform50`: 50% unique, uniform repeat.
- `zipf99_50`: 50% unique, Zipfian repeat with alpha 0.99.

Loading-side metrics:

- Loading time
- Total disk write
- Write amplification
- Final DB size
- Level별 size distribution
- Level별 SST count
- Average SST size
- Key-range overlap
- Filter/index/data block size

Read/mixed workloads:

- Read binary: `$VCOMP_PROF_DB_BENCH`
- YCSB: actual ported benchmarks `workloada` through `workloadf`.
- MixGraph: `mixgraph`, default paper-like get/put/seek mix.

Read/mixed metrics:

- Throughput
- Average latency
- 50p/95p/99p latency
- Total filter/index/data block reads
- Total I/O request count
- I/O latency
- Mixed-workload read amplification
- Mixed-workload write amplification

Source DB protection:

- Read/mixed runs never open the original loaded DB path.
- `run_q3_read_workloads.sh` stages a temp DB under
  `/work/vcomp/exp/q3_read_tmp` for every workload, including read-only YCSB-C.
- SST files are hard-linked, but mutable metadata files are copied.
- Temp staged DBs are deleted after each run unless `KEEP_RUN_DB=1`.

Scripts:

- Plan: `Q3_TESTBED_FLEXIBILITY.md`
- Matrix generator: `make_q3_read_matrix.py`
- Runner: `run_q3_read_workloads.sh`

First pass:

```bash
cd vcomp/experiments
SYSTEMS="baseline vcomp" \
SIZES_GB="500" \
KV_LABELS="1024B 91B" \
DISTRIBUTIONS="unique100 uniform50 zipf99_50" \
WORKLOADS="workloada workloadb workloadc workloadd workloade workloadf mixgraph" \
DURATION=60 \
THREADS=1 \
CACHE_PCT=0 \
scripts/read/run_q3_read_workloads.sh
```

Current gap:

- Baseline trace DBs exist for 500GB and 1TB.
- Final-design vcomp trace DBs currently exist for 500GB only.
- Full Q3 requires generating the 1TB final-design vcomp trace sweep before
  running the complete read/mixed matrix.

## Execution Order

1. Run A at 1TB for both KV sizes.
2. Run B baseline for 91B sizes from 500GB to 8TB.
3. Run B vcomp for 91B sizes from 500GB to 8TB in one later batch.
4. Reuse motivation 1KB results where sufficient; rerun only missing 1KB points if needed.
5. Run A at 10TB for final scale sanity.
6. Run C sweeps at 250GB.

Rationale:

- A/1TB gives the main correctness and realism anchor.
- B establishes scaling trend and exposes pathological size effects early.
- A/10TB validates that the 1TB conclusions still hold at large scale.
- C is cheaper and should run after the main loading path is stable.

Open decisions:

- 10TB 91B can be mandatory or best-effort depending on runtime and disk pressure.
- Flexibility sweep may not need full baseline for every point. Decide after the first alpha/ratio points.

Open follow-up:

- Trace-generated `unique100` keys currently use affine mapping
  `key_i = (a*i+b) mod domain`. This preserves uniqueness but creates a
  low-byte pattern that makes the radix-sort scatter phase much slower than the
  synthetic RNG path. Observed on 2026-06-07:
  `500GB/1KB synthetic` sort `4.995s` vs `500GB/1024B trace` sort `10.349s`.
  The increase is almost entirely radix scatter (`3.191s -> 8.648s`), especially
  lower-byte passes. Before using trace-based vcomp timings as final numbers,
  revisit trace generation and test a hash/permutation mapping with better
  low-byte randomness while preserving unique-key semantics.
  Raw analysis: `artifacts/log_loads/sort_bottleneck_analysis.tsv`.
