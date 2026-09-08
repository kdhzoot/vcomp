# Baseline 500GB Batch-size Sensitivity

## Question

For the 1KB baseline load, does increasing `db_bench --batch_size` remove
enough per-call write overhead to reduce end-to-end loading time?

## Run

- Run ID: `260730_batch500_1kb_skiplist`
- Target: 500GB, 524,288,000 operations
- Key/value: 24B key + 1000B value
- Compression: none
- Writer threads: 1
- Memtable: `skip_list`
- WAL: disabled
- Background jobs: 48
- Direct I/O: reads, flushes, and compactions
- Raw summary:
  `artifacts/log_loads/baseline_batch_size_500gb_260730_batch500_1kb_skiplist/summary.tsv`

The memtable is intentionally `skip_list`. The historical
`motivation_load_scaling_260602_cleanrocksdb_motivation` commands did not pass
`--memtablerep`, and their logs report `Memtablerep: SkipListFactory`.
The runner now fixes future baseline loads to `vector`; this artifact remains
a historical skip-list comparison.

## Result

| Batch | Fill time | Throughput | DB write calls | DB write path | Write-control wait | Benchmark side | Wait share | Device writes | Compaction writes | W-Amp |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 2,262.0s | 231,777 ops/s | 524,288,000 | 1,358.0s | 733.3s | 170.8s | 32.4% | 6,390GB | 6,170GB | 12.1x |
| 10 | 2,346.5s | 223,433 ops/s | 52,428,800 | 1,057.0s | 1,210.1s | 79.5s | 51.6% | 5,871GB | 5,682GB | 11.2x |
| 100 | 2,329.9s | 225,030 ops/s | 5,242,880 | 1,024.1s | 1,239.8s | 65.9s | 53.2% | 5,878GB | 5,667GB | 11.2x |
| 1000 | 2,353.0s | 222,812 ops/s | 524,288 | 1,042.3s | 1,239.7s | 71.0s | 52.7% | 5,902GB | 5,669GB | 11.2x |

`DB write path` means RocksDB's `DB_WRITE` time minus `STALL_MICROS`.
`Write-control wait` combines slowdown sleeps and hard-stop waits.
`Benchmark side` is `fillrandom wall time - DB_WRITE`.

Per-key critical-path cost:

| Batch | Benchmark side | DB write path | Write-control wait | Total |
|---:|---:|---:|---:|---:|
| 1 | 0.326us | 2.590us | 1.399us | 4.314us |
| 10 | 0.152us | 2.016us | 2.308us | 4.476us |
| 100 | 0.126us | 1.953us | 2.365us | 4.444us |
| 1000 | 0.135us | 1.988us | 2.365us | 4.488us |

## Interpretation

Batching removes measurable foreground overhead but does not improve the
end-to-end load:

- From batch 1 to batch 1000, non-write-control time falls from 1,528.7s to
  1,113.3s, saving 415.4s (27.2%).
- Over the same change, write-control wait rises from 733.3s to 1,239.7s,
  adding 506.4s (69.1%).
- The net fill time therefore increases by 91.0s (4.0%).
- Batch 100 and 1000 have almost identical write-control time. Once per-call
  overhead is amortized, compaction backpressure sets the sustained rate.

The slowdown cause also changes. Batch 1 records 311 L0-file delays and 1,939
pending-compaction-byte delays. Batch 1000 records 733 L0-file delays and 580
pending-byte delays. Larger write bursts shift pressure toward L0
accumulation, while hard stops remain negligible at this scale.

This supports using compaction I/O and foreground backpressure, rather than
single-key API overhead, as the main explanation for the baseline's sustained
loading rate.

## Caveats

- Each batch size has one run, executed in ascending order. The 3--4%
  elapsed-time difference is not statistically significant without repeats.
  The defensible result is that batching produced no observed speedup.
- `db_bench` consumes one RNG value per outer batch even with one DB, so the
  random key streams are distribution-equivalent but not byte-identical.
  All runs nevertheless drop about 164M duplicate keys and finish at
  349--350GB.
- RocksDB's reported ingest includes WriteBatch encoding overhead. Header
  amortization changes reported ingest from 507.81GB at batch 1 to 501.96GB
  at batch 1000 even though all runs execute 524,288,000 logical operations.
- Aggregate compaction worker time is concurrent and must not be stacked into
  the foreground wall-clock breakdown.
