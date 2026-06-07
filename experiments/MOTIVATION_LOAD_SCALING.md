# Motivation Experiments

## Goal

Motivation은 세 가지 결과로 정리한다.

1. 1KB value, no-compression에서 dataset size 증가에 따른 baseline loading time과 total write 증가.
2. 500GB 고정 DB size에서 value size와 compression 조합별 loading time 비교.
3. 2번과 동일한 네 번의 실행에서 나온 single-compaction breakdown 비교.

공통 원칙:

- 실행은 항상 sequential: 동시에 두 개의 `db_bench`를 돌리지 않는다.
- size-scaling baseline은 clean RocksDB `../rocksdb/db_bench` 사용.
- KV/compression + breakdown 실험은 timer가 들어간 `../vcomp-prof/db_bench` 사용.
- key size는 24B.
- write thread는 1개.
- memtable representation은 `vector`.
- WAL disabled.
- direct I/O enabled for read/flush/compaction.
- Bloom bits 10.
- index compression disabled.

## Experiment 1: Size Scaling

질문:

`1KB no-compress baseline loading에서 dataset size가 커질수록 loading time과 total write가 어떻게 증가하는가?`

설정:

- value size: 1000B
- compression: `none`
- sizes: 500GB, 1TB, 2TB, 4TB, 8TB
- script: `./run_motivation_load_scaling.sh`
- binary default: `../rocksdb/db_bench`

실행:

```bash
cd eval-vcomp
RUN_ID=260602_motivation BG_JOBS=48 SIZES_GB="500 1000 2000 4000 8000" \
  ./run_motivation_load_scaling.sh
```

주요 output:

- `log_loads/motivation_load_scaling_<RUN_ID>/summary.tsv`
- loading elapsed time
- `fillrandom` time
- DB size
- total device write GB
- ingest GB
- compaction write GB
- compaction write amplification

현재 결과:

`log_loads/motivation_load_scaling_260602_cleanrocksdb_motivation/summary.tsv`

## Experiment 2: KV Size and Compression

질문:

`500GB loading time은 value size와 compression 여부에 따라 얼마나 바뀌는가?`

설정:

- target logical DB size: 500GB
- script: `./run_motivation_kv_compression_matrix.sh`
- binary default: `../vcomp-prof/db_bench`

네 가지 조합:

| Case | Value size | Compression |
| --- | ---: | --- |
| `kv1000_nocompress` | 1000B | `none` |
| `kv1000_snappy` | 1000B | `snappy` |
| `kv91_nocompress` | 91B | `none` |
| `kv91_snappy` | 91B | `snappy` |

실행:

```bash
cd eval-vcomp
RUN_ID=260604_kv_compression BG_JOBS=48 TARGET_DB_GB=500 \
  ./run_motivation_kv_compression_matrix.sh
```

주요 output:

- `log_loads/motivation_kv_compression_<RUN_ID>/summary.tsv`
- 각 case별 loading elapsed time
- `fillrandom` time
- final DB size
- total device write GB
- ingest GB
- compaction write GB
- compaction W-amp

## Experiment 3: Single-Compaction Breakdown

질문:

`Experiment 2의 네 가지 조합에서 대표 single compaction의 phase별 시간이 어떻게 다른가?`

중요:

- 별도로 다시 로딩하지 않는다.
- `run_motivation_kv_compression_matrix.sh`가 네 번의 loading을 실행하면서 DB `LOG*`에서 `VCOMP_PERF_COMPACTION_BREAKDOWN` 라인을 추출한다.
- representative compaction은 각 case에서 `input_bytes`가 가장 큰 compaction으로 고른다. 동률이면 `total_tracked_us`가 큰 compaction을 사용한다.

`vcomp-prof`에서 출력하는 breakdown line:

`VCOMP_PERF_COMPACTION_BREAKDOWN key=value ...`

논문식 category:

- `read_us`
- `write_us`
- `merge_us`
- `compress_us`
- `decompress_us`
- `sst_build_us`
- `other_us`

보조 RocksDB phase:

- `prepare_us`
- `run_subcompactions_us`
- `subcompaction_setup_us`
- `process_kv_us`
- `process_kv_excl_output_us`
- `open_output_us`
- `finish_output_us`
- `sync_dirs_us`
- `verify_output_us`
- `input_stats_us`
- `install_edit_us`
- `log_and_apply_us`
- `install_total_us`

주요 output:

- 전체 compaction: `log_loads/motivation_kv_compression_<RUN_ID>/compaction_breakdown_all.tsv`
- 대표 compaction 4개: `log_loads/motivation_kv_compression_<RUN_ID>/compaction_breakdown_representative.tsv`
- case별 raw/parsed breakdown: `log_loads/motivation_kv_compression_<RUN_ID>/<case>/raw/`

### Planned Correction: True 91B Breakdown

The original `kv91_*` profiling run used `key_size=24,value_size=91`
(115B total). Loading-time summary rows have been replaced with the corrected
true-91B clean RocksDB runs (`key_size=48,value_size=43`), but those runs do
not include `VCOMP_PERF_COMPACTION_BREAKDOWN`.

Run this after current baseline jobs finish:

```bash
cd eval-vcomp
./run_true91b_breakdown_matrix.sh
```

This runs only:

- `kv91_nocompress`: `key_size=48`, `value_size=43`, `compression=none`
- `kv91_snappy`: `key_size=48`, `value_size=43`, `compression=snappy`

Outputs are written under
`log_loads/motivation_kv_compression_true91b_breakdown_<timestamp>/`.
After completion, use its `kv91_*` rows to repopulate the motivation matrix
breakdown TSVs.

## Monitoring

```bash
tail -f log_loads/<experiment_dir>/run.log
cat log_loads/<experiment_dir>/summary.tsv
ps -o pid,etime,pcpu,pmem,stat,cmd -C db_bench
df -h /work
```

## Safety

- scripts refuse to start if another `db_bench` is running.
- existing DB directories are not overwritten.
- page cache is dropped before each run when permission allows it.
