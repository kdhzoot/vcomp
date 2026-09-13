# Exact-membership sparse sequential loading for Chapter 3

## Question and authorization

The author requested a background experiment replacing dense fillseq with
fillseq(sparse): extract the baseline's distinct keys and insert those keys once
in sorted order. Continue manuscript writing with existing results; do not
replace paper text or figures until these measurements are validated.

The hypothesis is that matching the key set removes the positive-lookup
confound while leaving differences caused by sorted construction observable.
Equal logical membership does not imply equal physical DB size: incremental
construction retains some obsolete versions across levels, while a unique
sorted insertion omits them. Measure DB bytes, SST count, level placement,
and remaining physical entries rather than forcing byte equality.

## Source and controlled variables

- Source DB: `/work/vcomp/exp/paper_ch23_common_260905_approved_run3/full/baseline_1kb`.
- Source record: `results/paper_ch23_common_260905_approved_run3/loads.json`,
  `baseline_1kb`; compare against its recorded `db_identity.json` before use.
- Original logical input: 1,000 GiB, N = 1,048,576,000 insertions and key domain.
- Key/value lengths: 24/1,000 bytes. Keys use the original db_bench encoding.
- Baseline engine commit: `f455ab7bd6a8c67f00d48075bb310f131d9fae5f`.
- Clean executable SHA256:
  `8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b`.
- Reuse the clean engine's static library and existing db_bench objects.
  Compile only an isolated copy of db_bench_tool.cc with a sorted key-file
  input option inside the existing sequential DoWrite path. Preserve the
  original RandomGenerator, WriteBatch, memtable, flush, compaction and stats.
- Reuse `ch23_common.load_options(..., 'fillseq')`: single write thread,
  batch 1, vector memtable, 64 MiB buffers/target SSTs, 16 write buffers,
  48 background jobs, bloom10, no compression/WAL, direct I/O, format7,
  leveled compaction/min-overlap, fixed level capacities and seed12345678.
- Sparse load writes U distinct keys, while the YCSB query domain remains N.
  Record `num_keys=N`, `load_insertions=U`, and `distinct_keys=U` separately.
- Values retain the existing 1,000-byte generator and no-compression setting.
  Per-key value bytes and update history are not preserved; key membership is.

## Input preparation

Use a read-only iterator over a private metadata clone of the baseline. Its
SSTs are immutable hardlinks; original source metadata and SST identities are
checked before and afterwards. Export each visible user key once, in ascending
order, as uint64 little-endian IDs. Verify key encoding, value length, domain,
strict order, iterator status and file size. Record count, SHA256, wall time
and source identity. This directly extracts the real baseline's key set;
it does not substitute a different random set with the same cardinality.

The export reads the existing DB and is not a from-scratch fast loader. Report
extraction time and sparse insertion time separately, plus their sum. Do not
present insertion-only timing as the end-to-end construction cost of a loader
that can operate without a pre-existing baseline.

## Pilot and serial campaign

1. Build the isolated tools, recording compiler/link commands, source patch,
   source/library/object/executable hashes and original engine commit.
2. Create a fresh 1 GiB baseline with the clean engine and common options.
3. Export its exact distinct keys, load a fresh sparse DB through sequential
   writes, flush/compact0/drain, and require zero pending compaction bytes.
4. Verify the sparse DB's full visible key set against the exported file.
   Exercise the same YCSB/read/write measurement path with short pilot runs.
5. Only after the pilot passes, export the preserved 1 TB baseline's keys and
   create one fresh full-size sparse DB. Preserve source, sparse DB and key file.
6. Verify its entire visible key set before performance measurement.
7. Per the author's scope amendment, measure only the sparse DB under YCSB A
   and C for 300 seconds each. Use the same frozen profiler, 48 threads, 50 GiB
   cache, seed87654321, Zipfian distribution and online compaction as the
   historical baseline and flush-only campaigns. Each starts from a fresh
   hardlink clone with independent metadata and the existing cache reset.
8. Run only the existing C3s/A5s short pilots on the sparse source, then the
   two full measurements. Do not rerun baseline, uniform read, B/D/E/F, or the
   fixed-operation supplemental comparisons.
9. Compare every runtime option other than DB/report paths with both historical
   commands (`ch3_ycsb_cache50_260911_run2` flush_only and
   `ycsb_band_run3_260910` baseline). Require the same profiler binary hash.

Do not run competing storage benchmarks in parallel. Existing f11-f15 campaign
completed with 30 valid full cells and all 5 original DBs retained. Initial inspection
found no active db_bench, approximately 18 TiB free under /work and 991 GiB available
RAM. Recheck immediately before launching; require at least 3 TiB free.

## Metrics and completion

Record exact distinct count/ratio and membership verification, logical input
and physical SST bytes, SST counts/levels, extraction/loading/drain durations,
loading flush/compaction bytes, throughput and mean latency, filter checks per
lookup, positive lookups, read/write compaction bytes and device read/write
counts/bytes with the existing profiler protocol. Preserve raw histograms but
do not add p99 or normalized plots to the manuscript.

Fail the campaign on invalid key order/domain/encoding, membership mismatch,
nonzero process exit, missing required counters, incomplete workloads, nonzero
pending bytes after the fixed-work drain, source identity changes or competing
benchmarks. Preserve partial evidence and DBs; do not silently retry in place.
One loading per arm is an exploratory comparison, not a variation estimate.

## Locations and time budget

- Plan: this file.
- Tools: `scripts/load/fillseq_sparse_keys.cc`, `scripts/load/build_fillseq_sparse.py`.
- Controller: `scripts/paper/run_fillseq_sparse.py`.
- Measurements: `scripts/paper/run_fillseq_sparse_measurements.py`.
- Raw controller/load artifacts: `artifacts/log_loads/fillseq_sparse_260913_run1/`.
- DB/key artifacts: `/work/vcomp/exp/fillseq_sparse_260913_run1/`.
- Measurement results: separate new `fillseq_sparse_260913_run1_*` IDs.

Prior dense fillseq took 16 minutes for N records; sparse insertion is
provisionally 10-15 minutes. The amended full workload window is 10 minutes
(A and C only), plus short pilots and staging. Extraction and exact full-scan
verification are measured separately; total remaining time is therefore driven
by those scans and insertion rather than an additional hour of YCSB.

## Execution status

Implementation complete. The 1 GiB pilot exported 662,704 distinct keys and
verified exact membership in the newly loaded sparse DB; workload qualification
passed all 12 YCSB and 4 supplemental full cells on the small dataset. The successful isolated build is under `build2/`; the earlier
`build/` preserves an API-signature compile failure and was never used for a DB. The controller's status.json is authoritative
for transient execution status. The paper remains on existing measurements.

Full campaign launched in a detached process after the successful pilot.
See `launch.json`, `full_controller.log`, and `status.json` under the raw
controller root for the exact command, PID and current phase. Original baseline,
sparse DB and sorted key file are retained.

The A/C-only scope amendment is recorded in `measurement_scope_amendment.json`.
Four reference command comparisons passed; `scope_reference_option_audit.json`
records every matched option. The in-flight extraction and loader are unchanged.

The full export completed with 662,840,067 unique keys in 760.98 seconds.
Before sparse loading began, the controller stopped because another storage
benchmark was active. No sparse DB or sparse-load measurement directory existed.
Recovery uses `--resume-export`, which validates the original source identity,
qualified binary/helper hashes, documented A/C scope amendment, and exported
key-file SHA256 before loading. It preserves the failed status and new provenance
under `full/resume_<timestamp>/`; the original extraction evidence is unchanged.
This resumes the unstarted load rather than repeating an existing measurement.

The resumed load was interrupted after 570.95 seconds with 626,868,280 reported
insertions (94.57%). The strict concurrency check detected a different benchmark.
The competing `membership_1tb_260913b` load started at 07:03:47 UTC, before the
sparse abort at 07:04:14; its `f2load_claim2_260913/LOG` records active DB loading
and shutdown at 07:05:05. The interrupted sparse DB and raw logs remain intact
and are excluded from loading-time and workload comparisons.

Retry `fillseq_sparse_260913_run2` uses a new DB and new measurement paths with
`--reuse-export-from fillseq_sparse_260913_run1`. It validates and reuses the
completed export, original source identity, qualified binaries, and A/C scope
amendment. No engine or benchmark options changed. The original extraction time
is retained separately from the fresh insertion time.

Run2 completed loading in 607.734 seconds (10.129 minutes), with 10,264 final
SSTs totaling 689,530,007,679 bytes. Exact membership verification passed for
all 662,840,067 unique keys. Both 300-second YCSB A/C cells validated successfully;
original baseline and sparse source DBs are preserved.

Final bundle: `results/fillseq_sparse_260913_run2_full_measure/`.
`plot_metrics.tsv` contains only full A/C measurements with the existing fidelity
plot definitions, including filter checks per Get and separate device read/write
counts and bytes. `loading.tsv` records load and extraction times separately.
`tsv_provenance.json` records metric definitions, input hashes, and the difference
from older chapter-3 filter-cache-accesses-per-operation columns. Reproduce these
exports using `analysis/export_fillseq_sparse_tsv.py` with `--bundle` and
`--load-completion` pointing to the completed bundle and run2/full_completed.json.
The A result is an online 300-second measurement, not a replacement for the old
fixed-100-GiB-write plus compaction-drain experiment.

## Fixed-count A correction requested by the author

The author explicitly requested a new A measurement including compaction drain
to replace the historical write comparison. Run ID
`fillseq_sparse_fixed_a_260913_run1` uses the immutable run2 sparse source and a
fresh clone for each pilot/full cell. The runner
`scripts/paper/run_fillseq_sparse_fixed_a.py` reuses the existing fixed-A option
builder and qualified sparse measurement wrapper, and compares every option
with both historical commands under `results/ch3_write_fixed_260911/` (only DB
and report paths differ).

Full protocol: 210,000,000 mixed YCSB A operations, 48 threads, seed 87654321,
original 1,048,576,000-key domain, 24/1000-byte KVs, 50 GiB cache, duration=0,
and `workloada,stats,waitforcompaction,stats,levelstats`. The reference has no
explicit memtable flush, so none is added. Both historical arms generated
105,003,686 updates (about 100.139 GiB logical KV bytes); this exact operation
protocol, rather than a new exact-byte stopping rule, is reproduced.

The controller first runs a 48,000-operation fixed-A pilot on a separate fresh
clone, then runs full A. Full results require identical read/write counts to the
references, successful waitforcompaction, zero final pending compaction bytes,
unchanged source identity, matching binary/options, no swap pressure, and no
concurrent benchmark. Process timeout is two hours; expected runtime is roughly
5-10 minutes based on the completed 300-second A and historical fixed-A runs.
Source DBs and previous measurements are preserved. Only validated disposable
clones are removed following the established protocol. The separate result
bundle includes `write_metrics.tsv`, raw-log evidence, option audit, source
identity, and completion status; timed A/C results are retained separately.

The fixed-A correction completed at 07:53:40 UTC with 210,000,000 operations,
105,003,686 writes, successful compaction drain, and zero final pending bytes.
Compaction write bytes are 439,699,681,280. The source sparse DB was preserved.
The completed standalone result is under
`results/fillseq_sparse_fixed_a_260913_run1/`.

Combined plotting tables are under `results/paper_ch3_fillseq_sparse_260913/`:
`plot_metrics.tsv` has 300-second A/C rows for all three methods, retaining n01
as the original figure's baseline; `write_metrics.tsv` separately compares the
historical fixed-A baseline with the new sparse fixed-A result. Flush-only
timeout/unrun cells remain blank with explicit statuses. Both tables use the
display label `fillseq` and retain `source_system=fillseq_sparse` plus per-row
provenance. The reproducible exporter is `analysis/export_ch3_sparse_comparison.py`.
