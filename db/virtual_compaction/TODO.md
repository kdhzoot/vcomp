# Virtual Compaction — Pending TODO

## Grandparent boundary split — deferred items

The initial grandparent boundary split implementation in `SplitIntoSSTs`
replicates only 2 of the 4 cut conditions in baseline RocksDB's
`CompactionOutputs::ShouldStopBefore` (`db/compaction/compaction_outputs.cc`).

Implemented (2026-04-10):
- [x] **Size-based hard cut** at `target_sst_size` (pre-existing).
- [x] **Dynamic threshold pre-cut** — at each GP boundary, cut when current
      output file is ≥ `target × (50 + 5 × switched)%`, capped at 90%.
      Uses bytes (`avg_entry_size × keys`) to mirror baseline's size check.

Deferred — implement only if tree shape still diverges from baseline:

- [ ] **Max compaction bytes cut**
      Baseline: cut if `grandparent_overlapped_bytes + current_file_size > max_compaction_bytes`
      (default `max_compaction_bytes = 25 × target_sst_size`, i.e. ~1.6GB).
      Prevents future compactions from being oversized. Rarely triggers on
      random workloads but may matter when grandparent files are unusually
      large or the output key range is wide.
      **How to implement**: For each GP boundary, track a running estimate of
      grandparent_overlapped_bytes (sum of grandparent file sizes overlapping
      the span since `last_split`). Our current code only knows positions,
      not grandparent file sizes — we'd need to pass `grandparent_sizes[]`
      alongside `grandparent_boundaries[]` from `RunVirtualCompaction` in
      `db_impl_compaction_flush.cc`.

- [ ] **Skippable grandparent cut**
      Baseline: cut if a single key crosses ≥3 grandparent boundaries at once
      (or ≥2 if currently in a gap) AND the new grandparent overlap adds more
      than `target/8` bytes. Prevents "skippable" grandparent files from
      being kept for future compaction.
      **How to implement**: Track `num_boundaries_crossed_at_this_key` and
      `being_grandparent_gap` state machine. This requires distinguishing
      grandparent file `smallest` vs `largest` boundaries (currently we
      flatten them into a sorted unique list, losing that information).
      Pass two parallel arrays `gp_smallest[]`, `gp_largest[]` or a tagged
      list instead.

## Measurement

After each fix, re-load 250GB and 1TB and compare tree shape (per-level file
count and average size) against baseline via:

```bash
cd eval-vcomp
LOG_DIR=log_loads/vcomp_$(date +%y%m%d_%H%M)_250gb \
  DB_DIR=/work/vcomp/vcomp_250gb MODE=vcomp TARGET_DB_GB=250 \
  DB_ROOT=/work/vcomp bash load.sh
grep -A 10 "^Level Files Size(MB)" log_loads/vcomp_*_250gb/bench.out | tail
```

Target: vcomp L1~L3 files should match baseline within ±5% in both count
and average size. Currently (as of dynamic threshold fix) needs
re-validation.
