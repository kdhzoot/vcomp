# ADOC 1 TB Fillseq + 10% Overwrite Experiment

**Status:** stopped; this ADOC-artifact run does not match the corrected request

## Question

Measure ADOC-on and its version-matched ADOC-off control when a complete 1 TB
sequential key space is built and then receives random overwrite operations
equal to 10% of the record count.

## Workload semantics

- Key size: 24 B.
- Value size: 1,000 B.
- Logical KV size: 1,024 B.
- Target live key space: 1,000 GiB.
- Record count: 1,048,576,000.
- Phase 1: `fillseq` writes all 1,048,576,000 keys once.
- Phase 2: `overwrite` issues 104,857,600 writes over the same `[0, N)` key
  space.
- Total logical write operations: 1.1 times the live record count, or 1,100
  GiB of logical KV bytes.

RocksDB `db_bench overwrite` samples keys uniformly with replacement. The
second phase therefore performs exactly 10% as many writes as there are live
records, but is expected to touch about 9.516% distinct keys. It does not
select exactly 10% distinct keys.

`db_bench` exposes a single process-wide `--writes` flag, so phase 1 and phase
2 are separate invocations against the same DB. Phase 1 flushes, performs the
same final L0 compaction used by the existing 1 TB runner, and waits for
compaction. Phase 2 reopens the DB and begins with another
`waitforcompaction`, ensuring overwrite starts from a settled reopened state.
The ADOC tuner consequently reinitializes between phases; this boundary is
identical for ADOC-on and ADOC-off.

## Systems and controls

- Run order: ADOC-on, then ADOC-off.
- ADOC-on: `--FEA_enable=true --TEA_enable=true`.
- ADOC-off: `--FEA_enable=false --TEA_enable=false`.
- Both: `--DOTA_enabled=false`, one-second reporter and tuning gap, 48 initial
  background jobs, one subcompaction, one writer thread, batch size one,
  64 MiB initial write buffer, 512 MiB ADOC maximum memtable, vector memtable,
  WAL disabled, no compression, and direct I/O.
- Build: release RocksDB 7.7.0 author artifact, commit
  `5ed60f50d6cd8259b94e7f842ff06c6ab4df40a1`.
- Expected binary SHA-256:
  `68eb52fb2c6db555f47fa877c015015a98ee7ecc879fe3574004c9ec47f47a05`.
- Repetitions: one matched pair for this initial measurement.

## Timing and completion boundary

The primary time is wall-clock time from the start of phase 1 through the end
of phase 2, including the process boundary and the phase-2 pre-overwrite wait.
The runner also records phase wall times and the benchmark-reported `fillseq`
and `overwrite` times separately.

Both write phases must finish their final `waitforcompaction`. After phase 2,
the DB is reopened again, waits for any compaction triggered on reopen, and
must return 10,000 of 10,000 random point reads. Validation time is recorded
separately and is not included in the primary timed interval.

## Evidence and failure criteria

Raw logs, exact commands, binary/runner hashes, commit and patch state,
resource snapshots, `/proc` counters, and iostat are stored below
`experiments/artifacts/log_loads/paper_adoc_fillseq_overwrite_*`.

Fail or stop promotion if any of the following occurs:

- the binary is not the confirmed release build;
- fillseq or overwrite executes a record count different from the requested
  count;
- a write phase does not reach its wait boundary;
- corruption, assertion failure, segmentation fault, OOM, or non-zero exit;
- ADOC-on fails to initialize the tuner in either phase, or ADOC-off initializes
  it;
- the final reopen does not find all 10,000 sampled keys.

## Execution gates

1. Shell syntax and dry-run command audit.
2. One GiB ADOC-on/off pilot covering both phases and reopen validation.
3. Confirm no other `db_bench`, healthy RAID, sufficient memory, and at least
   several TiB of free `/work` space.
4. Start the 1 TB pair in ADOC-on then ADOC-off order.

Runner:
`experiments/scripts/artifact_baselines/run_adoc_fillseq_overwrite.sh`.

## Pilot result (2026-09-01)

The complete 1 GiB ADOC-on/off pilot passed under run ID
`260901_pilot2`. Each system executed 1,048,576 `fillseq` operations and
104,857 `overwrite` operations, reached both wait boundaries, reopened, and
found 10,000 of 10,000 random keys. ADOC-on initialized the tuner in both
phases and logged two FEA and two TEA triggers in total; ADOC-off did not
initialize it. The 27-column summary schema was also validated.

Pilot evidence:
`experiments/artifacts/log_loads/paper_adoc_fillseq_overwrite_1gib_260901_pilot2/`.

## 1 TB run

Run ID `260901_run1` started with ADOC-on at
`2026-09-01T10:42:55Z`. The runner will continue automatically with the 10%
overwrite phase, reopen validation, and ADOC-off. Initial runtime evidence
confirmed 1,048,576,000 entries, the vector memtable, FEAT tuner
initialization, and both FEA and TEA activation.

Run evidence:
`experiments/artifacts/log_loads/paper_adoc_fillseq_overwrite_1000gib_260901_run1/`.

The ADOC-on member completed, but the ADOC-off member was interrupted during
fillseq after the author clarified that the requested system was the separate
clean RocksDB implementation, not the ADOC artifact with its feature flags
disabled. Do not promote this run to the requested figure. The partial DB and
raw evidence are preserved rather than deleted.
