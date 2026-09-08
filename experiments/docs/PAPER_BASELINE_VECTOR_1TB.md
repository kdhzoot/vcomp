# Clean RocksDB Vector-Memtable Baseline for Figure 3

**Status:** Completed, validated, and promoted to the Background loading-time figure on 2026-09-02.

## Objective

Replace the provisional Figure 3 Baseline measurement that used RocksDB's
default skip-list memtable with one clean RocksDB measurement using a vector
memtable. Preserve the historical 4,889 s SkipList result and its raw evidence;
do not overwrite or delete it.

The rerun is needed because the current ADOC, last-compaction, fillseq plus
overwrite, and F2Load commands explicitly use a vector memtable, while the
existing Baseline log reports `Memtablerep: SkipListFactory`.

## Fixed configuration

- Engine: clean RocksDB 11.1.0 at commit
  `f455ab7bd6a8c67f00d48075bb310f131d9fae5f`.
- Expected release `db_bench` SHA-256:
  `8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b`.
- Logical dataset: 1,000 GiB.
- Record count: 1,048,576,000.
- Key/value size: 24 B / 1,000 B.
- Workload: `fillrandom,flush,compact0,waitforcompaction,stats,levelstats`.
- Writer threads and batch size: 1 and 1.
- Memtable: `--memtablerep=vector`.
- Background jobs and subcompaction cap: 48 and 1.
- WAL: disabled.
- Compression: none.
- Direct reads and direct I/O for flush/compaction: enabled.
- Bloom bits: 10; index compression: disabled.
- Seed: 12345678.

Use the existing `experiments/scripts/load/load.sh` baseline path so that the
generated command and monitoring layout remain consistent with the other
clean-RocksDB runs. Do not add ADOC, F2Load, Titan, or DiffKV flags.

## Existing qualification evidence

An exact clean-RocksDB vector-memtable baseline with the same binary, 24 B /
1,000 B records, 48 background jobs, subcompaction cap 1, direct I/O, disabled
WAL, no compression, and the same completion sequence already passed at
500 GiB on 2026-08-25. It completed in 2,314 s with zero swap, zero pending
compaction bytes, and 10,000/10,000 successful reopen reads. Therefore no new
capacity pilot is required unless the binary or command changes.

Qualification result:
`experiments/results/paper_clean_rocksdb_subcompaction_500gb_260825.tsv`.

## Executed invocation

```bash
DRY_RUN=0 TARGET_GIB=1000 RUN_ID=260902_direct_run1 \
DB_DIR=/work/vcomp/exp/paper_clean_vector_1000gib_260902_direct_run1 \
bash experiments/scripts/artifact_baselines/run_clean_vector_baseline_load.sh
```

The dedicated runner verifies the source commit and binary hash before calling
the shared load harness.

## Pre-run and validation gates

1. Confirm no `db_bench`, `titandb_bench`, or YCSB load is active and that
   `/dev/md0` is healthy and idle.
2. Confirm sufficient free space for the roughly 768 GiB final database plus
   transient files; do not remove existing databases without explicit approval.
3. Verify the RocksDB commit, release binary hash, and generated command before
   entering the timed region.
4. Require successful `fillrandom`, explicit flush, `compact0`, and
   `waitforcompaction`; final pending compaction bytes must be zero.
5. Record elapsed time, fill time, peak RSS, swap, device writes, final DB size,
   compaction statistics, and level shape.
6. Reopen separately and require 10,000/10,000 sampled random reads.
7. Reject the result on non-zero exit, corruption/assertion/OOM evidence,
   binary or command drift, nonzero benchmark swap, missing keys, or unsettled
   background work.

## Figure promotion rule

Only after every gate passes, change the Figure 3 Baseline source to the new
vector run and regenerate the plot. Keep the SkipList measurement as historical
evidence and label it superseded rather than deleting it. The provisional
figure may use one validated run because its other bars are also single runs;
a repetition-based final claim requires additional independent runs.

DiffKV remains an artifact-native skip-list exception because Titan warns that
its GC path performs poorly with a vector memtable. That exception must be
disclosed separately and does not change this clean-RocksDB baseline plan.

## Completed result

- Timed load: 3,472 s (57.8667 min).
- Peak RSS: 6,121,096 KB.
- Final DB size: 824,424,679,045 bytes.
- Completion boundary: `compact0` followed by successful
  `waitforcompaction`; reopen validation took 9 s.
- Correctness: 10,000 of 10,000 sampled reads found after reopen.
- Effective I/O: direct reads and direct flush/compaction I/O both enabled.
- Validated summary:
  `experiments/artifacts/log_loads/paper_clean_vector_1000gib_260902_direct_run1/validated_summary.tsv`.

The former 4,889 s (81.4833 min) Figure baseline used the default SkipList
memtable. Its raw artifact remains at
`experiments/artifacts/log_loads/motivation_load_scaling_260602_cleanrocksdb_motivation/baseline_1000gb/bench.out`
and is retained as superseded historical evidence.
