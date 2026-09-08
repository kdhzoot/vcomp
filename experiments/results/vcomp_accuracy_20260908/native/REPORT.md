# Exact-key oracle microexperiment

Canonical results are this native/ directory. The probe links the frozen seqfix librocksdb.a and uses its PLATFORM_CXXFLAGS, including native CPU floating-point instructions. It calls exported production PLR/KMV/merge/split/materialization APIs. It never opens a DB or writes an SST.

## Parameters and controls

- Four deterministic datasets, four files of 4,096 actual keys each: disjoint interleaved (16,384 unique); uniform two-copy placement, hot shared core, and wide gaps (8,192 unique each).
- Both global and range sketch BUILD budgets vary: 512, 2,048, and 16,384 samples; eight range buckets. The largest setting makes every input sketch complete.
- PLR rank error eight versus one; target 512 entries per output SST; zero or four grandparent boundaries.
- The exact_union_refit_control fits the true union directly, bypassing approximate model merging. It is a diagnostic oracle, not an implemented production fix.
- Two additional single-input dense controls use keys {0,1,2} and {0,1,2,3,4}, complete sketches, PLR error zero, and two entries per output.
- 32 deterministic domain offsets per large dataset and sample budget 512/2,048 isolate a single KMV union estimate before merge/split. Offsets are index * 10000019.

## Confirmed sampling-independent counterexamples

For {0,1,2,3,4}, the actual merge API returns the correct target five, but its slope is 1.25. The actual split API emits [0,1] with two planned entries, [2,2] with two, and [3,4] with one. The middle file has capacity one for two planned distinct keys. Same-level boundary trimming does not fix it. MaterializeKeys outputs contain only four distinct keys across files; the streaming count-only reference agrees. The exact-union PLR control has slope one and returns five feasible keys.

For {0,1,2}, merged slope 1.5 yields [0,1] with two planned entries and [1,2] with one, sharing key one. The existing same-level trimming policy repairs this small example. The exact-union PLR control creates no overlap.

These are merge/rank/split contract failures with exact input cardinality and zero PLR fitting error; increasing KMV samples cannot remove them.

## Cardinality and membership are separate

| Case | Path | PLR error | Exact target | Generated distinct | Missing original keys | Invented keys |
|---|---|---:|---:|---:|---:|---:|
| hot_shared_core | kmv_merge | 8 | 8192 | 8192 | 5528 | 5528 |
| hot_shared_core | exact_union_refit_control | 8 | 8192 | 8192 | 1559 | 1559 |
| hot_shared_core | kmv_merge | 1 | 8192 | 8192 | 5511 | 5511 |
| hot_shared_core | exact_union_refit_control | 1 | 8192 | 8192 | 0 | 0 |
| gapped_50 | kmv_merge | 8 | 8192 | 8192 | 5841 | 5841 |
| gapped_50 | exact_union_refit_control | 8 | 8192 | 8192 | 19 | 19 |
| gapped_50 | kmv_merge | 1 | 8192 | 8192 | 5829 | 5829 |
| gapped_50 | exact_union_refit_control | 1 | 8192 | 8192 | 23 | 23 |

These columns use the actual library-generated keys and exact set membership checks. Matching cardinality does not recover the input set. Reducing the input fit error does not guarantee better membership after range-based model merging.

## Isolated single-merge KMV estimate

| Dataset | Samples | Trials | Mean error % | Sample SD % | Under / exact / over |
|---|---:|---:|---:|---:|---|
| disjoint_interleaved | 512 | 32 | -0.8589 | 1.3598 | 13 / 19 / 0 |
| disjoint_interleaved | 2048 | 32 | -0.2449 | 0.4143 | 13 / 19 / 0 |
| gapped_50 | 512 | 32 | -0.6290 | 2.6956 | 20 / 0 / 12 |
| gapped_50 | 2048 | 32 | -0.1755 | 0.8117 | 18 / 0 / 14 |
| hot_shared_core | 512 | 32 | 0.6767 | 2.2742 | 11 / 0 / 21 |
| hot_shared_core | 2048 | 32 | 0.3067 | 1.1509 | 14 / 0 / 18 |
| uniform_50 | 512 | 32 | 0.4292 | 2.7792 | 11 / 0 / 21 |
| uniform_50 | 2048 | 32 | 0.2514 | 1.0846 | 14 / 0 / 18 |

For the disjoint cases the estimate is capped at the sum of input entries, so it cannot overestimate the true union; these 32 trials show the resulting one-sided errors. Other overlap families show both error signs. These deterministic offsets do not establish a universal bias or explain a particular 100-GiB run by themselves.

## Stage separation and limitations

The CSV/JSON preserve input sum, true union, isolated KMV estimate, merged target, split descriptor sum, per-file range capacity, sibling overlap, library raw vector rows, library per-file distinct sum, exact union of generated keys, missing keys, and invented keys. Unsplit merged models, raw split descriptors, and same-level-trimmed descriptors are separate columns.

All 100 pipeline records conserve their requested descriptor entry sum across SplitIntoSSTs. Every complete-sketch control estimates the exact union. Neither fact guarantees feasible per-file ranges or exact generated membership.

MaterializeKeys caps its vector tail but does not remove duplicates despite its source comment. Its raw vector length is not an SST write count. The probe therefore counts its distinct keys explicitly and separately evaluates a count-only copy of the production streaming walk. With production platform flags, the two agree after per-file deduplication in every tested output. The reference loop is corroborating evidence; actual exported-API ranges and exact set counts are the primary measurements.

The initial portable compilation, retained in the parent directory with probe_source_v1.cc, had floating-point/compiler-expression differences from the production library. It is preliminary evidence. The native compilation also aligns the reference expression spelling; this experiment does not independently identify which of those two alignment changes removed each discrepancy.

No multi-level DB state, compaction scheduling, repeated merge cascade, or load performance is measured here. The exact refit control has access to all original keys and therefore is not a proposal to obtain that information for free.

Build/run commands and hashes are in manifest.json; raw observations are probe.jsonl; full JSON/CSV are results.json and pipeline_results.csv. Reproduce summaries with `python3 -B summarize_probe.py` from this directory.
