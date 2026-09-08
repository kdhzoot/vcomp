# 100 GiB parallel cardinality reproduction

The requested experiment reproduces the old dataset configuration sweep at
100 GiB of logical input per case. Its primary measurement is the exact number
of visible unique keys in the final DB. Loading times from this concurrent run
are operational records and must not be used as performance comparisons.

## Matrix and counts

| Key + value | Input operations | unique100 expected unique | uniform50 / zipf99_50 expected unique |
|---|---:|---:|---:|
| 24 + 1000 B | 104,857,600 | 104,857,600 | 52,428,800 |
| 48 + 43 B | 1,179,936,070 | 1,179,936,070 | 589,968,035 |

Each KV pair uses unique100, uniform50, and zipf99_50 (alpha 0.99). Six trace
files are generated using seed 12345678, explicit unique count, and key domain
equal to the operation count. The verifier reads every trace record and counts
distinct key IDs before any load starts. Each trace is shared by a baseline
and F2Load load, giving twelve DBs. A barrier releases all twelve load workers.

The baseline uses `vcomp/db_bench baseload` with virtual compaction disabled,
the same ordinary RocksDB WriteBatch/Write path as the historical trace sweep.
The sibling clean `rocksdb/db_bench` is used only for final physical settling.
Both binaries and the generator/verifier/scanner are frozen into the run's
ignored artifact directory, together with hashes, source revisions, diffs,
and exact commands. The profiler binary is not part of this comparison.

The shared settings are one foreground thread, batch size one, vector memtable,
64 MiB write buffer, two write buffers, 48 background jobs, one subcompaction,
static level sizing (256 MiB base, multiplier ten), 64 MiB target SST,
format six, direct I/O, no compression, and WAL disabled. F2Load uses PLR error
eight, 64 MiB virtual flush, registration batch 256, visible window zero,
eight phase-one shards, 48 materialization workers, and both sort-detail and
LogAndApply timing explicitly off.
Concurrent scheduling can affect F2Load's approximate compaction outcomes;
retain this execution mode as part of each result's configuration.

The runtime source audit found three algorithm/scheduling environment controls:
`VCOMP_KMV_ENABLED`, `VCOMP_BG_COMMIT_BATCH_MAX`, and `VCOMP_BG_COMMIT_DELAY_US`.
Each child receives explicit values `1`, `16`, and `100` respectively, matching
the current defaults. All inherited `VCOMP_*` variables are removed first,
including the presence-triggered `VCOMP_COV_PERFILE` coverage-output toggle.
Historical wrapper variables are represented by explicit command flags rather
than inherited shell settings. The manifest and per-process `runtime.json`
record the effective controls, audited inherited values, and removed names.

Before spawning any child, the runner records inherited `RLIMIT_NOFILE` soft
and hard limits and requires at least 65,536 descriptors per process. If the
soft limit is lower and the hard limit permits it, the runner raises only the
soft limit to that floor; otherwise preparation fails. The effective limits
are saved with the runtime provenance. This server currently reports
1,048,576 for both soft and hard limits.

## Execution and storage gate

DBs and traces must remain on the mounted `/work` filesystem. There is no
root-filesystem fallback. Before either phase generates traces, the runner
requires successful buffered and direct writes, fsync, and complete readback
of two four-MiB probe files. Probe files and JSON evidence remain in the run.
A failed probe stops the campaign before workloads. No cache dropping or
automatic DB deletion is performed.

Before storage recovery on 2026-09-08, independent buffered-fsync and direct-write
probes on `/work` returned EIO, including in a newly created directory. At that
time `/sys/block/md0/md/array_state` reported `broken`, with an absent `nvme2n1`
member, and the campaign was blocked. These retained failures document the
pre-recovery storage qualification; they are not fidelity measurements or the
current campaign state.

After `/work` was restored, filesystem checking returned zero, the sampled
55 metadata files and 33 SST files remained unchanged, and buffered/direct
write, fsync, and readback qualification passed. The new
`fidelity_100gib_20260908_run2` campaign then ran its pilot and full matrix.
By 2026-09-08 12:10 KST (03:10 UTC), all twelve 100-GiB loads, clean settling
steps, and iterator scans had finished. All six baselines passed validation;
all six F2Load cases failed the strict-increasing iterator-key check. Finished
processes therefore do not imply a successfully validated full phase. The
separate read-only audit described below is still in progress.

From the vcomp repository, after storage repair and the instrumented release
binary plus helper binaries have been built:

```bash
python3 experiments/scripts/trace/run_fidelity_dataset_sweep.py \
  --run-id fidelity_100gib_YYYYMMDD_run1 --phase all
```

The default DB/trace directory is `/work/vcomp/exp/<run-id>` and the raw evidence
directory is `experiments/artifacts/<run-id>`. The default `all` first runs the
complete twelve-load matrix at one GiB, verifies all endpoints, then starts
the twelve-load matrix at 100 GiB. Use `--phase pilot` to qualify separately;
`--phase full` subsequently requires the same run ID, a completed pilot,
unchanged frozen binaries and runner, and an unused full output directory.
The runner never overwrites a prior phase or resumes an incomplete DB load.
Use a fresh run ID after a failed attempt; retain the failed attempt's evidence.

## Validation and interpretation

For F2Load, `fidelity/fidelity.json` and `files.tsv` record:

1. The live virtual descriptor entry sum immediately before materialization.
2. The actual SST entries materialized, including a separate live-file total.
3. The count returned by a full read-only DB iterator scan after a clean
   RocksDB reopen and physical compaction settling. This is a visible unique
   count only when the iterator's strict-increasing key check passes.

The first two values count entries per file; they are not global unique counts.
Their difference locates materialization loss. Materialized-entry excess over
visible unique keys includes cross-level duplicate versions and synthetic
collisions; those causes cannot be distinguished by subtraction alone.

Every load must finish its benchmark and compaction wait successfully. F2Load
must also report drained virtual L0, a complete descriptor snapshot, successful
SST materialization, applied VersionEdit, and successful final MANIFEST snapshot.
Both systems then reopen with the clean binary, wait for compaction, require
zero pending compaction bytes, and undergo a full exact scan. The scanner checks
iterator status, key ordering/encoding/domain, value lengths, and per-level SST
entry metadata. The baseline must exactly equal the verified trace's unique
count. A F2Load cardinality mismatch is a valid experimental result, including
in the pilot, provided all structural/readability checks pass.

Each phase writes `summary.tsv`, `results.json`, `REPORT.md`, and a `COMPLETED`
marker only after twelve validated cases. Per-case `result.json` files, process
arguments, combined stdout/stderr, exact scan reports, and F2Load reports remain
under `cases/`. Top-level `events.jsonl`, `process_status.json`, and `status.json`
record progress and failures. No existing result bundle or manuscript is updated
automatically.

## Read-only distinct-key audit of run2

The original `db_fidelity_check` increments `exact_unique_keys` for each
iterator row. For the six F2Load cases that failed strict ordering, this field
is an iterator row count, not a validated count of distinct keys. Its `delta`
and any derived relative error must not be presented as true unique-cardinality
error. The old `non_increasing_count` combines adjacent equal keys and decreasing
keys, so it alone cannot establish which condition occurred. The physical SST
entry totals remain per-file measurements.

The independent [db_fidelity_audit.cc](../../tools/db_fidelity_audit.cc) reopens
each existing DB read-only through the common clean RocksDB library, uses direct
reads and checksum verification, and records every returned key ID in a bounded
bitmap. This counts distinct returned IDs independently of iterator ordering
or duplicate suppression. The audit validates key encoding, domain, and value
lengths; reports iterator rows, distinct keys, duplicate rows, and separate
adjacent-equal/decreasing counts; and retains up to eight transition examples.
It does not compare the complete returned key set against the original trace.

The six full-size F2Load audits completed at 13:14:53 KST on 2026-09-08.
All six have zero decreasing transitions, and their bitmap duplicate counts
equal their adjacent-equal counts. Every returned row count matches the original
scan and final SST entry sum. All six distinct cardinalities differ from the
baseline; the unique100 cases are approximately 19.4% below baseline.
The new reports and interpretation are under
`experiments/artifacts/fidelity_100gib_20260908_run2/full/diagnostics_20260908/`,
with exact three-stage counts and remaining causal questions in `REPORT.md`.
The audit does not repair the DBs or change the original failed validation.
Original binaries, DBs,
load/settle/scan logs, `exact_cardinality.json`, and earlier failure records are
preserved; diagnostic output uses new files outside the DB directories.
