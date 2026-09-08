# Virtual-compaction accuracy evidence — 2026-09-08

This small, versionable bundle preserves the canonical CPU-only microexperiment observations and their analysis. No DB or SST was opened or written by these probes. The measurements used exported production APIs in the frozen seqfix static library; they are separate from the 100-GiB DB rerun.

| Evidence | Contents |
|---|---|
| [Single-stage report](native/REPORT.md) | 100 pipeline observations and 256 isolated KMV estimate trials; merge, split, range capacity, and generated key membership are measured separately. |
| [Repeated merge/split report](chain/REPORT.md) | 36 round observations: three fixed original key sets × three sketch budgets × four rounds, with no new input keys. |
| [Rank prototype report](rank_prototype/REPORT.md) and [design](rank_prototype/DESIGN.md) | Separately archived prototype evidence and proposed representation changes. |
| [Run2 entry-ledger summary](entry_ledger_run2/summary.tsv) | Separately archived measurements from the existing 100-GiB run's virtual-compaction logs. |
| [Run3 entry-ledger summary](entry_ledger_run3/summary.tsv) | The completed sequence-corrected 100-GiB rerun; all six count ledgers close exactly, and split-capacity failures remain. |

The integrated interpretation and next experiment order are in
[Virtual compaction accuracy](../../docs/VIRTUAL_COMPACTION_ACCURACY.md).

The single-stage experiment confirms an exact-sketch counterexample: keys `{0,1,2,3,4}` retain target five after merge, but one split descriptor plans two entries inside singleton range `[2,2]`. Actual `MaterializeKeys` output has four distinct keys across files. Increasing sketch accuracy alone cannot fix this range/rank contract failure.

The chain experiment separates descriptor count conservation from representation fidelity. Complete sketches stay marked complete while retained original sample IDs shrink; the hot-key control's target becomes `8192 → 8182 → 8173 → 8164` without new input. Conversely, the disjoint 512-sample control preserves target 16384 in every round while generated distinct counts decline. These controls identify possible mechanisms; they do not assign fractions of the 100-GiB DB deficit to them.

Generated distinct counts refer to exact set counts from the library helper, not measured SST writes. The count-only reference to the production materialization walk is a separate corroborating diagnostic. Cardinality, key membership, range coverage, and retained sketch IDs must not be conflated.

## Read and verify without rebuilding

From the repository root:

```sh
python3 -B experiments/results/vcomp_accuracy_20260908/validate_bundle.py
```

This standard-library-only command reads the bundle, verifies the archived copies' SHA-256 hashes and sizes, cross-checks JSONL/CSV observations, and checks the reported stage invariants. It needs neither the original absolute paths nor `librocksdb.a`; it does not write files or run probes. Its inventory scope is `native/`, `chain/`, and their shared source snapshot, so independently archived prototype and DB-ledger evidence can coexist here.

The canonical raw records are [native/probe.jsonl](native/probe.jsonl) and [chain/probe.jsonl](chain/probe.jsonl). The checked-in CSVs and reports are derived from those records. To regenerate the summaries in a disposable copy of this bundle:

```sh
python3 -B native/summarize_probe.py
python3 -B chain/summarize_chain.py
```

Those scripts rewrite their local reports/CSVs and create redundant pretty-printed `results.json` files. They do not rerun the experiments. The redundant JSON files were excluded from this archive to keep it small.

## Provenance and scope

[ORIGIN_INVENTORY.json](ORIGIN_INVENTORY.json) records original paths, byte sizes, and SHA-256 hashes for every copied file and the excluded binaries, build logs, static library, and preliminary portable probe. Archived measurement files are byte-identical to their originals. [bundle_manifest.json](bundle_manifest.json) records the archive scope and original compile working directory; [native/manifest.json](native/manifest.json) and [chain/manifest.json](chain/manifest.json) retain exact compiler/run argv, exit codes, parameters, and library/source hashes.

Probe source snapshots are included beside their measurements. The four measured PLR/virtual-SST core files are under [source_snapshot/db/virtual_compaction](source_snapshot/db/virtual_compaction). The frozen library SHA-256 is `d0fc6439d78880351943275a663a2e9a30bc0f2f18426c87a81289bdc0361014`. The recorded repository HEAD is archival context, not a claim that all measured sources came from a clean commit.

The exact builds used GCC 11, release flags, and `-march=native`. Compiler/CPU floating-point behavior matters for this probe, so the bundle preserves measured evidence without claiming that an arbitrary fresh build will be bit-identical. Full binaries and the large static library are intentionally omitted; hashes identify the original build artifacts.

Original local artifact root: `/home/smrc/virtual_compaction/vcomp/experiments/artifacts/vcomp_accuracy_20260908`. References in the byte-preserved reports to a parent directory, preliminary source, or omitted `results.json` refer to that original tree or files regenerated by the summary scripts. The separately managed prototype's local source artifacts are in its `rank_prototype/` subdirectory. The root-level origin inventory covers only this native/chain archive; later sibling evidence has independent provenance.
