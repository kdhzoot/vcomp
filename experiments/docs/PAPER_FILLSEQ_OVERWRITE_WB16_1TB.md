# Fillseq + 10% Overwrite with 16 Write Buffers at 1 TiB

**Status:** Completed and validated on 2026-09-03.

## Question

How does the fixed Figure 4 two-phase Fillseq + 10% Overwrite workload change
when only `max_write_buffer_number` is increased from its effective default of
2 to 16?

## Paired control

Use the exact binary and workload of the completed two-buffer run:

- Clean RocksDB 11.1.0 commit
  `cae42cd895f3bdeaaed1e58c0d27e2755b6ad308`.
- Release `db_bench` SHA-256
  `289f4783ed194761c98e740b9def72a68950c2d31bd172804f10fbd30e314259`.
- Existing control artifact:
  `paper_clean_rocksdb_fillseq_overwrite_1000gib_260901_run1`.
- Existing control result: 2,014 seconds total: 1,543 seconds for phase 1
  and 471 seconds for phase 2.

No version migration or concurrent-memtable policy change is allowed in this
paired sensitivity. The sole treatment is
`max_write_buffer_number=2 -> 16`, applied explicitly to both phases and to
the reopen command. Keep `write_buffer_size=67108864` and
`min_write_buffer_number_to_merge=1`.

## Workload and boundaries

1. Phase 1 writes all 1,048,576,000 records with `fillseq`, then runs
   `flush,compact0,waitforcompaction` and records the settled boundary.
2. Phase 2 reopens the same DB and issues exactly 104,857,600 random
   `overwrite` operations over the unchanged `[0, N)` key space, then runs
   `flush,compact0,waitforcompaction` and records the second settled boundary.
3. A separate reopen must settle and find 10,000/10,000 sampled random keys.

Common settings remain 24 B keys, 1,000 B values, one writer, batch size one,
VectorRep, concurrent memtable writes disabled, 48 background jobs, one
subcompaction, seed 12345678, WAL disabled, compression disabled, Bloom 10,
index compression disabled, and direct reads plus direct flush/compaction I/O.

## Execution plan

1. Parameterize the existing
   `run_clean_rocksdb_fillseq_overwrite.sh` runner with
   `MAX_WRITE_BUFFER_NUMBER` and `MIN_WRITE_BUFFER_NUMBER_TO_MERGE`; default
   them to 2 and 1 so historical behavior is preserved.
2. Add both flags to phase 1, phase 2, and reopen commands. Snapshot and audit
   the effective OPTIONS state at each phase boundary.
3. Run `bash -n`, `git diff --check`, and an exact dry run. Confirm that the
   only effective configuration change against the control commands is the
   buffer count.
4. When no other benchmark or benchmark suite is active, run one complete
   1 GiB pilot with a fresh DB. Require exact operation counts, both settled
   boundaries, and 1,000/1,000 reopen reads.
5. Only after the pilot passes, run one 1,000 GiB measurement in the
   background. Preserve its DB for later read workloads.

Reserved identifiers for the eventual run:

- Run ID: `paper_clean_fillseq_overwrite_wb16_1000gib_run1`
- Logs:
  `experiments/artifacts/log_loads/paper_clean_fillseq_overwrite_wb16_1000gib_run1/`
- DB:
  `/work/vcomp/exp/paper_clean_fillseq_overwrite_wb16_1000gib_run1`

These are reservations only; no directory, DB, or background process should
be created during planning.

## Measurements and acceptance

The primary metric is combined phase-1 plus phase-2 wall time, matching the
2,014-second control. Also retain each phase's wall and `db_bench` time,
throughput, memtable/L0/pending-byte delay and stop counts, total stall time,
compaction count/time/read/write bytes, device writes and input-normalized
WAF, peak RSS, final DB size, and reopen result.

The phase-1 result should be checked against the completed standalone
Fillseq-16 result (1,257 seconds total, 1,250.636-second benchmark phase). A
material disagreement is an interference or configuration-drift signal, not
something to average away.

Reject the run for benchmark overlap, binary/configuration drift, nonzero
exit, incorrect operation counts, a missing settled boundary, corruption,
assertion/OOM evidence, nonzero final pending compaction, or missing sampled
reads. Do not update Figure 4 until the result is reviewed and the author
chooses whether the entire figure adopts the 16-buffer sensitivity setting.

## Hypothesis, not a result

The two-buffer control recorded 15,738 memtable-limit stops totaling 239.478
seconds in phase 1, and 2,036 memtable-limit stops with 293.145 seconds of
total write-stall time in phase 2. The 16-buffer treatment is expected to
remove or sharply reduce those stops. It should not be described as reducing
I/O amplification unless the measured compaction and device-write counters
also decrease.

## Result

The full two-phase load took 1,745 seconds (29.08 minutes): 1,272 seconds for
Fillseq and 473 seconds for the 10% overwrite phase. The benchmark-local times
were 1,261.282 and 447.438 seconds. Both phase-local waits settled, and the
final reopen found 10,000/10,000 sampled keys.

Against the two-buffer control, combined time fell from 2,014 to 1,745 seconds
(13.36%, 1.154x speedup). The preserved DB is
`/work/vcomp/exp/paper_clean_fillseq_overwrite_wb16_1000gib_260903_run1/fillseq_overwrite_1000gib`.
