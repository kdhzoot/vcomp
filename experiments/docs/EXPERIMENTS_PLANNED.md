# Planned Experiments

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
