# PLR-Based Virtual Compaction

This README is the current implementation spec for `fillvirtual` and the
historical notebook for how the design got here.

- **Current implementation**: scope, run commands, defaults, architecture,
  algorithms, correctness constraints, and open work.
- **Historical changelog**: dated notes for past experiments. Treat old entries
  as snapshots from that date, not as the current design.

Measurement results (current best numbers) live in
[experiments/docs/RESULTS.md](experiments/docs/RESULTS.md). Queued
experiments live in
[experiments/docs/EXPERIMENTS_PLANNED.md](experiments/docs/EXPERIMENTS_PLANNED.md).
Git-published result bundles are indexed in
[experiments/results/README.md](experiments/results/README.md). For the shared
directory convention and setup on another server, see
[experiments/README.md](experiments/README.md).

---

## Current State

Fidelity qualification, 2026-09-08: this working tree includes an experimental
discrete-CDF correction for merge, split, and materialization. Its six 100 GiB
F2Load cases preserve descriptor counts through SST writing and pass strict
iterator checks. Global distinct-key fidelity remains inaccurate, and small
ECDF controls expose distribution regressions. This is a count-consistency
candidate, not a completed fidelity correction. The working tree also adds
`--vcomp_global_unique_keys`, an opt-in Phase 2 mode for 100%-unique inputs
that materializes a globally distinct key set. Its 100 GiB run
(`fidelity_100gib_20260908_unique_run5`) cuts the unique100 distinct-key error
from −19.2% to −3.6%/−3.8% with zero repeated keys, and its remaining loss is
60x above the proven lower bound for the same descriptors, so the shallow-first
allocation order is not optimal. See the
[same-input accuracy experiment review](experiments/docs/REAL_INPUT_COMPACTION_ACCURACY.md) and
[preceding diagnosis](experiments/docs/VIRTUAL_COMPACTION_ACCURACY.md).

As of 2026-07-29, this branch is focused on the synthetic metadata-only
`fillvirtual` loader. Twitter/trace KV work is parked; the paper path is the
synthetic loader plus final materialization.

The goal is to produce a tree **structurally identical to baseline RocksDB**,
not a "better" tree. Any structural divergence is treated as a bug.

Virtual Compaction replaces RocksDB's I/O-intensive compaction with
lightweight **Piecewise Linear Regression (PLR) model merging** during
bulk loading. No actual SST data is written until the final
materialization step.

```
Traditional:  Write keys → Memtable (SkipList)
              → Flush (write SST) → Compaction (read+merge+write SSTs)

Virtual:      Generate keys → vector buffer → Radix sort → PLR Fit
              → Queue/refill virtual L0
              → BG PLR Merge (concurrent)
              → Materialize + SST write (direct I/O)
```

The virtual path bypasses the RocksDB memtable entirely. Keys are
accumulated in a flat `std::vector<uint64_t>` and bulk-sorted with radix
sort before PLR fitting, trading the O(N log N) per-insert SkipList cost
for O(N) batch radix sort.

Current implementation:

- `fillvirtual` defaults to `--vcomp_sst_size_model=calibrated`. Bounded
  in-memory SST probes with the materialization options fit physical bytes
  (including table overhead/compression) for virtual registration and splitting.
  Logical KV bytes still determine input batch capacity. Use `logical` to
  reproduce the old size estimate, not the old binary/merge implementation.
  The model is approximate; per-level predicted/actual byte diagnostics and
  final compaction draining remain required. See
  [SST size model and validation](experiments/docs/SST_SIZE_MODEL.md).
- Phase 1 queues generated L0 VSSTs in an in-memory pending window.
- `RefillVirtualL0Window()` registers only a bounded visible L0 window through
  batched VersionEdits. The refill condition is:

  ```
  virtual_l0_visible_bytes_ < target_bytes
  ```

- Registered virtual L0 files are immediately visible to RocksDB's regular L0
  score and compaction picker. There is no hidden eligibility bit and no
  separate visibility worker.
- Code defaults are
  `--vcomp_register_batch_max=256` and `--vcomp_visible_l0_batch_mb=0`;
  zero means use the column family's `max_compaction_bytes` as the visible L0
  byte target.
- BG virtual compaction commits are batched through a commit queue. Code
  defaults are `VCOMP_BG_COMMIT_BATCH_MAX=16` and
  `VCOMP_BG_COMMIT_DELAY_US=100`.
- Virtual compaction successful jobs defer obsolete-file collection/purge;
  `fillvirtual` performs one `CleanupVirtualCompactionObsoleteFiles()` call
  after BG drain and before materialization.
- VersionBuilder has a vcomp-friendly unchanged-level fast path in
  `SaveSSTFilesTo()`, and compaction-priority scoring avoids the old temporary
  score map.
- Latest 1 TB `SaveSSTFilesTo()` bulk unchanged-level fast path run reduced
  `SaveSSTFilesTo` from `6.684 s` to `4.393 s` and total `LogAndApply` from
  `17.125 s` to `14.325 s`. End-to-end time was not compared cleanly because
  the run overlapped a clean-RocksDB 8 TB baseline write.
- Current best 5 TB metadata-only synthetic run:
  `589.665 s = phase1 136.652 + bg_wait 386.706 + phase2 66.307`.
- Current 10 TB run with the same cap16/delay100 setting:
  `2461.699 s = phase1 260.858 + bg_wait 2063.894 + phase2 136.946`.
- The main current bottleneck is still serialized VersionSet metadata work in
  `LogAndApply`, especially `SaveSSTFilesTo`, compaction priority rebuild, and
  level-file brief/index rebuild.
- Motivation baseline scaling experiment setup lives in
  `experiments/docs/MOTIVATION_LOAD_SCALING.md` and
  `experiments/scripts/load/run_motivation_load_scaling.sh`.
- Latest baseline/vcomp-version comparison CSV:
  `experiments/artifacts/log_loads/load_comparison_versions.csv`.
- Experiment runners, analysis code, documentation, curated results, and raw
  artifacts are managed together under [experiments/](experiments/README.md).

---

## Quick Start

Build from this repository:

```bash
./make.sh
```

Run the standard experiment wrapper from this repository:

```bash
MODE=vcomp TARGET_DB_GB=1000 DB_ROOT=/work/vcomp \
  experiments/scripts/load/load.sh
```

The wrapper defaults to the same current values as the code:

| Setting | Default | Meaning |
|---------|---------|---------|
| `--vcomp_register_batch_max` / `VCOMP_REGISTER_BATCH_MAX` | `256` | Max pending virtual L0 files registered per VersionEdit |
| `--vcomp_visible_l0_batch_mb` / `VCOMP_VISIBLE_L0_BATCH_MB` | `0` | Visible virtual-L0 byte target in MiB; zero uses `max_compaction_bytes` |
| `VCOMP_BG_COMMIT_BATCH_MAX` | `16` | Max virtual compaction commit requests grouped into one manifest write |
| `VCOMP_BG_COMMIT_DELAY_US` | `100` | Leader wait before draining the virtual commit queue |
| `--vcomp_log_apply_timing` / `VCOMP_LOG_APPLY_TIMING` | `true` | Collect detailed LogAndApply timing breakdowns |

`--vcomp_visible_l0_batch_mb=0` is the default: `db_bench` uses the effective
column-family `max_compaction_bytes` as `target_bytes`.

---

## Runtime Design

`fillvirtual` has three runtime phases:

1. **Generate and register a bounded L0 window**
   - Generate keys in flat vectors.
   - Radix-sort each batch.
   - Fit a PLR model and enqueue a virtual L0 file in memory.
   - Register pending virtual L0 files only while
     `virtual_l0_visible_bytes_ < target_bytes`.

2. **Run background virtual compactions**
   - Registered virtual L0 files are normal picker-visible RocksDB files from
     the metadata path's point of view.
   - If a picked compaction has at least one input in `VirtualSSTRegistry`,
     `BackgroundCompaction` runs `RunVirtualCompaction()`.
   - The virtual compaction merges PLR models, splits outputs using
     RocksDB-like grandparent boundary rules, and commits the VersionEdit
     through the virtual commit queue.

3. **Materialize remaining virtual files**
   - After BG compaction drain, each remaining VirtualSST is converted to real
     keys by the discrete-CDF cursor (or legacy PLR inverse walking when the
     discrete path is disabled).
   - Workers write real SST files with direct I/O. With
     `--vcomp_global_unique_keys` they run one level group at a time so that
     each file can exclude the key ids the levels above it already generated.
   - One final VersionEdit deletes virtual files and adds the materialized real
     SSTs.

Important current semantics:

- Registered virtual L0 files are immediately visible. There is no
  separate visibility or eligibility bit.
- L0 refill is byte-targeted by `virtual_l0_visible_bytes_ < target_bytes`.
- The real-vs-virtual dispatch decision is based on registry membership of
  compaction inputs, not on file-level eligibility metadata.

---

## PLR Model

### Core idea

A sorted sequence of N keys $k_0 < k_1 < \cdots < k_{N-1}$ defines a
cumulative distribution function (CDF):

$$
\text{pos}(k_i) = i
$$

PLR approximates this mapping with a small number of linear segments:

$$
\text{pos}(k) \approx s_j \cdot k + b_j \quad \text{for } k \in [k_j^{\text{start}}, k_j^{\text{end}}]
$$

```cpp
struct PLRSegment {
    uint64_t key_start, key_end;   // key range [key_start, key_end]
    double   slope, intercept;     // pos(k) = slope * k + intercept
};
```

### Greedy-PLR fit (shrinking-cone algorithm)

Given sorted keys and an error bound $\delta$, the algorithm greedily
extends each segment as far as possible while keeping the prediction
error within $\pm\delta$.

```
Input:  sorted keys K[0..N-1], error bound delta
Output: list of PLRSegments

seg_start = 0
while seg_start < N:
    x0 = K[seg_start],  y0 = seg_start
    s_lo = -inf,  s_hi = +inf         // feasible slope range (the "cone")

    for i = seg_start+1 to N-1:
        dx = K[i] - x0
        dy = i - y0
        if dx == 0: continue           // duplicate key
        new_s_lo = (dy - delta) / dx
        new_s_hi = (dy + delta) / dx
        if new_s_lo > s_hi or new_s_hi < s_lo:
            break                       // can't extend, close segment
        s_lo = max(s_lo, new_s_lo)
        s_hi = min(s_hi, new_s_hi)
        seg_end = i

    slope     = (s_lo + s_hi) / 2      // midpoint of feasible range
    intercept = y0 - slope * x0
    emit PLRSegment(K[seg_start], K[seg_end], slope, intercept)
    seg_start = seg_end + 1
```

**Properties**
- Time complexity: O(N) — single pass, each key examined once.
- Output: S segments where $S \approx N / \delta$ for uniform input.
- Guarantee: $|\text{pos}_{\text{predicted}}(k) - \text{pos}_{\text{actual}}(k)| \leq \delta$.

### PLR inverse

For a model with a discrete certificate, `Predict(key)` delegates to integer
`CountLessThan(key)` and `Inverse(position)` delegates to rank selection.
Split and materialization call the integer API directly. The continuous
formula below applies to a model without a certificate.

Given a rank, recover the estimated key:

$$
k = \frac{\text{pos} - b_j}{s_j}
$$

The implementation finds the segment whose position range
$[\text{pos}(k_j^{\text{start}}), \text{pos}(k_j^{\text{end}})]$ contains
the target position (or is closest to it), then applies the inverse
formula with clamping into the segment's key range.

```cpp
uint64_t Inverse(double position) {
    best_seg = argmin_j |distance(position, [pos_lo_j, pos_hi_j])|
    seg = segments[best_seg]
    if |seg.slope| < epsilon:
        return (seg.key_start + seg.key_end) / 2
    key = (position - seg.intercept) / seg.slope
    return clamp(key, seg.key_start, seg.key_end)
}
```

---

## Discrete CDF and KMV merge candidate

The working-tree default is `VCOMP_KMV_ENABLED=1` and
`VCOMP_DISCRETE_CDF_ENABLED=1`. Set the latter to `0` to run the legacy KMV
model path in the same build. Disabling KMV retains the older continuous
merge path. Record both environment values when comparing experiments.

`DiscreteCDF` represents half-open integer intervals with exact cumulative
mass `F(b) = count(keys < b)`. Local mass cannot exceed integer-key capacity.
For an interval of span C with m entries, zero-based rank t selects
`lo + ceil((t+1)*C/m) - 1`. Wide arithmetic handles the endpoint above
`UINT64_MAX`. Split slices rank intervals while retaining the original
rounding phase, so child counts and generated sets partition the parent.

`BuildDiscreteMergeModel` retains input extrema and sampled key IDs as
mandatory witnesses. It uses range-KMV estimates as local allocation weights,
projects the global KMV target into feasible capacity/witness bounds, and
reconciles integer masses by capped weighted allocation. Sample witnesses
are preserved without retaining or enumerating all original keys. Initially
fitted flush descriptors are certified through the same builder.

`EstimateKMVUnionEntries` reports the merged distinct count as a dedup ratio
applied to the summed input counts. The K-minimum samples of every input are
merged under one theta, and `sampled_unique / sampled_entries` scales
`naive_entries`; theta cancels, so the estimate carries no absolute-cardinality
sampling error, is at most one by construction, and returns `naive_entries`
exactly for disjoint inputs. `EstimateKMVUnionEntriesForRange` multiplies the
same ratio by the proportional in-range density. The earlier form rescaled the
sample count by `1/theta` and clamped it to `naive_entries` from above only,
which turned sampling noise into a systematic undercount on every merge.

The model's exact reconstructed count and the sketch's estimated original
count are distinct. Output range buckets store both values; a reconstructed
count is not reused as an upper bound on original-key cardinality. Legacy
PLR segments remain as compatibility metadata, while the attached certificate
controls rank, split boundaries, and key generation.

The streaming cursor performs division at cell boundaries and advances
within each cell with integer quotient/remainder arithmetic. Materialization
requires exactly the descriptor's count and fails before final registration
on cursor, count, or encoded-order errors. Compaction validates output count,
capacity, and sibling ranges before applying input deletions.

These are local invariants. They do not guarantee original key membership,
accurate incomplete-sketch estimates, preservation of the initial PLR error
bound, or global uniqueness across separately generated files. In particular,
the candidate's allocation weights can distort ECDF shape; the linked
experiment record reports that failure alongside count-conservation results.
`discrete_cell_payload_bytes` counts 64 bytes per logical CDF cell, not RSS,
allocator overhead, sketch memory, or peak live memory.

## Legacy continuous N-Way PLR Merge

The following continuous merge and density-dedup explanation describes the
non-KMV path. It is not the default discrete/KMV algorithm above.

### Mathematical foundation

When merging N sorted sequences each with its own PLR model
$\text{pos}_i(k)$, the merged rank at key $k$ is:

$$
\text{pos}_{\text{merged}}(k) = \sum_{i=1}^{N} \text{pos}_i(k)
$$

Since each $\text{pos}_i$ is piecewise linear, the sum is also piecewise
linear. The breakpoints of the merged model are the **union** of all
input segment boundaries.

### Algorithm

```
Input:  N PLR models with their key ranges
Output: merged PLR model

1. Collect all segment boundaries (key_start, key_end) from all models.
2. Sort and deduplicate → breakpoints[].
3. For each interval [breakpoints[i], breakpoints[i+1]]:
     slope_sum     = 0
     intercept_sum = 0
     for each active model j (key_min_j ≤ midpoint ≤ key_max_j):
         find the segment of model j covering midpoint
         slope_sum     += seg_j.slope
         intercept_sum += seg_j.intercept
     for each finished model j (key_max_j < midpoint):
         intercept_sum += num_entries_j   // all keys are before this point

     if dedup:
         dedup_prod    = Π (1 - clamp(slope_j, 0, 1)) for each active j
         adjusted     = 1 - dedup_prod
         emit PLRSegment(breakpoints[i], breakpoints[i+1], adjusted, 0)
     else:
         emit PLRSegment(breakpoints[i], breakpoints[i+1],
                         slope_sum, intercept_sum)

4. (dedup only) Recompute intercepts for continuity:
     cumulative_pos = 0
     for each segment:
         segment.intercept = cumulative_pos - segment.slope * segment.key_start
         cumulative_pos += segment.slope * (segment.key_end - segment.key_start)
     adjusted_total = round(cumulative_pos)

5. Merge adjacent segments with identical (slope, intercept).
```

### Why slope/intercept addition works

Consider two sorted sequences A and B merged into C. For any key k:

$$
\text{rank}_C(k) = \text{rank}_A(k) + \text{rank}_B(k)
$$

If $\text{rank}_A(k) = s_A k + b_A$ and $\text{rank}_B(k) = s_B k + b_B$
in some interval, then:

$$
\text{rank}_C(k) = (s_A + s_B) k + (b_A + b_B)
$$

This is still linear, so slope and intercept simply add.

### Probabilistic dedup correction

The naive merge counts every key from every input, including duplicates
across levels. For random workloads, duplicate keys can be estimated
probabilistically without tracking individual keys.

**Key insight**: each PLR segment's slope is key density (keys per unit
of key space). When multiple models overlap in `[a, b]`, the probability
that a key position is occupied by model $i$ is `slope_i` (clamped to
`[0, 1]`). The expected unique density follows the inclusion-exclusion
principle:

$$
\text{adjusted\_slope} = 1 - \prod_{i=1}^{N} (1 - s_i)
$$

After computing adjusted slopes, intercepts are recomputed to maintain
position-function continuity. The total `adjusted_entries` is capped to
never exceed `naive_entries` (PLR approximation error at low densities
can briefly cause `adjusted > naive`).

### Adjacent-segment merging

The breakpoint-based merge produces one segment per sub-interval; many
adjacent segments end up with identical `(slope, intercept)` because
consecutive intervals share the same active set. They are merged with
tolerances `|s1 - s2| < 1e-12` and `|b1 - b2| < 1e-9`. Without this
step, the segment count after N-way merge equals the breakpoint count
(potentially thousands).

---

## Virtual SSTs

### Structure

A virtual SST stores no key/value data — only the PLR model and metadata:

```cpp
struct VirtualSST {
    PLRModel plr_model;     // local rank mapping (rank starts at 0)
    uint64_t key_min;       // smallest key
    uint64_t key_max;       // largest key
    uint64_t num_entries;
    int      level;
    uint64_t size_bytes;    // = num_entries * avg_entry_size
};
```

The registry (`VirtualSSTRegistry`) maps `file_number → VirtualSST*`
under a thread-safe map and is consulted by `RunVirtualCompaction` and
the BG dispatch logic.

### SplitIntoSSTs — output sizing for virtual compaction

After a merged model is computed, it is split into output virtual
SSTs. The split logic mirrors RocksDB's
`CompactionOutputs::ShouldStopBefore` so that the resulting tree shape
matches a real compaction. With a discrete certificate, cuts are exact rank
positions, child bounds use `Select`, and child models use phase-preserving
`Slice`; the size and grandparent policy below remains in use:

```
Inputs:
  plr               : merged PLRModel
  total_entries     : number of (deduped) keys in plr
  target_sst_size   : per-level target file size
  avg_entry_size    : key + value bytes
  global_min/max    : key range covered by plr
  target_level      : LSM level the outputs go to
  grandparent_boundaries : sorted unique grandparent file
                           {smallest, largest} keys for the next level

# Hard size cap for non-bottom levels matches baseline:
#   max_output_file_size = bottom || no grandparents
#                          ? target_sst_size
#                          : 2 * target_sst_size
has_grandparents = grandparent_boundaries.empty() == false
                   AND target_level > 0
max_sst_size     = has_grandparents ? 2 * target_sst_size : target_sst_size
keys_per_sst     = max(1, max_sst_size / avg_entry_size)

# Convert grandparent boundary KEYS to integer POSITIONS via plr.Predict.
# Positions are NOT deduplicated — adjacent grandparent files contribute
# (largest_i, smallest_{i+1}) pairs that often round to the same integer
# position but represent two distinct boundary transitions in baseline's
# state machine. Removing one halves the `switched` counter and breaks
# the dynamic threshold (see below).
gp_positions = sorted([round(plr.Predict(b)) for b in grandparent_boundaries
                       if global_min < b < global_max
                       and 0 < plr.Predict(b) < total_entries])

# Build cut points by scanning size-based cuts and grandparent boundaries
# together. Mirrors baseline's ShouldStopBefore dynamic threshold.
split_positions = []
last_split   = 0
next_size_cut = keys_per_sst
switched      = 0          # GP boundaries crossed since last cut
gp_idx        = 0
while next_size_cut < total_entries OR gp_idx < len(gp_positions):
    next_gp = gp_positions[gp_idx] if gp_idx < len(gp_positions) else total_entries

    if next_size_cut <= next_gp AND next_size_cut < total_entries:
        # Hard size cap fired (file reached max_sst_size).
        split_positions.append(next_size_cut)
        last_split    = next_size_cut
        next_size_cut = last_split + keys_per_sst
        switched      = 0
        # Skip GP boundaries we passed.
        while gp_idx < len(gp_positions) and gp_positions[gp_idx] <= last_split:
            gp_idx += 1
    elif next_gp < total_entries:
        # Dynamic threshold cut at GP boundary, evaluated in BYTES.
        # Mirrors baseline ShouldStopBefore: pre-cut at target_sst_size *
        #   (50 + 5*switched)% capped at 90%.
        switched += 1
        cur_bytes        = (next_gp - last_split) * avg_entry_size
        pct              = 50 + min(switched * 5, 40)
        threshold_bytes  = (target_sst_size * pct) / 100
        if cur_bytes >= threshold_bytes:
            split_positions.append(next_gp)
            last_split    = next_gp
            next_size_cut = last_split + keys_per_sst
            switched      = 0
        gp_idx += 1
    else:
        break

# Each output VirtualSST takes [pos_start, pos_end), key bounds derived
# from plr.Inverse, with key_end-1 on non-last files to enforce strict
# non-overlap. The sub-PLR has its intercept shifted so local rank starts
# at 0 (essential for materialization).
```

**Key correctness considerations**

- **Dynamic threshold escalation** (50% → 90%): copied verbatim from
  baseline's `ShouldStopBefore`. The escalation is what allows files to
  grow toward `target_sst_size` when grandparent boundaries are dense.
- **No GP position dedup**: each grandparent file contributes two
  boundary keys (smallest, largest); the gap between adjacent files is
  also a boundary. They must all advance `switched`, even when their
  PLR-predicted positions round to the same integer.
- **Bytes, not key counts**: the threshold check uses
  `(next_gp - last_split) * avg_entry_size`, matching baseline's
  byte-based `current_output_file_size_` check.
- **2× max for non-bottom**: matches
  `Compaction::max_output_file_size_ = bottom || no_grandparents ? target : 2*target`.
- **`key_end - 1` on non-last files**: required for strict non-overlap
  at L1+ since `plr.Inverse(pos_end)` for file `i` and
  `plr.Inverse(pos_start)` for file `i+1` map to the same key boundary.

### Materialization

The default discrete path streams strictly increasing keys from its
certificate and requires the planned count to be written exactly. For models
without a certificate, converting a VirtualSST back to keys uses PLR inverse
walking (segments scanned in order, O(N) total instead of O(N log S)
per-key search):

```
For pos = 0, 1, 2, ..., num_entries-1:
    while seg_idx+1 < segments.size and pos > pos_end_of(segments[seg_idx]):
        seg_idx += 1
    seg = segments[seg_idx]
    if |seg.slope| < epsilon:
        key = (seg.key_start + seg.key_end) / 2
    else:
        key = round((pos - seg.intercept) / seg.slope)
    key = clamp(key, seg.key_start, seg.key_end)
    key = clamp(key, vsst.key_min, vsst.key_max)
    keys.append(key)

# Post-process: ensure strictly increasing
for i = 1..len(keys)-1:
    if keys[i] <= keys[i-1]:
        keys[i] = keys[i-1] + 1
# If +1 accumulation pushed past key_max, cap and dedup from the tail.
```

Each VirtualSST is materialized independently. Non-overlap is a within-level
property of L1+; different levels can still generate the same user key.
Materialization and SST writing are fused into a single parallel step using
direct I/O. Neither path guarantees global distinct cardinality. The discrete
path enforces per-file count/capacity; the legacy inverse path can lose entries
when a descriptor's planned count does not fit its generated key range.

Final registration uses `InstallVirtualCompactionMaterialization()` to assign
distinct positive file-global sequences through MANIFEST metadata. Higher
levels in lookup order (smaller level numbers) receive higher sequences; L0
files follow epoch/file precedence. The helper updates exact InternalKey
bounds, commits the edit, publishes the sequence counters, and installs the
new SuperVersion while writes are serialized. Clean RocksDB readers apply the
external-SST global sequence override without an SST rewrite. This fixes
duplicate iterator output while retaining the independently generated keys
and chosen levels; it does not correct cardinality or key-distribution errors.

### Global-unique materialization (`--vcomp_global_unique_keys`)

Every VirtualSST generates keys from its own model, so two files can produce
the same key id even when the input held it once. For a 100%-unique input that
is always an error, and it is the dominant measured term of the unique100
deficit: repeated synthetic keys in the final SSTs, 16.4% of the 19.4% total
in the run2 decomposition. KMV sketches do not prevent it, because they
describe cardinality, not which key ids another file will emit.

This opt-in mode makes Phase 2 produce a globally distinct key set:

- The flag asserts a property of the input, so it is checked rather than
  trusted: `fillvirtual` fails unless the load trace header declares
  `unique_count == num_records`. The synthetic generator draws keys with
  replacement and is refused outright.
- One reservation bit per key id in the trace key domain records every key a
  file generated. The 100 GiB/91 B domain needs 141 MiB. The budget is
  `--vcomp_global_unique_keys_max_mb` (default 1024), and the bitmap is
  allocated and checked before Phase 1 so an oversized domain cannot fail
  after hours of loading.
- Phase 2 materializes one level group at a time, shallowest first, with a
  barrier between groups; `--vcomp_global_unique_keys_deep_first` reverses the
  order. Each file excludes the key ids the levels before it already took.
  Only overlapping ranges can collide, so one bit test replaces intersecting
  the overlapping files' generated key sets.
- Same-level non-L0 materialize ranges are disjoint, so a level group stays
  fully parallel and its outcome does not depend on worker interleaving. L0
  files do overlap each other, so an L0 group runs single-threaded in a fixed
  order.
- Each file reserves one free key per planned entry: output `i` may not exceed
  the `(free - target + i)`-th free key of its range, which always leaves
  `target - i` free keys below the range top. The count therefore survives the
  exclusion whenever the range still holds enough free keys. Both cursors move
  forward only, so a file costs one pass over its range plus one bitmap probe
  per entry.
- Entries that no free key can carry are reported per file as
  `unique_shortfall` with `stop_reason=unique_capacity_exhausted`, and the run
  continues. `unique_reserved_keys == stage2_put_successes`
  (`unique_globally_distinct` in `fidelity.json`) is the in-run oracle: taken
  bits are distinct by construction, so equality proves that no key id was
  written twice.

#### 100 GiB measurement

`fidelity_100gib_20260908_unique_run5`, the same twelve-load concurrent
matrix as run3/run4, with the mode enabled on the two 100%-unique datasets
only. All six baselines matched their exact input cardinality. `U` is the
clean-RocksDB read-only exact distinct count after settling.

| unique100 case | run4 `U` error, mode off | run5 `U` error, mode on | repeated physical entries |
|---|---:|---:|---:|
| 1024 B KV | −19.163% | **−3.592%** | 0 |
| 91 B KV | −19.282% | **−3.769%** | 0 |

| Term | 1024 B | 91 B |
|---|---:|---:|
| `N − D`, KMV dedup estimate | 2,343,645 (2.235%) | 29,089,180 (2.465%) |
| `D − M`, exclusion shortfall | 1,422,981 (1.357%) | 15,386,782 (1.304%) |
| `M − U`, repeated synthetic keys | 0 | 0 |

`unique_reserved_keys == stage2_put_successes` in both cases, and
`sst_entry_sum` equals the distinct count, so no key id is written twice
anywhere in the tree. The four non-unique F2 cases ran with the mode off and
stayed within 0.4 pp of run4, which is the campaign's run-to-run variability;
the 15.5 pp unique100 change is far outside it. Phase 2a cost 4.583 s → 5.515 s
(1024 B) and 18.992 s → 18.366 s (91 B) against a 9-66 s total, so the extra
work is not a bottleneck. Twelve loads run concurrently, so all times are
operational only.

**The shortfall is not forced by the descriptors.** For an interval `[a,b]`,
every file whose whole range lies inside it must draw from `b - a + 1` key
ids, so the largest excess of contained planned entries over that width is a
lower bound on the shortfall of any assignment order. The measured shortfall
is about 60x that bound: 1,422,981 against 32,471 (1024 B) and 15,386,782
against 231,883 (91 B). All of it lands in L4, and the level densities say
why: planned entries over summed range width are 0.009/0.033/0.278/0.700 for
L1/L2/L3/L4. Shallowest-first lets the three sparse levels scatter 30% of the
entries across the whole domain before the dense level places its 70%, so L4
runs out of free keys locally. Deep-first ordering
(`--vcomp_global_unique_keys_deep_first`) serves the dense level first and
leaves the sparse, wide-ranged files to fill the gaps; it has not yet been run
at 100 GiB.

#### 1 GiB development measurement

Buffers reduced to 16 MiB so the tree reaches L3. Evidence:
`experiments/artifacts/unique_keys_dev_20260908/`.

| Setting | D | M | U | U error | repeated physical entries | Phase 2a |
|---|---:|---:|---:|---:|---:|---:|
| off | 11,693,484 | 11,693,484 | 11,190,758 | −5.158% | 502,726 | 0.131 s |
| on, shallow first | 11,693,484 | 11,609,767 | 11,609,767 | −1.607% | 0 | 0.234 s |
| on, deep first | 11,693,484 | 11,612,726 | 11,612,726 | −1.582% | 0 | 0.234 s |

Here the level densities are inverted (L1 0.227, L2 0.909, L3 0.989: the deep
levels are already saturated), the interval bound is 80,755, and deep-first
reaches 80,758 - three keys off optimal. Ordering therefore looks irrelevant
at this scale and decisive at 100 GiB; the small case does not predict it.
The sweep's own 1 GiB pilot is weaker still: with 64 MiB buffers it produces
no cross-file collisions at all, and run5's pilot reproduced run4's numbers
exactly for every case, which does check that the mode's Phase 2 restructure
leaves the flag-off path unchanged.

`experiments/scripts/trace/run_fidelity_dataset_sweep.py --global-unique-keys`
enables the mode for the sweep's 100%-unique datasets only, and records the
effective flags in each case's `options.json`.

---

## RocksDB Integration

### Components

```
┌──────────────────────────────────────────────────────────────┐
│  db_bench (FillVirtual)                                      │
│    Main thread: keygen → radix sort → PLRFit                 │
│    Queue generated virtual L0 files in a pending window       │
│    Refill visible L0 through bounded VersionEdit batches      │
└────────────────────────┬─────────────────────────────────────┘
                         │ LogAndApply for bounded visible registration
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  RocksDB Core (concurrent with flush)                        │
│    VersionSet: tracks virtual files (no physical SST)        │
│    CompactionPicker: picks files based on metadata           │
│    BackgroundCompaction (N threads):                         │
│      - if at least one input is virtual:                     │
│            RunVirtualCompaction (PLR merge)                  │
│      - else: normal CompactionJob (real I/O compaction)      │
│    VirtualSSTRegistry: file_number → VirtualSST              │
│                                                              │
│  Virtual-mode bypasses:                                      │
│    LoadTableHandlers / VerifyFileMetadata / file deletion    │
└────────────────────────┬─────────────────────────────────────┘
                         │ After WaitForCompact
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  Materialization (worker threads, direct I/O)                │
│    Per VirtualSST:                                           │
│      MaterializeKeys → GenerateKeyFromInt → SstFileWriter    │
│    VersionEdit: delete virtual files + add real files        │
│      (smallest InternalKey uses kMaxSequenceNumber to        │
│       match RegisterVirtualL0File's convention)              │
└──────────────────────────────────────────────────────────────┘
```

### Flow

1. **Phase 1 — Generate + refill L0**: radix sort each batch of keys →
   `GreedyPLRFit` → create `VirtualSST` → enqueue it in an in-memory
   pending L0 window.

   `RefillVirtualL0Window()` registers only a bounded amount of pending
   virtual L0 through `VersionEdit` batches. Registered virtual L0 files
   are immediately visible to RocksDB's regular L0 score and compaction
   picker; there is no separate visibility bit or worker.

   ```
   virtual_l0_visible_bytes_ < target_bytes
   ```

   For `db_bench fillvirtual`, `target_bytes` comes from
   `--vcomp_visible_l0_batch_mb`. The default flag value is zero, which maps to
   the effective column-family `max_compaction_bytes`. After
   successful virtual compaction commits, the commit path accounts
   consumed/output L0 bytes and refills the visible window from the pending
   queue.

2. **BG compactions (concurrent with Phase 1)**: `CompactionPicker`
   picks input files; if at least one input is still in the virtual
   registry, the dispatch routes to `RunVirtualCompaction`:
   - Look up input PLR models from registry.
   - `NWayMergePLR` with mutex released during merge (CPU-intensive).
   - `SplitIntoSSTs` for outputs with grandparent boundaries from
     `c->grandparents()`, escalating threshold (50% → 90%).
   - Register outputs in registry, `LogAndApply` removes inputs and
     adds outputs.

3. **Phase 2 — Materialization**: after residual virtual L0 is drained and
   `WaitForCompact` returns, walk
   the registry and produce real SST files in parallel. Each worker:
   - `MaterializeKeys` (PLR inverse walk).
   - `GenerateKeyFromInt` (uint64 → string).
   - `SstFileWriter::Put` per key (block builder + bloom filter).
   - `SstFileWriter::Finish` (flush + fsync).

   A single `VersionEdit` then deletes all virtual files and adds all
   real files atomically. `compact0` and `waitforcompaction` after
   Phase 2 are kept as a safety net.

### BG compaction dispatch

`DBImpl::BackgroundCompaction` routes each picked compaction:

```cpp
} else if (immutable_db_options_.use_virtual_compaction &&
           virtual_sst_registry_ &&
           AnyInputIsVirtual(c)) {
    status = RunVirtualCompaction(c.get(), job_context, log_buffer);
    if (status.ok()) {
        InstallSuperVersionAndScheduleWork(...);
    }
    *made_progress = true;
} else {
    // Normal CompactionJob path on real SSTs.
}
```

Where `AnyInputIsVirtual` walks `c->num_input_levels()` × `c->input(lvl,
i)->fd.GetNumber()` and checks `virtual_sst_registry_->Lookup(fnum) !=
nullptr`. **This dispatch guard is essential**: after Phase 2 has materialized
all virtual files into real SSTs, a subsequent `compact0` /
`waitforcompaction` will pick a compaction whose inputs are now entirely
real. Without the guard, dispatch would still call
`RunVirtualCompaction`, find no virtual inputs, return `OK` without
making progress, and the picker would immediately re-pick the same
compaction — an infinite spin that hangs `WaitForCompact`.

### Key implementation map

| File | Change |
|------|--------|
| `include/rocksdb/options.h` | `use_virtual_compaction`, `plr_error_bound` options |
| `options/db_options.{h,cc}` | `ImmutableDBOptions` mapping |
| `db/db_impl/db_impl.h` | `VirtualSSTRegistry`, pending L0 window state, refill/configuration APIs, virtual compaction commit queue |
| `db/db_impl/db_impl.cc` | Initialize registry when `use_virtual_compaction` is enabled |
| `db/db_impl/db_impl_compaction_flush.cc` | Real/virtual dispatch guard, `RegisterVirtualL0File()`, `RefillVirtualL0Window()`, `RunVirtualCompaction()`, commit batching, deferred obsolete cleanup, sequence-safe `InstallVirtualCompactionMaterialization()` |
| `db/db_impl/db_impl_files.cc` | Skip disk deletion for virtual files |
| `db/version_set.cc` | Skip `LoadTableHandlers` / `VerifyFileMetadata` for virtual files; use per-level score inputs to avoid rescanning unchanged Version append paths |
| `db/compaction/compaction_picker*.cc` | Use registered L0 files directly; no virtual eligibility filtering |
| `db/virtual_compaction/virtual_sst.{h,cc}` | `SplitIntoSSTs` (dynamic threshold + GP), `MaterializeKeys`, `VirtualCompact` |
| `db/virtual_compaction/virtual_sst_registry.h` | Thread-safe `file_number → VirtualSST` registry |
| `db/virtual_compaction/plr_model.{h,cc}` | `GreedyPLRFit`, `NWayMergePLR` with optional dedup |
| `tools/db_bench_tool.cc` | `fillvirtual` benchmark with pending-window L0 registration, Phase 2 workers using direct I/O, `coverage` benchmark for per-level file-coverage probes; Phase 2b supplies exact sequence-zero bounds to the materialization installer, which assigns effective file-global sequences |

### Key optimizations in FillVirtual

| Optimization | Before | After | Effect |
|--------------|--------|-------|--------|
| Radix sort (uint64_t keys) | `std::sort` O(N log N) | 8-bit radix O(N) | sort step ~6× faster |
| Pending L0 window | all generated L0 files registered immediately | register only a bounded visible byte window | prevents unbounded L0 backlog |
| Fused materialize+write | inverse → merge/dedup → SST write | inverse → SST write | eliminates cross-file merge step |
| Direct I/O | buffered | `use_direct_writes`, `!use_mmap_writes` | sys CPU 66% → 9% |
| Phase 2 workers | `max_background_jobs` (32) | `hardware_concurrency()` (48) | Phase 2 ~1.3× faster |

---

## Tree-Shape Invariants

The split logic in `SplitIntoSSTs` is the main mechanism for matching
baseline's file count and per-level file-size distribution. Two structural
properties that follow directly from the design:

- **Per-level file count**: bounded above by `total_entries /
  keys_per_sst` and below by `gp_count + 1`. With dynamic threshold the
  loop converges to near baseline's count for uniform random workloads.
- **Per-level file boundaries**: `key_min` of the first output uses
  `global_min`; `key_max` of the last output uses `global_max`; all
  internal boundaries come from `plr.Inverse(split_position)`. This
  means each VirtualSST's claimed key range covers its content but does
  not produce arbitrary gaps between adjacent files. (Erosion gaps —
  segments where no file in a level contains data — only appear when
  some file gets removed by a later compaction; see Open Work.)

---

## Open Work

The split logic implements 2 of the 4 cut conditions in baseline
RocksDB's `CompactionOutputs::ShouldStopBefore`. The remaining two are
deferred until they are shown to drive a measurable structural gap:

- **Max compaction bytes cut**
  Baseline cuts when
  `grandparent_overlapped_bytes + current_file_size > max_compaction_bytes`
  (default = 25 × `target_sst_size`). Prevents future compactions from
  becoming oversized. Rarely fires on random workloads.
  *To implement*: pass a parallel array `gp_sizes[]` alongside
  `grandparent_boundaries[]` from `RunVirtualCompaction`, accumulate
  overlap byte sums in the scan loop, cut when the sum exceeds
  `max_compaction_bytes`.

- **Skippable grandparent cut**
  Baseline cuts when one key crosses ≥3 grandparent boundaries at once
  (or ≥2 in a gap) AND the new overlap adds more than `target_sst_size /
  8` bytes. Prevents holding many "skippable" grandparent files.
  *To implement*: track `num_boundaries_crossed_at_this_key` and a
  `being_grandparent_gap` state machine, distinguishing
  `gp_smallest`/`gp_largest` boundaries (currently flattened into a
  sorted list, losing that distinction).

- **L2 erosion gap (open structural difference)**
  Baseline's L2 has coverage 70% ± 9pp across 30 fresh loads, while
  vcomp's L2 sits at 86% ± 3pp — vcomp L2 never gets eroded as much by
  L2→L3 cascades during loading. The cause is not yet understood; it
  may be a side effect of how virtual L1→L2 compactions choose inputs
  vs how baseline's real picker does. Investigation pending.

---
---

# Historical Changelog

Time-ordered narrative of the vcomp project. Old entries are preserved
as-is — they are snapshots of the state at the time, not current truth.
For current measurements, see
[experiments/docs/RESULTS.md](experiments/docs/RESULTS.md).

## Test environment

- **CPU**: 48-core Intel Xeon Gold 6336Y @ 2.40 GHz (HT off, 96 logical offline)
- **Storage**: RAID0 over 20+ NVMe SSDs, 108 TB ext4 (`/work`)
- **RocksDB config**: bloom_bits=10, disable_wal=true, direct I/O, compression=none
- **VComp config**: plr_error_bound=8, memtable_flush_size=64 MB
- **Key/Value**: key_size=24, value_size=1000

## Baseline reference (early measurements)

| Data Size | Time (sec) | Throughput | Compaction I/O | Write Amp | DB Size |
|-----------|-----------|------------|----------------|-----------|---------|
| 250 GB    | 837       | 311 MB/s   | 4,476 GB R+W   | 9.2x      | 177 GB  |
| 1 TB      | 3,830     | 269 MB/s   | ~18,000 GB R+W | ~9x       | 769 GB  |

Baseline = `fillrandom,waitforcompaction,stats,levelstats`,
`max_background_jobs=32`, `seed=12345678`. These were the first single-load
values used as a target during early optimization. Current 30-load
batch numbers are in [RESULTS.md §1.1](experiments/docs/RESULTS.md#11-250-gb--30-run-batch).

---

## Version history

### v0 — Single-threaded VirtualLSMTree (pre-`72ea9a2c6d`)

Custom VirtualLSMTree with single-threaded compaction simulation.
Materialization with parallel SST writes.

Commits: `f95d719f55` → `16906e0271`

| Data Size | Phase 1 | Phase 2 | Total | vs Baseline |
|-----------|---------|---------|-------|-------------|
| 25 GB     | 6.3s    | 2.5s    | 9s    | 47x         |
| 250 GB    | >58 min | —       | DNF   | —           |

**Bottleneck**: Single-threaded NWayMergePLR with exploding segment count.
250GB never completed (stuck in Phase 1 compaction).

---

### v1 — RocksDB bg thread integration (`72ea9a2c6d`)

Replaced VirtualLSMTree with RocksDB's native BackgroundCompaction.
Virtual SSTs registered in VersionSet, compaction does PLR merge.

| Data Size | Phase 1 | Phase 2 | Total | vs Baseline |
|-----------|---------|---------|-------|-------------|
| 25 GB     | 2.1s    | 3.2s    | 5.3s  | —           |
| 250 GB    | 25.4s   | 32.7s   | 58s   | 14.4x       |

Phase 1 breakdown (250GB):
- keygen: 2.5s, sort: 17.0s, PLR fit: 0.7s, register: 5.3s

Phase 2 breakdown (250GB):
- PLR inverse: 0.1s, merge/dedup: 16.6s, SST write: 15.0s, VersionEdit: 1.0s

**Issues found**:
- SST files missing bloom filter (bare `Options()` instead of `open_options_`)
- Phase 2 merge/dedup 16.6s was unnecessary (bg compaction already ensures non-overlapping)
- Phase 1 sort 17s: `std::sort` O(N log N) on uint64_t keys

---

### v2 — Fix SST options + k-way merge (`2b9d647717`)

SstFileWriter inherits `open_options_` (bloom filter, compression settings).
Phase 2b changed from concat+sort to k-way merge (minor improvement).

| Data Size | Phase 1 | Phase 2 | Total | vs Baseline |
|-----------|---------|---------|-------|-------------|
| 250 GB    | 25.3s   | 30.9s   | 56s   | 14.9x       |

DB size corrected: 139GB → 255GB (previous was accidentally snappy-compressed).

---

### v3 — Radix sort + batch register + eliminate merge/dedup (`002c945f9e`)

Three optimizations targeting top bottlenecks:
1. **Radix sort**: `std::sort` O(N log N) → radix sort O(N) for uint64_t. 16.8s → 2.6s.
2. **Batch LogAndApply**: Accumulate l0_compaction_trigger files per call. 5.2s → 1.7s.
3. **Eliminate merge/dedup**: BG compaction ensures L1+ non-overlapping, so each VirtualSST
   materializes directly to SST without cross-file merge. 15.9s → 0s.

| Data Size | Phase 1 | Phase 2 | Total | vs Baseline |
|-----------|---------|---------|-------|-------------|
| 25 GB     | 0.6s    | 6.7s    | 7.3s  | —           |
| 250 GB    | 8.1s    | 13.1s   | 21s   | 39x         |

Phase 1 breakdown (250GB):
- keygen: 2.5s, sort: 2.6s, PLR fit: 0.8s, register: 1.7s

Phase 2 breakdown (250GB):
- materialize + SST write: 12.6s (single fused step), VersionEdit: 0.5s

---

### v4 — Direct I/O + max threads (`1a98ee91c8`)

1. **Direct I/O**: SstFileWriter uses O_DIRECT (`use_direct_writes=true`,
   `use_mmap_writes=false`). Reduces kernel sys CPU from 66% to 9%.
2. **Max threads**: Phase 2 uses `hardware_concurrency()` (48) instead of
   `max_background_jobs` (32).

| Data Size | Phase 1 | Phase 2 | Total | vs Baseline |
|-----------|---------|---------|-------|-------------|
| 250 GB    | 8.2s    | 7.3s    | 16s   | **52x**     |
| 1 TB      | 76.3s   | 25.4s   | 102s  | **37.5x**   |

Phase 2 CPU profile (1TB):
- Before (buffered): user 33% + sys 66% + idle 0%
- After (direct I/O): user 88% + sys 9% + idle 2%

**Remaining bottleneck**: Phase 1 register (LogAndApply) scales non-linearly.
250GB: 1.8s, 1TB: 39s. MANIFEST write cost grows with file count.

---

## Summary Chart

```
250 GB Load Time (seconds)
                                                    
Baseline (fillrandom) ████████████████████████████████████████████ 837s
v0 (single-thread)    ██████████████████████████████ >3480s (DNF)
v1 (bg compaction)    ███ 58s
v2 (fix SST opts)     ███ 56s
v3 (radix+batch+fuse) ██ 21s
v4 (direct I/O)       █ 16s
```

### v5 — Probabilistic dedup correction (historical)

PLR-based cross-level dedup estimation using inclusion-exclusion principle.
Per breakpoint interval: `adjusted_slope = 1 - Π(1 - slope_i)` where each
`slope_i` represents key density from an input model. This corrects the merged
model's total entry count without any additional data structures.

Changes:
- `NWayMergePLR` gains `dedup` flag — when true, uses inclusion-exclusion
  instead of naive slope sum, then recomputes intercepts for continuity.
- `RunVirtualCompaction` calls with `dedup=true`, caps adjusted ≤ naive.
- Trace output includes naive/adjusted/dedup fields.
- `waitforcompaction` added to vcomp bench (fixes residual L0 files).
- Naive path preserved as comments for easy rollback.

| Data Size | Phase 1 | Phase 2 | Total | vs Baseline |
|-----------|---------|---------|-------|-------------|
| 250 GB    | 8.2s    | 5.9s    | 14s   | **56x**     |
| 1 TB      | 74.0s   | 24.5s   | 99s   | **39x**     |

Dedup estimation per level (250GB):

| Level | Compactions | Naive     | Adjusted  | Dedup   | Dedup% |
|-------|-------------|-----------|-----------|---------|--------|
| 0→0   | 137         | 59.7M    | 59.7M     | 0       | 0.00%  |
| 0→1   | 746         | 444.7M   | 444.7M    | 13K     | 0.00%  |
| 1→2   | 3,982       | 1,319M   | 1,317M    | 2.2M    | 0.17%  |
| 2→3   | 3,888       | 1,638M   | 1,618M    | 20.5M   | 1.25%  |
| 3→4   | 2,569       | 590M     | 539M      | 51.7M   | 8.76%  |
| Total | 11,322      | 4,052M   | 3,978M    | 74.4M   | 1.84%  |

Dedup estimation per level (1TB):

| Level | Compactions | Naive     | Adjusted  | Dedup    | Dedup% |
|-------|-------------|-----------|-----------|----------|--------|
| 0→0   | 1,131       | 514M     | 514M      | 73       | 0.00%  |
| 0→1   | 2,014       | 1,548M   | 1,548M    | 4K       | 0.00%  |
| 1→2   | 15,774      | 4,587M   | 4,585M    | 2.3M     | 0.05%  |
| 2→3   | 15,886      | 6,769M   | 6,746M    | 22.7M    | 0.34%  |
| 3→4   | 14,530      | 5,786M   | 5,610M    | 175.6M   | 3.04%  |
| 4→5   | 2,088       | 297M     | 258M      | 39.3M    | 13.25% |
| Total | 51,423      | 19,501M  | 19,261M   | 240M     | 1.23%  |

Phase 2 detailed breakdown (250GB, 48 threads):
```
Phase 2a (sst_write): 5.9s    version_edit: 0.7s
Per-thread cumulative: materialize=1.7s  open+put=265.2s  finish(sync)=10.8s
```

Phase 2 `perf record` profiling (top self-time functions):
```
Self%   Function                              Category
21.4%   __memcpy_avx (BlockBuilder::Add)       CPU - key/value block copy
12.0%   WriteMaybeCompressedBlockImpl          CPU - checksum + block header
 4.4%   XXH3_hashLong (checksum)               CPU - data block XXH3
 4.3%   BlockBuilder::AddWithLastKeyImpl       CPU - delta encoding
 4.0%   FillVirtual lambda (GenerateKeyFromInt) CPU - key generation
 2.2%   __memcmp_avx2 (key compare)            CPU - key ordering
 1.9%   BloomFilter::AddHash                   CPU - bloom filter add
 1.4%   BloomFilter::Finish                    CPU - bloom filter finalize
 1.2%   kernel: gup_pud_range                  IO  - Direct I/O page pinning
 1.1%   kernel: do_direct_IO                   IO  - Direct I/O syscall
 1.1%   MaterializeKeys                        CPU - PLR inverse
 0.9%   native_safe_halt                       IDLE
```

Phase 2 is **CPU-bound** at 48 cores. Kernel I/O is only ~2.3% of cycles.
Disk write bandwidth: ~40 GB/s (peak 42 GB/s) out of ~100 GB/s RAID capacity.
Thread oversubscription (96 threads) degrades throughput due to context switching:
48 threads: tps=90K, 40 GB/s  vs  96 threads: tps=70K, 32 GB/s.

Attempted optimizations with no significant improvement:
- 10MB write buffer (vs 1MB default): -9% Put time but +140% Finish/sync time
- 2x thread oversubscription: context switch overhead negates I/O overlap

**Then-current limit**: SST build cost (memcpy + checksum + bloom) was the bottleneck.
HT (96 logical cores) would provide ~1.3x improvement.
Kernel boot param `nosmt` prevents runtime HT enable; requires reboot.

---

## v5-era snapshot (2026-04-08, single load each)

The first-pass v0~v5 numbers above were measured on single loads. At
the v5 point the structural snapshot looked like this. Treat as
historical — current values are in [RESULTS.md](experiments/docs/RESULTS.md).

**1 TB structure (v5 vs baseline, single load each)**

```
                        VComp (dedup)         Baseline (fillrandom)
Loading time            99 sec                3,889 sec (64.8 min)
DB Size                 762 GB                768 GB
Total SST files         12,774                13,496
Compaction R/W          0 / 0 GB              12,100 / 12,867 GB
Write Amp               1.0x                  12.7x
L1..L5 file/MB          3/195  40/2.5G        4/220  49/2.5G
                        408/25.3G             440/25.0G
                        4151/254G             4137/250G
                        8172/480G             8865/490G
```

**1 TB readrandom (16 threads × 180 s, v5)**

`cache=0%`: vcomp 298,208 ops/s vs baseline 286,104 ops/s (+4.2%);
P99 146 µs vs 163 µs. `cache=2%`: vcomp 275,864 vs baseline 267,978
(+2.9%). These were the first read benchmarks done; later analysis
(see 2026-04-15 entry) showed they were polluted by disk-state
freshness skew and should not be used for fair comparison.

**250 GB compaction stats (v5)**

BG compaction events: ~11,300 (L0→L0: 137, L0→L1: 746, L1→L2: 3,982,
L2→L3: 3,888, L3→L4: 2,569). Compaction I/O 0 bytes; write
amplification 1.0×; DB size 176 GB.

---

## Summary chart

```
250 GB Load Time (seconds)
                                                    
Baseline (fillrandom) ████████████████████████████████████████████ 837s
v0 (single-thread)    ██████████████████████████████ >3480s (DNF)
v1 (bg compaction)    ███ 58s
v2 (fix SST opts)     ███ 56s
v3 (radix+batch+fuse) ██ 21s
v4 (direct I/O)       █ 16s
v5 (prob. dedup)      █ 14s

1 TB Load Time (seconds)

Baseline (fillrandom) █████████████████████████████████████████ 3889s
v4 (direct I/O)       █ 102s
v5 (prob. dedup)      █ 99s
```

---

## 2026-04-09 ~ 10 — Fair comparison setup

### Goal
**Measure vcomp against baseline under identical conditions.** The target is
a tree structurally identical to baseline (same file count, same level shapes),
not a "better" tree. Any metric where vcomp appears faster must first be
explained by tree shape differences before being claimed as a real improvement.

### Infrastructure changes

1. **Clean L0 after load**: `load.sh` now runs
   `fillrandom,flush,waitforcompaction,compact0,stats,levelstats`.
   Previously `fillrandom,waitforcompaction` left a residual ~30MB L0 from the
   final memtable. All 4 DBs (baseline/vcomp × 250GB/1TB) now have L0=0.

2. **Grandparent boundary splitting in vcomp**: `VirtualSST::SplitIntoSSTs`
   accepts `grandparent_boundaries` and applies RocksDB's
   `ShouldStopBefore` heuristic (split at grandparent boundary if file is
   ≥50% of target size). Before: 2,926 files; after: 3,503 (closer to
   baseline's 3,300).

3. **`parse_runs.py`**: walks `log_runs/<db>/<run>/` and produces a wide CSV
   (`runs_summary.csv`) with 50 columns covering top-line metrics, Get/SST
   latency histograms, block cache, bloom, per-level hits, md0 disk bytes,
   RocksDB byte counters, RAF/WAF, and CPU breakdown from `/proc/stat`.

4. **`load_batch.sh`**: groups N runs per mode under a single batch directory
   and emits summary CSV. Removed unused `CACHE_SIZE_GB` requirement.

### Loading time (30x batch, 2026-04-10)

Baseline 250GB, `fillrandom,flush,waitforcompaction,compact0`, n=30:
- **Mean**: 903.4 sec
- **Stddev**: 12.8 sec (1.4%)
- **Min/Max**: 883 / 926 sec

VComp most recent single runs:
- **250GB**: Phase1 9.87s + Phase2 6.44s = **16.3s** (55.4x vs baseline)
- **1TB**: Phase1 103.8s + Phase2 27.3s = **131.1s**
  (with `force_consistency_checks=true`; with CC=false: 99.9s)

### Tree shape comparison (early single-DB, later proven misleading)

At this point we compared **one** vcomp 250GB load against one baseline
250GB load and concluded "vcomp has 6–13% more files, L1~L3 files are
30–40% smaller". That conclusion turned out to be a sampling artifact
once we had the 30×30 numbers — see the 2026-04-14/15 entries below.
The early 250GB and 1TB single-DB tables are kept here as the snapshot
that motivated the next round of work, but the **current** tree shape
comparison lives in [RESULTS.md §2](experiments/docs/RESULTS.md#2-tree-shape--3030-coverage-comparison).

Single-load early snapshot (baseline n=30 mean ± std, vcomp n=1):

```
250 GB
  L1: base 4.3±0.8 / 232 MB    vcomp 7 / 245 MB
  L2: base 46.7±3.4 / 2528 MB  vcomp 80 / 2565 MB
  L3: base 439±4 / 25566 MB    vcomp 673 / 26006 MB
  L4: base 2810±12 / 152 GB    vcomp 2743 / 152 GB
  Sum: base 3300±11             vcomp 3503 (+6.2%)

1 TB (single load both)
  Sum: base 13,495              vcomp 15,277 (+13.2%)
```

### Readrandom comparison (1M ops, 1 thread, cache=1 byte)

The first batch readrandom comparison. `cache=1 byte` forces every
block access through the block cache without caching anything,
enabling block-level stats while keeping disk I/O equivalent to
cold-cache. **Single-DB-pair only**; later 30×30 numbers in
[RESULTS.md §3.1](experiments/docs/RESULTS.md#31-250-gb--3030-readrandom-1m-reads-1-thread-cache0)
supersede these.

Highlight rows from this snapshot:

```
250 GB:  base 479s vs vcomp 468s, P99 SST 211 vs 166 µs, RAF 753 vs 643
1 TB:    base 540s vs vcomp 494s, P99 SST 225 vs 166 µs, RAF 867 vs 668
```

At the time we suspected vcomp's apparent advantage came from
smaller L1~L3 files reducing per-file I/O size — i.e. a tree-shape
artifact, not a real loading-path benefit. The next round (2026-04-14)
fixed the split logic, and a follow-up 60-run comparison showed the
gap is mostly **disk fragmentation skew** between freshly loaded vcomp
and 5-day-old baseline DBs. Investigation pending in
[EXPERIMENTS_PLANNED.md](experiments/docs/EXPERIMENTS_PLANNED.md).

### Data block cache anomaly (investigated, resolved as transient)

First vcomp 1TB readrandom run (260409_1354) showed
`data.miss = 3,420,328` vs `data.add = 661,252` → **5.17x duplicate** reads.
Re-run 6 times back-to-back: every subsequent run produced
`data.miss = data.add = 661,246` (1.00x, deterministic).

Wall clock 1st run: 502.2s; runs 2–7: 493.3–494.4s (σ < 0.3s).
Every ticker besides `data.miss` was identical across runs 1–7. Cause
unknown — likely a transient OS / NVMe / block cache state from loading
+ other concurrent work immediately before the first run. Marked as
measurement anomaly, not a vcomp code issue.

### CheckConsistencyDetails cost

`force_consistency_checks=true` (default) adds ~30s to vcomp 1TB load:
- CC=ON:  Phase1 104.4s (keygen 34.2s, sort 12.0s, plr 3.6s, register 54.5s)
- CC=OFF: Phase1 74.8s  (keygen 17.2s, sort 11.7s, plr 3.5s, register 42.4s)

`keygen` diff (~17s) is mostly mutex contention with BG threads, measured
as `phase1_total − sort − plr − flush`. Safe to disable for vcomp because
virtual metadata is generated deterministically by our code.

### Tooling
- [load.sh](experiments/scripts/load/load.sh),
  [run.sh](experiments/scripts/read/run.sh),
  [load_batch.sh](experiments/scripts/load/load_batch.sh)
- [parse_runs.py](experiments/analysis/parse_runs.py) →
  `experiments/artifacts/log_runs/runs_summary.csv`
- `experiments/artifacts/log_batch/260410_0339_250gb_x30/` — 30x baseline 250GB batch

---

## 2026-04-14 — Tree-shape convergence work

### Context
Follow-up from 2026-04-10. Earlier conclusion that "vcomp's L1~L3 files are
30–40% smaller than baseline" turned out to be a sampling artifact: we had
compared one `vcomp` DB against one `baseline` DB, and the ±25pp variance
in baseline's L1 coverage dominated any real difference. Several vcomp
split-logic bugs were discovered and fixed while trying to close the gap.

### Fixes applied (in chronological order)

**1. Grandparent split — dynamic threshold** (`virtual_sst.cc`)
The initial implementation cut at a fixed `50%` of target whenever a GP
boundary was crossed, producing files 56–67% of target on levels with
dense GP coverage. Baseline's `CompactionOutputs::ShouldStopBefore` uses
an escalating threshold:

```
pct = 50 + 5 * switched  (capped at 90)
```

where `switched` counts GP boundaries seen since the last cut and resets
on every new output file. Replaced the fixed 50% with the escalating
formula and converted the size comparison to bytes to mirror baseline.

**2. Non-bottom `max_output_file_size = 2 × target`** (`virtual_sst.cc`)
Baseline's `Compaction` constructor sets
`max_output_file_size_ = bottommost || grandparents.empty() ? target : 2 * target`.
The 2× slack on non-bottom levels lets files grow past target when GP
boundaries are sparse, so the size-based hard cut rarely fires.
Our split loop was using 1× target as the hard cut on all levels,
clipping files that baseline would leave alone. Fixed the hard cut to use
`2 * target` when `grandparent_boundaries` is non-empty and
`target_level > 0`.

**3. Grandparent position dedup bug** (`virtual_sst.cc`)
Biggest bug. For each grandparent file we passed (`smallest`, `largest`)
into `SplitIntoSSTs`, which predicted positions via PLR and deduped by
rounded integer. Because adjacent grandparents have `largest[i]` and
`smallest[i+1]` that differ by 1 user-key, the PLR predictions often
rounded to the same integer, so `std::unique` halved the boundary count.
Each removed boundary was a missing `switched++` — the dynamic threshold
advanced at half speed, cutting at 50~55% instead of 80~90%. Removed the
dedup entirely; duplicate positions are harmless in the scan loop.

Trace output showing the issue before the fix:

```
[vsplit] level=3 gp_raw=22 gp_pos=11 drop(dedup=11)
  gp[1]=32785777 pred=32745.356
  gp[2]=32785778 pred=32745.357     ← rounds to same 32745
  gp[3]=65372278 pred=65479.781
  gp[4]=65372279 pred=65479.782     ← rounds to same 65479
  ...
```

After the three fixes above, a single vcomp 250GB load produced:

```
L1:  4 / 246 MB (base: 4.3 ± 0.8 / ~224 MB)
L2: 44 / 2,561 MB (base: 46.7 ± 3.4 / ~2,506 MB)
L3: 420 / 25,994 MB (base: 439.2 ± 4.4 / ~25,548 MB)
L4: 2,641 / 152,249 MB (base: 2,810.1 ± 11.8 / ~152,170 MB)
Total: 3,109 files (-5.9% vs baseline 3,303)
```

File counts within 6% at every level — tree shape is now very close.

### Compact0 hang at end of load — 3 root causes chained

Adding `compact0` back to vcomp's load sequence (`fillvirtual,flush,
compact0,waitforcompaction,stats,levelstats`) exposed a long-standing
hang. Diagnosed and fixed in stages:

**1. Residual virtual L0 → L0 drain hack (reverted)**
`fillvirtual`'s `WaitForCompact` returns when the BG queue drains. If L0
holds fewer files than `level0_file_num_compaction_trigger=4` when that
queue empties, no further virtual L0→L1 is scheduled and Phase 2
materialises those virtual L0 files as real L0 files. An earlier workaround
temporarily lowered `level0_file_num_compaction_trigger` to 1 after the
wait and called `WaitForCompact` again to force them through, then
restored the trigger. This was intended to avoid having compact0 encounter
real L0 files. Removed in favour of the real fixes below — the natural
end-of-load flow requires compact0 to be able to handle whatever Phase 2
leaves at L0 (just like baseline).

**2. BG compaction dispatch looped forever on real inputs** (real root cause, fixed)
`DBImpl::BackgroundCompaction` dispatched to `RunVirtualCompaction`
whenever `use_virtual_compaction` was true — regardless of whether the
picked compaction's input files were still virtual.
`RunVirtualCompaction` looks up each input in
`virtual_sst_registry_`; when none are found (because Phase 2 already
materialised everything), it hits
`if (models.empty()) { ReleaseCompactionFiles(OK); return OK; }` and
returns without making progress. The compaction picker immediately
re-picks the same score-exceeding compaction and the BG loop spins
forever, starving `WaitForCompact`.

Fixed by gating the virtual dispatch on "at least one input is still
in the registry":

```cpp
} else if (use_virtual_compaction && virtual_sst_registry_ &&
           [&]() {
             for (size_t lvl = 0; lvl < c->num_input_levels(); lvl++) {
               for (size_t i = 0; i < c->num_input_files(lvl); i++) {
                 uint64_t fnum = c->input(lvl, i)->fd.GetNumber();
                 if (virtual_sst_registry_->Lookup(fnum)) return true;
               }
             }
             return false;
           }()) {
  status = RunVirtualCompaction(c.get(), ...);
  ...
```

Post-fix, compact0 and its triggered L1→L2 cascade run through the normal
`CompactionJob` path. Load completes in ~18s across all 30 trials.

**3. Phase 2b VersionEdit seqno mismatch** (defensive fix)
`Phase 2b` was building `InternalKey smallest(first_key, 0, kTypeValue)`
for the newly-materialised files, while `RegisterVirtualL0File` uses
`kMaxSequenceNumber`. Internal key ordering puts `(user_key, max_seqno) <
(user_key, 0)`, so the claimed smallest was larger than the file's actual
smallest content. Not the cause of the hang (the hang was #2), but a
latent bug — fixed to match `kMaxSequenceNumber` for smallest.

### Level-coverage probe (new `coverage` bench)

Added a `coverage` benchmark to `db_bench` that calls
`GetLiveFilesMetaData`, extracts per-file uint64 key bounds, and prints
per-level statistics:

- files, size (MB)
- key_min, key_max, union_span, sum of file_spans
- `cov_pct = union_span / (key_max − key_min)` — how much of the
  level's overall span is actually covered by files

### 30-run coverage comparison (baseline vs vcomp, fresh loads via `load_batch.sh`)

Both directories loaded 30 times via `load_batch.sh` with the fixed
vcomp (all three split fixes + the BG dispatch fix applied) and the
same `load.sh` benchmark order. `baseline_250gb_x30` was loaded
2026-04-10, `vcomp_250gb_x30` was loaded 2026-04-14.

Highlights of this round (full table now in
[RESULTS.md §2](experiments/docs/RESULTS.md#2-tree-shape--3030-coverage-comparison)):

- File counts match baseline within ≤7% at every level.
- L1 has wide variance in both modes (baseline 7–100%, vcomp 27–100%);
  variance is comparable — this is a normal stochastic effect of when
  the final compact0 happens to trigger an L1→L2 cascade.
- **L2 stays the real outlier**: baseline 70% ± 9pp vs vcomp 86% ± 3pp
  — vcomp L2 never gets eroded as much by L2→L3 cascades during loading.

### Coverage gap explains filter_per_get exactly

From the 2026-04-10 single-DB readrandom snapshot, `filter_per_get` was
baseline 2.44 vs vcomp 2.96 — a gap of **+0.52** that was unexplained at
the time. The 30×30 coverage numbers above decompose it exactly:

```
+0.359 (L1)  +0.159 (L2)  +0.034 (L3)  +0.000 (L4)  ≈ +0.55
```

Every extra pp of coverage at level L means one more bloom probe per
Get for the affected fraction of random queries.

### Key structural remainder: L2 is too uniform in vcomp

vcomp's L2 sits in a narrow band around 86% (stddev 2.53pp), while
baseline's L2 spans 54–87% (stddev 9.18pp). vcomp L2 just doesn't get
eroded as much — suggesting that L2→L3 cascades happen at a different
cadence during vcomp's Phase 1 than during baseline's fillrandom. This
is the next thing to investigate.

### Status at the end of this round

- Hang fixed — vcomp loads with the full baseline-matching benchmark
  sequence and no longer needs an L0 drain workaround.
- Tree shape (file counts) matches baseline within 7% at every level.
- L1 variance matches baseline.
- L2 coverage still +16pp with much tighter variance — the remaining
  structural difference; explains most of the readrandom filter_per_get
  gap.

### Files changed
- [vcomp/db/virtual_compaction/virtual_sst.cc](../vcomp/db/virtual_compaction/virtual_sst.cc) — dynamic threshold, 2× max, removed GP dedup
- [vcomp/db/db_impl/db_impl_compaction_flush.cc](../vcomp/db/db_impl/db_impl_compaction_flush.cc) — virtual dispatch only when inputs are virtual
- [vcomp/tools/db_bench_tool.cc](../vcomp/tools/db_bench_tool.cc) — Phase 2b seqno fix, `coverage` bench, removed L0 drain workaround

---

## 2026-04-15 — 60×readrandom and the disk-fragmentation question

### Goal
Run readrandom 1M against all 30 baseline DBs and all 30 vcomp DBs from
the previous day's batches. The 30-fold sample was meant to remove the
per-DB sampling noise that had dominated earlier single-pair comparisons
of `filter_per_get`, `bloom_fpr`, and `sst_p99`.

### Tooling additions
- `run.sh` learned to honour an external `RESULT_DIR` env var (instead
  of always picking its own timestamped path).
- New [run_batch.sh](experiments/scripts/read/run_batch.sh) walks
  `${BATCH_DB_DIR}/{mode}_run{1..N}/` and invokes `run.sh` with that
  DB and `RESULT_DIR=log_batch/${BATCH}/${WORKLOAD}_*/${mode}_run${i}`,
  appending an aggregate `results.csv` per batch.

### Results — clean structural metrics
Full table in
[RESULTS.md §3.1](experiments/docs/RESULTS.md#31-250-gb--3030-readrandom-1m-reads-1-thread-cache0).
Block-level stats came out exactly as predicted by the coverage gap:

```
                  baseline (n=30)   vcomp (n=30)   Δ
filter_per_get    2.67 ± 0.24       3.13 ± 0.17    +17.0%
data_per_get      0.65 ± 0.00       0.66 ± 0.00    +0.84%
bloom_fpr (%)     2.97 ± 0.34       3.62 ± 0.29    +22.0%
```

The `filter_per_get` gap is structural; `data_per_get` is essentially
identical; `bloom_fpr` is consistently ~22% higher in vcomp because
PLR-materialised keys differ slightly from baseline's fillrandom keys
while readrandom queries the original key space.

### Results — disk-state-sensitive metrics

```
                  baseline           vcomp        Δ
elapsed_s         524 ± 55           472 ± 17    -10%
sst_p50 (µs)      121 ± 13           94 ± 0.06   -23%
sst_p99 (µs)      237 ± 9            167 ± 0.2   -30%
```

vcomp looks ~10% faster and has one or two orders of magnitude lower
SST-latency variance. **This is almost certainly disk fragmentation,
not vcomp speed.** baseline's DBs were 5 days old by the time
readrandom ran, and every vcomp load/measurement we did in those 5
days wrote tens of TB of unrelated data on the same NVMe RAID — the
hardware-level fragmentation around baseline's SST files is the most
likely source of the higher and noisier per-file read latency. vcomp's
DBs were freshly loaded into clean free-block pools.

A direct check: the same baseline 250 GB DB measured before and after
~6 hours of unrelated vcomp writes shifted from 40.30 s → 41.71 s on
100k reads (+2.8%). Not enough to explain the 10% gap on its own, but
clearly in the direction we suspected.

### Decisions that came out of this round
1. The fragmentation effect deserves an isolated experiment to be
   queued and run later (without polluting our current measurements).
   Recorded in [EXPERIMENTS_PLANNED.md](experiments/docs/EXPERIMENTS_PLANNED.md): N=20
   baseline-only loads, readrandom on `fill_run_1` after each new
   load, ~7.3 hours.
2. Reload baseline 30× from scratch *now*, alongside the 4/14 vcomp
   batch, so a future readrandom comparison can use a clean 30-vs-30
   pair without cross-day disk skew. Currently in progress as
   `260415_0635_250gb_x30/`.
3. Documentation reorganisation:
   - `vcomp/README.md` is now a pure spec of the
     current code, no time-ordered narrative or measurement tables.
   - `experiments/docs/RESULTS.md` collects only the current best
     measurements with caveats.
   - This README's historical changelog (formerly
     `BENCHMARK_HISTORY.md`) keeps the time-ordered narrative; old
     measurement tables are summarised inline as historical snapshots,
     full numbers live in RESULTS.md.
   - `experiments/docs/EXPERIMENTS_PLANNED.md` holds queued experiments.

### Open at the end of this round
- L2 erosion gap (vcomp 86% ± 3pp vs baseline 70% ± 9pp).
- bloom_fpr +22% gap (PLR-materialised keys vs original).
- Disk fragmentation effect on readrandom — quantitative answer
  pending the queued experiment.
- Fresh baseline 30×30 readrandom pending finish of the 4/15 reload
  batch.

## 2026-05-11 — Tree-shape root cause: intra-L0 absence in vcomp

### Goal
Pin down *why* vcomp produces a structurally different tree from
baseline (L1/L2/L3 coverage all shifted upward, end-state L1 files 6×
wider). Carried over from the §6 open question in
[RESULTS.md](experiments/docs/RESULTS.md).

### Investigation chain (in the order it actually happened)

1. **Coverage as the load-bearing structural metric.** Re-extracted
   per-level coverage for fresh baseline (260415) and vcomp (260414)
   batches via `coverage` bench. Stable picture: vcomp ↑ at every
   non-bottom level. Side-by-side box plot:
   [coverage_box_sg_final.png](experiments/results/coverage/coverage_box_sg_final.png).

2. **Coverage → filter_per_get is direct and quantitative.** For a
   uniform query workload, expected `filter_per_get` at level L equals
   `coverage(L) × span(L) / query_range`. Summing across L1–L4:
   ```
   predicted Δ filter_per_get = +0.520
   measured  Δ filter_per_get = +0.512
   ```
   So fixing coverage *will* close most of the +17% filter-read gap.
   Plot/CSV: [/tmp/l2_at_l0l1.csv](file:///tmp/l2_at_l0l1.csv) (side experiment).

3. **Compaction input/output joint stats — first big clue.**
   Per-(start_level → output_level) pair, across 30+30 runs:

   | Pair       | baseline (cnt, in, out, MB/file_out, ratio) | vcomp (cnt, in, out, MB/file_out, ratio) |
   |------------|----------------------------------------------|------------------------------------------|
   | L0→L0      | 1034, 4.08, **1.00**, **309 MB**, 0.25       | 139, 6.64, 6.64, 64 MB, 1.00             |
   | L0→L1      | 76, 27.5, **52.0**, 85 MB, **1.89**          | 731, 9.3, 9.8, 58 MB, 1.05               |
   | L1→L2      | 1418, 2.28, 3.19, 52 MB, 1.40                | 4309, 5.09, 5.05, 58 MB, 0.99            |
   | L2→L3      | 3709, 4.09, 4.03, 60 MB, 0.98                | 4055, 6.09, 5.94, 64 MB, 0.98            |
   | L3→L4      | 2634, 2.93, 2.71, 52 MB, 0.92                | 2493, 3.48, 3.28, 54 MB, 0.94            |

   Two qualitatively different rows: **L0→L0** (intra-L0) and **L0→L1**.
   Everything below L1→L2 is essentially identical.

4. **Intra-L0 in baseline is a true megafile consolidator.** 1034
   events per run, every event takes ~4 raw L0 files (each ~64 MB) and
   produces *1* output of ~309 MB (5× target_sst_size). vcomp's
   intra-L0 is a no-op: takes 6.64 files and emits 6.64 files of the
   same target size.

5. **Why vcomp's intra-L0 is a no-op — code reading.**
   - Baseline's `CompactionOutputs::ShouldStopBefore`
     ([db/compaction/compaction_outputs.cc:275–278](db/compaction/compaction_outputs.cc#L275-L278))
     has an explicit early return when `output_level() == 0`. So real
     intra-L0 never gets split. The output file size is unbounded by
     `max_output_file_size`.
   - vcomp's `SplitIntoSSTs`
     ([db/virtual_compaction/virtual_sst.cc](db/virtual_compaction/virtual_sst.cc))
     has no such exemption. When called with `target_level=0` it goes
     through the same size-based split loop and produces N×target_sst_size
     outputs.

6. **Why baseline triggers intra-L0 so often — also code reading.**
   In `LevelCompactionBuilder::PickFileToCompact`
   ([db/compaction/compaction_picker_level.cc:793–800](db/compaction/compaction_picker_level.cc#L793-L800)):
   ```cpp
   if (start_level_ == 0 &&
       !compaction_picker_->level0_compactions_in_progress()->empty()) {
     if (PickSizeBasedIntraL0Compaction()) return true;
     ...
   }
   ```
   The "already an L0 compaction in progress" condition is the gate.
   Baseline's L0→L1 is slow real I/O (seconds), so whenever the picker
   wakes up to schedule the next compaction, the previous L0→L1 is
   still running — `level0_compactions_in_progress()` is non-empty,
   so it cascades into intra-L0. vcomp's L0→L1 is microseconds, so
   that set is almost always empty by the time the picker wakes —
   intra-L0 path is bypassed.

7. **Initial wrong hypothesis: L2 grandparent density.** Theory was
   that vcomp's faster Phase 1 outruns L1→L2 cascade, leaving L2
   sparse at L0→L1 time, so grandparent boundaries are scarce and
   splits are weak. **Direct measurement rejected this.** At each
   L0→L1 event, snapshot L2 file count: baseline median 42, vcomp
   median 93. vcomp has *more* grandparents, not fewer. Side
   experiment: [/tmp/l2_at_l0l1.csv](file:///tmp/l2_at_l0l1.csv),
   [/tmp/l2_at_l0l1.png](file:///tmp/l2_at_l0l1.png).

8. **Write-stall observation (separately confirmed).** Baseline runs
   spend 47.8% ± 1.3% of their write time stalled (`l0-file-count-limit-delays`,
   `pending-compaction-bytes-delays`). vcomp's `RegisterVirtualL0File`
   bypasses RocksDB's `WriteController`, so vcomp stalls 0% — L0
   never accumulates, intra-L0 never gets a reason to fire.

### Fixes applied today

Both fixes were scoped to L0 behavior; deferred L1+ grandparent cut items
remained out of scope.

**Fix 1 — intra-L0 megafile output** ([db/virtual_compaction/virtual_sst.cc](db/virtual_compaction/virtual_sst.cc))

```cpp
// Intra-L0 (target_level == 0): baseline never splits L0 outputs.
// Emit a single VirtualSST per intra-L0 compaction.
if (target_level == 0) {
    VirtualSST vsst;
    vsst.plr_model  = plr;
    vsst.key_min    = global_min;
    vsst.key_max    = global_max;
    vsst.num_entries = total_entries;
    vsst.level      = 0;
    vsst.size_bytes = VirtualSST::EstimateSize(total_entries, avg_entry_size);
    result.push_back(std::move(vsst));
    return result;
}
```

**Fix 2 — force intra-L0 firing in vcomp picker**
([db/compaction/compaction_picker_level.cc](db/compaction/compaction_picker_level.cc),
top of `PickFileToCompact`)

```cpp
if (start_level_ == 0 && ioptions_.use_virtual_compaction) {
    const auto& level_files = vstorage_->LevelFiles(0);
    const size_t min_files = static_cast<size_t>(
        mutable_cf_options_.level0_file_num_compaction_trigger);
    if (level_files.size() >= min_files && !level_files[0]->being_compacted) {
        start_level_inputs_.clear();
        if (FindIntraL0Compaction(level_files, kMinFilesForIntraL0Compaction,
                                  std::numeric_limits<uint64_t>::max(),
                                  mutable_cf_options_.max_compaction_bytes,
                                  &start_level_inputs_)) {
            output_level_ = 0;
            return true;
        }
    }
}
```

### Result of Fix 1 + Fix 2 (single 250 GB load)

| Pair       | baseline | vcomp default | **vcomp (after fixes)** |
|------------|----------|----------------|---------------------------|
| L0→L0 events     | 1054        | 139            | **996** ✓                 |
| L0→L0 in/out     | 4 / **1**   | 6.64 / 6.64    | **4.51 / 1.00** ✓         |
| L0→L1 events     | 76          | 731            | 497                       |
| L0→L1 in / out   | 27.5 / 52.0 | 9.3 / 9.8      | 4.84 / 12.69              |
| L0→L1 out/in     | **1.89**    | 1.05           | **2.62** (split firing)   |
| L1 cov %         | 35          | 65             | 61                        |
| L2 cov %         | 66          | 87             | 90                        |
| L1 file width    | ~7 M        | ~41 M          | ~31 M                     |

intra-L0 frequency now matches baseline. L0→L1's output/input ratio
jumped from 1.05 → 2.62, evidence that the per-compaction split
algorithm is now seeing enough data per call to fire its grandparent
cuts. But:

- **L0→L1 input mean fell to 4.84** (baseline 27.5). Without write
  stall, L0 still drains too fast — when the picker fires L0→L1, most
  L0 files are being_compacted by parallel intra-L0 BG threads, so
  only ~5 free files are available per pick.
- Resulting L1 widths (~31 M) narrowed from default 41 M but are still
  4× baseline's 7 M.
- L1 coverage moved 65% → 61% (toward baseline 35%) — partial.
- L2 coverage barely moved (87 → 90, baseline 66) — essentially flat.

### Open at the end of this round

Intra-L0 frequency matching alone is **necessary but not sufficient**.
The remaining gap is the *L0→L1 input size*, which is driven by L0
accumulation, which is driven by write stall.

Next: implement write stall in vcomp's `RegisterVirtualL0File` path
so the foreground sees baseline-like back-pressure
(`l0-file-count-limit-delays`, `pending-compaction-bytes-delays`).
Plan recorded in [EXPERIMENTS_PLANNED.md](experiments/docs/EXPERIMENTS_PLANNED.md).

### Files changed (uncommitted)
- `db/virtual_compaction/virtual_sst.cc` — `SplitIntoSSTs` early
  return for `target_level == 0`.
- `db/compaction/compaction_picker_level.cc` — force intra-L0 try at
  top of `PickFileToCompact` when `use_virtual_compaction`.

### Side-experiment artifacts kept under /tmp
- `/tmp/l2_at_l0l1.py`, `.csv`, `.png` — L2 grandparent count at L0→L1
  time (used to reject the early "sparse grandparent" hypothesis).
- `/tmp/outputs_per_l0l1.py`, `.csv`, `.png` — per-event input/output
  count.
- `/tmp/compaction_summary.py`, `.csv`, `.txt` — per-pair compaction
  summary table (the one quoted in step 3).

## 2026-05-12 — Tree-shape gap closed: L0→L1 size gate + final L0 drain

### Goal
Continue from 2026-05-11. The force-intra-L0 patch closed half the gap
but left L0→L1 picking too-small batches in vcomp because L0 never
accumulated enough between picks. Goal of today: make vcomp's tree
structurally match baseline.

### Hypothesis path (in the order it actually happened)

1. **"Add write stall to vcomp" — discarded.** Reading the picker
   code more carefully: write stall fires off L0 file *count*, but
   vcomp's BG drains L0 faster than foreground produces it once you
   force intra-L0. So a stall trigger would basically never fire in
   vcomp anyway. Stall is a *symptom* of baseline's slow real-I/O,
   not the cause of its tree shape.

2. **"Use existing `level0_file_num_compaction_trigger` higher" —
   works empirically.** L0_TRIGGER=60 + force-intra-L0 + intra-L0
   megafile gave L2 cov 61.4 % vs baseline 65.8 % (Δ 4.4 pp); L3 cov
   88.65 % vs 88.8 % (essentially matched). But the coupling means
   baseline would also have to run with `level0_file_num_compaction_trigger=60`
   for a fair compare, which is silly — the *structural* variable is
   per-event L0→L1 *data volume*, not file count.

3. **"Size-gate the L0→L1 picker, vcomp-only" — implemented.**
   Replace the file-count score gate with a data-volume gate in
   vcomp mode. Baseline measurement (n=83 events from
   `baseline_run1` in the 260415 batch) gave `input_data_size`
   mean **4876 MB**, median 4910 MB. Hardcoded threshold 4 GB.

4. **L0 accumulating breaks `flush` benchmark.** With size-gate 4 GB,
   end-of-Phase-1 left ~32 virtual L0 files, materialised to real
   L0 files. RocksDB's `level0_slowdown_writes_trigger=20` /
   `stop_writes_trigger=36` defaults trigger write stall, and the
   subsequent `flush` step's `WaitUntilFlushWouldNotStallWrites`
   blocks indefinitely. Fix in `experiments/scripts/load/load.sh`: set both
   triggers to 10000 in vcomp mode (`RegisterVirtualL0File` already
   bypasses `WriteController`, so disabling stall has no effect on
   foreground pacing).

5. **`compact0` after Phase 2 was real I/O on materialised L0 leftovers.**
   This added 10–13 s on top of the 12–13 s fillvirtual. To eliminate:
   drain L0 *during Phase 1 wrap-up*, while files are still virtual.
   Added an end-of-load step in `FillVirtual`:
   ```cpp
   SetVcompL0L1MinDataBytes(0);                    // drop the gate
   db_.db->SetOptions({{"level0_file_num_compaction_trigger", "3"}});
                                                    // wake picker
   db_.db->WaitForCompact(...);                     // BG drains L0
   SetVcompL0L1MinDataBytes(4000ULL * 1024 * 1024); // restore
   db_.db->SetOptions({{"level0_file_num_compaction_trigger", "4"}});
   ```
   Drain takes ~80 ms. After, Phase 2 sees `L0:0`, and the subsequent
   `compact0` benchmark reports "found 0 files to compact".

### Patches in this round
- **`db/compaction/compaction_picker_level.cc`** — replace the
  force-intra-L0 patch with a vcomp-only size gate at the top of
  `PickFileToCompact`. Gate reads from a process-global atomic so
  `FillVirtual` can lower it to 0 for the end-of-load drain. Also
  re-blocks the line-242 intra-L0 fallback in vcomp mode (it's a
  no-op with the size gate, but keeps the picker decision tree
  clean).
- **`tools/db_bench_tool.cc`** — declare `SetVcompL0L1MinDataBytes`
  (defined in the picker), insert end-of-load drain block in
  `FillVirtual` between the first `WaitForCompact` and
  `PauseBackgroundWork`.
- **`experiments/scripts/load/load.sh`** — set
  `--level0_slowdown_writes_trigger=10000` and
  `--level0_stop_writes_trigger=10000` unconditionally in vcomp mode
  (was previously only set when `L0_TRIGGER` env was passed).

Phase-2 epoch fix, `PauseBackgroundWork`/`ContinueBackgroundWork` around
Phase 2, and the `target_level==0` early return in `SplitIntoSSTs`
all stay in place from yesterday — even though intra-L0 doesn't fire
in vcomp anymore, those keep the failure modes covered if it ever
does.

### Result — 30-run batch at 250 GB

`260512_0555_250gb_x30` vs baseline reference `260415_0635_250gb_x30`:

| Metric                | baseline (n=30) | vcomp drain (n=30) | Δ |
|-----------------------|------------------|---------------------|---|
| L1 cov %              | 34.6 ± 28.96 (8–100) | 19.6 ± 11.9 (8.5–64) | within range |
| L2 cov %              | 65.8 ± 9.06 (44–81) | 61.4 ± 2.6 (56–65) | within range |
| L3 cov %              | **88.8 ± 0.96**     | **87.9 ± 0.62**     | **−0.9 pp** ✓ |
| L4 cov %              | 100                  | 100                  | 0 |
| L1 files              | 3.6 ± 0.9            | **3.9 ± 0.6** ✓     | +0.3 |
| L2 files              | **45.7 ± 3.1**       | **48.5 ± 2.6** ✓    | +2.8 |
| L3 files              | **439.7 ± 5.1**      | **427.6 ± 3.9** ✓   | −12.1 |
| L4 files              | 2809.2 ± 11.8        | 2622.3 ± 11.4        | −186.9 |
| Per-event L0→L1 data  | 4876 MB              | ~4900 MB             | match |
| L0→L1 events / run    | 76                   | ~63                  | close |

L2/L3/L4 file counts within 3 % of baseline. L3 cov, L4 cov match. L1
and L2 cov sit in the lower half of baseline's distribution (vcomp's
trees are slightly sparser there) but inside baseline's range.

Box-plot artifact:
[coverage_box_sg_final.png](experiments/results/coverage/coverage_box_sg_final.png).

### What is *still* slightly off
- L1/L2 coverage medians sit lower than baseline's (19.6 vs 34.6 for
  L1; 61.4 vs 65.8 for L2). Both inside baseline's range but lower
  half. Not chased further — diminishing returns vs. the file-count
  + L3/L4 match we already have.
- L4 file count: vcomp 2622 vs baseline 2809 (−6.7 %). vcomp's L4
  files are individually larger (less aggressive grandparent split
  with the simpler split algorithm). Deferred grandparent cut conditions
  remain the natural next lever if we ever need to close this.

### Loading time
Phase 1 8.0 s + drain 0.08 s + Phase 2 4.5 s = ~12.6 s. Total wall
clock 18 s (incl. RocksDB startup/teardown). Same as default vcomp.
`compact0` reports "found 0 files to compact" — Phase 2 sees only
L1+ files, no L0 leftover for real I/O.

### Side experiments superseded
The L0_TRIGGER=60 30-batch (`260512_0445_250gb_x30`) and the size-gate
@ 3 GB 30-batch (`260512_0457_250gb_x30`) both kept on disk but are
strictly worse than the drain version — useful only for the
methodology trail. Coverage dumps under
`experiments/artifacts/coverage_dumps/vcomp_260512_l0t60/`,
`vcomp_260512_sg3000/`, `vcomp_260512_sg4000/`.

## 2026-05-13 — Postscript: read-side gap is mostly NVMe state, not tree

After the 2026-05-12 tree-shape fix, the 30×30 readrandom comparison
(baseline `260415_0635` vs vcomp_new `260512_0555`) still showed
vcomp_new −18.2 % on elapsed. To check whether this was a vcomp
benefit or a measurement artefact:

1. Remeasured all 30 baseline DBs on 2026-05-13 (same binary, cache=0,
   1 thread). Per-block latency 125.9 → 141.9 µs. Originally-fast DBs
   (April CoV-driven outliers at ~112 µs) lost ~29 µs each;
   originally-slow DBs lost ~7 µs each; all 30 converged to ~142 µs.
2. Deep-copied 5 baseline DBs (run4/7/14/8/12, spanning the original
   fast/slow spectrum) to new LBAs on `/work/vcomp/copy_test/`, then
   readrandom each. **All 5 copies hit 105.9-107.1 µs/block — identical
   to vcomp_new's 107 µs, regardless of the source DB's identity**.
   Same SST file content, only physical location on NVMe differs.

**Conclusion**:
- ~13 pp of the −18.2 % elapsed gap is the **NVMe physical-state
  effect**: aged LBAs (1 month) → 141 µs/block; fresh LBAs →
  106 µs/block. Independent of DB content.
- ~5 pp is the **genuine tree-shape advantage**: sst_reads_per_get
  3.92 → 3.77, filter_per_get −5.9 %.
- vcomp_new's tight CoV (0.8 % on elapsed) vs baseline's wide CoV
  (11.9 %) was the same NVMe-state effect. All 30 vcomp DBs were
  loaded inside one 17-min window → uniform fresh layout; baseline's
  30 DBs aged unevenly over the past month into different physical
  positions.

Trustworthy structural metrics (independent of NVMe state): all
`*_per_get` counts, bloom FPR, per-level hits, bytes_per_sst_read.
These show the tree-shape parity goal achieved (§"Result" table
above). The throughput / latency numbers in
[experiments/docs/RESULTS.md §3.1](experiments/docs/RESULTS.md) should be
read as "vcomp_new vs aged-baseline"; §3.3 there gives the
decomposition.

Predicts vcomp_new will degrade to ~141 µs/block if remeasured in a
month (matching aged-baseline today). Untested; would confirm
"fresh-write" effect is purely a function of time-on-disk, not a
vcomp property.

## 2026-05-31 — Synthetic-only reset: L0 pacing and byte-based baseline targets

We decided to park the Twitter/trace KV line for now and make
`vcomp-proto` complete as a **synthetic fillvirtual** branch. The research
target for this branch is the original shape-only vcomp result: preserve
baseline-like LSM shape and read behavior while avoiding real compaction I/O
during loading. KV preservation remains useful engineering work, but it is not
the current paper path.

### Stable result to keep as the headline

The clean no-KV synthetic result is still the 250 GB pre-KV-preservation
measurement in
[RESULTS.md §1.3](experiments/docs/RESULTS.md#13-250-gb-pre-kv-preservation-vcomp-breakdown).
This is the focused phase-breakdown run; the broader scaling table below uses
the full load-script wall-clock numbers from separate single-load runs.

| Metric | Value |
|--------|-------|
| Baseline `fillrandom` | 763.40 s |
| vcomp total | 12.59 s |
| Speedup | 60.6x |
| vcomp virtual metadata work | 8.24 s |
| vcomp materialize + VersionEdit | 4.25 s |

250 GB vcomp breakdown:

| Component | Time |
|-----------|------|
| Key generation | 3.11 s |
| Sort | 2.72 s |
| PLR fit | 0.79 s |
| VSST registration | 1.62 s |
| BG wait | 0.10 s |
| Materialize | 3.47 s |
| VersionEdit | 0.78 s |
| Total | 12.59 s |

Large-load write-amplification result from [RESULTS.md §1.2](experiments/docs/RESULTS.md#12-single-load-scaling):

| Scale | Baseline elapsed | vcomp elapsed | Load speedup | Baseline compaction I/O | vcomp compaction I/O |
|-------|------------------|---------------|--------------|--------------------------|----------------------|
| 250 GB | 781 s | 18 s | 43x | 2.6 TB | 0.18 TB final write |
| 1 TB | 3,891 s | 109 s | 36x | 13.5 TB | 0.75 TB final write |
| 5 TB | 20,465 s | 1,862 s | 11x | 101.4 TB | 3.5 TB final write |
| 10 TB | 50,818 s | 8,469 s | 6x | 225.4 TB | 7.7 TB final write |

### KV-preserving trace work, parked

KV preservation cannot reconstruct original keys from metadata alone. The
least magical design is an append-only KV/key log plus metadata-only virtual
compaction, followed by Phase 2 routing of real keys into final VSST ranges.
The trace implementation reached exact found-key parity, but Phase 2
materialization was the bottleneck.

Exactness checks from [RESULTS.md §4.2](experiments/docs/RESULTS.md#42-found-key-exactness):

| Load | Read window | Baseline misses | vcomp misses |
|------|-------------|-----------------|--------------|
| 1M puts | first 1M records | 10,941 / 198,797 gets | exact |
| 10M puts | first 5M records | 15,045 / 1,006,713 gets | exact |
| 100M puts | first 5M records | 15,040 / 1,006,713 gets | exact |

Load-time snapshot:

| Scale | Baseline elapsed | vcomp-w/kv elapsed | Baseline core load | vcomp core load |
|-------|------------------|--------------------|--------------------|-----------------|
| 10M | 40 s | 28 s | 22.45 s | 18.16 s |
| 100M | 490 s | 337 s | 463.44 s | 232.31 s |

100M vcomp-w/kv core breakdown: Phase 1 25.76 s, Phase 2 206.48 s,
total 232.31 s. 98,734,869 raw keys materialized, 61,731 gap-patch keys
written, 4,659 duplicate keys skipped.

### Why the old fast path diverges structurally at large scale

The fast batch-register path exposes L0 VSSTs much faster than baseline
flush can expose real L0 files. That creates two failures:

- L0 score stays artificially high, so RocksDB repeatedly picks L0->Lbase
  compactions.
- L1 drain can be starved because the picker keeps seeing pending L0 work.

The 1 TB batch-register run is fast but not a trustworthy structural model:

| Run | Wall-clock uptime | `fillvirtual` core | Phase 1 | Phase 2 | Phase 1 register | BG virtual jobs |
|-----|-------------------|--------------------|---------|---------|------------------|-----------------|
| 250 GB regbatch256 | 15.8 s | 10.814 s | 6.154 s | 4.210 s | 0.019 s / 16 calls | 4,720 |
| 1 TB batchpatch | 60.3 s | 55.281 s | 34.876 s | 18.916 s | 0.727 s / 63 calls | 26,504 |
| 1 TB 4GB L0 cap | 65.7 s | 60.717 s | 37.986 s | 19.984 s | 0.638 s / 63 calls | 34,447 |

The 4GB L0->Lbase cap did not solve the root problem; it added more BG jobs
and slightly slowed 1 TB. It was also a fixed heuristic, while baseline's
actual L0 input bytes vary with intra-L0 compaction history.

### LogAndApply bottleneck and thread sweep

`perf` on the large vcomp runs showed that most sampled time was waiting on
synchronization, not doing PLR work:

| Symbol / region | Overhead |
|-----------------|----------|
| `__GI___futex_abstimed_wait_cancelable64` | 80.57% |
| `__GI___lll_lock_wait` | 15.02% |
| `NWayMergePLR` | 0.48% |
| `std::__introsort_loop<uint64_t>` | 0.23% |

The important interpretation: the observed cost is the RocksDB DB mutex /
VersionSet writer queue / manifest-and-version critical section around
`LogAndApply`, not `PerfStepTimer` itself. `PerfStepTimer` appears in the
stack because it stops while the thread is already inside the measured
critical path.

1 TB BG-thread sweep with the batch-register implementation:

| `max_background_jobs` | Status | Total | Phase 1 | BG wait | LogAndApply calls | LogAndApply total | Writer wait |
|-----------------------|--------|-------|---------|---------|-------------------|-------------------|-------------|
| 4 | fail | - | 23.535 s | - | - | - | - |
| 8 | fail | - | 24.391 s | - | - | - | - |
| 12 | fail | - | 28.474 s | - | - | - | - |
| 16 | ok | 81.717 s | 35.576 s | 28.677 s | 30,493 | 381.770 s | 338.387 s |
| 24 | ok | 60.327 s | 38.859 s | 3.476 s | 32,163 | 431.744 s | 396.613 s |
| 36 | ok | 57.335 s | 37.143 s | 1.604 s | 31,842 | 453.406 s | 425.383 s |
| 48 | ok | 54.957 s | 35.691 s | 0.986 s | 31,171 | 473.233 s | 448.974 s |

More BG threads increase cumulative writer-wait time, but still reduce wall
time up to 48 jobs because they expose more parallel PLR work and shorten the
final BG wait. This is why cumulative wait alone is misleading; it must be
read together with wall clock and per-thread work.

### Baseline L0 behavior must be parsed by bytes, not file count

Important correction from today's discussion: intra-L0 compaction means a
single L0 file is often already a 4x-consolidated file. Therefore L0->L1
matching must use **L0 input bytes**. File count alone is not a stable target.

Baseline LOG data source:

- `EVENT_LOG_v1` `compaction_started` has `files_L0`, optional `files_L1`,
  and total `input_data_size`.
- `EVENT_LOG_v1` `table_file_creation` maps `file_number -> file_size`.
- L0-only input bytes are computed as `sum(file_size[f] for f in files_L0)`.

1 TB baseline reference:

- DB: `/work/vcomp/baseline_1000gb`
- LOG: `/work/vcomp/baseline_1000gb/LOG.old.1775738985241643`
- Cutoff used for fillrandom-only parsing: `2026/04/09-12:49:21`
- Options: `max_background_jobs=48`, `level0_file_num_compaction_trigger=4`,
  `level0_slowdown_writes_trigger=20`, `level0_stop_writes_trigger=36`

| Compaction | Events | L0 files avg / median / p99 / max | L0 input bytes avg / median / p99 / max | Total input bytes avg / median / p99 / max |
|------------|--------|------------------------------------|------------------------------------------|---------------------------------------------|
| L0->L0 | 3,777 | 4.09 / 4 / 6 / 7 | 259.7 MB / 251.0 MB / 376.5 MB / 1.53 GB | same |
| L0->L1 | 403 | 12.21 / 14 / 17 / 17 | 2,579.2 MB / 3,261.9 MB / 4,829.7 MB / 5.51 GB | 2,883.1 MB / 3,576.7 MB / 5,696.5 MB / 7.28 GB |

5 TB baseline reference:

- DB: `/work/vcomp/baseline_5tb`
- LOG: `/work/vcomp/baseline_5tb/LOG`
- Cutoff used for fillrandom-only parsing: `2026/04/30-22:00:52`
- Same L0 trigger/slowdown/stop options as the 1 TB run

| Compaction | Events | L0 files avg / median / p99 / max | L0 input bytes avg / median / p99 / max | Total input bytes avg / median / p99 / max |
|------------|--------|------------------------------------|------------------------------------------|---------------------------------------------|
| L0->L0 | 18,568 | 4.05 / 4 / 5 / 7 | 275.1 MB / 251.0 MB / 1,066.7 MB / 1.53 GB | same |
| L0->L1 | 4,644 | 6.09 / 6 / 13 / 16 | 1,147.5 MB / 1,003.9 MB / 3,199.7 MB / 4.53 GB | 1,681.5 MB / 1,479.5 MB / 3,983.0 MB / 8.31 GB |

This changes the design target. The current design keeps a bounded visible L0
byte window and refills it while:

```
virtual_l0_visible_bytes_ < target_bytes
```

Registered VSSTs are immediately visible to RocksDB's regular L0 score and
compaction picker.

Strict apples-to-apples note: the 1 TB and 5 TB baseline logs above were
loaded with the same options but different git SHAs. For final paper numbers,
rerun baseline and vcomp with the same binary and parse the same byte metrics.

## 2026-06-01 — Size-gated pending window, failed file-number experiments, intra-L0-off ablation

This checkpoint finalized the bounded visible-L0 window design:

- Queue generated L0 VSSTs in memory.
- Register bounded visible L0 batches through `RefillVirtualL0Window()`.
- Use `--vcomp_register_batch_max=256` as the maximum files per registration
  `VersionEdit`.
- Use `--vcomp_visible_l0_batch_mb=4096` as the visible L0 byte target.

Latest stable runs before the intra-L0-off patch:

| Scale | Run | Total | Phase 1 | BG wait | Phase 2 | Final SSTs | Final size | Materialized keys |
|-------|-----|-------|---------|---------|---------|------------|------------|-------------------|
| 250 GB | `visible_window_250gb_260601_072152` | 10.153 s | 6.460 s | 0.251 s | 3.442 s | 3,102 | 176.59 GB | 182,272,697 |
| 1 TB | `visible_window_1tb_260601_072600` | 57.248 s | 39.708 s | 1.307 s | 16.233 s | 12,849 | 769.26 GB | 794,002,100 |

1 TB BG virtual compaction breakdown:

| Jobs | Inputs | Outputs | Total | LogAndApply | Commit queue wait |
|------|--------|---------|-------|-------------|-------------------|
| 35,623 | 189,376 | 186,225 | 710.299 s | 568.290 s | 203.726 s |

### Baseline vs vcomp input shape

The current vcomp run does not simply suffer from larger prepared SSTs. L0
flush-size is similar to baseline. The structural issue is that vcomp exposes
too many L0 files per L0->L1 event, causing more L1 overlap.

1 TB comparison:

| Metric | Baseline | vcomp current |
|--------|----------|---------------|
| L0 flush files | 16,590 | 16,000 |
| Avg flush size | 62.4 MiB | ~64 MiB |
| Total compaction jobs | 48,254 | 35,623 |
| Total input files | 195,870 | 189,376 |
| Total input size | 12,083 GiB | 11,411 GiB |

L0->L1 specifically:

| Metric | Baseline | vcomp current |
|--------|----------|---------------|
| Jobs | 403 | 250 |
| Avg L0 input files | 12.2 | 60.1 |
| Avg L1 overlap files | 4.9 | 31.2 |
| Avg total input | 2.8 GiB | 6.7 GiB |

Before disabling intra-L0, vcomp intra-L0 was far below baseline:

| Scale / run | L0->L0 jobs | Avg input files | Avg input size | Output |
|-------------|-------------|-----------------|----------------|--------|
| Baseline 1 TB | 3,779 | 4.09 | 259.7 MiB | normal RocksDB split |
| vcomp 250 GB | 51 | 6.00 | 421.6 MiB | always 1 file |
| vcomp 1 TB | 193 | 6.00 | 393.9 MiB | always 1 file |

Interpretation: the next tuning target is the visible L0 byte target, not
file-number allocation. The 4 GiB visible batch is larger than baseline's
average L0->L1 total input, and much larger than baseline's average L0-only
input.

### Failed file-number experiments

Two attempts tried to remove Phase 1 file-number mutex cost:

- No-lock Phase 1 `NewFileNumber()`.
- Reserved L0 file-number ranges.

Both reduced the explicit Phase 1 file-number mutex cost but changed the final
LSM and materialized key count. They were reverted.

1 TB comparison:

| Run | Total | Phase 1 | BG wait | Phase 2 | Final SSTs | Final size | Final keys |
|-----|-------|---------|---------|---------|------------|------------|------------|
| Normal visible-window | 57.248 s | 39.708 s | 1.307 s | 16.233 s | 12,849 | 769.26 GB | 794,002,100 |
| No-lock Phase 1 file numbers | 70.687 s | 24.777 s | 31.345 s | 14.566 s | 11,535 | 687.32 GB | 709,435,976 |
| Reserved L0 file numbers | 70.024 s | 24.187 s | 30.418 s | 15.419 s | 11,545 | 672.08 GB | 693,698,022 |

Root cause found by parsing virtual compaction logs: final key-count
divergence exactly equals cumulative virtual dedup estimate divergence.

| Run | Initial entries | Cumulative dedup | Final keys |
|-----|-----------------|------------------|------------|
| Normal | 1,048,543,569 | 254,541,469 | 794,002,100 |
| No-lock | 1,048,543,569 | 339,107,593 | 709,435,976 |
| Reserved | 1,048,543,569 | 354,845,547 | 693,698,022 |

Reserved file numbers caused 100,304,078 more deduped entries than the normal
run, exactly matching the 100,304,078-key final difference. This means the
performance regression was not just mutex contention; the modified timing/order
changed compaction overlap and therefore PLR dedup estimates.

### Current intra-L0-off patch

For the next ablation, vcomp-mode intra-L0 compaction is disabled:

- `PickIntraL0Compaction()` returns false when
  `ioptions_.use_virtual_compaction` is true.
- `PickSizeBasedIntraL0Compaction()` returns false under the same condition.
- Baseline RocksDB behavior is unchanged.

Smoke validation:

- Run: `/home/smrc/virtual_compaction/vcomp/experiments/artifacts/log_loads/intra_l0_off_smoke_260601_115612/bench.out`
- Size: 20 GiB (`num=20,971,520`)
- Result transitions: `L0->L1 = 5`, `L1->L2 = 21`, `L2->L3 = 40`,
  `L0->L0 = 0`
- `fillvirtual` total: 1.180 s

Next required validation: rerun 250 GB and 1 TB with intra-L0 disabled and
compare L0->L1 input size, cumulative dedup, final keys, runtime, and final
level shape against the normal visible-window runs above.

## 2026-06-02 - Metadata critical path and profiling plan

After the intra-L0-off ablation, we found high-level range outliers in virtual
compaction. The PLR merge path was updated so a model contributes density only
when `k_mid` falls inside the current active PLR segment. Gaps between segments
now contribute zero density instead of smearing rank mass over empty key space.
Range-stat logging was also added for high-level or unusually wide outputs.

The latest 1 TB bottleneck is not PLR CPU. Most time is in serialized
VersionSet metadata work around `LogAndApply`.

| Run | Scale | Total | Phase 1 | BG wait | Phase 2 | LogAndApply total | Key metadata cost |
|-----|-------|-------|---------|---------|---------|-------------------|-------------------|
| `metadata_timer` | 1 TB | 62.167 s | 24.198 s | 23.751 s | 14.219 s | 44.955 s | compaction_pri 8.098 s, file_index 9.859 s, bottommost 6.038 s |
| `skipindexer` | 1 TB | 57.661 s | 23.666 s | 20.586 s | 13.409 s | 41.450 s | compaction_pri 12.042 s, file_index 6.925 s, bottommost 0 s |
| `resetpri` current | 1 TB | 57.561 s | 23.555 s | 20.787 s | 13.219 s | 41.456 s | compaction_pri 8.131 s, file_index 8.766 s, bottommost 0 s |
| `resetpri` current | 250 GB | 9.709 s | 5.828 s | 0.331 s | 3.550 s | 2.575 s | compaction_pri 0.689 s, file_index 0.445 s |

Current kept optimizations:

- Skip `GenerateBottommostFiles()` in vcomp mode.
- Skip `GenerateFileIndexer()` in vcomp mode.
- Rebuild `files_by_compaction_pri_` only for changed levels.
- Add direct timers for `PrepareForVersionAppend()`, per-level compaction
  priority, file-index substeps, and post-prepare VersionSet work.

Rejected optimizations:

| Attempt | 1 TB total | Reason |
|---------|------------|--------|
| direct compaction-pri fast path (`prifast`) | 61.111 s | slower than current |
| AddFile-time file-location map (`locfast`) | 60.625 s | moved cost into `SaveTo()` and slowed total |
| skip L0 non-overlap generation (`l0skip`) | 58.272 s | no meaningful gain |
| obsolete-file fast path (`obsoletefast`) | 67.808 s | slower than current |

Low CPU utilization means the next question is specific: are BG threads mostly
blocked on locks/waits, or is one serialized CPU region doing all useful work?
We should profile active `rocksdb:low` threads, not whole-process futex time.

Profiling plan:

1. Run a 1 TB vcomp load with the current binary and keep the process alive
   through Phase 1 and BG wait.
2. Capture `db_bench` PID and `rocksdb:low` TIDs with `ps -L -p <pid>`.
3. Record on-CPU stacks: `perf record -g -e task-clock -p <pid> -- sleep 30`.
4. Record off-CPU scheduling: `perf sched record -p <pid> -- sleep 30`, then
   inspect `perf sched latency`.
5. If futex wait dominates, record lock/wakeup stacks:
   `perf record -g -e sched:sched_switch,sched:sched_wakeup -p <pid> -- sleep 30`.
6. Classify wait sites by stack: idle BG condition variable, VersionSet writer
   queue, DB mutex, virtual compaction commit condition variable, or L0 refill
   wait.
7. Decision rule: one busy BG thread in VersionSet prepare plus many waiting
   followers means serialized CPU; many BG threads blocked in writer queue or
   DB mutex means lock/wait; many BG threads active in PLR merge/split means
   CPU parallel work is the bottleneck.

Next action: add a small profiling runner under `experiments/` so the 1 TB load
starts, captures PID/TIDs, records both on-CPU and off-CPU profiles, and stores
reports next to the benchmark log.

Completed profiling run:

- Run folder:
  `/work/vcomp/profile_runs/vcomp_profile_1000gb_260602_051634_cpuwait`
- Runner:
  `experiments/scripts/profile/profile_vcomp_load.sh`
- Profiles collected: on-CPU task-clock, scheduler latency, sched
  switch/wakeup, futex syscalls, and 2-second thread snapshots.
- Result: `fillvirtual` 63.089 s
  (`phase1=23.639`, `bg_wait=25.503`, `phase2=13.915`), final DB 662 GB,
  682,344,167 keys, 11,377 SSTs.
- Total `LogAndApply`: 45.737 s / 10,192 calls. Prepare cost was
  19.170 s: `compaction_pri=8.947`, `file_index=9.963`,
  `bottommost=0`.
- BG virtual compaction cumulative time: 511.829 s, with
  `log_apply=257.133`, `mutex_wait=66.680`, `merge=21.939`,
  `split=10.512`.
- Commit batching: 10,126 batches / 41,560 jobs, average batch 4.10,
  queue wait 62.748 s.

Interpretation: the low-CPU symptom is not Linux scheduler starvation.
`perf sched latency` shows low scheduling delay for `rocksdb:low` threads.
The current bottleneck is mixed, but the scalable problem is serialized
VersionSet metadata work and visible-window scans. PLR CPU is visible
(`NWayMergePLR`, sort, `PLRModel::Inverse`, `SplitIntoSSTs`), but it is much
smaller than cumulative `LogAndApply` and commit-queue wait.

Next target:

- Maintain visible L0 count/bytes incrementally instead of scanning L0 files.
- Reduce VersionSet prepare cost, especially `GenerateLevelFilesBrief`,
  `GenerateLevel0NonOverlapping`, `GenerateFileLocationIndex`, and
  `CheckConsistencyDetails`.
- In the next profiling run, trigger profiles by phase boundary instead of
  fixed sequential windows.

## 2026-06-02 - BG commit batching, obsolete deferral, and latest 5 TB checkpoint

After profiling showed the dominant cost was serialized VersionSet metadata
commit work, we tried two metadata-path patches:

- `SaveSSTFilesTo()` unchanged-level fast path: if a level has no added or
  deleted files, copy base files directly instead of merge/sort/checking every
  file through `MaybeAddFile()`.
- Deferred obsolete cleanup in vcomp mode: successful virtual compaction jobs
  skip per-job `FindObsoleteFiles()` / purge; `fillvirtual` calls
  `CleanupVirtualCompactionObsoleteFiles()` once after BG compaction drain.

The patches worked locally on their target subcosts but were not sufficient by
themselves. On 5 TB, obsolete cleanup fell from `50.689 s` to `2.471 s`, and
compaction-priority work also fell, but commit batching degraded:

| 5 TB run | Total | Phase 1 | BG wait | Phase 2 | BG commit avg batch | Total LogAndApply |
|----------|-------|---------|---------|---------|---------------------|-------------------|
| latest before patch | 838.009 s | 138.456 s | 630.403 s | 69.150 s | 3.31 | 760.175 s |
| save-fast + obsolete defer | 985.705 s | 137.790 s | 781.968 s | 65.947 s | 2.75 | 911.430 s |

Root cause: L0 registration did not increase. Final L0 queued/registered/
consumed stayed `80,000 / 80,000 / 80,000`. The regression came from smaller
BG commit batches, which increased `LogAndApply` calls from `77,006` to
`93,242`.

### BG commit batch delay

We added environment-controlled BG virtual compaction commit batching:

- `VCOMP_BG_COMMIT_BATCH_MAX` (default `16`)
- `VCOMP_BG_COMMIT_DELAY_US` (default `100`)

The leader briefly releases the DB mutex and sleeps before draining the commit
queue, allowing follower jobs that are already near commit-ready to join the
same `LogAndApply` batch.

Current defaults:

```bash
VCOMP_BG_COMMIT_BATCH_MAX=16
VCOMP_BG_COMMIT_DELAY_US=100
```

1 TB result:

| Run | Total | BG wait | BG commit avg batch | Total LogAndApply |
|-----|-------|---------|---------------------|-------------------|
| save-fast + obsolete defer | 54.330 s | 17.711 s | 2.54 | 37.330 s |
| cap16 delay100 | 44.205 s | 5.752 s | 4.63 | 17.125 s |

5 TB result:

| Run | Total | Phase 1 | BG wait | Phase 2 | BG commit avg batch | Total LogAndApply |
|-----|-------|---------|---------|---------|---------------------|-------------------|
| save-fast + obsolete defer | 985.705 s | 137.790 s | 781.968 s | 65.947 s | 2.75 | 911.430 s |
| cap16 delay100 | 589.665 s | 136.652 s | 386.706 s | 66.307 s | 4.75 | 382.475 s |
| cap16 delay200 | 621.807 s | 138.011 s | 415.911 s | 67.885 s | 4.92 | 391.451 s |

10 TB cap16/delay100 result:

| Run | Total | Phase 1 | BG wait | Phase 2 | BG commit avg batch | Total LogAndApply |
|-----|-------|---------|---------|---------|---------------------|-------------------|
| cap16 delay100 | 2461.699 s | 260.858 s | 2063.894 s | 136.946 s | 4.84 | 1707.888 s |

10 TB confirms the remaining scaling problem: Phase 1 and Phase 2 scale close
to 2x from 5 TB, while BG wait grows from `386.706 s` to `2063.894 s`.

Interpretation: 100 us is near the current sweet spot. Increasing the delay to
200 us slightly increased average batch size but queue wait grew more than the
batching benefit.

LOG-level batch distribution for 5 TB:

| Run | Avg | p50 | p90 | p99 | batch=1 | batch>=8 | batch=16 |
|-----|-----|-----|-----|-----|---------|----------|----------|
| delay0 bad patch | 3.26 | 3 | 5 | 10 | 5.09% | 4.09% | 0.01% |
| cap16 delay100 | 5.59 | 5 | 8 | 13 | 0.40% | 15.02% | 0.16% |
| cap16 delay200 | 5.75 | 5 | 8 | 13 | 0.34% | 16.43% | 0.25% |

The batch cap is only an upper bound. Batch size does not reach 16 often
because commit-ready virtual compaction jobs arrive in bursts of roughly 4--8
jobs; waiting longer mostly adds queue latency rather than enough additional
followers.

### Motivation experiment setup

The paper motivation experiment for baseline dataset-size scaling was added
under `experiments/`:

- `experiments/docs/MOTIVATION_LOAD_SCALING.md`
- `experiments/scripts/load/run_motivation_load_scaling.sh`

It runs baseline `fillrandom` sequentially for `500 GB, 1 TB, 2 TB, 4 TB,
8 TB`, records elapsed time and WAF-related summary metrics in `summary.tsv`,
and refuses to start if another `db_bench` is already running.

## 2026-06-03 - SaveSSTFilesTo bulk unchanged-level fast path

After the 10 TB cap16/delay100 run, the largest metadata subcost was still
VersionSet commit work:

- `SaveSSTFilesTo`: `732.848 s`
- manifest prepare: `630.226 s`
- compaction priority: `398.261 s`
- level brief/index rebuild: `197.831 s`
- append compaction score: `155.201 s`

We first tried a broader virtual-only skip of auxiliary compaction metadata
after score calculation. That reduced `append_compaction_score` on 1 TB but
did not reduce `SaveSSTFilesTo`, and it risks changing picker-visible state.
That part was rolled back.

Current patch:

- Keep RocksDB compaction score and picker state calculation intact.
- Add `VersionStorageInfo::AddFilesForUnchangedLevel()`.
- In `VersionBuilder::SaveSSTFilesTo()`, if a level has no added/deleted files,
  bulk-copy the base level file vector and rebuild file locations directly,
  instead of calling `AddFile()` for every unchanged file.
- Keep the existing changed-level priority invalidation path.

1 TB comparison against the previous cap16/delay100 checkpoint:

| Metric | Previous cap16 | New savesstfast | Change |
|--------|----------------|-----------------|--------|
| `SaveSSTFilesTo` | 6.684 s | 4.393 s | -34% |
| Total `LogAndApply` | 17.125 s | 14.325 s | -16% |
| BG wait | 5.752 s | 2.903 s | -50% |
| BG queue wait | 44.630 s | 37.118 s | -17% |
| Phase 2 materialize | 15.096 s | 21.739 s | slower, likely disk contention |

The 1 TB savesstfast run overlapped the clean RocksDB 8 TB baseline motivation
experiment, so materialization and end-to-end wall time should not be treated
as clean. The metadata subcosts are still useful: the patch directly reduces
the targeted `SaveSSTFilesTo` path.

## 2026-06-03 - Per-level score input cache

We added a narrower `ComputeCompactionScore` optimization after the
`SaveSSTFilesTo` fast path:

- `ComputeCompensatedSizes()` now also collects per-level score inputs:
  total file size, compensated size, and non-compacting size/count. L0 score
  and compaction-needed estimates use the registered L0 files directly.
- `ComputeCompactionScore()` and `EstimateCompactionBytesNeeded()` reuse those
  cached inputs on the Version append path instead of rescanning all SST files.
- Finalized/current versions still fall back to live scanning, preserving
  correctness when `being_compacted` state changes outside the append path.

Contended 1 TB run while the clean RocksDB 8 TB baseline was still running:

| Metric | New savesstfast | Score-cache patch | Change |
|--------|-----------------|-------------------|--------|
| `append_compaction_score` | 1.134 s | 0.646 s | -43% |
| `install_version` | 1.139 s | 0.651 s | -43% |
| Total `LogAndApply` | 14.325 s | 15.986 s | noisy |
| Core fillvirtual time | 48.317 s | 50.747 s | noisy |

The targeted append-score path improved, but the run had more BG jobs and
LogAndApply groups (`9262 -> 9804`), so end-to-end time is not a clean
comparison. Re-run after the baseline scaling experiment finishes.

Next candidates:

- Reuse or incrementally update `LevelFilesBrief` for unchanged levels. The
  current no-key-copy path still rebuilds brief arrays every Version.
- Reduce `UpdateFilesByCompactionPri()` cost. It still dominates
  `prepare_detail.compaction_pri` and repeatedly rebuilds per-level priority
  vectors.
- Reduce repeated `VersionBuilder::SaveTo()` full-level vector construction
  further. The current patch bulk-copies unchanged levels but still rebuilds
  file-location entries and refcounts for every file.
- Re-run 1 TB after the clean baseline experiment finishes to get clean
  end-to-end and Phase 2 numbers.
