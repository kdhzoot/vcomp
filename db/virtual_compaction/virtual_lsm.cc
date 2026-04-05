//  Copyright (c) 2011-present, Facebook, Inc.  All rights reserved.
//  This source code is licensed under both the GPLv2 (found in the
//  COPYING file in the root directory) and Apache 2.0 License
//  (found in the LICENSE.Apache file in the root directory).

#include "db/virtual_compaction/virtual_lsm.h"

#include <algorithm>
#include <cassert>
#include <sstream>

namespace ROCKSDB_NAMESPACE {

VirtualLSMTree::VirtualLSMTree(const VirtualLSMConfig& config)
    : config_(config), levels_(config.num_levels) {}

void VirtualLSMTree::FlushMemtable(const std::vector<uint64_t>& sorted_keys) {
  if (sorted_keys.empty()) return;

  PLRModel plr = GreedyPLRFit(sorted_keys, config_.plr_error_bound);

  VirtualSST vsst;
  vsst.plr_model = std::move(plr);
  vsst.key_min = sorted_keys.front();
  vsst.key_max = sorted_keys.back();
  vsst.num_entries = sorted_keys.size();
  vsst.level = 0;
  vsst.size_bytes =
      VirtualSST::EstimateSize(sorted_keys.size(), config_.avg_entry_size);

  levels_[0].push_back(std::move(vsst));
  total_flushes_++;

  Log("Flush #" + std::to_string(total_flushes_) + ": " +
      std::to_string(sorted_keys.size()) + " keys → L0 (" +
      std::to_string(levels_[0].size()) + " files)");

  MaybeTriggerCompaction(0);
}

const std::vector<VirtualSST>& VirtualLSMTree::GetLevel(int level) const {
  assert(level >= 0 && level < config_.num_levels);
  return levels_[level];
}

uint64_t VirtualLSMTree::LevelSize(int level) const {
  uint64_t total = 0;
  for (const auto& sst : levels_[level]) {
    total += sst.size_bytes;
  }
  return total;
}

uint64_t VirtualLSMTree::MaxLevelSize(int level) const {
  if (level == 0) return 0;  // L0 uses file count, not size.
  uint64_t max_size = config_.l1_size;
  for (int i = 1; i < level; i++) {
    max_size *= config_.size_ratio;
  }
  return max_size;
}

double VirtualLSMTree::CompactionScore(int level) const {
  if (level == 0) {
    return static_cast<double>(levels_[0].size()) /
           config_.l0_compaction_trigger;
  }
  uint64_t max = MaxLevelSize(level);
  if (max == 0) return 0.0;
  return static_cast<double>(LevelSize(level)) / max;
}

std::vector<std::vector<std::vector<uint64_t>>> VirtualLSMTree::MaterializeAll()
    const {
  std::vector<std::vector<std::vector<uint64_t>>> result(config_.num_levels);
  for (int level = 0; level < config_.num_levels; level++) {
    for (const auto& vsst : levels_[level]) {
      result[level].push_back(MaterializeKeys(vsst));
    }
  }
  return result;
}

uint64_t VirtualLSMTree::TotalVirtualSSTs() const {
  uint64_t count = 0;
  for (const auto& level : levels_) {
    count += level.size();
  }
  return count;
}

void VirtualLSMTree::MaybeTriggerCompaction(int level) {
  if (level >= config_.num_levels - 1) return;

  while (true) {
    bool need_compact = false;

    if (level == 0) {
      need_compact =
          static_cast<int>(levels_[0].size()) >= config_.l0_compaction_trigger;
    } else {
      need_compact = LevelSize(level) > MaxLevelSize(level);
    }

    if (!need_compact) break;

    total_compactions_++;

    int output_level = level + 1;
    // For L0: compact all L0 files + overlapping L1 files.
    // For L1+: pick one SST + overlapping files in next level.

    std::vector<const VirtualSST*> inputs;
    std::vector<size_t> remove_from_level;
    std::vector<size_t> remove_from_next;

    if (level == 0) {
      // All L0 files participate.
      for (size_t i = 0; i < levels_[0].size(); i++) {
        inputs.push_back(&levels_[0][i]);
        remove_from_level.push_back(i);
      }
    } else {
      // Pick one SST from this level.
      size_t pick = PickSSTToCompact(level);
      inputs.push_back(&levels_[level][pick]);
      remove_from_level.push_back(pick);
    }

    // Find key range of selected SSTs.
    uint64_t range_min = std::numeric_limits<uint64_t>::max();
    uint64_t range_max = 0;
    for (const auto* sst : inputs) {
      range_min = std::min(range_min, sst->key_min);
      range_max = std::max(range_max, sst->key_max);
    }

    // Find overlapping SSTs in output level.
    VirtualSST range_sst;
    range_sst.key_min = range_min;
    range_sst.key_max = range_max;
    remove_from_next = FindOverlappingSSTs(output_level, range_sst);

    for (size_t idx : remove_from_next) {
      inputs.push_back(&levels_[output_level][idx]);
    }

    // Virtual compaction.
    auto new_ssts = VirtualCompact(inputs, config_.target_sst_size,
                                   config_.avg_entry_size, output_level);

    uint64_t in_entries = 0;
    for (const auto* s : inputs) in_entries += s->num_entries;
    uint64_t out_entries = 0;
    for (const auto& s : new_ssts) out_entries += s.num_entries;

    Log("Compact #" + std::to_string(total_compactions_) + ": L" +
        std::to_string(level) + " → L" + std::to_string(output_level) + " (" +
        std::to_string(inputs.size()) + " inputs, " +
        std::to_string(new_ssts.size()) + " outputs, " +
        std::to_string(in_entries) + " → " + std::to_string(out_entries) +
        " keys)");

    // Remove old SSTs (in reverse index order to preserve indices).
    std::sort(remove_from_next.rbegin(), remove_from_next.rend());
    for (size_t idx : remove_from_next) {
      levels_[output_level].erase(levels_[output_level].begin() + idx);
    }
    std::sort(remove_from_level.rbegin(), remove_from_level.rend());
    for (size_t idx : remove_from_level) {
      levels_[level].erase(levels_[level].begin() + idx);
    }

    // Add new SSTs to output level.
    for (auto& sst : new_ssts) {
      levels_[output_level].push_back(std::move(sst));
    }

    // Cascade: check if output level now needs compaction.
    MaybeTriggerCompaction(output_level);

    // Re-check current level (L0 might have accumulated more files
    // during cascade, but typically we break here).
    if (level != 0) break;
  }
}

size_t VirtualLSMTree::PickSSTToCompact(int level) const {
  assert(!levels_[level].empty());

  // Simple policy: pick the SST with the largest size.
  size_t best = 0;
  uint64_t best_size = 0;
  for (size_t i = 0; i < levels_[level].size(); i++) {
    if (levels_[level][i].size_bytes > best_size) {
      best_size = levels_[level][i].size_bytes;
      best = i;
    }
  }
  return best;
}

std::vector<size_t> VirtualLSMTree::FindOverlappingSSTs(
    int level, const VirtualSST& target) const {
  std::vector<size_t> result;
  for (size_t i = 0; i < levels_[level].size(); i++) {
    const auto& sst = levels_[level][i];
    if (sst.key_max < target.key_min || sst.key_min > target.key_max) {
      continue;  // No overlap.
    }
    result.push_back(i);
  }
  return result;
}

void VirtualLSMTree::Log(const std::string& msg) {
  if (log_cb_) log_cb_(msg);
}

}  // namespace ROCKSDB_NAMESPACE
