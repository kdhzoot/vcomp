# F2Load fidelity correction plan

Status: sequence/registration correction implemented; small regressions and the
corrected 100 GiB matrix completed, 2026-09-08. The preserved original measurement is
`fidelity_100gib_20260908_run2`, six configurations × two loaders, with all
six additional read-only distinct audits complete.

The initial patch implements only the sequence/read-consistency correction. Descriptor
feasibility is a separate virtual-compaction estimation/splitting problem;
the current implementation also has feasible ranges exhausted by greedy
inverse generation. Neither path is changed by this patch. Global coordinated
key assignment is deferred because its performance cost is unresolved; the
proposal below is not an approved implementation requirement.

The subsequently requested rerun, `fidelity_100gib_20260908_seqfix_run3`, finished
at 14:23:04 KST. Its 1 GiB pilot and 100 GiB matrix each completed 12/12 cases;
all six baselines match the input distinct counts and all six corrected F2Load
iterators are strictly increasing. Cardinality errors remain, including
−19.427% / −19.520% for unique100 at 1024 B / 91 B KV. The old run2 DBs and
results remain unchanged. See the
[corrected-run comparison](../results/fidelity_100gib_20260908_seqfix_run3/full/run2_comparison/REPORT.md).

The new virtual-compaction diagnosis establishes exact-input merge/split
counterexamples, loss under repeated merge/split with complete sketches, and
failure of a standalone N−1 rescale. These are diagnostics and an isolated
prototype, not additional production corrections. The current design and
experiment order are in [Virtual compaction accuracy](VIRTUAL_COMPACTION_ACCURACY.md);
the sections below retain the original run2 diagnosis and proposal context.

`InstallVirtualCompactionMaterialization()` now assigns distinct file-global
sequences consistent with level and L0 epoch precedence, updates exact bounds,
serializes with writers, and publishes sequence counters plus SuperVersion
after MANIFEST commit. It keeps existing file levels and generated keys and
does not rewrite SST data. Installation failure now fails `fillvirtual`.
The release build and small regression evidence are kept under
`../artifacts/fidelity_seqfix_20260908/` and
`../artifacts/fidelity_seqfix_20260908_tests/`.

Validation passed for L1/L2 overlaps and overlapping L0 files through both the
new installer and a separately linked clean release reader: forward/reverse
iteration, Get/MultiGet, old snapshot/iterator visibility, sequence publication,
reopen, later Put, and compaction with a held snapshot. A 128 MiB real
`fillvirtual` comparison reproduced 9,748 repeated iterator rows with the old
binary and zero with the correction. Both generated D=128,780, M=126,988 and
U=117,240, with identical materialization-level file counts. Later physical
settling initially differed. After both small DBs were reopened with clean
RocksDB and the same normal compaction wait, both had zero pending bytes,
535 SSTs, 126,791 physical entries and 117,240 distinct keys. The old iterator
still returned 9,551 duplicates; the corrected iterator returned none. This is
not a loading-time comparison or a guarantee of identical trees across runs.
The existing 100 GiB DBs are preserved and have not been retroactively repaired
or reloaded with this correction.

## Confirmed failures and interpretation

1. All six F2Load public iterators return repeated user keys. Every original
   non-increasing transition is equality; there are no decreasing transitions.
   The independent bitmap duplicate count equals the adjacent-equal count.
2. All six F2Load distinct cardinalities differ from the verified input and
   baseline. Correcting iterator suppression cannot recreate missing keys.
3. All six have fewer materialized live SST entries than planned descriptor
   entries. Every missing entry in this step is attributed to
   `stop_reason=range_exhausted`; there are no Put failures or encoded-key skips.
4. For unique100, even the pre-materialization descriptor entry sum is below
   the input cardinality. The precise contributions of estimator error,
   repeated estimation, and splitting require additional instrumentation.

For unique100, the following is an accounting decomposition, not proof of
four independent causal mechanisms. Percentages use input unique count N.
D = live descriptor entries; M = materialized live entries;
R = final physical entries / returned rows; U = bitmap distinct keys.

| Difference | 1024 B KV | 91 B KV |
|---|---:|---:|
| N − D | 2,715,775 (2.590%) | 32,134,614 (2.723%) |
| D − M | 209,160 (0.199%) | 2,277,579 (0.193%) |
| M − R | 203,043 (0.194%) | 10,158,759 (0.861%) |
| R − U | 17,236,895 (16.438%) | 184,447,374 (15.632%) |
| N − U | 20,364,873 (19.421%) | 229,018,326 (19.409%) |

The dominant measured term is repeated synthetic keys in final SSTs. Since
unique100 covers the entire finite key domain, its distinct deficit is exactly
missing input keys. For the 50% inputs, equal cardinality would not establish
equal key membership. In 1024 B uniform50, the excess cardinality already proves
at least 1,189,463 returned keys absent from the input set.

Source evidence:

- `tools/db_bench_tool.cc:5944`: flush keys are summarized into PLR and KMV;
  full original key identity is not preserved in the descriptor.
- `db/virtual_compaction/virtual_sst.cc:176`: KMV union estimate is capped by
  input entry sum; `:316` uses it for range-aware merging. Asymmetric clipping
  of approximate estimates is a candidate for downward bias, not a measured
  attribution of the complete N−D difference.
- `tools/db_bench_tool.cc:6846`: each final VSST independently inverse-generates
  keys. Local monotonicity does not preserve cross-level key identity.
- `tools/db_bench_tool.cc:6877`: generation stops when its allowed range is
  exhausted, even before the planned number of entries is written.
- `table/sst_file_writer.cc:112` and `tools/db_bench_tool.cc:6982`: actual entries
  use sequence zero and files are installed directly with VersionEdit.
- `db/db_iter.cc:441`: release iterator optimization assumes a sequence-zero
  entry is not followed by another version of the same user key. This is a
  concrete mechanism consistent with the observed duplicate output; validate
  a causal fix with a controlled small fixture.

## Proposed correction order

### 1. Make validation and DB sequence semantics reliable

Retain separate counters for iterator rows, bitmap distinct keys, adjacent
equal keys, and decreasing keys. Do not call an invalid iterator row count
`exact_unique_keys`. D−M must be explicit and cannot be hidden by a successful
SST Finish or materialization status.

Use RocksDB's normal external-SST ingestion as a reference for valid sequence
assignment and publication. Direct installation into chosen levels needs the
same coherent handling of effective entry sequence, file metadata, internal-key
bounds, allocated/published/last sequence, and reopen recovery. Changing only
VersionEdit boundaries, giving every file the same positive sequence, or
assigning arbitrary per-file sequences is insufficient for full semantics.
Normal ingestion may change chosen levels, so it is a correctness reference,
not automatically the final tree-fidelity implementation.

Test two overlapping SSTs with different values using the clean release
reader: forward/reverse iteration, Get/MultiGet, reopen, compaction, snapshots,
and subsequent Put must agree on cardinality and the intended winning value.

### 2. Make every descriptor realizable and preserve its planned count

Require N ≤ available integer-key capacity before writing each file. The
unique100 short files include 46 (1024 B) and 52 (91 B) impossible descriptors.
For example, one 1024 B descriptor plans 63,606 entries in a range of only
54,832 integers. Fix counts and boundaries jointly at estimation/splitting;
otherwise fail explicitly before writing an incomplete file.

For feasible ranges, reserve room for all remaining output keys during inverse
generation. At output index i, constrain the candidate upper bound to
`key_max - (N - 1 - i)` and the lower bound to the allowed minimum / previous
key plus one. This can guarantee strict monotonicity and N entries within a
contiguous allowed range. Also measure the resulting CDF/rank distortion.
With restricted input membership, capacity and rank selection must instead
refer to allowed keys, not merely integer range width.

### 3. Preserve global cardinality and control cross-file key overlap

First establish an exact control for fully verified unique100 input: virtual
merges must preserve the sum of entries because input key sets are disjoint.
Do not infer this property solely from an unverified trace header. Skipping
dedup estimation in this control is not a general fix for repeated-key inputs.

Replace independent per-file key synthesis with a coordinated allocation that
controls the global union and intended intersections while respecting file
ranges, per-file counts, and level placement. For unique100 there should be no
cross-file repeated user keys. For repeated-key inputs, cross-level physical
versions are normal and must not all be removed; their intended overlap and
visibility must be modeled separately from global distinct count.

An exact input-membership bitmap is practical for this experiment: 12.5 MiB
for the 1024 B case and about 140.7 MiB for the 91 B case. It provides a useful
oracle and can constrain synthesis if exact input key membership is required.
It does not retain per-file ownership, version chronology, or values. Keeping
and merging exact per-descriptor key IDs supplies a stronger small-scale
reference, with additional CPU/memory cost.

If the product target is statistical synthetic fidelity, exact original key
identity need not be mandatory; global cardinality, key CDF, cross-file overlap,
level coverage and read-hit behavior still need explicit acceptance criteria.
If exact trace replay is the target, preserve key membership and version/value
semantics as well. KMV sample tuning or a final global count multiplier alone
does not resolve these requirements.

## Remaining validation and experiment sequence

Current scans validate key encoding/domain, value length and counts. They do
not compare complete key sets, Get versus iterator behavior, winning values,
read-hit distribution, or coverage fidelity. Trace files store only key IDs;
baseline generates values in input order and F2Load generates values per
materialization worker, so byte-identical per-key values are not guaranteed by
the present workload definition.

Saved metadata already shows structural differences, e.g. 91 B unique100
L4 SST count is baseline 1,567 versus F2Load 1,227. A single concurrently loaded
pair does not establish the size of structural error relative to scheduling
variation. Total SST bytes alone conceal cardinality errors: unique100 total
bytes are only about 3.0% / 3.8% below baseline while distinct keys are about
19.4% below it.

Proposed validation sequence: small deterministic regression fixtures, then a
pilot that deliberately reaches overlapping levels (the original 1 GiB pilot
did not), then the same 100 GiB matrix. Repeat the final matrix three times to
measure run variability. Keep the user's parallel execution mode for all 12
loads in each repetition and keep timing out of performance comparisons.
Parallel scheduling may still affect compaction selection and final tree shape.
Freeze a new binary/run ID for each meaningful correction and preserve run2.

Raw counts and source-backed diagnosis are in
`../artifacts/fidelity_100gib_20260908_run2/full/diagnostics_20260908/REPORT.md`
and its companion JSON/TSV files. This plan does not start a new experiment.
