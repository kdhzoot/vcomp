//  Copyright (c) 2011-present, Facebook, Inc.  All rights reserved.
//  This source code is licensed under both the GPLv2 (found in the
//  COPYING file in the root directory) and Apache 2.0 License
//  (found in the LICENSE.Apache file in the root directory).
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "db/virtual_compaction/plr_model.h"

namespace ROCKSDB_NAMESPACE {

// A virtual SST file represented by a PLR model of its key distribution.
// No actual data is stored — only the model of the key CDF.
struct VirtualSST {
  PLRModel plr_model;
  uint64_t key_min;
  uint64_t key_max;
  uint64_t num_entries;
  int level;
  uint64_t size_bytes;

  // Full byte bounds for byte-string sources (Twitter trace). L0 virtual
  // SSTs keep exact raw key bounds. BG-compaction outputs keep byte-range
  // envelopes derived from their prefix8 PLR bounds, so Phase 2 can route
  // original keys by byte comparator while PLR shape remains uint64-based.
  // When empty (fillrandom path), callers derive bounds with EncodeUserKey.
  std::string key_min_bytes;
  std::string key_max_bytes;

  // Debug/materialization lineage. Phase 2 routes by final full-byte ranges;
  // source_run_ids are retained to describe which input runs contributed to
  // a virtual SST, not as the primary routing key.
  std::vector<uint64_t> source_run_ids;

  // Estimate size from entry count and average entry size.
  static uint64_t EstimateSize(uint64_t entries, uint64_t avg_entry_size) {
    return entries * avg_entry_size;
  }
};

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
                                      const std::vector<uint64_t>& grandparent_boundaries = {});

// Materialize keys from a VirtualSST using PLR Inverse.
// Returns sorted keys reconstructed from the model.
std::vector<uint64_t> MaterializeKeys(const VirtualSST& vsst);

// Perform virtual compaction: merge N input VirtualSSTs into output VirtualSSTs.
std::vector<VirtualSST> VirtualCompact(
    const std::vector<const VirtualSST*>& inputs,
    uint64_t target_sst_size,
    uint64_t avg_entry_size,
    int output_level);

}  // namespace ROCKSDB_NAMESPACE
