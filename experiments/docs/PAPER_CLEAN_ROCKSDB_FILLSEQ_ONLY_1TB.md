# Clean RocksDB 1 TB Fillseq-Only Database

**Status:** Completed and validated on 2026-09-02.

- DB: `/work/vcomp/paper_clean_rocksdb_fillseq_only_1000gib_260902_run1`
- Evidence: `experiments/artifacts/log_loads/paper_clean_rocksdb_fillseq_only_1000gib_260902_run1`
- Result: 1,578 s wall time (26.3 min); `fillseq` itself reported 1,572.213 s.
- Validation: final `waitforcompaction` succeeded and reopen found 10,000 of
  10,000 requested keys.
- Final DB bytes: 1,090,892,486,821.

## Objective

Create and preserve a clean RocksDB database populated only by `fillseq`,
separately from the existing `fillseq` plus 10% random-overwrite database. The
database is intended both as a loading-time measurement and as an immutable
starting state for later read workloads.

## Configuration

- Clean RocksDB 11.1.0 release binary, SHA-256
  `289f4783ed194761c98e740b9def72a68950c2d31bd172804f10fbd30e314259`.
- 1,048,576,000 records, 24 B keys, and 1,000 B values (1,000 GiB logical).
- One writer, batch size one, vector memtable, 64 MiB write buffer.
- 48 background jobs and subcompaction cap one.
- WAL disabled, compression disabled, Bloom filter 10 bits/key, and index
  compression disabled.
- Direct reads and direct I/O for flush and compaction enabled.
- Seed `12345678`.
- Completion boundary:
  `fillseq,flush,compact0,waitforcompaction,stats,levelstats`.

The configuration is identical to phase 1 of the validated 1 TB
`fillseq`+10%-overwrite experiment. That phase completed successfully and is
the capacity qualification for this run; no new pilot is required.

## Validation and preservation

The timed command must exit successfully, execute exactly 1,048,576,000
`fillseq` operations, and finish its final `waitforcompaction`. A separate
reopen validation must settle successfully and find 10,000 of 10,000 random
keys. Fatal, corruption, assertion, OOM, binary-drift, operation-count, or
missing-key evidence rejects the run.

The DB directory must not be deleted or reused for writes after validation.
Later read workloads should reopen it with `--use_existing_db=true` and use a
separate log directory per workload.

Runner:
`experiments/scripts/artifact_baselines/run_clean_rocksdb_fillseq_only.sh`.

Outputs:
`experiments/artifacts/log_loads/paper_clean_rocksdb_fillseq_only_*`.

The promoted Figure 4 value is recorded in
`experiments/results/paper_figure4_loading_time_1tb_single.tsv` as `Fillseq`.
