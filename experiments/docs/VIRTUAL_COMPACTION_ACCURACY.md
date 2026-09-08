# Virtual compaction accuracy: measured failures and next experiments

Follow-up inspection: the earlier experiment on identical **real compaction
input SSTs** has been located. Its collector, retained June results, metric
limitations, and proposed reuse are documented in
[Accuracy on identical real compaction inputs](REAL_INPUT_COMPACTION_ACCURACY.md).
The diagnosis below records the run3/pre-discrete-candidate implementation;
see the repository README for the current working-tree implementation.

2026-09-08. The sequence-registration correction was rechecked in
`fidelity_100gib_20260908_seqfix_run3`, completed at 14:23:04 KST. The 1 GiB
pilot and 100 GiB full matrix each completed all 12 load/settle/scan cases.
All six full baselines match exact input cardinality; all six F2Load scans
pass strict iterator ordering, while substantial cardinality errors remain.
The six full traces match run2 by SHA-256, and all frozen executable hashes
match their initial manifest. This is one corrected-binary matrix repetition.

| Distribution | F2 U error, 1024 B KV | F2 U error, 91 B KV |
|---|---:|---:|
| unique100 | −19.427% | −19.520% |
| uniform50 | +2.253% | −0.405% |
| zipf99_50 | −4.362% | −5.363% |

The [run2/run3 D/M/U comparison](../results/fidelity_100gib_20260908_seqfix_run3/full/run2_comparison/REPORT.md)
uses old bitmap distinct counts and new strictly increasing iterator counts.
Scheduling can change approximate compaction outcomes, so numerical differences
between these two large runs are not attributed solely to the sequence patch.

The new accuracy experiments below use the existing, frozen implementation.
Only an isolated copy tests a rank-normalization change; it is **not adopted**.
No merge, split, dedup, or global key-allocation production change is included.

## What each measurement means

- N: independently verified distinct input keys.
- D: sum of live virtual-descriptor entry counts before materialization.
- M: entries actually written into the live materialized SSTs.
- U: distinct visible keys after reopening and normal compaction settling.

D and M include versions in different files; neither is generally a global
distinct count. With unique100 input, however, every input key is unique, so
any N−D decrease is erroneous relative to the original input. Correcting the
sequence-zero iterator behavior makes U readable correctly; it cannot restore
missing input keys. Old-run comparisons use the independent bitmap U, not the
old iterator's repeated rows.

The immediate objective is to improve cardinality and distribution/structure
fidelity. Exact key sets are small-experiment oracles. They do not imply a
requirement to preserve all key IDs or coordinate every output key in the
large production loader.

## 1. Existing 100 GiB logs locate descriptor-count changes

`summarize_virtual_entry_ledger.py` reads retained virtual-compaction job and
output records. Across all six run2 F2Load cases, the sum of logged estimated
dedup equals flush-local unique entries minus final D exactly. Every recorded
split preserves its merged target entry sum.

| Run2 case | Virtual jobs | Sum of estimated dedup | Capacity-invalid output descriptors | Sibling overlaps |
|---|---:|---:|---:|---:|
| 1024 B unique100 | 2,723 | 2,715,775 | 52 | 25 |
| 1024 B uniform50 | 2,110 | 42,839,625 | 0 | 188 |
| 1024 B zipf99_50 | 1,388 | 27,942,115 | 0 | 89 |
| 91 B unique100 | 2,702 | 32,134,614 | 58 | 11 |
| 91 B uniform50 | 2,077 | 487,695,848 | 1 | 1,078 |
| 91 B zipf99_50 | 1,351 | 283,827,640 | 0 | 572 |

Capacity-invalid means planned entries exceed `key_max − key_min + 1`.
These are all historical job outputs, not just surviving materialization
files. An output can be compacted away or its bounds subsequently trimmed.
Sibling overlap here is a logged boundary condition, not proof of a final
RocksDB level invariant violation.

This accounting identifies the count-changing stage; it does not attribute
the whole decrease to one KMV estimator formula. It also does not explain the
much larger final N−U deficit by itself. Evidence and per-output log locations:
[run2 entry ledger](../results/vcomp_accuracy_20260908/entry_ledger_run2/entry_ledger.json).

The completed run3 ledger reproduces the same accounting: all six dedup sums
close exactly and no split loses its requested entry sum. For unique100,
estimated dedup totals are 2,858,198 (1024 B) and 33,904,321 (91 B); historical
capacity-invalid output descriptors number 66 and 77, respectively.
See the [run3 ledger summary](../results/vcomp_accuracy_20260908/entry_ledger_run3/summary.tsv).

## 2. Exact inputs reveal a merge/split contract error

The actual exported functions were exercised with small, retained exact key
sets. Four dataset families cover disjoint interleaving, uniform duplicates,
a shared hot core, and wide gaps. Controls vary both global and range sketch
build budgets (512, 2,048, complete), PLR error (8, 1), and grandparent cuts.
They link the frozen release library with its native platform flags. No DB
or SST is written by these CPU probes.

The smallest decisive example has keys `{0,1,2,3,4}`, complete sketches,
zero PLR fitting error, and a split target of two entries:

| Output interval | Planned entries | Integer-key capacity |
|---|---:|---:|
| [0,1] | 2 | 2 |
| [2,2] | 2 | 1 |
| [3,4] | 1 | 2 |

Merge correctly chooses total five but produces slope 1.25; split preserves
the sum five while making one output impossible. Actual library-generated
keys contain only four distinct IDs. Directly fitting the exact union gives
slope one and five feasible keys. More samples cannot repair this example.

The current merge accumulates mass over inclusive intervals and normalizes
the model to a position span of N. The fitter and splitter use ranks 0…N−1.
There are additional local boundary issues: inclusive adjacent range queries,
independent inverse rounding, and no joint count/capacity constraint.
See the [single-stage report](../results/vcomp_accuracy_20260908/native/REPORT.md).

## 3. Repeated merge/split loses information even with complete sketches

A closure control starts with four exact input files and repeatedly merges
and splits only the previous round's output descriptors. The original key set
stays fixed; no new keys or generated materialization keys enter the chain.

| Control | Original N | Target D in rounds 1 → 4 | Generated distinct U in rounds 1 → 4 |
|---|---:|---|---|
| Disjoint, 512 samples | 16,384 | 16,384 → 16,384 → 16,384 → 16,384 | 16,381 → 15,656 → 15,475 → 15,451 |
| Hot shared core, complete sketches | 8,192 | 8,192 → 8,182 → 8,173 → 8,164 | 8,192 → 8,181 → 8,170 → 8,158 |

In the complete hot-core control, retained sample-ID unions are
8,182 → 8,173 → 8,164 → 8,153, although every global/range sketch remains
marked complete. Split bounds exclude some original keys, range filtering
discards their samples, and later exact counting sees an already reduced set.
Completeness relative to a current descriptor is not completeness relative
to the original union. Expanding a later descriptor's range does not recover
the previously discarded IDs. KMV samples identify original logical keys;
PLR/select describe reconstructed model keys. A complete sketch does not
establish that these two sets agree.

The disjoint control separates shape distortion from count estimation:
D never changes, but generated distinct counts decline. The complete control
shows that incomplete-sketch sampling error is not required for repeated
information loss.
Neither is a measurement of the normal scheduler's exact multi-level history
or a quantitative attribution of the 100 GiB deficit.
See the [four-round report](../results/vcomp_accuracy_20260908/chain/REPORT.md).

## 4. A global N−1 rescale is insufficient

An isolated candidate changes only the merge position span to N−1, including
its singleton and zero-local-mass branches. It fixes all 18 small dense
conditions. With 20 random/gapped datasets across six PLR/split settings,
however, conditions with generated cardinality loss rise from 63/120 to
95/120, and capacity violations rise from 27/120 to 39/120.

For one zero-fit-error case, N=D=49 and endpoint ranks are correctly 0 and 48,
yet an output assigns two entries to `[81,81]` and only 47 distinct keys are
generated. The candidate is not a production fix. Global endpoints and local
discrete mass/boundary semantics must be corrected together.
See the [isolated candidate report](../results/vcomp_accuracy_20260908/rank_prototype/REPORT.md).

## 5. Parameters matter after the representation contract is sound

Thirty-two deterministic key-domain offsets per dataset isolate one KMV union
estimate before model merging. For disjoint input, mean error changes from
−0.859% at 512 samples to −0.245% at 2,048. Because the estimate is capped at
the sum of input entries, this disjoint control retains negative errors while
clipping positive ones. Other overlap families show both error signs.

These small measurements support testing sample budget and uncertainty, but
do not prove a universal estimator bias or explain a specific large run.
Changing only the estimate function's `max_samples` argument does not change
the retained input information: sketch **construction** budgets must vary.
The default stores a global sketch plus range-local sketches; 512 is not the
combined total number of stored samples.

Smaller PLR error also does not guarantee a better merged distribution:
it changes breakpoints and local range queries. In the complete hot-core
single-stage control, merged generation has the correct cardinality 8,192
but misses 5,528 original keys and invents 5,528 others at PLR error eight.
Cardinality, CDF error, membership, and cross-file intersections are different
acceptance measures.

## Next implementation and experiment order

1. Define one discrete cumulative-count convention for merge, split, inverse
   selection, and sketch range restriction. Candidate form: half-open integer
   intervals with `F(b) = count(keys < b)`, nonnegative local mass bounded by
   integer-key capacity, and local masses reconciled with the global target.
   Specify empty/singleton cases, flat gaps, boundary ties, and the endpoint
   above UINT64_MAX. Keep PLR rank and cumulative-count meanings explicit.
2. Make split partition support and counts together. Require child counts to
   sum to the target, valid capacity in every output, disjoint sibling
   ownership, consistent range-bucket counts, and no unaccounted loss of
   retained sample IDs. Merely moving the final SST bounds cannot restore
   samples discarded earlier. Check preservation through repeated splitting
   and remerging, including complete-sketch controls. Protect retained samples
   as witnesses/anchors at metadata scale, while distinguishing raw estimates
   of original keys from counts certified by the reconstructed model's CDF.
   The latter are not proven upper bounds on original-key cardinality.
3. Reassess KMV union estimation and repeated clipping using disjoint and
   overlapping exact oracles. Vary compaction depth/fan-in and sampling
   independently. Record unclipped estimates, chosen targets, theta, sample
   counts, local mass adjustments, and any fallback. Avoid treating a final
   count multiplier as a validated calibration.
4. Sweep samples 512/1,024/2,048/4,096 and range buckets 4/8/16 only after the
   preceding invariants pass. First hold PLR error fixed, then test 8/4/1.
   Include metadata bytes and CPU cost alongside count/CDF errors. Full exact
   sets remain small controls, not a large-workload implementation requirement.
5. Qualify a corrected candidate on a pilot that reaches overlapping levels,
   then repeat the same 100 GiB matrix under fresh run IDs. Use three completed
   repetitions of the same candidate for variability; run3 alone is one
   corrected-binary repetition. Keep the user's 12-load parallel mode and
   exclude these runs from load-time performance claims.

A local count/boundary correction can improve descriptor fidelity without
coordinating every generated key across the DB. It does **not** guarantee
global U when separate levels independently generate overlapping synthetic
keys. If that term remains dominant after the local corrections, it must be
measured and addressed separately; this work does not promise that merge/split
changes alone eliminate the approximately 19.5% unique100 deficit.

Source entry points: `db/virtual_compaction/virtual_sst.cc` (union estimates,
range-aware merge, sketch filtering, split), `plr_model.cc` (fit/inverse), and
`tools/db_bench_tool.cc` (streaming materialization). The probes measure actual
`MaterializeKeys` distinct sets, not its raw vector length as an SST count;
the separate native streaming reference corroborates per-file counts.

The [discrete-CDF design note](../results/vcomp_accuracy_20260908/rank_prototype/DESIGN.md)
specifies the proposed rank/select contract, integer mass reconciliation,
preservation of rounding phase across splits, and limits of local guarantees.
It is a design proposal, not an implemented or performance-qualified fix.
