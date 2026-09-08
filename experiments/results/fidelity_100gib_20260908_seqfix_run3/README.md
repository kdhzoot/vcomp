# 100 GiB fidelity sweep after the sequence-registration correction

Completed **2026-09-08 14:23:04 KST** (`2026-09-08T05:23:04.738300+00:00`).
This bundle preserves **one completed full matrix with the corrected binary**,
after its 1 GiB pilot. Run2 used an earlier binary and is not a repetition of the
corrected candidate. No merge/split/dedup accuracy change was applied for run3.

The full matrix has six datasets: 1024 B entries (24 B key + 1000 B value) and
91 B entries (48 B key + 43 B value), each with unique100, uniform50, and
zipf99_50 input. Each dataset has a 100 GiB logical input target and two loading
paths, giving 12 loads. The 12 loads started concurrently in each phase under the
user's requested mode. Pilot and full phases both completed 12/12 technical
validations. This is a cardinality experiment; elapsed times are operational
records and must not be used as isolated load-performance measurements.

## Results and count semantics

Full baseline cases all match their trace's independently verified unique count.
All full F2 scans pass strict increasing order, with zero non-increasing rows,
zero encoding/domain/size mismatch counters, and zero pending compaction bytes.
F2 cardinality differences remain measured errors:

| F2 case | Expected unique | Visible unique | Relative error |
|---|---:|---:|---:|
| s100gib_1024B_unique100 | 104,857,600 | 84,487,007 | -19.427% |
| s100gib_1024B_uniform50 | 52,428,800 | 53,609,965 | +2.253% |
| s100gib_1024B_zipf99_50 | 52,428,800 | 50,141,610 | -4.362% |
| s100gib_91B_unique100 | 1,179,936,070 | 949,616,388 | -19.520% |
| s100gib_91B_uniform50 | 589,968,035 | 587,580,583 | -0.405% |
| s100gib_91B_zipf99_50 | 589,968,035 | 558,329,556 | -5.363% |

[Full report](full/REPORT.md), [summary TSV](full/summary.tsv), and
[case results](full/results.json) retain the original measured values.
`validated` means the load and correctness-measurement protocol completed; it
does not mean that F2's unique count matches the input. The per-case
`exact_cardinality.json` records make that distinction explicit.

D is the sum of live descriptor entries before materialization; M is the sum of
entries written into live materialized SSTs; U is the final visible distinct key
count after settling. D is a per-file planned total and M a per-file written
total; both may include keys present in multiple levels. They are not
interchangeable with U, and M−U alone
does not identify which overlaps are intentional versions or synthetic collisions.

## Baseline, reader, and run2 comparison

Baseline loading uses the same campaign-frozen **f2_db_bench** as F2 loading,
with `baseload` and `use_virtual_compaction=false`. It does not use clean_db_bench
for the load. Both paths subsequently reopen/settle using the fixed clean RocksDB
executable; the final checker links the clean RocksDB static library and performs
a read-only exact iterator scan with strict-order validation.

The source base revisions are F2 `ee51a6e72172cef98a4e160cdd2beeeaaa75117c` and clean
RocksDB `0760803d5d3ac75731ae6cbdd0de862fab7b44b0`. The F2 build includes the saved
working-tree changes: use `f2.diff`, the status records, and the three-file
`seqfix_source_snapshot` for the actual corrected source, rather than treating
the base revision as the complete build identity. Binary fingerprints, runtime
controls, configuration, and exact per-case argv are preserved in the manifest
and process records.

[Run2 comparison](full/run2_comparison/REPORT.md) uses run2's independent complete
bitmap distinct audit for old U. Run2's original iterator had repeated rows, so
its old `exact_unique_keys` field is not used as a distinct-count oracle. New U
uses run3's clean iterator after strict ordering passes. All six full trace
SHA256 values match between the campaigns. Concurrent scheduling can change D,
M, and U, so their run2/run3 differences are not an isolated causal estimate of
the sequence correction. Matching cardinality also does not prove matching key
identities or values. The exact original comparison helper is retained at
[launch/compare_run2_run3.py](launch/compare_run2_run3.py).

## Original paths and preserved evidence

- Original run artifacts: `/home/smrc/virtual_compaction/vcomp/experiments/artifacts/fidelity_100gib_20260908_seqfix_run3`
- Original launch records: `/home/smrc/virtual_compaction/vcomp/experiments/artifacts/fidelity_100gib_20260908_seqfix_run3_launch`
- Original DB and trace root: `/work/vcomp/exp/fidelity_100gib_20260908_seqfix_run3`
- Old run2 audit locations and fingerprints: `full/run2_comparison/comparison.json`

All copied files preserve their original bytes, including absolute paths and
line endings. Those absolute paths describe the original server; the curation
does not rewrite them to bundle-relative locations. Relative paths within each
phase retain the source tree layout. Per-case results are named `result.json`
in this run; no `case_result.json` was present or renamed.

[SOURCE_INVENTORY.json](SOURCE_INVENTORY.json) maps every copied file to its
original absolute path, byte size, and SHA256. Copied evidence includes campaign
and process status, the full/pilot reports and JSON/TSV summaries, all trace
verification JSON, full case argv/options/process records, per-case exact counts
and F2 aggregate fidelity reports, source/runner/checker/generator/verifier
snapshots, source diffs/status, and small launch/build-generator provenance.
Pilot case results and aggregate fidelity checks are preserved alongside its
report and trace manifest.

[SOURCE_HASH_VERIFICATION.json](SOURCE_HASH_VERIFICATION.json) separately verifies
the three frozen sequence-fix snapshots against the pre-launch fingerprints and
launch snapshots, the top-level tool snapshot, and the runner against its manifest
hash. [CURATION_VALIDATION.json](CURATION_VALIDATION.json) records read-only checks
of saved results; no DB scans, trace verification jobs, or other workloads were
rerun during curation. [sha256.json](sha256.json) hashes all package files except
itself, including the generated curation documents.

Binaries, DB/SST contents, trace payloads, per-SST TSVs, and runtime logs are
excluded. Their original identities and locations remain in copied manifests
and records; this portable bundle is not a data backup. Source evidence and
measurements are copied without modification. No build, experiment, or commit
was performed to produce this bundle.
