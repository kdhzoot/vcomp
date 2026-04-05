//  Copyright (c) 2011-present, Facebook, Inc.  All rights reserved.
//  This source code is licensed under both the GPLv2 (found in the
//  COPYING file in the root directory) and Apache 2.0 License
//  (found in the LICENSE.Apache file in the root directory).
#pragma once

#include <cstdint>
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

  // Estimate size from entry count and average entry size.
  static uint64_t EstimateSize(uint64_t entries, uint64_t avg_entry_size) {
    return entries * avg_entry_size;
  }
};

// Split a merged PLR model into multiple VirtualSSTs, each with approximately
// target_sst_size bytes. Uses PLR Inverse to find split key boundaries.
std::vector<VirtualSST> SplitIntoSSTs(const PLRModel& plr,
                                      uint64_t total_entries,
                                      uint64_t target_sst_size,
                                      uint64_t avg_entry_size,
                                      uint64_t global_min,
                                      uint64_t global_max,
                                      int target_level);

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
