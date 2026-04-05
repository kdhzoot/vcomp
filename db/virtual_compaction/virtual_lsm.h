//  Copyright (c) 2011-present, Facebook, Inc.  All rights reserved.
//  This source code is licensed under both the GPLv2 (found in the
//  COPYING file in the root directory) and Apache 2.0 License
//  (found in the LICENSE.Apache file in the root directory).
#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

#include "db/virtual_compaction/virtual_sst.h"

namespace ROCKSDB_NAMESPACE {

// Configuration for VirtualLSMTree.
struct VirtualLSMConfig {
  int num_levels = 7;              // L0 ~ L6
  int size_ratio = 10;             // Level size multiplier
  uint64_t l1_size = 256ULL * 1024 * 1024;       // L1 max size (256MB)
  uint64_t target_sst_size = 64ULL * 1024 * 1024; // 64MB per SST
  int l0_compaction_trigger = 4;   // Compact L0 when this many files
  double plr_error_bound = 8.0;    // PLR δ parameter
  uint64_t avg_entry_size = 1024 + 24;  // key_size + value_size
  size_t segment_threshold = 10000; // Compress PLR if segments exceed this
};

// Callback for logging compaction events.
using VCompLogCallback = std::function<void(const std::string&)>;

// A simulated LSM-Tree that uses virtual compaction (PLR model merging)
// instead of actual I/O-based compaction.
class VirtualLSMTree {
 public:
  explicit VirtualLSMTree(const VirtualLSMConfig& config);

  // Flush a sorted batch of keys (simulates memtable flush to L0).
  // Keys must be sorted in ascending order.
  void FlushMemtable(const std::vector<uint64_t>& sorted_keys);

  // Get all VirtualSSTs at a given level.
  const std::vector<VirtualSST>& GetLevel(int level) const;

  // Get the number of levels.
  int NumLevels() const { return config_.num_levels; }

  // Get total size of a level in bytes.
  uint64_t LevelSize(int level) const;

  // Get max allowed size for a level.
  uint64_t MaxLevelSize(int level) const;

  // Get compaction score for a level.
  // For L0: num_files / l0_compaction_trigger.
  // For L1+: level_size / max_level_size.
  double CompactionScore(int level) const;

  // Materialize all VirtualSSTs to key lists.
  // Returns keys per level per SST.
  std::vector<std::vector<std::vector<uint64_t>>> MaterializeAll() const;

  // Set a log callback for observing compaction events.
  void SetLogCallback(VCompLogCallback cb) { log_cb_ = std::move(cb); }

  // Statistics.
  uint64_t TotalCompactions() const { return total_compactions_; }
  uint64_t TotalFlushes() const { return total_flushes_; }
  uint64_t TotalVirtualSSTs() const;

 private:
  // Check if any level needs compaction and trigger it.
  void MaybeTriggerCompaction(int level);

  // Pick the SST to compact from a level (largest overlap with next level).
  size_t PickSSTToCompact(int level) const;

  // Find SSTs in next_level that overlap with the given SST's key range.
  std::vector<size_t> FindOverlappingSSTs(int level,
                                          const VirtualSST& target) const;

  void Log(const std::string& msg);

  VirtualLSMConfig config_;
  std::vector<std::vector<VirtualSST>> levels_;
  VCompLogCallback log_cb_;

  uint64_t total_compactions_ = 0;
  uint64_t total_flushes_ = 0;

 public:
  // Timing stats (microseconds).
  uint64_t plr_fit_us_ = 0;
  uint64_t merge_us_ = 0;
  uint64_t split_us_ = 0;
};

}  // namespace ROCKSDB_NAMESPACE
