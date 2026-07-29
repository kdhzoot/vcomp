# Virtual Compaction Experiments

This directory contains the runners, analysis code, documentation, and curated
results used to compare this virtual-compaction implementation with baseline
RocksDB.

## Layout

- `scripts/`: workload runners grouped by load, read, trace, and profiling.
- `analysis/`: parsers, aggregation scripts, and plotting tools.
- `docs/`: experiment plans, runbooks, and historical reports.
- `results/`: small curated tables, figures, and source samples tracked by Git.
- `artifacts/`: raw logs and per-run outputs. This directory is ignored by Git.
- `lib/common.sh`: shared repository, binary, database, and artifact locations.

Large databases and traces remain under `/work/vcomp` by default. Override any
location without editing a runner:

```bash
export VCOMP_DB_BENCH="$PWD/db_bench"
export BASELINE_DB_BENCH="$PWD/../rocksdb/db_bench"
export VCOMP_PROF_DB_BENCH="$PWD/../vcomp-prof/db_bench"
export VCOMP_DB_ROOT=/work/vcomp
export VCOMP_ARTIFACT_ROOT="$PWD/experiments/artifacts"
```

Run scripts from any working directory. For example:

```bash
MODE=vcomp TARGET_DB_GB=1 DB_ROOT=/work/vcomp/exp \
  experiments/scripts/load/load.sh

WORKLOAD=readrandom DB_DIR=/work/vcomp/vcomp_1000gb DB_SIZE_GB=1000 \
  experiments/scripts/read/run.sh
```

Promote only stable summaries and figures from `artifacts/` into `results/`.
Every promoted result should record the vcomp commit and the command or runner
configuration that produced it.
