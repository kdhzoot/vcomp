# Fillrandom SST Coverage: 100 GiB to 1,000 GiB

## Purpose

Measure how the SST key-range layout produced by natural RocksDB loading
changes with database scale under a fixed uniform-random write distribution.
Retain every database so its per-level SST ranges can be analyzed after all
loads finish.

## Configuration

- Systems: clean baseline RocksDB only
- RocksDB version: 11.1.0
- RocksDB commit: `cae42cd895f3bdeaaed1e58c0d27e2755b6ad308`
- `db_bench` SHA-256:
  `289f4783ed194761c98e740b9def72a68950c2d31bd172804f10fbd30e314259`
- Workload: `fillrandom,flush,compact0,waitforcompaction,stats,levelstats`
- Logical sizes: 100, 200, 300, 400, 500, 600, 700, 800, 900, and 1,000 GiB
- Key/value: 24 B key + 1,000 B value = 1,024 B
- Distribution: RocksDB `fillrandom`, seed `12345678`
- Write threads: 1
- Memtable: vector
- Compression: none
- WAL: disabled
- Direct reads and direct flush/compaction I/O: enabled
- Background jobs: 48
- Subcompactions: 1 (key-range subcompaction disabled)
- Execution: sequential, with no concurrent `db_bench`

`subcompactions=1` disables compaction splitting; normal leveled compaction
remains enabled.

## Execution

Run ID:

```text
260827_fillrandom_100to1000_sub1_r1
```

Runner invocation:

```bash
SIZES_GB="100 200 300 400 500 600 700 800 900 1000" \
RUN_ID=260827_fillrandom_100to1000_sub1_r1 \
DB_ROOT=/work/vcomp/exp/fillrandom_sst_coverage_100to1000_sub1_r1 \
LOG_ROOT=/home/smrc/virtual_compaction/vcomp/experiments/artifacts/log_loads/fillrandom_sst_coverage_260827_100to1000_sub1_r1 \
DB_BENCH=/home/smrc/virtual_compaction/rocksdb/db_bench \
SUBCOMPACTIONS=1 BG_JOBS=48 DISKSTAT_DEV=md0 \
bash experiments/scripts/load/run_motivation_load_scaling.sh
```

## Outputs and validation

- Raw logs and summary:
  `experiments/artifacts/log_loads/fillrandom_sst_coverage_260827_100to1000_sub1_r1/`
- Retained databases:
  `/work/vcomp/exp/fillrandom_sst_coverage_100to1000_sub1_r1/`
- Each run must finish `fillrandom`, explicit flush, compaction settling,
  `stats`, and `levelstats` successfully.
- The generated command must contain `--subcompactions=1`,
  `--key_size=24`, and `--value_size=1000`.
- Start/end diskstats and elapsed-time files must be present and non-empty.
- No other `db_bench` may overlap a measurement.
- After completion, extract SST smallest/largest keys and level membership
  from each retained DB for the key-coverage comparison.

Expected total loading time is approximately 7.5--8 hours based on the
existing 500 GiB and 1,000 GiB cap-1 measurements. This estimate excludes the
post-load SST-range analysis.
