# Motivation Experiment: Dataset Size vs. Loading Time and WAF

## Goal

Measure how RocksDB baseline loading cost grows as dataset size increases.

Experiment title:

`Increasing dataset size increases loading time and write amplification`

## Scope

- Mode: baseline only
- Workload: `fillrandom`
- Dataset sizes: 500GB, 1TB, 2TB, 4TB, 8TB
- Execution policy: strictly sequential; only one `db_bench` run at a time
- Script: `./run_motivation_load_scaling.sh`
- Base loader: `./load.sh`

## Metrics

Primary metrics:

- End-to-end loading time from `raw/elapsed_sec.txt`
- RocksDB benchmark time from `bench.out`
- Compaction WAF from final compaction stats
- Total write amplification estimate from compaction write GB and ingest GB

Raw data collected by `load.sh`:

- `bench.out`
- `report.rep`
- `raw/diskstats.start`
- `raw/diskstats.end`
- `raw/procstat.start`
- `raw/procstat.end`
- `raw/iostat.log` when `iostat` is available
- `raw/load_cmd.sh`

## Output Layout

The script creates one experiment directory under:

`log_loads/motivation_load_scaling_<RUN_ID>/`

Each size gets one subdirectory:

`log_loads/motivation_load_scaling_<RUN_ID>/baseline_<SIZE>gb/`

DB directories are created under:

`/work/vcomp/motivation_baseline_<SIZE>gb_<RUN_ID>/`

The script also writes:

- `summary.tsv`: one row per completed or failed size
- `run.log`: high-level progress log

## Run

From `eval-vcomp/`:

```bash
nohup ./run_motivation_load_scaling.sh > motivation_load_scaling.nohup.out 2>&1 &
```

Optional environment variables:

```bash
DB_ROOT=/work/vcomp
BG_JOBS=48
RUN_ID=260602_motivation
SIZES_GB="500 1000 2000 4000 8000"
```

Example:

```bash
RUN_ID=260602_motivation BG_JOBS=48 ./run_motivation_load_scaling.sh
```

## Monitor

```bash
tail -f log_loads/motivation_load_scaling_<RUN_ID>/run.log
cat log_loads/motivation_load_scaling_<RUN_ID>/summary.tsv
ps -o pid,etime,pcpu,pmem,stat,cmd -C db_bench
df -h /work
```

## Notes

- Do not run vcomp or another baseline load at the same time.
- The script refuses to start if `db_bench` is already running.
- Existing DB directories are not overwritten.
- `load.sh` drops page cache before each run when permission allows it.
