# Clean RocksDB 1 TB Fillseq + 10% Overwrite Experiment

**Status:** 1 GiB pilot and clean RocksDB 1 TB run completed

## Corrected objective

Run the two-phase workload on the separate clean RocksDB implementation, with
no ADOC code or feature flags involved:

1. populate the complete 1 TB key space using `fillseq`;
2. issue random overwrite operations equal to 10% of the record count over the
   same key space.

This supersedes the mistakenly launched ADOC-artifact pair documented in
`PAPER_ADOC_FILLSEQ_OVERWRITE_1TB.md`.

## Workload

- Clean implementation: `/home/smrc/virtual_compaction/rocksdb`.
- RocksDB version: 11.1.0.
- Git commit: `cae42cd895f3bdeaaed1e58c0d27e2755b6ad308`.
- Release binary SHA-256:
  `289f4783ed194761c98e740b9def72a68950c2d31bd172804f10fbd30e314259`.
- Key/value size: 24 B / 1,000 B, or 1,024 B logical per record.
- Target records: 1,048,576,000.
- Fillseq operations: 1,048,576,000.
- Overwrite operations: 104,857,600 over the same `[0, N)` key space.
- Final live logical key space: 1,000 GiB.
- Total logical writes: 1,100 GiB.

The overwrite count is exactly 10% of the record count, but random selection
is with replacement and therefore touches about 9.516% distinct keys in
expectation.

## Configuration and boundaries

Use the existing clean release binary and the established 1 TB baseline
settings: 48 background jobs, one subcompaction, disabled WAL, no compression,
direct I/O, 24 B keys, 1,000 B values, and seed 12345678. Retain the author's
explicit vector-memtable requirement with one writer, batch size one, a 64 MiB
write buffer, and concurrent memtable writes disabled.

Because `--writes` is process-wide, fillseq and overwrite run as separate
`db_bench` processes. Phase 1 flushes, performs `compact0`, and waits. Phase 2
reopens the same DB, begins with another wait, performs overwrite, flushes,
performs `compact0`, and waits. Primary elapsed time covers both phases and the
process boundary. A final reopen waits again and must find 10,000 of 10,000
random keys; validation time is recorded separately.

## Gates and evidence

1. Shell syntax and exact dry-run audit.
2. One GiB full-pipeline pilot.
3. Confirm release hash, no competing db_bench, healthy RAID, and storage.
4. Run one 1 TB measurement.

Failure criteria include wrong operation counts, a missing settled boundary,
non-zero exit, corruption/assertion/OOM patterns, binary hash mismatch, or any
missing key in the final sampled reads.

Runner:
`experiments/scripts/artifact_baselines/run_clean_rocksdb_fillseq_overwrite.sh`.

Outputs:
`experiments/artifacts/log_loads/paper_clean_rocksdb_fillseq_overwrite_*`.

## Pilot result (2026-09-01)

Run `260901_clean_pilot1` passed the complete 1 GiB pipeline. It executed
1,048,576 fillseq operations and 104,857 overwrite operations, reported both
settled boundaries, reopened, and found 10,000 of 10,000 keys. Runtime logs
confirmed `VectorRepFactory` and contained no ADOC tuner markers. The
19-column summary schema was validated.

Pilot evidence:
`experiments/artifacts/log_loads/paper_clean_rocksdb_fillseq_overwrite_1gib_260901_clean_pilot1/`.

## 1 TB run

Run ID `260901_run1` started at `2026-09-01T11:18:59Z` using the clean
RocksDB 11.1.0 binary. The exact dry run contained no ADOC, FEA, TEA, or DOTA
flags.

Run evidence:
`experiments/artifacts/log_loads/paper_clean_rocksdb_fillseq_overwrite_1000gib_260901_run1/`.

The run completed at `2026-09-01T11:52:42Z`. Fillseq took 1,543 s wall time
(1,536.611 s reported by `db_bench`), and the 10% overwrite phase took 471 s
wall time (444.558 s reported by `db_bench`). The Figure 3 measurement is the
2,014 s combined wall time, or 33.57 minutes. Both phase-local
`waitforcompaction` operations reported `finished`; the final database occupied
1,195,654,258,131 bytes, peak RSS was 8,279,636 KB, and a separate reopen
validation found 10,000 of 10,000 sampled keys. The empty
`validation_errors.txt` and successful completion marker satisfy the planned
promotion gates.
