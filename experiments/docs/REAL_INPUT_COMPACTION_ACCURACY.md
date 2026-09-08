# Accuracy on identical real compaction inputs

2026-09-08 inspection and implementation of a new capture/replay experiment.
The historical evidence and its limits are recorded below. The new opt-in
collector passes a real-DB fixture: the exact union of overlapping input
SSTs and real output SSTs is 3,072 keys. Delete, invalid key-padding, and
snapshot fixtures are rejected for their expected reasons. Release build and
fixture evidence are retained under
`experiments/artifacts/real_input_accuracy_20260908_prepare/`.

## New capture and replay path

`VCOMP_ACCURACY_CAPTURE_DIR` enables `MaybeCaptureVCompInputs()` on successful
real compaction jobs. It is independent of the historical summary-only
`--vcomp_accuracy_trace_dir` option. All input and actual output SSTs are
scanned; file-local distinct IDs are written as little-endian uint64 streams,
with physical entry counts retained separately. The collector verifies fixed
sizes, the eight-byte big-endian ID plus ASCII-zero key padding, sorted keys,
SST properties, extrema, and binary length. It supports ordinary Put-only,
default-column-family bytewise compactions without snapshots or filters.

Each `cf0_job_ID/manifest.tsv` records the input/output grouping, actual output
level, target size, and grandparent boundaries. It is published by rename
only after the binary writes succeed. Unsupported or failed captures leave
`failed.txt` (or `cf0_job_ID.failed.txt` if the job directory cannot be
created) and `VCOMP_ACCURACY_CAPTURE_FAILED` in the log. The real compaction
continues; experiment automation must treat that marker as a failed capture.
Trivial moves do not execute this collector because they do not rebuild SSTs.

`tools/virtual_compaction_replay.cc` consumes a completed capture without
opening a DB. It verifies the exact input/output union and compares virtual
models and generated sets with the captured real output. Current variants
separate raw fitted inputs from certified inputs. A separately compiled
archived-source executable supplies the pre-discrete legacy comparison;
its source and binary provenance must accompany results. The current-build
`legacy` path is labeled separately and is not substituted silently for the
archived executable.

`VCOMP_KMV_SAMPLES` and `VCOMP_KMV_RANGE_BUCKETS` now permit experimental
construction/retention budgets; unset, empty, zero, malformed, or overflowing
values retain the defaults 512 and 8. The replay CLI rejects invalid budget
arguments and checks the effective getter values. Large exact key sets are
measurement data on disk, not a new requirement for the F2Load production
algorithm.

## Background campaign

Run `real_input_accuracy_100gib_20260908_run1` was launched at 16:54 KST on
2026-09-08 as detached controller PID 1737452. The initial stage is the 4 GiB
pilot; consult its live `STATUS.json` for completion rather than treating the
launch as a completed 100 GiB measurement.

`experiments/scripts/trace/run_real_input_accuracy.py` runs a six-case 4 GiB
pilot followed by six 100 GiB real-compaction captures under `/work/vcomp/exp`.
The pilot uses 16 MiB buffers/SST targets and a 64 MiB L1 base to reach L3.
Full cases use the previous 100 GiB matrix's 64 MiB buffers/SST targets and
256 MiB L1 base. Existing independently verified traces are reused through
read-only links, with their content hashes checked.

The runner first checks the release, capture-fixture, replay-build and replay
fixture evidence, then freezes its executable and source inputs. Capture runs
use six processes in parallel. Every nonempty real compaction job must match
the corresponding completion event in the DB log. Clean RocksDB settling and
strict full-DB scans verify each baseline's expected unique count.

All eligible jobs are captured. Offline replay selects the smallest, lower
median, and largest input-entry job in each case and input/output-level pair.
It is a deterministic diagnostic subset, not full replay coverage or a random
statistical sample. Exclusions are recorded. Pilot replay compares three
initial variants. Full replay adds seven separate changes: samples
1,024/2,048/4,096; buckets 4/16; PLR error 4/1. Replay uses two workers, a
64 GiB address-space limit per worker, and a 50-million-key per-file buffer
limit; merged models and exact unions can be streamed.

Qualified launch (the run ID must be fresh):

```bash
python3 experiments/scripts/trace/run_real_input_accuracy.py \
  --run-id real_input_accuracy_100gib_20260908_run1 --execute --background \
  --replay-binary experiments/artifacts/fidelity_replay_20260908/build3/replay \
  --legacy-replay-binary experiments/artifacts/fidelity_replay_20260908/build3/replay_archived_legacy \
  --replay-provenance experiments/artifacts/fidelity_replay_20260908/build3/manifest.json \
  --replay-provenance experiments/artifacts/fidelity_replay_20260908/validation2/validation.json
```

The detached controller writes atomic `STATUS.json` with a ten-second
heartbeat under `experiments/artifacts/<run-id>/`. Exact commands, process
records, capture inventories and hashes, selected jobs, and metric JSON/TSV
files remain alongside it. Controller logs and its PID are recorded under
`experiments/artifacts/<run-id>_launch/`. A failed gate stops progression;
low measured fidelity alone is an observation, not an execution failure.
`--resume` verifies completed steps and their hashes but refuses to rerun an
incomplete step or overwrite an existing DB/result. Without `--execute`, the
runner only prints its plan.

## Existing experiment

`--vcomp_accuracy_trace_dir=<directory>` enables
`CompactionJob::MaybeRecordVCompAccuracy()` in
[`compaction_job.cc`](../../db/compaction/compaction_job.cc). It runs after a
successful real compaction and before that job's inputs are retired:

1. Scan the actual input SSTs selected by RocksDB, file by file.
2. Fit each input's PLR and construct its global and range KMV sketches.
3. Run `NWayMergeKMVRangeAware` and `SplitIntoSSTs` with that actual job's
   output level, target SST size, and grandparent boundaries.
4. Compare the predicted virtual output descriptors with the real job's
   output metadata, and write `vcomp_accuracy_job_<job>.json`.

Thus both sides use the same actual compaction input files. Merely using the
same initial load trace in two independently scheduled DBs is a different
experiment. Prediction does not replace the real output or affect the next
job's data. Every job starts from models freshly fitted to real SSTs, so this
is a one-job approximation measurement, not propagation of virtual errors
through the compaction history. Collection adds scans and CPU work, making
its elapsed time unsuitable for loading-performance comparisons.

## Retained measurements

The June 8, 2026 evidence is under
[`fig_eval_vcomp/source_data/vcomp/experiments/artifacts/log_loads/`](../paper_evidence/current/fig_eval_vcomp/source_data/vcomp/experiments/artifacts/log_loads/).
It includes a four-run summary, per-output-level summaries, and the 500 GB
run's 21,828 job rows. The original labels are retained below; exact byte
units and complete original commands cannot be established from these
summary tables alone.

| Historical label | Samples / buckets | Jobs | Mean absolute entry-count error | p95 absolute error |
|---|---|---:|---:|---:|
| 100 GB, global-total KMV | 512 / 8 | 2,616 | 1.961046% | 5.121886% |
| 500 GB, global-total KMV | 512 / 8 | 21,828 | 1.624110% | 4.805043% |
| 100 GB, range reference | 1,024 / 16 | 2,673 | 1.478176% | 3.902577% |
| 100 GB, range-local-sum variant | 512 / 8 | 2,621 | 4.775149% | 15.959889% |

The recorded workload fields are 24-byte keys, 1,000-byte values, and no
compression. Error here is `100 * (kmv_entries - actual_entries) /
actual_entries`, with absolute value before aggregating the absolute metrics.
It is not ECDF error or final whole-DB distinct-key error. Jobs contribute
equally to these averages and may repeatedly process related data. The
different runs do not have identical recorded job counts; this is not a
paired, one-parameter comparison between 512/8 and 1,024/16.

Original absolute trace/log paths and copied-file hashes survive in the
tables and `provenance.tsv`. The original accuracy JSONs, real input/output
key dumps, and original run logs were not found at those paths or in the
current sibling repositories and `/work/vcomp` search. Some provenance rows
say `copied` although the named `source_logs` files are absent in this
checkout. The TSVs support reanalysis of recorded scalar errors; they cannot
reconstruct the exact old input key sets for a new algorithm replay.

## What is collected, and what is missing

The hook records input/output file counts, entries, bytes, key extrema,
PLR segment count, KMV sample statistics, and scan/prediction times. The
accuracy JSON contains file summaries, not the full keys, fitted models,
or sketches. `predicted_entries` is a descriptor target; it is not the
distinct union of actually generated keys.

The current hook needs these qualifications before reuse:

- It creates raw fitted input models but does not call `CertifyVirtualSST`,
  which the experimental F2Load flush path now calls. A candidate comparison
  must explicitly measure both fresh raw-fit input and the corresponding
  production input-construction path.
- It scans physical internal entries, extracts only the first eight user-key
  bytes, and sorts without removing per-file duplicates before setting
  `num_entries`. It neither enforces Put-only input nor interprets snapshots
  and deletion semantics. Physical entries must not silently become an exact
  unique-key oracle.
- It reads actual output metadata instead of actual output key sets. This
  cannot measure ECDF shape, invented/missing IDs, or generated uniqueness.
- It compares boundaries by sorted output index up to the smaller file
  count. With different split layouts this is not a robust file pairing, and
  unmatched output boundaries do not enter the reported boundary mean.
- It logs an input scan warning and continues; parse/write failures are not
  fully represented as invalid observations. A new collector must reject
  partial records instead of admitting them to accuracy aggregates.
- Predicted bytes use input physical bytes per entry, while the F2Load path
  uses configured key/value size. Preserve that historical metric, but
  distinguish logical bytes from actual SST bytes and their encoding overhead.

The separate `--compaction_trace_dir` path can log input and output key IDs
with sequence/type, but emits every key as text and does not save all replay
context (such as grandparent boundaries). It is not a complete replay format.

## Proposed reuse for fidelity correction

First qualify the collector with a small Put-only case using the current
injective 64-bit key encoding, no snapshots, one column family, and the
production split policy. Independently check exact output unions. Preserve
physical counts as separate fields and invalidate a record on an unsupported
operation or failed scan. Give every captured case a fresh ID and source,
binary, options, input-file, output-file, and content-hash provenance.

For the 100 GiB matrix, use the six current KV/distribution configurations
and `/work/vcomp`. Real RocksDB compaction supplies the reference input and
output for every selected job. Keep a compact capture of key IDs and job
context (input grouping, actual output grouping, GP boundaries, target size,
levels, options) so every virtual variant can replay identical inputs without
reloading the entire DB. Account for capture volume and job coverage; if
sampling is used, record a deterministic policy spanning levels, fan-in,
overlap, and tails. Do not represent sampled jobs as the whole campaign.

For each fixed job, separate these measurements:

| Stage | Accuracy against actual output keys |
|---|---|
| Input model construction | Per-file ECDF error before and after certification; original distinct counts versus reconstructed counts |
| Dedup estimate | Exact input-union/output-unique count versus raw and chosen KMV target; signed bias, absolute error, clipping |
| Merged model before split | Normalized ECDF KS and unnormalized cumulative-count discrepancy; actual generated distinct count |
| Split output | Predicted cumulative mass at each actual SST boundary; output SST count, capacities, gaps/overlap, logical and physical size fields |
| Generated output set | Unique count, missing/invented key IDs, retained-witness membership, and equality to the unsplit generated set |

Keep PLR error and sample/bucket settings fixed for the old/candidate design
comparison. Then vary sketch construction budgets and bucket count separately,
followed by PLR error. Record metadata footprint and prediction CPU time, but
do not use concurrent loading time as a performance claim. Select parameters
on some jobs and assess them on held-out jobs/seeds before a final rerun.

The first target is lower dedup and CDF error on identical real inputs while
maintaining count/range invariants. After that passes, assess repeated virtual
propagation separately. A replay on the real job graph must explicitly map
different virtual split layouts to later inputs; mapping files by ordinal is
not sufficient. End-to-end 100 GiB unique fidelity remains a further check,
because independent surviving files/levels can still synthesize overlapping
keys even when each one-job prediction is accurate.

## Entry points

- [`include/rocksdb/options.h`](../../include/rocksdb/options.h): option contract.
- [`db/compaction/compaction_job.cc`](../../db/compaction/compaction_job.cc):
  input scan, fresh model construction, merge/split, and actual-output comparison.
- [`db/compaction/compaction_trace_logger.cc`](../../db/compaction/compaction_trace_logger.cc):
  separate text key logging path.
- [`plot_vcomp_accuracy_onepage.py`](../analysis/plot_vcomp_accuracy_onepage.py):
  historical JSON plots. Its 100 GB title is hard-coded and must not label a
  different run without correction.
- [`build_figure_evidence_bundles.py`](../scripts/paper/build_figure_evidence_bundles.py):
  historical evidence source mapping. It is not a workload runner.
