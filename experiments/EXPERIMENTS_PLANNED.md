# Planned Experiments

Experiments queued up for later. Each entry has enough detail to pick
up without re-deriving the context.

## 2026-04-15: Fragmentation isolation (baseline 250GB) — RESOLVED (2026-05-13, different method)

**Status**: Resolved via a cleaner method on 2026-05-13. The original
plan (re-load N times and measure fill_run_1 each time) was
unnecessary once we realised the underlying variable is *NVMe
physical state of the data being read*, not *fragmentation of free
space around it*. The 2026-05-13 deep-copy experiment (
[RESULTS.md §3.3](RESULTS.md#33-nvme-physical-state-effect--2026-05-13-isolation-experiment))
isolated the effect: same DB content read at 141 µs/block from its
April LBAs vs 106 µs/block from fresh-copied LBAs — independent of
DB tree shape or surrounding load history.

Quantified split: ~13 pp of the original §3.1 −18.2 % elapsed gap
was NVMe state; ~5 pp was real tree-shape advantage.

The plan below is kept for the record.

---

### Original plan (fragmentation isolation via N reloads)

**Goal**: quantify how much of the 10~12% readrandom variance across our
batch measurements is driven by disk fragmentation vs. actual DB shape
differences. All current cross-DB comparisons have been contaminated by
the fact that DBs loaded on different days see different underlying
NVMe/md0 allocations.

**Setup**

- Mode: **baseline only** (fillrandom → compact0 → waitforcompaction,
  the existing `load.sh` flow)
- N = **20** fresh loads
- Reads: **1M keys**, 1 thread, cache=0 (same as our standard readrandom
  comparison)
- For each iteration i (1..N):
  1. `load.sh` creates `fill_run_i`
  2. `run.sh` executes `readrandom` against **only `fill_run_1`**
     (the first DB — the same logical content is read every time)

So there are N readrandom measurements, all on the *same* DB, but with
progressively more unrelated writes surrounding it on disk.

**What we expect to see**

- `filter_per_get`, `data_per_get`, `bloom_fpr` — should be constant
  (same DB, same queries). Sanity check.
- `elapsed_s`, `sst_read_p50`, `sst_read_p99`, `disk_read_MBs` — should
  drift (probably upward in latency) as new writes fragment the NVMe
  allocation around `fill_run_1`'s SSTs.
- The shape of that drift quantifies the fragmentation effect and tells
  us how to weight cross-DB comparisons.

**Cost**

- Loading dominates: 20 × ~15min = **~5 hours**
- Readrandom: 20 × ~7min = **~2.3 hours**
- Total: **~7.3 hours**, ~3.5 TB peak disk usage (20 × 177 GB)

**Implementation sketch**

New `fragmentation_exp.sh` orchestrating existing `load.sh` and `run.sh`:

```bash
BATCH_TS=$(date +%y%m%d_%H%M)
BATCH_DB=/work/vcomp/${BATCH_TS}_frag_baseline_x20
BATCH_LOG=log_batch/${BATCH_TS}_frag_baseline_x20
mkdir -p "$BATCH_DB" "$BATCH_LOG"

for i in $(seq 1 20); do
  # Load i-th fill DB
  LOG_DIR="$BATCH_LOG/loads/fill_run_${i}" \
    DB_DIR="$BATCH_DB/fill_run_${i}" \
    MODE=baseline TARGET_DB_GB=250 DB_ROOT="$BATCH_DB" \
    bash load.sh

  # Measure readrandom on fill_run_1 (always the same DB)
  RESULT_DIR="$BATCH_LOG/reads/fill_run_1_after_load_${i}" \
    WORKLOAD=readrandom DB_DIR="$BATCH_DB/fill_run_1" DB_SIZE_GB=250 \
    CACHE_PCT=0 THREADS=1 DURATION=0 READS=1000000 \
    bash run.sh
done
```

Aggregate via the existing `parse_runs.py` (extend if needed) or a
dedicated script that reads the per-iteration `summary.txt` + `stdout.txt`
and plots elapsed/latency vs. iteration index.

**Pre-flight checklist**

- [ ] `load.sh` accepts `DB_DIR` override (already true as of 2026-04-10).
- [ ] `run.sh` accepts `RESULT_DIR` override (already true as of 2026-04-14).
- [ ] Enough disk for 20 × 177 GB ≈ 3.5 TB (currently >40 TB free).
- [ ] No other batch jobs running that would pollute the disk state
      mid-experiment.

**Not in scope**

- vcomp loading — this experiment is about the baseline loading path
  and its interaction with NVMe allocation.
- Varying N by DB index (e.g. readrandom on fill_run_i as well) — we
  tried that idea and settled on "only fill_run_1" to keep the matrix
  small. One trajectory is enough to see the drift.

## 2026-05-12: Write-stall path in vcomp (continuation of 2026-05-11) — RESOLVED (different path)

**Status**: Tree-shape gap closed 2026-05-12, but **not via the write-stall
path proposed here**. The original premise — that the missing mechanism
was foreground stall driving L0 accumulation → bigger L0→L1 picks — was
discarded once we re-checked the numbers: vcomp's BG drains L0 fast
enough that a stall trigger basically never fires, so adding stall
wouldn't change anything.

What actually closed the gap was a different patch in
`db/compaction/compaction_picker_level.cc`: a vcomp-only **L0→L1 size
gate** at the picker (4 GB ≈ baseline's measured per-event L0→L1
input volume), combined with an end-of-load **L0 drain** in
`FillVirtual` that lowers the gate to 0 and lets BG fully empty L0 as
virtual compactions before Phase 2. After the drain, `compact0`
reports "found 0 files to compact" and the load is back to ~18 s.

Result: L2/L3/L4 file counts within 3 % of baseline (n=30 each), L3
cov within 1 pp of baseline, L4 cov identical. Full narrative in
[README.md §"2026-05-12"](../vcomp/README.md);
measurements in [RESULTS.md §2](RESULTS.md#2-tree-shape--3030-coverage-comparison).

Kept below for the record of what was originally planned and why we
chose a different path.

---

### Original plan (write-stall path)

**Context.** 2026-05-11 narrowed the tree-shape gap to two missing
mechanisms in vcomp:

1. **intra-L0 megafile output** — fixed today
   (`SplitIntoSSTs` early-returns one VirtualSST when `target_level==0`).
2. **forced intra-L0 firing** — fixed today
   (`PickFileToCompact` tries `FindIntraL0Compaction` at the top when
   `use_virtual_compaction`).

After both fixes, intra-L0 frequency matched baseline (996 vs 1054
events/run), but L0→L1 input fell to 4.84 (baseline 27.5) and L1
coverage moved from 65% → 61% (baseline 35%) — partial. The remaining
gap is L0→L1 input size, controlled by how much L0 accumulates before
the picker fires. Baseline gets natural L0 accumulation from RocksDB's
write stall (47.8% of write time is stalled). vcomp's
`RegisterVirtualL0File` bypasses `WriteController` and never stalls.

**Goal**: make vcomp's foreground respect RocksDB's natural write
stall so L0 accumulates baseline-like, intra-L0 cascade absorbs the
buildup, and L0→L1 picks larger batches.

**Implementation sketch**

- In `db/db_impl/db_impl_compaction_flush.cc` `DBImpl::RegisterVirtualL0File`,
  check the column family's stall conditions before applying the edit.
  RocksDB's normal write path calls
  `ColumnFamilyData::RecalculateWriteStallConditions` and uses
  `WriteController::WaitOnCV()` / `GetDelay()` to throttle the writer.
  vcomp needs to do the equivalent: either invoke `WriteController`
  directly or implement an explicit wait when
  `vstorage->NumLevelFiles(0) >= level0_slowdown_writes_trigger`.
- Default triggers: slowdown at 20, stop at 36. Worth re-testing with
  baseline's actual settings (the load.sh `L0_TRIGGER` env scales these
  proportionally; coupled mode = same as L0 trigger × 5 / × 9 — see
  load.sh:90–98).
- Don't apply the stall in Phase 2 materialization (which writes real
  SSTs and shouldn't be paced like virtual L0 registrations).

**Measurements after the fix (250 GB single load each)**

Compare against:
- baseline reference: 30-run batch at `260415_0635_250gb_x30/`
- vcomp default reference: 30-run batch at `260414_1710_250gb_x30/`
- vcomp w/ Fix 1+2 (today): `vcomp_250gb_forceintraL0` (preserved on
  disk)

Per-pair compaction summary (use `/tmp/compaction_summary.py`) and
coverage (use `dump_coverage.sh` + `parse_coverage.py`). Compare
specifically:
- L0→L1 events per run (target: ~76, currently 497)
- L0→L1 input mean (target: ~27.5, currently 4.84)
- L1 cov % (target: ~35%, currently 61%)
- L1 file widths (target: ~7 M, currently ~31 M)
- L1 → L2 events (target: 1418, currently 4369)

**Pre-flight checklist**

- [ ] Decide between calling `WriteController` directly vs adding an
      explicit L0-count wait in `RegisterVirtualL0File`. The former is
      more faithful to baseline but more invasive; the latter is a
      narrow patch.
- [ ] Verify Phase 2 materialization is not affected by whatever stall
      we add (it shouldn't go through `RegisterVirtualL0File`).
- [ ] After fix, sanity-check `Write Stall (count)` in the load's
      `bench.out` shows non-zero values comparable to baseline.

**Not in scope (yet)**

- N-run batch (30×) — finalize after a single load shows the metrics
  converged.
- L2/L3/L4 cov regressions — if the L0/L1 stall fix alone closes the
  full coverage gap, no further work is needed.
- Adjusting the `level0_slowdown_writes_trigger` / `stop_writes_trigger`
  defaults — only if the stock RocksDB values give the wrong shape.
