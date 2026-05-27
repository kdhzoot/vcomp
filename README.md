# PLR-Based Virtual Compaction

This document has two parts:

- **Part 1 — Specification** (§1–§7): algorithms, code structure, and
  deferred TODOs of the current vcomp implementation.
- **Part 2 — Changelog** (§8+): time-ordered narrative of what was
  tried, decided, and discovered. Old entries are snapshots of the state
  at the time, not the current state.

Measurement results (current best numbers) live in
[../eval-vcomp/RESULTS.md](../eval-vcomp/RESULTS.md). Queued
experiments live in
[../eval-vcomp/EXPERIMENTS_PLANNED.md](../eval-vcomp/EXPERIMENTS_PLANNED.md).

---

## 1. Overview

Virtual Compaction replaces RocksDB's I/O-intensive compaction with
lightweight **Piecewise Linear Regression (PLR) model merging** during
bulk loading. No actual SST data is written until the final
materialization step.

```
Traditional:  Write keys → Memtable (SkipList)
              → Flush (write SST) → Compaction (read+merge+write SSTs)

Virtual:      Generate keys → vector buffer → Radix sort → PLR Fit
              → Register virtual L0
              → BG PLR Merge (concurrent)
              → Materialize + SST write (direct I/O)
```

The virtual path bypasses the RocksDB memtable entirely. Keys are
accumulated in a flat `std::vector<uint64_t>` and bulk-sorted with radix
sort before PLR fitting, trading the O(N log N) per-insert SkipList cost
for O(N) batch radix sort.

Goal of the project: produce a tree **structurally identical to baseline
RocksDB**, not a "better" tree. Any structural divergence is treated as
a bug to fix.

---

## 2. PLR Model

### 2.1 Core idea

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

### 2.2 Greedy-PLR fit (shrinking-cone algorithm)

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

### 2.3 PLR inverse

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

## 3. N-Way PLR Merge

### 3.1 Mathematical foundation

When merging N sorted sequences each with its own PLR model
$\text{pos}_i(k)$, the merged rank at key $k$ is:

$$
\text{pos}_{\text{merged}}(k) = \sum_{i=1}^{N} \text{pos}_i(k)
$$

Since each $\text{pos}_i$ is piecewise linear, the sum is also piecewise
linear. The breakpoints of the merged model are the **union** of all
input segment boundaries.

### 3.2 Algorithm

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

### 3.3 Why slope/intercept addition works

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

### 3.4 Probabilistic dedup correction

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

### 3.5 Adjacent-segment merging

The breakpoint-based merge produces one segment per sub-interval; many
adjacent segments end up with identical `(slope, intercept)` because
consecutive intervals share the same active set. They are merged with
tolerances `|s1 - s2| < 1e-12` and `|b1 - b2| < 1e-9`. Without this
step, the segment count after N-way merge equals the breakpoint count
(potentially thousands).

---

## 4. Virtual SSTs

### 4.1 Structure

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

### 4.2 SplitIntoSSTs — output sizing for virtual compaction

After a merged PLR model is computed, it is split into output virtual
SSTs. The split logic mirrors RocksDB's
`CompactionOutputs::ShouldStopBefore` so that the resulting tree shape
matches a real compaction:

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

### 4.3 Materialization

Converting a VirtualSST back to actual keys uses sequential PLR inverse
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

Each VirtualSST is materialized independently — no cross-file merge is
needed because BG compaction has already ensured L1+ files have
non-overlapping key ranges. Materialization and SST writing are fused
into a single parallel step using direct I/O.

---

## 5. Architecture: integration with RocksDB

### 5.1 Components

```
┌──────────────────────────────────────────────────────────────┐
│  db_bench (FillVirtual)                                      │
│    Main thread: keygen → radix sort → PLRFit                 │
│    Batch register virtual L0 files (l0_trigger per batch)    │
└────────────────────────┬─────────────────────────────────────┘
                         │ VersionEdit::AddFile(L0) batched
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

### 5.2 Flow

1. **Phase 1 — Flush (main thread)**: radix sort each batch of keys →
   `GreedyPLRFit` → create `VirtualSST` → register in
   `VirtualSSTRegistry` → accumulate `VersionEdit::AddFile` →
   batch `LogAndApply` every `l0_compaction_trigger` files →
   `InstallSuperVersionAndScheduleWork` triggers BG compaction.

2. **BG compactions (concurrent with Phase 1)**: `CompactionPicker`
   picks input files; if at least one input is still in the virtual
   registry, the dispatch routes to `RunVirtualCompaction`:
   - Look up input PLR models from registry.
   - `NWayMergePLR` with mutex released during merge (CPU-intensive).
   - `SplitIntoSSTs` for outputs with grandparent boundaries from
     `c->grandparents()`, escalating threshold (50% → 90%).
   - Register outputs in registry, `LogAndApply` removes inputs and
     adds outputs.

3. **Phase 2 — Materialization**: after `WaitForCompact` returns, walk
   the registry and produce real SST files in parallel. Each worker:
   - `MaterializeKeys` (PLR inverse walk).
   - `GenerateKeyFromInt` (uint64 → string).
   - `SstFileWriter::Put` per key (block builder + bloom filter).
   - `SstFileWriter::Finish` (flush + fsync).

   A single `VersionEdit` then deletes all virtual files and adds all
   real files atomically. `compact0` and `waitforcompaction` after
   Phase 2 push any leftover real L0 file into L1, possibly triggering
   an L1→L2 cascade — same flow as baseline's `fillrandom` ending.

### 5.3 BG compaction dispatch

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
nullptr`. **This gate is essential**: after Phase 2 has materialized
all virtual files into real SSTs, a subsequent `compact0` /
`waitforcompaction` will pick a compaction whose inputs are now entirely
real. Without the gate, dispatch would still call
`RunVirtualCompaction`, find no virtual inputs, return `OK` without
making progress, and the picker would immediately re-pick the same
compaction — an infinite spin that hangs `WaitForCompact`.

### 5.4 Key modifications to RocksDB

| File | Change |
|------|--------|
| `include/rocksdb/options.h` | `use_virtual_compaction`, `plr_error_bound` options |
| `options/db_options.{h,cc}` | `ImmutableDBOptions` mapping |
| `db/db_impl/db_impl.h` | `VirtualSSTRegistry` member, `RegisterVirtualL0File()`, `RunVirtualCompaction()` |
| `db/db_impl/db_impl.cc` | Initialize registry when `use_virtual_compaction` is enabled |
| `db/db_impl/db_impl_compaction_flush.cc` | BG dispatch gating (real vs virtual path), `RegisterVirtualL0File()` impl, `RunVirtualCompaction()` impl with grandparent boundary extraction |
| `db/db_impl/db_impl_files.cc` | Skip disk deletion for virtual files |
| `db/version_set.cc` | Skip `LoadTableHandlers` / `VerifyFileMetadata` for virtual files |
| `db/virtual_compaction/virtual_sst.{h,cc}` | `SplitIntoSSTs` (dynamic threshold + GP), `MaterializeKeys`, `VirtualCompact` |
| `db/virtual_compaction/virtual_sst_registry.h` | Thread-safe `file_number → VirtualSST` registry |
| `db/virtual_compaction/plr_model.{h,cc}` | `GreedyPLRFit`, `NWayMergePLR` with optional dedup |
| `tools/db_bench_tool.cc` | `fillvirtual` benchmark with batched `LogAndApply`, Phase 2 workers using direct I/O, `coverage` benchmark for per-level file-coverage probes; Phase 2b uses `kMaxSequenceNumber` for output `smallest` InternalKey to match `RegisterVirtualL0File`'s convention |

### 5.5 Key optimizations in FillVirtual

| Optimization | Before | After | Effect |
|--------------|--------|-------|--------|
| Radix sort (uint64_t keys) | `std::sort` O(N log N) | 8-bit radix O(N) | sort step ~6× faster |
| Batch LogAndApply | 1 call per flush | 1 call per `l0_trigger` flushes | ~3× fewer MANIFEST writes |
| Fused materialize+write | inverse → merge/dedup → SST write | inverse → SST write | eliminates cross-file merge step |
| Direct I/O | buffered | `use_direct_writes`, `!use_mmap_writes` | sys CPU 66% → 9% |
| Phase 2 workers | `max_background_jobs` (32) | `hardware_concurrency()` (48) | Phase 2 ~1.3× faster |

---

## 6. Tree-shape considerations

The split logic in §4.2 is the main mechanism for matching baseline's
file count and per-level file-size distribution. Two structural
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
  some file gets removed by a later compaction; see §7.)

---

## 7. Twitter trace replay

vcomp's loading path was originally designed for fillrandom's synthetic
uniform-random keys. To validate baseline ↔ vcomp parity on real-world
workloads, we integrated Twitter cache traces (`cache-trace` repo,
OSDI '20) as a second key source.

### 7.1 Purpose and scope

Fillrandom gives uniform keys and fixed value sizes; Twitter traces
provide real-world key access patterns, variable value sizes, and
skewed distributions that exercise LSM compaction in ways fillrandom
cannot.

In scope:
- **Write-only replay** for loading experiments (Put path).
- Preserve trace order and original key/value sizes.
- Independent of any specific cluster — trace file is a CLI argument.
- Both baseline (real compaction via `twitterload`) and vcomp
  (`fillvirtual --twitter_trace_file=...`) paths.

Out of scope (load phase):
- Timing-accurate replay (no fast-forward, no inter-op sleeps).
- Read/Delete replay during loading (the format reserves op bytes for
  a future run-phase benchmark).
- Multi-threaded replay.

### 7.2 Component layout

| Component | Path | Language | Role |
|---|---|---|---|
| Converter | `vcomp/tools/twitter_trace_convert.py` | Python 3 | Twitter CSV → compact binary |
| Prefix analyzer | `vcomp/tools/analyze_trace_prefix.py` | Python 3 | Validate Option D applicability (§7.8) |
| Baseline benchmark | `vcomp/tools/db_bench_tool.cc::WriteFromTwitterTrace` | C++ | Replay binary trace through DB::Write |
| vcomp benchmark | `vcomp/tools/db_bench_tool.cc::FillVirtual` (trace branch) | C++ | Replay binary trace through virtual L0 register |

### 7.3 Binary trace format

All integers little-endian. No padding, no alignment.

**File header (fixed 40 bytes)**

| Offset | Size | Field | Value |
|---|---|---|---|
| 0 | 8 | magic | ASCII `"VCMPTRC1"` |
| 8 | 4 | version | `u32` = 1 |
| 12 | 4 | reserved | `u32` = 0 |
| 16 | 8 | total_puts | `u64` (count of Put records in file) |
| 24 | 8 | total_kv_bytes | `u64` (sum of key_len + value_size over Puts) |
| 32 | 4 | key_len_fixed | `u32` (Put key length if all match; 0 = variable) |
| 36 | 4 | padding | `u32` = 0 |

`total_puts` / `total_kv_bytes` / `key_len_fixed` are computed by the
converter in a single streaming pass and patched into the header before
file close. They let the load-time reader skip the otherwise expensive
pre-scan needed to set the registry's `avg_entry_size` and `key_size`.

**Record (variable length)**

| Size | Field |
|---|---|
| 1 byte | `op` — see table below |
| 4 bytes | `key_len` (u32) |
| `key_len` | `key` bytes (raw, as-anonymized in trace) |
| 4 bytes | `value_size` (u32) |

No trailing length, no per-record checksum. Reader detects EOF.

**Op byte values**

| Value | Name | Used in load phase? |
|---|---|---|
| 1 | Put | yes |
| 2 | Get | reserved for run phase |
| 3 | Delete | reserved |

Reader rejects any other value.

Design properties: compact (~10 bytes overhead + key length per
record), streaming (single pass, no backpatching), extensible (op
byte lets us add Get/Delete later without format break), simple
parser (both Python and C++ in ~20 lines).

### 7.4 CSV → binary mapping

Input CSV schema (from `cache-trace/README.md`):
```
timestamp, anonymized_key, key_size, value_size, client_id, operation, TTL
```

| Twitter op | Action | Emitted op |
|---|---|---|
| `set`, `add`, `replace`, `cas` | write key with value_size | Put (1) |
| `get`, `gets` | skip by default; emit op=2 with `--include-reads` | Get (2) for run phase |
| `delete` | skip by default; emit op=3 with `--include-deletes` | Delete (3) |
| `append`, `prepend`, `incr`, `decr` | skip | — |

Key bytes = the anonymized key string from column 2, UTF-8 encoded,
truncated/padded to match column 3 (`key_size`) if they differ.
`value_size` is copied from column 4 verbatim, unless
`--value-size-scale` or `--value-size-clip` is set. Timestamp and
TTL are currently ignored.

### 7.5 Converter CLI

```
vcomp/tools/twitter_trace_convert.py INPUT_CSV OUTPUT_BIN [flags]
```

| Flag | Default | Purpose |
|---|---|---|
| `--max-ops N` | 0 (unlimited) | Stop after N emitted records |
| `--sample N` | 0 (off) | Keep 1 of every N qualifying ops |
| `--include-reads` | off | Also emit Get records for `get`/`gets` |
| `--include-deletes` | off | Also emit Delete for `delete` |
| `--value-size-scale F` | 1.0 | Multiply every value_size by F |
| `--value-size-clip N` | 0 (off) | Cap value_size at N bytes |
| `--min-key-len N` | 0 | Drop records whose key is shorter than N |
| `--progress` | off | Print progress every 1M input lines |

Use `-` as INPUT_CSV for stdin streaming:
```
zstd -dc cluster12.sort.zst | twitter_trace_convert.py - out.vcomptrace
```

Writes a one-line summary to stderr at the end: emitted record count,
op breakdown, key/value size stats (min/median/max).

### 7.6 db_bench integration — baseline path (`twitterload`)

Benchmark name: **`twitterload`**.

**Design principle: mirror fillrandom exactly.**
`Benchmark::WriteFromTwitterTrace` is a stripped-down copy of
`Benchmark::DoWrite` (which backs fillrandom). All branches that
fillrandom does not use are removed.

Preserved verbatim from `DoWrite`:
- `RandomGenerator gen` — same value pool, same dummy value
  generation logic as fillrandom
- `WriteBatch` construction
- `write_options_`, `DB::Write` call path
- `bytes` accounting, `thread->stats.FinishedOps`, progress reporting

Replaced (the only two hooks):

| `DoWrite` step | `WriteFromTwitterTrace` replacement |
|---|---|
| `key_gens[0]->Next()` + `GenerateKeyFromInt(...)` | Read next record from trace, set `Slice key` to raw key bytes |
| `gen.Generate(value_size_)` | `gen.Generate(record.value_size)` |

Consequence: any LSM tree-shape difference between `fillrandom` and
`twitterload` is attributable only to input key/value distribution,
never to benchmark machinery divergence.

**Flags**:

| Flag | Default | Purpose |
|---|---|---|
| `--twitter_trace_file=<path>` | (required) | Path to binary trace file |
| `--twitter_trace_max_ops=N` | 0 (unlimited) | Stop after N records |

**Out of scope** at this benchmark: multi-threaded replay
(`-threads=1` only), Get/Delete replay (rejected at op-byte check),
BlobDB (asserted off).

### 7.7 db_bench integration — vcomp path (`fillvirtual --twitter_trace_file=...`)

For vcomp, we **extend the existing `fillvirtual` benchmark** (which
generates uint64 keys via Random64) to optionally read keys from a
trace file. The Phase-1 algorithm is otherwise identical:

```
for each trace record (Put only):
    key_uint64 = BE_uint64(key_bytes[:8])         # PLR domain
    memtable_buf_uint64.push(key_uint64)
    raw_keys.push(key_bytes)                      # parallel array
    accumulated_bytes += key_len + value_size

    when accumulated_bytes >= memtable_flush_size MB:
        radix sort memtable_buf_uint64 (raw_keys ride along on permutation)
        std::unique on uint64 → drop matching raw_keys
        plr = GreedyPLRFit(memtable_buf_uint64, plr_error_bound)
        vsst.plr_model     = plr
        vsst.key_min       = memtable_buf_uint64.front()        # uint64
        vsst.key_max       = memtable_buf_uint64.back()         # uint64
        vsst.key_min_bytes = raw_keys.front()                   # 44 B raw
        vsst.key_max_bytes = raw_keys.back()                    # 44 B raw
        RegisterVirtualL0File(vsst)
```

The branching point: if `--twitter_trace_file` is non-empty,
`FillVirtual` opens the trace and replaces the synthetic key loop with
a trace read loop. Phase 1 and virtual compaction still operate on
metadata, but the trace path also appends raw key/value records to
per-flush source-run logs. Final materialization partitions those logs
by final `VirtualSST` lineage and range, then writes real SSTs with the
original raw keys.

The reason for keeping both `key_min/max` (uint64) and
`key_min_bytes/max_bytes` (raw): PLR predict/inverse stays in uint64
domain (no perf regression — §7.8), but RocksDB's
`VersionEdit::AddFile` requires full byte-string smallest/largest_key
for SST overlap checks at L1+. The two representations are encoded
from the same trace key.

`load_twitter.sh` dispatches MODE=baseline to `twitterload` and
MODE=vcomp to `fillvirtual --twitter_trace_file=...` — mirroring
how `load.sh` dispatches to `fillrandom` vs `fillvirtual` for the
synthetic-key path.

### 7.8 Design decision: PLR key encoding (Option D)

vcomp's PLR engine (§2) operates on `uint64`. fillrandom feeds it via
`GenerateKeyFromInt`: 8 B big-endian uint64 + (`key_size`-8) B '0'
padding. PLR predict order ↔ byte-comparator order match exactly
because the encoding is monotonic in the uint64 domain.

For Twitter trace keys (raw byte strings, 44 B in cluster012), we
chose **Option D**: take the first 8 B of the trace key, interpret as
big-endian uint64, feed to PLR. The full raw key bytes are stored
separately in `VirtualSST` for `smallest_key`/`largest_key` bounds
passed to RocksDB.

#### Why this works

Option D preserves byte-comparator order iff distinct keys differ in
their first 8 bytes. RocksDB's default `BytewiseComparator` compares
byte by byte; if all keys in a memtable batch have distinct prefix8,
then `sort(prefix8 BE uint64)` produces the same order as
`sort(full_bytes)`. PLR resolution is then identical to fillrandom's.

#### Validation procedure

Run before adding a new cluster:

```
python3 vcomp/tools/analyze_trace_prefix.py <trace.vcomptrace> <sample_n>
```

Reports unique full keys, unique prefix8, and number of prefix8
buckets containing multiple distinct full keys.

**Threshold**: prefix8-collision rate ≤ 1% relative to unique full
keys. Above this, Option D loses PLR resolution → tree-shape parity
breaks.

cluster012 (1M sample, 2026-05-16):
- Unique full keys: 985,854 / 1,000,000 (98.59%)
- Unique prefix8:   985,854 / 1,000,000 (98.59%)
- prefix8 buckets with >1 distinct full key: **0**

Keys are 44 B base64-encoded hashes — first 8 B is effectively uniform
random over the 64-symbol alphabet, so collisions are extremely rare
in practice.

#### Alternatives considered and rejected

- **Option A** (first 8 B BE uint64, no raw-bytes storage in
  VirtualSST): same PLR fidelity as D but breaks SST overlap checks at
  L1+ because RocksDB needs full byte-key bounds.
- **Option B** (byte-string PLR throughout `plr_model.h` /
  `virtual_sst.h`): true generalization but ~3–5× slowdown in sort,
  dedup, predict; ~9× memory traffic (memtable_buf no longer fits in
  L2 cache); and regresses fillrandom because that path would also
  have to convert uint64 → byte string before PLR fit. Quantitative
  estimate at 250 GB load: vcomp could grow from ~500 s to ~1000 s,
  eliminating vcomp's loading advantage.
- **Option C** (hash → uint64): destroys sort order, violates RocksDB
  SST overlap invariants. Rejected outright.

#### When Option D will fail

Patterns expected to break the prefix8 threshold:
- Keys with short common prefixes (e.g., `user_12345`, `session_xyz`)
- Monotonically increasing IDs (low entropy in first 8 B)
- Schema-prefixed keys (`schema:tenant:row:...`)

If validation fails (>1% prefix8 collision):
1. **D′** — pick a different 8 B window (skip the common prefix).
   Requires a configurable offset and verification on the new window.
2. **B** — byte-string PLR; heavy refactor with fillrandom regression.
3. Reject the cluster.

### 7.9 Cluster selection reference

From `cache-trace/stat/2020Mar.md` (Twitter's published per-cluster
characterization):

| Cluster | Operation mix | Key size | Zipf α | One-hit ratio | Verdict |
|---|---|---|---|---|---|
| **cluster12** | set:0.80 get:0.20 | 44 B | 0.30 | 81.4% | **primary** — write-heavy, near-uniform; Option D verified |
| cluster17 | get:0.99 | 19 B | 2.11 | 38.9% | skip for write experiments |
| cluster19 | get:0.75 set:0.25 | 42 B | 0.74 | 7.5% | future hot-key stress test |
| cluster31, 32 | set:0.94 get:0.06 | 41 B | 0.00 | 94.1% | future write-heavy candidates |
| cluster37 | set:0.37 get:0.63 | 72 B | 0.43 | 14.9% | future read-write candidate |

Official write-heavy candidates: cluster12, 15, 31, 37, 38, 39.

Full traces (`clusterN.0.zst`, `clusterN.sort.zst`) downloadable from
CMU PDL:
`https://ftp.pdl.cmu.edu/pub/datasets/twemcacheWorkload/open_source/`.

To switch clusters: convert with `twitter_trace_convert.py`, **then run
prefix8 validation per §7.8**, then point `--twitter_trace_file` at
the new output.

### 7.10 Implementation checklist

**Phase 0 — Baseline path (write-only load) — DONE**:
- [x] Converter `twitter_trace_convert.py` + binary format `VCMPTRC1` v1
- [x] `twitterload` benchmark (DB::Write path)
- [x] `eval-vcomp/load_twitter.sh` wrapper
- [x] cluster012 baseline DBs: 10M (11 GB), 100M (104 GB), 500M (515 GB)
      under `twitter_dbs/`

**Phase 1 — vcomp path**:
- [x] `analyze_trace_prefix.py` in `vcomp/tools/`
- [x] cluster012 prefix8 uniqueness validated (2026-05-16, §7.8)
- [x] Extend `FillVirtual` with `--twitter_trace_file` branch (§7.7)
- [x] Extend `VirtualSST` to carry raw `key_min_bytes`/`key_max_bytes`
- [x] Extend `load_twitter.sh` to dispatch MODE=vcomp to `fillvirtual`
- [x] Baseline ↔ vcomp tree-shape sanity diff on cluster012
      (10M/100M/500M single runs; see
      [../eval-vcomp/RESULTS.md](../eval-vcomp/RESULTS.md))

**Phase 2 — exact KV materialization + run phase**:
- [x] Preserve raw key/value records in append-only per-flush source-run logs
- [x] Propagate `source_run_ids` through virtual compaction
- [x] Parallel materialization: partition source logs by final VSST lineage,
      sort/dedup per partition, write raw-key SSTs
- [x] Re-convert cluster012 with `--include-reads`
- [x] `twitterrun` raw-key Get replay on loaded DBs
- [x] Baseline ↔ vcomp found-key parity on 1M and 10M cluster012 tests
      (see [../eval-vcomp/RESULTS.md](../eval-vcomp/RESULTS.md))

---

## 8. Deferred TODOs

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

These TODOs are tracked in
[db/virtual_compaction/TODO.md](db/virtual_compaction/TODO.md).

---
---

# Part 2 — Changelog

Time-ordered narrative of the vcomp project. Old entries are preserved
as-is — they are snapshots of the state at the time, not current truth.
For current measurements, see
[../eval-vcomp/RESULTS.md](../eval-vcomp/RESULTS.md).
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
batch numbers are in [RESULTS.md §1.1](RESULTS.md#11-250-gb--30-run-batch).

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

### v5 — Probabilistic dedup correction (current)

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

**Current limit**: SST build cost (memcpy + checksum + bloom) is the bottleneck.
HT (96 logical cores) would provide ~1.3x improvement.
Kernel boot param `nosmt` prevents runtime HT enable; requires reboot.

---

## v5-era snapshot (2026-04-08, single load each)

The first-pass v0~v5 numbers above were measured on single loads. At
the v5 point the structural snapshot looked like this. Treat as
historical — current values are in [RESULTS.md](RESULTS.md).

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
comparison lives in [RESULTS.md §2](RESULTS.md#2-tree-shape--3030-coverage-comparison).

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
[RESULTS.md §3.1](RESULTS.md#31-250-gb--3030-readrandom-1m-reads-1-thread-cache0)
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
[EXPERIMENTS_PLANNED.md](EXPERIMENTS_PLANNED.md).

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
- [eval-vcomp/load.sh](load.sh), [eval-vcomp/run.sh](run.sh),
  [eval-vcomp/load_batch.sh](load_batch.sh)
- [eval-vcomp/parse_runs.py](parse_runs.py) → `log_runs/runs_summary.csv`
- [eval-vcomp/log_batch/260410_0339_250gb_x30/](log_batch/260410_0339_250gb_x30/) — 30x baseline 250GB batch

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
[RESULTS.md §2](RESULTS.md#2-tree-shape--3030-coverage-comparison)):

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
- [vcomp/db/virtual_compaction/TODO.md](../vcomp/db/virtual_compaction/TODO.md) — deferred GP cut conditions (skippable, max-compaction-bytes)

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
- New [run_batch.sh](run_batch.sh) walks
  `${BATCH_DB_DIR}/{mode}_run{1..N}/` and invokes `run.sh` with that
  DB and `RESULT_DIR=log_batch/${BATCH}/${WORKLOAD}_*/${mode}_run${i}`,
  appending an aggregate `results.csv` per batch.

### Results — clean structural metrics
Full table in
[RESULTS.md §3.1](RESULTS.md#31-250-gb--3030-readrandom-1m-reads-1-thread-cache0).
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
   Recorded in [EXPERIMENTS_PLANNED.md](EXPERIMENTS_PLANNED.md): N=20
   baseline-only loads, readrandom on `fill_run_1` after each new
   load, ~7.3 hours.
2. Reload baseline 30× from scratch *now*, alongside the 4/14 vcomp
   batch, so a future readrandom comparison can use a clean 30-vs-30
   pair without cross-day disk skew. Currently in progress as
   `260415_0635_250gb_x30/`.
3. Documentation reorganisation:
   - `vcomp/README.md` is now a pure spec of the
     current code, no time-ordered narrative or measurement tables.
   - `eval-vcomp/RESULTS.md` (new) collects only the current best
     measurements with caveats.
   - This file (`eval-vcomp/CHANGELOG.md`, formerly
     `BENCHMARK_HISTORY.md`) keeps the time-ordered narrative; old
     measurement tables are summarised inline as historical snapshots,
     full numbers live in RESULTS.md.
   - `eval-vcomp/EXPERIMENTS_PLANNED.md` (new) holds queued experiments.

### Open at the end of this round
- L2 erosion gap (vcomp 86% ± 3pp vs baseline 70% ± 9pp).
- bloom_fpr +22% gap (PLR-materialised keys vs original).
- Disk fragmentation effect on readrandom — quantitative answer
  pending the queued experiment.
- Fresh baseline 30×30 readrandom pending finish of the 4/15 reload
  batch.

## 2026-04-15 — Twitter trace porting (Phase 0 done)

Parallel track to the readrandom work above. The full design now
lives in §7; this entry is the chronological narrative of how Phase 0
came together.

### Phase 0 spec drafted
Initial spec written. Binary format defined (`VCMPTRC1` v1, op byte +
key_len + key + value_size). Converter CLI and db_bench flags
specified. cluster012 chosen as primary target based on sample
analysis: 98.6% unique write keys, fixed 44B keys, high-variance
value sizes. Decisions: op byte included in format despite Phase 0
being Put-only, for forward compatibility; values generated C++-side
via existing `RandomGenerator` (fillrandom-consistent), not pre-baked
into the trace file; no timing replay, no multi-thread replay in
Phase 0.

### Converter implemented and verified
`vcomp/tools/twitter_trace_convert.py` created per §7.3–§7.5.
Dry-run on `cache-trace/samples/2020Mar/cluster012` (1M input lines):
emitted 801,203 Put records, key_size fixed 44B, value_size min=6 /
median=6 / mean=1051.2 / max=312480. Binary output validated:
`VCMPTRC1` header + 801,203 records, file size 42,463,775 bytes =
`16 + 801203*53` exactly as predicted.

### Benchmark renamed to `twitterload`, design locked to fillrandom parity
Originally drafted as `twittertrace`; renamed to `twitterload` (aligns
with "loading-specialized DB" framing). §7.6 specifies the design
principle: `WriteFromTwitterTrace` is a copy of `DoWrite` with all
non-fillrandom branches stripped, and exactly two hooks replaced (key
source, value_size source). `RandomGenerator` preserved verbatim.
Rationale: guarantees that any baseline-vs-vcomp tree shape
difference under twitterload is attributable only to input
distribution, not benchmark machinery.

### `twitterload` benchmark implemented and smoke-tested
Added `DEFINE_string(twitter_trace_file, ...)` and
`DEFINE_int64(twitter_trace_max_ops, ...)`. Registered
`"twitterload"` in the dispatch switch with `fresh_db = true`.
Implemented `Benchmark::WriteFromTwitterTrace`: header verification,
single-DB / single-CF / no-blob-db guards, reusable key buffer,
batch-by-`entries_per_batch_` loop, `gen.Generate(value_size)` from
trace, standard `DB::Write` + `FinishedOps` path. Rejects non-Put ops
and out-of-bound value sizes with actionable errors. Build: `make
static_lib db_bench -j` (gcc-11). Smoke test on 10K records: 229.5
MB/s, 0 malformed.

### load_twitter.sh + stdin support + RG bound fix + sample E2E
Added `eval-vcomp/load_twitter.sh` mirroring every db_bench flag of
`load.sh`. Differences: benchmarks chain uses `twitterload`,
`--num`=`MAX_OPS`, `--value_size`=`RG_VALUE_SIZE` (RandomGenerator
data_ buffer bound, not per-record), `--key_size` cosmetic.
Converter: accept `-` as stdin (`zstd -dc file.zst | convert.py -
out.vcomptrace`). **Bugfix** in `WriteFromTwitterTrace` value-size
bound check: the original check compared against
`FLAGS_value_size_max`, but `RandomGenerator` sizes its internal
`data_` to `max(1 MiB, effective_max_size)` (where
`effective_max_size` follows the kFixed/kUniform/kNormal rule). The
original check was too permissive and would buffer-overrun in release
builds (the `assert` inside `Generate` is compiled out under NDEBUG).
Fix: compute the actual bound correctly, error out early with a
message telling the user to pass `--value_size=N`. End-to-end on
801K-record sample: 558.9 MB/s, 11s wall, DB 845 MB, tree L0=0 /
L1=4@183 MB / L2=8@662 MB / L3+ empty.

### Full cluster12 download + Stage 1 (10M) + Stage 2 (100M) baselines
Downloaded `cache-trace/open_source/cluster12.sort.zst` from CMU PDL
(79 GiB compressed, ~90 min @ 14 MB/s). Confirmed: the `.sort` suffix
means timestamp-sorted (not key-sorted) — first record matches the
sample file exactly. Keys are 44 B base64-like anonymized strings.

Stage 1 (10M records): 506 MB binary trace, 34s conversion. Run
`baseline_tl_cluster012_10M_260415_1516_stage1`: 445K ops/s / 454
MB/s / 22.5s, full chain 40s. DB 11 GB, tree L0=0 / L1=4@235 MB /
L2=24@2546 MB / L3=130@7506 MB.

Stage 2 (100M records): 5m53s conversion, 5.0 GB binary trace. Run
`baseline_tl_cluster012_100M_260415_1523_stage2`: 216K ops/s / 228
MB/s (compaction backpressure halved throughput vs stage 1), full
chain 490s. DB 104 GB, tree extends to L4.

Input ratios: Twitter CSV ≈ 12.6 input lines per emitted Put (≈79.7%
write fraction). Binary trace ≈ 53 B per 44 B-key record.

### Stage 3 scoped to 500M records (disk constraint)
Initial plan had Stage 3 = full cluster12 trace. After Stage 2 showed
1.04 GB-per-1M-records DB growth, projected full-trace DB was ~3 TB
against 1.3 TB free on `/`. Converting the full trace to binary would
also produce ~200 GB. Killed the in-progress full conversion (which
had disowned itself twice — bash `&` vs harness `run_in_background`
race — leaving two concurrent writers on the same output, also
fixed). Scoped Stage 3 = **500M records**; expected ~26.5 GB binary
trace, ~520 GB DB.

### Stage 3 (500M baseline) completed
Conversion ~22 min (5.0 GB → 25 GB binary trace). 500M Put records
from 621.8M CSV lines (80.4% write fraction). Run
`baseline_tl_cluster012_500M_260415_1602_stage3`: 133,533 ops/s /
139.9 MB/s / 3744s, full chain 3776s (63 min). DB 515 GB.

Tree shape: L0=0 / L1=3@189 MB / L2=47@2502 MB / L3=447@25600 MB /
L4=4397@255951 MB / L5=4066@242470 MB / L6=0. First stage to reach
L5. Total 8960 files / 515 GB live.

Throughput trend: 454 → 228 → 140 MB/s. Drop dominated by compaction
backpressure as tree deepens; Stage 2 final W-Amp 5.6.

Value-size cap: max observed 316,612 B, under 1 MiB RandomGenerator
bound → `RG_VALUE_SIZE=2097152` default safe. Disk after Stage 3:
2.1 TB of 3.5 TB used.

### Open at the end of this round
- Tree-shape comparison `twitterload` vs `fillrandom` at equivalent
  size.
- Baseline vs vcomp tree-shape diff on cluster012 (project goal).
- vcomp path for trace input (the only Phase 0 path is the
  baseline/twitterload one going through `DB::Write`).

## 2026-05-11 — Tree-shape root cause: intra-L0 absence in vcomp

### Goal
Pin down *why* vcomp produces a structurally different tree from
baseline (L1/L2/L3 coverage all shifted upward, end-state L1 files 6×
wider). Carried over from the §6 open question in
[RESULTS.md](../eval-vcomp/RESULTS.md).

### Investigation chain (in the order it actually happened)

1. **Coverage as the load-bearing structural metric.** Re-extracted
   per-level coverage for fresh baseline (260415) and vcomp (260414)
   batches via `coverage` bench. Stable picture: vcomp ↑ at every
   non-bottom level. Side-by-side box plot:
   [coverage_dumps/coverage_box.png](../eval-vcomp/coverage_dumps/coverage_box.png).

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

Both unrelated to the deferred items in
[db/virtual_compaction/TODO.md](db/virtual_compaction/TODO.md) (those
still apply only at L1+).

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
Plan recorded in [EXPERIMENTS_PLANNED.md](../eval-vcomp/EXPERIMENTS_PLANNED.md).

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
   blocks indefinitely. Fix in `eval-vcomp/load.sh`: set both
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
- **`eval-vcomp/load.sh`** — set
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
[eval-vcomp/coverage_dumps/coverage_box_drain.png](../eval-vcomp/coverage_dumps/coverage_box_drain.png).

### What is *still* slightly off
- L1/L2 coverage medians sit lower than baseline's (19.6 vs 34.6 for
  L1; 61.4 vs 65.8 for L2). Both inside baseline's range but lower
  half. Not chased further — diminishing returns vs. the file-count
  + L3/L4 match we already have.
- L4 file count: vcomp 2622 vs baseline 2809 (−6.7 %). vcomp's L4
  files are individually larger (less aggressive grandparent split
  with the simpler split algorithm). The two deferred grandparent
  cut conditions in [db/virtual_compaction/TODO.md](db/virtual_compaction/TODO.md)
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
`eval-vcomp/coverage_dumps/vcomp_260512_l0t60/`,
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
[../eval-vcomp/RESULTS.md §3.1](../eval-vcomp/RESULTS.md) should be
read as "vcomp_new vs aged-baseline"; §3.3 there gives the
decomposition.

Predicts vcomp_new will degrade to ~141 µs/block if remeasured in a
month (matching aged-baseline today). Untested; would confirm
"fresh-write" effect is purely a function of time-on-disk, not a
vcomp property.

## 2026-05-16 — Twitter Phase 1 design + Option D selection

Reopened the Twitter trace work after closing the fillrandom
tree-shape parity gap (2026-05-12). Realised the existing Phase 0
hybrid path is not equivalent to vcomp's `fillvirtual` fast path: it
goes through `DB::Write` → memtable → flush hook, paying the memtable
insertion cost even in vcomp mode. Designed the proper vcomp path
(§7.7): extend `fillvirtual` with a `--twitter_trace_file` branch
that bypasses memtable just like the synthetic-key path does.

### PLR key encoding: chose Option D

vcomp's PLR engine is uint64-domain; trace keys are byte strings.
Three candidate encodings were compared (§7.8):

- **B** (byte-string PLR refactor): ~3–5× sort/predict slowdown, ~9×
  memory traffic (memtable_buf no longer fits in L2), and regresses
  fillrandom. Quantitative estimate at 250 GB: vcomp ~500 s → ~1000 s,
  eliminating vcomp's loading advantage.
- **C** (hash → uint64): destroys sort order, violates RocksDB SST
  invariants.
- **D** (first 8 B BE uint64 + raw bytes parallel): preserves
  byte-comparator order iff distinct keys differ in their first 8
  bytes. PLR resolution same as fillrandom's when that holds.

Chose D. Verified on cluster012 (1M sample from
`cluster012_10M.vcomptrace`): 985,854 unique full keys, 985,854
unique prefix8, **0 buckets with prefix8 collision**. Keys are 44 B
base64-encoded hashes — first 8 B is effectively uniform random,
so collisions are vanishingly rare.

Risk: future clusters with common prefixes (`user_`, `session_`) or
monotonic IDs will fail the prefix8 threshold. **Validation
procedure is now mandatory before adding any new cluster** — see
§7.8 and `vcomp/tools/analyze_trace_prefix.py`.

### Doc consolidation
Merged the standalone `vcomp/TWITTER_TRACE_REPLAY.md` (Phase 0 spec)
into `vcomp/README.md` §7. The standalone file is removed. Going
forward, all vcomp design (PLR + Twitter integration) lives in one
document.

### Completed next (Phase 1 implementation)
Implemented the real vcomp trace path after the 2026-05-16 design:
`FillVirtual` now treats `--twitter_trace_file` as an alternate key
source, reads the 40 B `VCMPTRC1` header for `total_puts`,
`total_kv_bytes`, and fixed key length, then feeds prefix8 BE uint64
keys through the existing PLR pipeline. `VirtualSST` carries
`key_min_bytes` / `key_max_bytes` for trace L0 `AddFile` bounds while
keeping uint64 PLR internals unchanged.

`eval-vcomp/load_twitter.sh` now dispatches `MODE=baseline` to
`twitterload` and `MODE=vcomp` to
`fillvirtual --twitter_trace_file=... --use_virtual_compaction=true`.

Single-run cluster012 sanity measurements were collected for 10M, 100M,
and 500M Put records. Load speedups were 5.0x, 14.8x, and 24.2x
respectively; final tree sizes matched within 0.4%, and 500M file count
matched within 4.0%. The detailed table is in
[../eval-vcomp/RESULTS.md](../eval-vcomp/RESULTS.md).

## 2026-05-25 — Exact KV materialization for Twitter traces

Branch: `vcomp-w/kv`.

The original trace vcomp path only preserved metadata shape. That cannot
reconstruct exact raw keys at materialization time without retaining a real
source of the input key set. The current implementation keeps Phase 1 and
background virtual compaction metadata-only, then materializes exact KV from
the append-only trace source in Phase 2.

### Design

- L0 virtual SSTs carry `source_run_ids`; virtual compaction unions lineage
  into output virtual SSTs.
- Phase 2 re-scans the trace source, routes each raw key plus value offset to
  the final virtual range for its source run, deduplicates by newest sequence
  number, and writes real SSTs once.
- Routing partitions are in memory; no per-vSST log scan is repeated.
- Records that fall outside final virtual ranges are written through a small
  L0 patch path instead of expanding same-level SST bounds.
- Output splitting uses conservative naive entry counts. This prevents PLR
  dedup underestimation from creating oversized materialized SSTs that hurt
  index/filter direct-read cost.

### 10M cluster012 result

`cluster012_100M.vcomptrace`, first 10M Put records:

| Mode | load elapsed | core time | final files | DB size |
|------|--------------|-----------|-------------|---------|
| baseline | 40 s | `twitterload` 22.45 s | 158 | 11 GB |
| vcomp-w/kv | 28 s | `fillvirtual` 18.16 s | 167 | 11 GB |

At 100M Put records, the same exact-KV path also passes:
baseline `490 s` wrapper / `463.44 s` core, vcomp-w/kv `337 s` wrapper /
`232.31 s` core, both final DBs `104 GB`.
For 100M vcomp, the non-primary remainder is `104.69 s`
(`337 - 232.31`), covering post-primary benchmarks plus DB teardown.

Run-phase validation uses raw trace keys:
`twitterrun_encode_for_vcomp=false`, `twitterrun_replay_writes=false`.
For the first 5M records of `cluster012_100M.vcomptrace.with_reads`,
baseline and vcomp both produce `15,045 / 1,006,713` misses. The binary
miss-key logs compare equal with `cmp`.

The 100M DB validates the same way: both baseline and vcomp produce
`15,040 / 1,006,713` misses, and the miss-key logs compare equal.

`run.sh`-style read settings (`cache_size=1`,
`cache_index_and_filter_blocks=true`, direct reads):

| Mode | read ops/s | elapsed | filter miss/hit | index miss/hit |
|------|------------|---------|-----------------|----------------|
| baseline | 2,402 | 418.98 s | 1,553,663 / 355 | 996,013 / 1,133 |
| vcomp-w/kv | 3,524 | 285.67 s | 1,011,590 / 105 | 991,801 / 38 |

100M read under the same settings: baseline `1,793 ops/s`, vcomp-w/kv
`2,621 ops/s`.

Detailed logs and counters are in
[../eval-vcomp/RESULTS.md §5.3](../eval-vcomp/RESULTS.md#53-exact-kv-materialization-sanity-2026-05-25).
