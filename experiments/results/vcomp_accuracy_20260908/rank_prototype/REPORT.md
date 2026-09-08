# N−1 rank-span prototype: insufficient as a standalone fix

This is an isolated experiment using copies of the actual implementation. It is
not a production patch, DB benchmark, or measurement of the running 100 GiB jobs.
The production source and binary were not modified.

## Candidate and build

`candidate.diff` contains exactly three changes inside
`NWayMergeKMVRangeAware`: normalize the rank span to `max(N−1, 0)` instead of N,
apply the same span to the zero-local-mass fallback, and use rank 0 for a singleton.
The descriptor's cardinality remains N. No local mass redistribution, capacity
constraint, half-open CDF, split-boundary change, or KMV estimator change is added.

Both variants compile the same actual `plr_model.cc` and probe against their own
copy of `virtual_sst.cc`, using GCC 11, the current `make_config.mk`
`PLATFORM_CXXFLAGS` (including `-march=native`), and `-O2 -DNDEBUG`. Neither build
links or overwrites the production binary or static library. `commands.json`
contains every compiler/probe argv; `make_config.mk.snapshot` and
`compiler_version.txt` preserve build context. The driver raises on any nonzero
compiler/probe exit and completed with exit 0.

Reproduction command from the repository root:

```sh
python3 experiments/artifacts/vcomp_accuracy_20260908/rank_prototype/run.py
```

## Inputs and measurements

- Dense inputs of 1, 3, and 5 keys, each as one input descriptor.
- A zero-local-mass fallback case with three singleton descriptors: 0, 100, 200.
- Ten deterministic random datasets of 49–58 keys sampled without replacement
  from a domain twice the cardinality; seeds are `2026090800 + index`.
- Ten gapped datasets of 49–58 keys: dense groups of 13 keys separated by gaps.
- Random/gapped keys are distributed round-robin across three input descriptors,
  so the key sets are disjoint while their min/max intervals may overlap.
- Every dataset is tested with PLR error 0 and 8, and split targets 2, 7, and 16
  entries. All input global and range sketches are explicitly checked complete;
  their sample budget is 4096, with 8 range buckets. There is no sketch sampling
  error in these inputs.

Each variant has 144 cases: 18 dense, 6 fallback, and 120 random/gapped conditions.
The random/gapped conditions reuse the 20 datasets across the six PLR/split
settings; they are not 120 independent random trials.

Materialization uses the actual `MaterializeKeys` function, and distinct counts
are computed from its returned keys. It does not execute the separate streaming
SST-writing loop in `db_bench_tool.cc`, create a database, or scan an existing DB.

## Results

| Metric | Original | N−1 candidate |
|---|---:|---:|
| Dense conditions with cardinality loss / 18 | 4 | 0 |
| Dense conditions with capacity violation / 18 | 2 | 0 |
| Dense conditions with sibling overlap / 18 | 2 | 0 |
| Random/gapped conditions with cardinality loss / 120 | 63 | 95 |
| Random/gapped conditions with capacity violation / 120 | 27 | 39 |
| Random/gapped conditions with sibling overlap / 120 | 39 | 40 |
| Random/gapped conditions with bucket/descriptor count mismatch / 120 | 113 | 114 |
| Conditions with split entry-sum mismatch / 144 | 0 | 0 |

Both variants preserve the exact merged cardinality and the sum of split
descriptor cardinalities in all conditions. The remaining losses occur after
those totals have been assigned to concrete output key ranges/materialization.

The zero-local-mass fallback and singleton branches are included in the candidate
and complete without cardinality loss in all six fallback and six dense-singleton
conditions. The fallback is still a uniform approximation, not a general sparse
CDF solution.

Concrete residual counterexample: `random_0`, PLR error 0, target 2. The candidate
has 49 exact input/merged/split entries and the intended endpoint ranks 0 and 48,
but returns only 47 distinct materialized keys. Output file index 18 has range
`[81,81]`, cardinality 2, and capacity 1. The condition also has one sibling-range
overlap. Its bucket entry sum is 45 versus descriptor total 49, and six original
keys fall outside all output ranges. Thus fixing the global endpoint does not
enforce feasible local ranks or boundaries.

`original.tsv` and `candidate.tsv` contain per-condition metrics;
`*.files.tsv` contain every output's bounds, count, capacity, unique reconstructed
count, bucket entry sum, and bucket overlap count. `summary.json` contains the
aggregates and before/after production-source SHA-256 values. All four observed
production core files remained byte-identical. `sha256.json` covers the saved
sources, commands, results, and isolated probe binaries.

## Decision

N−1 normalization repairs the simple dense counterexamples but is insufficient
as a standalone production fix. On these small random/gapped cases it increases
the number of cardinality-loss and capacity-violation conditions. A subsequent
design must define consistent discrete rank/CDF boundary semantics and constrain
local allocations and sibling boundaries; it cannot rely on a global rescale
alone. This experiment does not implement or validate that broader design.
