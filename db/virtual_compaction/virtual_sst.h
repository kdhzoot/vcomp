//  Copyright (c) 2011-present, Facebook, Inc.  All rights reserved.
//  This source code is licensed under both the GPLv2 (found in the
//  COPYING file in the root directory) and Apache 2.0 License
//  (found in the LICENSE.Apache file in the root directory).
#pragma once

#include <cstdint>
#include <limits>
#include <vector>

#include "db/virtual_compaction/plr_model.h"
#include "db/virtual_compaction/sst_size_model.h"

namespace ROCKSDB_NAMESPACE {

struct KMVSample {
  uint64_t key;
  uint64_t hash;
};

struct KMVSketch {
  std::vector<KMVSample> samples;
  uint64_t theta_hash = std::numeric_limits<uint64_t>::max();
  bool complete = false;
};

struct KMVRangeSketch {
  uint64_t key_min = 0;
  uint64_t key_max = 0;
  uint64_t num_entries = 0;
  // A certified model count is not an upper bound on original membership.
  // Retain the independent range estimate separately for later estimation.
  uint64_t raw_estimated_entries = 0;
  bool entries_are_modeled = false;
  KMVSketch sketch;
};

// A virtual SST file represented by a PLR model of its key distribution.
// No actual data is stored — only the model of the key CDF.
struct VirtualSST {
  PLRModel plr_model;
  KMVSketch kmv_sketch;
  std::vector<KMVRangeSketch> kmv_ranges;
  uint64_t key_min;
  uint64_t key_max;
  uint64_t num_entries;
  int level;
  uint64_t size_bytes;

  // Estimate size from entry count and average entry size.
  static uint64_t EstimateSize(uint64_t entries, uint64_t avg_entry_size) {
    return entries * avg_entry_size;
  }
};

// Fixed number of samples retained per virtual SST.
size_t VirtualSSTKMVSamples();

// Fixed number of range-local KMV buckets retained per virtual SST.
size_t VirtualSSTKMVRangeBuckets();

// Runtime switch for KMV metadata and KMV-based dedup. Set
// VCOMP_KMV_ENABLED=0 to fall back to the pre-KMV PLR dedup path.
bool VirtualSSTKMVEnabled();

// Experimental discrete count/select path. Set VCOMP_DISCRETE_CDF_ENABLED=0
// to reproduce the legacy approximate merge/split/materialization path.
bool VirtualSSTDiscreteCDFEnabled();

// Keep a flush's known entry count while making its reconstructed model
// feasible and protecting retained KMV witnesses. Does not retain all keys.
Status CertifyVirtualSST(VirtualSST* vsst);

// Build a KMV sketch from sorted unique keys.
KMVSketch BuildKMVSketchFromSortedKeys(const std::vector<uint64_t>& sorted_keys,
                                       size_t max_samples = 0);

// Build range-local KMV sketches from sorted unique keys. The total KMV sample
// budget is split across ranges, so metadata remains bounded per vSST.
std::vector<KMVRangeSketch> BuildKMVRangeSketchesFromSortedKeys(
    const std::vector<uint64_t>& sorted_keys,
    size_t max_samples = 0,
    size_t max_ranges = 0);

// Estimate the union cardinality of the input VSSTs using their KMV sketches.
// Returns naive_entries when sketches are missing or too sparse.
uint64_t EstimateKMVUnionEntries(const std::vector<const VirtualSST*>& inputs,
                                 uint64_t naive_entries,
                                 size_t max_samples = 0);

uint64_t EstimateKMVUnionEntriesForRange(
    const std::vector<const VirtualSST*>& inputs, uint64_t key_min,
    uint64_t key_max);

// Merge input PLR shapes while estimating dedup cardinality using only KMV.
// Global KMV determines total entries; range-local KMV distributes interval
// mass without using PLR-based dedup fallback.
PLRModel NWayMergeKMVRangeAware(const std::vector<const VirtualSST*>& inputs,
                                uint64_t* adjusted_total,
                                size_t kmv_samples = 0,
                                Status* status = nullptr);

// Merge input sketches and retain only samples that fall in [key_min, key_max].
KMVSketch MergeKMVSketchesForRange(const std::vector<const VirtualSST*>& inputs,
                                   uint64_t key_min, uint64_t key_max,
                                   size_t max_samples = 0);

// Scale rank positions in a PLR model when KMV cardinality is capped by the
// input entry count.
PLRModel ScalePLRPositions(const PLRModel& plr, double scale);

// Split a merged PLR model into multiple VirtualSSTs, each with approximately
// target_sst_size bytes. Uses PLR Inverse to find split key boundaries.
// If grandparent_boundaries is non-empty, output SSTs are also split at these
// key boundaries to limit overlap with the next level (matching RocksDB's
// grandparent boundary split behavior).
std::vector<VirtualSST> SplitIntoSSTs(const PLRModel& plr,
                                      uint64_t total_entries,
                                      uint64_t target_sst_size,
                                      uint64_t avg_entry_size,
                                      uint64_t global_min,
                                      uint64_t global_max,
                                      int target_level,
                                      const std::vector<uint64_t>& grandparent_boundaries = {},
                                      const std::vector<const VirtualSST*>* kmv_inputs = nullptr,
                                      size_t kmv_samples = 0,
                                      const SSTSizeModel* size_model = nullptr);

// Materialize keys from a VirtualSST using PLR Inverse.
// Returns sorted keys reconstructed from the model.
std::vector<uint64_t> MaterializeKeys(const VirtualSST& vsst);

// Perform virtual compaction: merge N input VirtualSSTs into output VirtualSSTs.
std::vector<VirtualSST> VirtualCompact(
    const std::vector<const VirtualSST*>& inputs,
    uint64_t target_sst_size,
    uint64_t avg_entry_size,
    int output_level,
    const SSTSizeModel* size_model = nullptr);

}  // namespace ROCKSDB_NAMESPACE
