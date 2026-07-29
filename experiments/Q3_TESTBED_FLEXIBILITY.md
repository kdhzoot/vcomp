# Q3. Testbed Construction Flexibility

## Goal

Evaluate whether F2Load can construct synthetic datasets across different
configuration dimensions while preserving both loading-side and read-side
behavior.

## Dataset Sweep

Target matrix:

| Factor | Values |
| --- | --- |
| DB size | 500GB, 1TB |
| KV size | 91B (`key_size=48,value_size=43`), 1024B (`key_size=24,value_size=1000`) |
| Distribution | `uniform`, `zipfian` |
| Unique key ratio | 100%, 50% |

In the generated trace matrix, 100%-unique uniform and 100%-unique zipfian are
equivalent for put-only loading, so they are represented as `unique100`.
The 50%-unique cases are represented as `uniform50` and `zipf99_50`.

## Loading Metrics

- Loading time
- Total disk write
- Write amplification
- Final DB size
- Per-level size distribution
- Per-level SST count
- Average SST size
- Key-range overlap
- Filter/index/data block size

## Read/Mixed Workloads

Read evaluation uses `../vcomp-prof/db_bench`.

`vcomp-prof` already includes the YCSB port from `himeta_perf`, so Q3 calls the
actual YCSB benchmarks directly:

| Paper label | db_bench benchmark | Meaning |
| --- | --- | --- |
| `workloada` | `workloada` | 50% read / 50% update, Zipfian |
| `workloadb` | `workloadb` | 95% read / 5% update, Zipfian |
| `workloadc` | `workloadc` | 100% read, Zipfian |
| `workloadd` | `workloadd` | 95% read / 5% insert, latest |
| `workloade` | `workloade` | 95% scan / 5% insert |
| `workloadf` | `workloadf` | 50% read / 50% read-modify-write |
| `mixgraph` | `mixgraph` | MixGraph get/put/seek workload |

## Source DB Protection

Read/mixed workload runs must never open or modify the original loaded DB.

`run_q3_read_workloads.sh` always stages a temporary DB under
`/work/vcomp/exp/q3_read_tmp` and passes that path to `db_bench`.

- SST files are hard-linked because RocksDB SSTs are immutable.
- MANIFEST, OPTIONS, CURRENT, LOG, LOCK, and other metadata files are copied,
  not linked.
- Mixed workloads may compact/write/delete files only inside the staged DB.
- The staged DB is removed after each run unless `KEEP_RUN_DB=1`.

## Read/Mixed Metrics

- Throughput
- Average latency
- 50p/95p/99p latency
- Total filter block reads
- Total index block reads
- Total data block reads
- Total I/O request count
- I/O latency
- Mixed-workload read amplification
- Mixed-workload write amplification

RocksDB statistics used:

- `rocksdb.block.cache.filter.{hit,miss,bytes.insert}`
- `rocksdb.block.cache.index.{hit,miss,bytes.insert}`
- `rocksdb.block.cache.data.{hit,miss,bytes.insert}`
- `rocksdb.bytes.read`
- `rocksdb.bytes.written`

Device I/O metrics are derived from `/proc/diskstats` deltas.

## Load-Time Write Statistics

Trace loading scripts now extract per-case write statistics under:

```text
<case log dir>/write_stats/
```

Files:

- `write_jobs.tsv`: one row per compaction job.
- `write_outputs.tsv`: one row per vcomp output SST.
- `write_level_summary.tsv`: level-pair aggregate distribution.

At minimum, the extracted vcomp statistics include:

- output SST count
- output bytes
- output entry count
- output key range
- output split boundary position/key
- dedup/drop count and rate
- per-level compaction count
- per-level input/output byte and entry distributions

For baseline RocksDB, `write_jobs.tsv` and `write_level_summary.tsv` are derived
from RocksDB `compaction_started`/`compaction_finished` event logs. Baseline
per-output key ranges and split boundaries are not emitted by the default event
log, so `write_outputs.tsv` is only populated for vcomp unless a separate
compaction trace logger is enabled.

## Scripts

Generate a DB matrix from existing load summaries:

```bash
cd /home/smrc/virtual_compaction/eval-vcomp
python3 make_q3_read_matrix.py
```

Run read/mixed workloads sequentially:

```bash
cd /home/smrc/virtual_compaction/eval-vcomp
./run_q3_read_workloads.sh
```

Useful smaller first pass:

```bash
SYSTEMS="baseline vcomp" \
SIZES_GB="500" \
KV_LABELS="1024B 91B" \
DISTRIBUTIONS="unique100 uniform50 zipf99_50" \
WORKLOADS="workloada workloadb workloadc workloadd workloade workloadf mixgraph" \
DURATION=60 \
THREADS=1 \
CACHE_PCT=0 \
./run_q3_read_workloads.sh
```

## Current Availability

Existing baseline trace DBs cover 500GB and 1TB for both KV sizes and all three
trace cases.

Current final-design vcomp trace DBs cover 500GB for both KV sizes and all
three trace cases:

- `kmv512_final_sweep500_1024b_traceparallel8_260608_200650`
- `kmv512_final_sweep500_91b_traceparallel8_260608_195813`

The 1TB final-design vcomp trace sweep still needs to be generated before the
full Q3 matrix is complete.
