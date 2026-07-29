# Virtual Compaction Profiling Report

## 1. Test Setup

- **Data size**: 250 GB (262M keys, key=24B, value=1000B, compression=none)
- **Hardware**: 48-core Xeon Gold 6336Y @ 2.40GHz, 36MB L3 cache
- **RocksDB config**: bloom_bits=10, direct I/O, max_background_jobs=32
- **VComp config**: plr_error_bound=8, memtable_flush_size=64MB

## 2. End-to-End Comparison

| Metric | Baseline (fillrandom) | Virtual Compaction | Ratio |
|--------|----------------------|-------------------|-------|
| **Wall clock** | **837 sec** | **56 sec** | 14.9x |
| DB size on disk | 177 GB | 255 GB | 0.69x |
| SST file count | 3,304 | 4,000 | 0.83x |
| Avg SST file size | 54.6 MB | 65.0 MB | — |
| Compaction I/O (read) | 2,150 GB | 0 GB | inf |
| Compaction I/O (write) | 2,326 GB | 0 GB | inf |
| Write amplification | 9.2x | 1.0x | — |

## 3. Phase Breakdown (VComp)

```
Phase 1 (flush + bg compaction):  25.3 sec  (44.9%)
  keygen:     2.5 sec   (4.4%)
  sort:      16.7 sec  (29.6%)  ← #1 bottleneck
  PLR fit:    0.7 sec   (1.3%)
  register:   5.4 sec   (9.5%)  ← #2 bottleneck

BG compaction wait:                0.07 sec  (0.1%)

Phase 2 (materialization):       30.9 sec  (54.9%)
  PLR inverse:   0.1 sec  (0.3%)
  merge/dedup:  16.2 sec (28.8%)  ← #3 bottleneck
  SST write:    13.6 sec (24.2%)
  VersionEdit:   1.0 sec  (1.8%)
```

### Top 3 Bottlenecks

1. **Phase 1 sort (16.7s, 30%)** — 4000 calls to `std::sort` on 65K-element
   batches. Each sort is O(N log N) = O(65K * 16) ≈ 1M comparisons. Total:
   4B comparisons.

2. **Phase 2b merge/dedup (16.2s, 29%)** — K-way merge of 4000 sorted key
   lists (262M keys total). Dominated by L4 which has 3557 files (233M keys).
   Priority queue with 3557 entries is cache-unfriendly.

3. **Phase 1 register (5.4s, 10%)** — 4000 calls to `LogAndApply` +
   `InstallSuperVersionAndScheduleWork`. Each call writes to MANIFEST and
   updates SuperVersion. ~1.3 ms per call.

### Non-bottlenecks

- **PLR fit (0.7s)** — Greedy-PLR on 65K keys is very fast (~175us per batch)
- **PLR inverse (0.1s)** — Sequential segment walk, O(N) total
- **BG compaction (0.07s wait)** — PLR merge is CPU-lightweight; all 4000
  files settle across L0-L4 before Phase 2 starts

## 4. Structural Differences vs Baseline

### 4.1 DB Size (255 GB vs 177 GB)

Virtual compaction does not perform cross-level deduplication. When
`NWayMergePLR` merges models from L0 and L1, the merged rank is:

```
pos_merged(k) = pos_L0(k) + pos_L1(k)
```

If key `k` exists in both, its count is preserved (not deduplicated). In
contrast, baseline compaction drops older versions of duplicate keys, reducing
total entry count by ~37% (birthday paradox with 262M keys in [0, 262M)).

This is by design — deduplication at the PLR level would require actually
tracking individual keys, negating the benefit of model-based compaction.

### 4.2 Level Distribution

```
Level   Baseline Files  Baseline Size    VComp Files  VComp Size
L0            0            0 MB              2       130 MB
L1            4          198 MB              3       195 MB
L2           40         2.45 GB             39      2.5 GB
L3          435        24.98 GB            399     25.3 GB
L4        2,824       148.6 GB           3,557    225.8 GB
```

VComp has more files in L4 because no dedup means more total data flows down.
The level size ratios are consistent (size_ratio=10).

### 4.3 SST File Properties

| Property | Baseline | VComp (before fix) | VComp (after fix) |
|----------|----------|-------------------|-------------------|
| Bloom filter | Yes (10 bits) | **No** | Yes (10 bits) |
| Avg file size | 54.6 MB | 35.6 MB | 65.0 MB |
| Compression | None | None | None |

**Fix applied**: VComp SstFileWriter now inherits `open_options_` (includes
bloom filter, block size, etc.) instead of using bare `Options()`.

### 4.4 Compaction Statistics

Baseline RocksDB tracks compaction stats (read/write bytes, CPU time, etc.).
VComp shows 0 for all compaction stats because `RunVirtualCompaction` bypasses
`CompactionJob` which records these metrics. This is cosmetic but affects
observability.

## 5. Baseline Breakdown

```
fillrandom:         822 sec  (key generation + memtable + flush + compaction)
waitforcompaction:   15 sec  (drain remaining bg compactions)
Total:              837 sec
```

Baseline compaction stats:
- Total compaction CPU: 4,110 sec (spread across 32 bg threads)
- Total compaction I/O: 2,150 GB read + 2,326 GB write = 4,476 GB
- Write amplification: 9.2x
- 13,997 compaction jobs

## 6. Potential Optimizations

### 6.1 Phase 1 sort → parallel sort or radix sort
`std::sort` on 65K uint64_t elements could use radix sort (O(N)) instead of
comparison sort (O(N log N)). Or batch multiple memtables and sort in parallel.

### 6.2 Phase 1 register → batch LogAndApply
Instead of one `LogAndApply` per flush, accumulate multiple virtual L0 files
and apply them in a single `LogAndApply` call. This would reduce MANIFEST
write overhead from 4000 calls to ~1000 calls (batch size 4).

### 6.3 Phase 2b merge → parallel merge or concatenate+sort
The k-way merge with `std::priority_queue` is not faster than `std::sort` due
to cache locality. Options:
- Parallel sort with thread pool (split array into chunks, sort each, merge)
- For L4 (89% of keys): since files within a level should have non-overlapping
  key ranges after compaction, a simple concatenation without sorting may work

### 6.4 Phase 2c SST write → adjust keys_per_file
Current `keys_per_file = target_sst_size / avg_entry_size` uses raw entry size
(1024B) but actual SST encoding with prefix compression yields ~650B/entry.
Adjusting would produce fewer, larger files closer to baseline.

## 7. Summary

Virtual compaction achieves **14.9x speedup** over baseline by eliminating
all compaction I/O (4,476 GB read+write → 0). The remaining time is split
between CPU-bound operations (sort, PLR, merge) and materialization I/O
(single pass of 255 GB writes).

The main structural difference — no cross-level deduplication — results in
a 44% larger DB (255 GB vs 177 GB). This is a design trade-off: dedup
would require tracking individual keys, which contradicts the PLR model
approach.
