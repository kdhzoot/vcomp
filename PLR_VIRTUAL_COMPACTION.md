# PLR-Based Virtual Compaction

## 1. Overview

Virtual Compaction replaces RocksDB's I/O-intensive compaction with lightweight
**Piecewise Linear Regression (PLR) model merging**.  During bulk loading, no
actual SST data is written until the final materialization step.  This reduces
250 GB loading time from ~14 minutes (baseline `fillrandom`) to ~16 seconds.

```
Traditional:  Write keys -> Memtable (SkipList) -> Flush (write SST) -> Compaction (read+merge+write SSTs)
Virtual:      Generate keys -> vector buffer -> Radix sort -> PLR Fit -> Register virtual L0
              -> BG PLR Merge (concurrent) -> Materialize + SST write (direct I/O)
```

The virtual path bypasses the RocksDB memtable entirely.  Instead of inserting
keys into a SkipList (which maintains sorted order on insert), keys are
accumulated in a flat `std::vector<uint64_t>` and bulk-sorted with radix sort
before PLR fitting.  This trades the O(N log N) per-insert SkipList cost for
O(N) batch radix sort.

---

## 2. PLR Model

### 2.1 Core Idea

A sorted sequence of N keys $k_0 < k_1 < \cdots < k_{N-1}$ defines a
cumulative distribution function (CDF):

$$
\text{pos}(k_i) = i
$$

where $\text{pos}$ is the **rank** (0-based position) of key $k$ in the sorted
order.  PLR approximates this mapping with a small number of linear segments:

$$
\text{pos}(k) \approx s_j \cdot k + b_j \quad \text{for } k \in [k_j^{\text{start}}, k_j^{\text{end}}]
$$

Each segment is defined by four values:

```cpp
struct PLRSegment {
    uint64_t key_start, key_end;   // key range [key_start, key_end]
    double   slope, intercept;     // pos(k) = slope * k + intercept
};
```

### 2.2 Greedy-PLR Fit (Shrinking Cone Algorithm)

Given sorted keys and an error bound $\delta$, the algorithm greedily extends
each segment as far as possible while keeping the prediction error within
$\pm\delta$.

**Algorithm:**

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

        // Narrow the cone
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

**Properties:**
- Time complexity: $O(N)$ — single pass, each key examined once
- Output: $S$ segments where $S \ll N$ (typically $S \approx N / \delta$ for
  uniform distributions)
- Guarantee: $|\text{pos}_{\text{predicted}}(k) - \text{pos}_{\text{actual}}(k)| \leq \delta$ for all input keys

### 2.3 PLR Inverse

Given a rank (position), recover the estimated key:

$$
k = \frac{\text{pos} - b_j}{s_j}
$$

The implementation finds the segment whose position range
$[\text{pos}(k_j^{\text{start}}), \text{pos}(k_j^{\text{end}})]$ is closest to
the target position, then applies the inverse formula with clamping:

```cpp
uint64_t Inverse(double position) {
    // Find segment whose position range is closest to target
    best_seg = argmin_j | distance(position, [pos_lo_j, pos_hi_j]) |

    seg = segments[best_seg]
    if |seg.slope| < epsilon:
        return (seg.key_start + seg.key_end) / 2
    key = (position - seg.intercept) / seg.slope
    return clamp(key, seg.key_start, seg.key_end)
}
```

---

## 3. N-Way PLR Merge

### 3.1 Mathematical Foundation

When merging $N$ sorted sequences, each with its own PLR model
$\text{pos}_i(k)$, the merged rank at key $k$ is:

$$
\text{pos}_{\text{merged}}(k) = \sum_{i=1}^{N} \text{pos}_i(k)
$$

Since each $\text{pos}_i$ is piecewise linear, the sum is also piecewise
linear.  The breakpoints of the merged model are the **union** of all input
segment boundaries.

### 3.2 Algorithm

```
Input:  N PLR models with their key ranges
Output: merged PLR model

1. Collect all segment boundaries (key_start, key_end) from all models
2. Sort and deduplicate -> breakpoints[]

3. For each interval [breakpoints[i], breakpoints[i+1]]:
    slope_sum     = 0
    intercept_sum = 0
    
    for each active model j (key_min_j <= midpoint <= key_max_j):
        find segment of model j covering midpoint
        slope_sum     += seg_j.slope
        intercept_sum += seg_j.intercept
    
    for each finished model j (key_max_j < midpoint):
        intercept_sum += num_entries_j    // all keys are before this point
    
    if dedup:
        // Inclusion-exclusion: estimate unique key density
        dedup_prod = Π(1 - clamp(slope_j, 0, 1)) for each active model j
        adjusted_slope = 1 - dedup_prod
        emit PLRSegment(breakpoints[i], breakpoints[i+1], adjusted_slope, 0)
    else:
        emit PLRSegment(breakpoints[i], breakpoints[i+1], slope_sum, intercept_sum)

4. If dedup: recompute intercepts for continuity
    cumulative_pos = 0
    for each segment:
        segment.intercept = cumulative_pos - segment.slope * segment.key_start
        cumulative_pos += segment.slope * (segment.key_end - segment.key_start)
    adjusted_total = round(cumulative_pos)

5. Merge adjacent segments with identical (slope, intercept)
```

**Step 4 — Adjacent Segment Merging:**

The breakpoint-based merge produces one segment per sub-interval, but many
adjacent segments end up with identical (slope, intercept).  This happens when
consecutive intervals have the same set of active models — the slope/intercept
sums don't change across the boundary.

```
Before merge:  [0,100] s=0.5 b=3  |  [100,200] s=0.5 b=3  |  [200,350] s=0.7 b=1
After merge:   [0,200] s=0.5 b=3  |  [200,350] s=0.7 b=1
```

Two segments are merged if `|s1 - s2| < 1e-12` and `|b1 - b2| < 1e-9`.  This
is valid because a linear function with the same slope and intercept over
adjacent key ranges is simply one longer segment.

Without this step, the segment count after N-way merge equals the number of
breakpoint intervals (potentially thousands).  With merging, it drops to the
number of distinct (slope, intercept) transitions — typically close to the
sum of input segment counts.  In the 250GB trace, average output segments per
file (~345) stay close to input segments (~345), confirming this works well.

**Optimization — Sweep Line:**

Models are sorted by `key_min`.  As we sweep breakpoints left to right:
- **Activate** models whose `key_min <= midpoint`
- **Deactivate** (finish) models whose `key_max < midpoint`, adding
  `num_entries` to a running `finished_intercept`
- Only active models are iterated per interval

### 3.3 Probabilistic Dedup Correction

The naive merge (`slope_sum = Σ slope_i`) counts every key from every input,
including duplicates across levels.  For random workloads, duplicate keys can
be estimated probabilistically without tracking individual keys.

**Key insight:**  Each PLR segment's slope represents key density — the number
of keys per unit of key space.  When multiple models overlap in a key range
`[a, b]`, the probability that a given key position is occupied by model $i$
is `slope_i` (clamped to [0, 1]).  The expected unique density follows the
inclusion-exclusion principle:

$$
\text{adjusted\_slope} = 1 - \prod_{i=1}^{N} (1 - s_i)
$$

**Example:**  In interval `[0, 100]`, three models have slopes 0.3, 0.4, 0.2:
- Naive: `0.3 + 0.4 + 0.2 = 0.9` (90 keys)
- Dedup: `1 - (0.7 × 0.6 × 0.8) = 1 - 0.336 = 0.664` (66 keys, 26% dedup)

After computing adjusted slopes, intercepts are recomputed by accumulating
`cumulative_pos` across segments to maintain position function continuity.
The total `adjusted_entries` is capped to never exceed `naive_entries` (PLR
approximation error at low densities can cause adjusted > naive).

**Measured dedup rates (1TB, uniform random keys):**

| Level | Dedup% | Why |
|-------|--------|-----|
| L0→L1 | ~0% | Low density, few overlapping models |
| L2→L3 | 0.3% | Moderate density |
| L3→L4 | 3.0% | Higher density as keys accumulate |
| L4→L5 | 13.3% | Highest density, most overlap |

**Impact:**  DB size matches baseline (762GB vs 768GB for 1TB), compared to
~1016GB without dedup correction.  The correction reduces output SST count
from `SplitIntoSSTs`, which in turn reduces Phase 2 materialization time.

### 3.4 Why Slope/Intercept Addition Works

Consider two sorted sequences $A$ and $B$ merged into $C$.  For any key $k$:

$$
\text{rank}_C(k) = \text{rank}_A(k) + \text{rank}_B(k)
$$

If $\text{rank}_A(k) = s_A \cdot k + b_A$ and $\text{rank}_B(k) = s_B \cdot k + b_B$ in some interval:

$$
\text{rank}_C(k) = (s_A + s_B) \cdot k + (b_A + b_B)
$$

This is still linear, so slope and intercept simply add.

---

## 4. Virtual SST and SplitIntoSSTs

### 4.1 VirtualSST Structure

A virtual SST stores **no actual key-value data** — only a PLR model of the
key distribution plus metadata:

```cpp
struct VirtualSST {
    PLRModel plr_model;     // key -> local rank mapping
    uint64_t key_min;       // smallest key
    uint64_t key_max;       // largest key
    uint64_t num_entries;   // number of keys
    int      level;         // LSM level
    uint64_t size_bytes;    // estimated size = num_entries * avg_entry_size
};
```

### 4.2 Splitting a Merged Model

After N-way merge, the merged PLR model covers all keys.  We split it into
multiple VirtualSSTs, each covering approximately `target_sst_size` bytes:

```
keys_per_sst = target_sst_size / avg_entry_size

For SST i:
    pos_start = i * keys_per_sst
    pos_end   = min((i+1) * keys_per_sst, total_entries)
    
    key_start = PLR.Inverse(pos_start)    // first SST uses global_min
    key_end   = PLR.Inverse(pos_end) - 1  // -1 to avoid overlap (see below)
    
    sub_segments = segments overlapping [key_start, key_end]
    for each sub_segment:
        intercept -= pos_start            // adjust to local rank (start from 0)
```

Two critical details:

**Intercept adjustment**: The sub-PLR's rank must start from 0 (local rank),
not from the global position.  Without this, `MaterializeKeys` would use
local positions (0, 1, 2, ...) against global intercepts, producing wildly
wrong keys.

**Non-overlapping boundaries (`key_end - 1`)**: `PLR.Inverse(pos_end)` for
SST i and `PLR.Inverse(pos_start)` for SST i+1 map to the same position
boundary, so they can return the same key.  For L1+ levels, RocksDB requires
strictly non-overlapping key ranges between files in the same level.  Without
the `-1`, `VersionBuilder` rejects the files with:
```
force_consistency_checks: L1 has overlapping ranges:
file #12 largest key: ... vs. file #13 smallest key: ...
```

---

## 5. Materialization

Converting a VirtualSST back to actual keys using PLR Inverse:

```
For pos = 0, 1, 2, ..., num_entries-1:
    key = (pos - seg.intercept) / seg.slope
    key = clamp(key, seg.key_start, seg.key_end)
    key = clamp(key, vsst.key_min, vsst.key_max)

Post-process: ensure strictly increasing
    if keys[i] <= keys[i-1]:  keys[i] = keys[i-1] + 1
```

The implementation walks segments sequentially (O(N) total) rather than doing
binary search per key (O(N log S)).

Each VirtualSST is materialized independently — no cross-file merge is needed
because BG compaction ensures L1+ files have non-overlapping key ranges.
Materialization and SST writing are fused into a single parallel step using
direct I/O (O_DIRECT) to bypass page cache overhead.

---

## 6. Architecture: Integration with RocksDB

### 6.1 Components

```
┌──────────────────────────────────────────────────────────────┐
│  db_bench (FillVirtual)                                      │
│    Main thread: keygen -> radix sort -> PLRFit               │
│    Batch register virtual L0 files (l0_trigger per batch)    │
└────────────────────────┬─────────────────────────────────────┘
                         │ VersionEdit::AddFile(L0) batched
                         v
┌──────────────────────────────────────────────────────────────┐
│  RocksDB Core (concurrent with flush)                        │
│    VersionSet: tracks virtual files (no physical SST)        │
│    CompactionPicker: selects files based on metadata         │
│    BackgroundCompaction (32 threads):                        │
│      if virtual -> RunVirtualCompaction (PLR merge)          │
│      else       -> CompactionJob (normal I/O compaction)     │
│                                                              │
│  VirtualSSTRegistry: file_number -> VirtualSST (thread-safe) │
│                                                              │
│  Bypasses for virtual mode:                                  │
│    - LoadTableHandlers: skip (no SST file to open)           │
│    - VerifyFileMetadata: skip (no file size to check)        │
│    - DeleteObsoleteFileImpl: skip (no file to delete)        │
└────────────────────────┬─────────────────────────────────────┘
                         │ After all compactions settle
                         v
┌──────────────────────────────────────────────────────────────┐
│  Materialization (48 threads, direct I/O)                     │
│    Per VirtualSST:                                           │
│      MaterializeKeys -> GenerateKeyFromInt -> SstFileWriter  │
│    VersionEdit: delete virtual files, add real files         │
└──────────────────────────────────────────────────────────────┘
```

### 6.2 Flow

1. **Flush (main thread):**  Radix sort keys in batch -> `GreedyPLRFit` ->
   create `VirtualSST` -> register in `VirtualSSTRegistry` ->
   accumulate `VersionEdit::AddFile` entries -> batch `LogAndApply` every
   `l0_compaction_trigger` files -> `InstallSuperVersionAndScheduleWork`
   triggers BG compaction

2. **Compaction (RocksDB background threads, concurrent with step 1):**
   `CompactionPicker` selects input files -> `RunVirtualCompaction`:
   - Look up input PLR models from registry
   - `NWayMergePLR` (mutex released during merge)
   - `SplitIntoSSTs` for output (non-overlapping boundaries for L1+)
   - Register outputs in registry, `LogAndApply`
   - 250GB: ~11,700 compaction events across L0-L4

3. **Materialization (all CPU cores, direct I/O):**  Each VirtualSST is
   independently materialized and written to a real SST file in a single
   fused step. No cross-file merge/dedup needed since BG compaction ensures
   non-overlapping ranges within each level.
   `VersionEdit`: delete all virtual files + add all real files atomically.

### 6.3 Key Modifications to RocksDB

| File | Change |
|------|--------|
| `include/rocksdb/options.h` | `use_virtual_compaction`, `plr_error_bound` options |
| `options/db_options.h` + `.cc` | `ImmutableDBOptions` mapping |
| `db/db_impl/db_impl.h` | `VirtualSSTRegistry` member, `RegisterVirtualL0File()`, `RunVirtualCompaction()` |
| `db/db_impl/db_impl.cc` | Initialize registry when `use_virtual_compaction` enabled |
| `db/db_impl/db_impl_compaction_flush.cc` | Virtual compaction branch in `BackgroundCompaction()`, `RegisterVirtualL0File()` impl |
| `db/db_impl/db_impl_files.cc` | Skip disk deletion for virtual files in `DeleteObsoleteFileImpl()` |
| `db/version_set.cc` | Skip `LoadTableHandlers` and `VerifyFileMetadata` for virtual mode |
| `db/virtual_compaction/virtual_sst.cc` | Non-overlapping split boundaries (key_end - 1) for L1+ |
| `db/virtual_compaction/virtual_sst_registry.h` | Thread-safe `file_number -> VirtualSST` registry |

### 6.4 Key Optimizations in FillVirtual

| Optimization | Before | After | Impact |
|-------------|--------|-------|--------|
| Radix sort (uint64_t keys) | `std::sort` O(N log N) | 8-bit radix O(N) | 6.5x faster |
| Batch LogAndApply | 1 call per flush | 1 call per `l0_trigger` flushes | 3x fewer MANIFEST writes |
| Fused materialize+write | PLR inverse -> merge/dedup -> SST write | PLR inverse -> SST write per file | Eliminates 16s merge step |
| Direct I/O (O_DIRECT) | Buffered (page cache) | `use_direct_writes=true, use_mmap_writes=false` | sys CPU 66% -> 9% |
| Max parallelism | `max_background_jobs` (32) threads | `hardware_concurrency()` (48) threads | 1.3x Phase 2 |

---

## 7. Performance

### 250 GB (262M keys, key=24B, value=1000B, compression=none)

| Metric | Baseline (`fillrandom`) | Virtual Compaction (v5) |
|--------|------------------------|------------------------|
| **Total time** | **837 sec** | **14 sec** |
| Throughput | 311 MB/s | 12,483 MB/s |
| Compaction I/O | 4,476 GB (read+write) | **0 bytes** |
| Write amplification | 9.2x | 1.0x |
| DB size | 177 GB | 176 GB |
| SST files | ~3,300 | 2,934 |

Phase breakdown:
```
Phase 1 (8.2s):   keygen 3.2s | radix sort 2.7s | PLR fit 0.8s | register 1.5s
BG compaction:    ~0s (11,322 PLR merge events, concurrent with Phase 1)
Phase 2 (5.9s):   materialize + SST write 5.2s | VersionEdit 0.7s
```

### 1 TB (1.05B keys)

| Metric | Baseline (`fillrandom`) | Virtual Compaction (v5) |
|--------|------------------------|------------------------|
| **Total time** | **3,889 sec** | **99 sec** |
| Throughput | 264 MB/s | 7,772 MB/s |
| Compaction I/O | 12,100 GB read + 12,867 GB write | **0 bytes** |
| Write amplification | 12.7x | 1.0x |
| BG compaction events | 64,828 | 51,423 |
| DB size | 768 GB | 762 GB |
| SST files | 13,496 | 12,774 |

Phase breakdown:
```
Phase 1 (74s):    keygen 22s | radix sort 12s | PLR fit 3.5s | register 36s
BG compaction:    ~0s (51,423 events, concurrent with Phase 1)
Phase 2 (25s):    materialize + SST write 21s | VersionEdit 3.5s
```

### 1 TB Read Performance (readrandom, 16 threads, 180s)

| Metric | Baseline | VComp | Δ |
|--------|----------|-------|---|
| **ops/sec (cache=2%)** | 267,978 | 275,864 | +2.9% |
| **ops/sec (cache=0%)** | 286,104 | 298,208 | +4.2% |
| GET P50 (cache=0%) | 64.0 µs | 62.9 µs | -1.7% |
| GET P99 (cache=0%) | 163.2 µs | 146.4 µs | -10.3% |
| SST read P99 (cache=0%) | 152.0 µs | 109.4 µs | -28.0% |
| Found ratio | 63.2% | 63.0% | ≈ |
| Bloom FP rate | 4.5% | 4.3% | ≈ |

VComp-generated SSTs are fully functional standard RocksDB SST files with
bloom filters, correct key ordering, and proper level structure.  Read
performance is equal to or slightly better than baseline.

### Design Trade-offs

- **Probabilistic dedup (not exact)**: The inclusion-exclusion dedup correction
  estimates duplicate count from PLR slopes.  It cannot identify *which* keys
  are duplicates — only how many.  This is sufficient for accurate SST sizing
  but does not reduce actual write amplification within the virtual compaction
  pipeline.

- **Memtable bypass**: The virtual path does not use RocksDB's memtable
  (SkipList). Keys are accumulated in a flat vector and bulk-sorted with
  radix sort.  This means WAL, sequence numbers, and write stall feedback
  are not used.  Acceptable for a loading-specialized DB.

- **Phase 1 register scaling**: `LogAndApply` cost grows non-linearly with
  MANIFEST size (0.5ms/call at start -> 11ms/call at 4000th call for 1TB).
  This is the current bottleneck for scaling beyond 1TB.  Increasing the
  batch size reduces calls but risks data loss if `WaitForCompact` returns
  before all compactions are scheduled.
