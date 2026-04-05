//  Copyright (c) 2011-present, Facebook, Inc.  All rights reserved.
//  This source code is licensed under both the GPLv2 (found in the
//  COPYING file in the root directory) and Apache 2.0 License
//  (found in the LICENSE.Apache file in the root directory).

#include "db/virtual_compaction/virtual_sst.h"

#include <algorithm>
#include <cassert>
#include <cmath>

namespace ROCKSDB_NAMESPACE {

std::vector<VirtualSST> SplitIntoSSTs(const PLRModel& plr,
                                      uint64_t total_entries,
                                      uint64_t target_sst_size,
                                      uint64_t avg_entry_size,
                                      uint64_t global_min,
                                      uint64_t global_max,
                                      int target_level) {
  std::vector<VirtualSST> result;

  if (total_entries == 0 || plr.Empty()) return result;

  uint64_t keys_per_sst = target_sst_size / avg_entry_size;
  if (keys_per_sst == 0) keys_per_sst = 1;

  uint64_t num_ssts = total_entries / keys_per_sst;
  if (num_ssts == 0) num_ssts = 1;
  // If remainder is significant, add one more SST.
  if (total_entries % keys_per_sst > keys_per_sst / 4) {
    num_ssts++;
  }

  result.reserve(num_ssts);

  for (uint64_t i = 0; i < num_ssts; i++) {
    uint64_t pos_start = i * keys_per_sst;
    uint64_t pos_end = std::min((i + 1) * keys_per_sst, total_entries);

    if (pos_start >= total_entries) break;

    uint64_t key_start = (i == 0) ? global_min
                                  : plr.Inverse(static_cast<double>(pos_start));
    uint64_t key_end = (i == num_ssts - 1)
                           ? global_max
                           : plr.Inverse(static_cast<double>(pos_end));

    // Ensure key ordering.
    if (key_end < key_start) key_end = key_start;

    // Extract sub-PLR: collect segments that overlap [key_start, key_end],
    // and adjust intercepts so that local rank starts at 0.
    std::vector<PLRSegment> sub_segments;
    for (const auto& seg : plr.Segments()) {
      if (seg.key_end < key_start) continue;
      if (seg.key_start > key_end) break;

      PLRSegment s = seg;
      s.key_start = std::max(s.key_start, key_start);
      s.key_end = std::min(s.key_end, key_end);
      s.intercept -= static_cast<double>(pos_start);
      sub_segments.push_back(s);
    }

    uint64_t n_entries = pos_end - pos_start;

    VirtualSST vsst;
    vsst.plr_model = PLRModel(std::move(sub_segments));
    vsst.key_min = key_start;
    vsst.key_max = key_end;
    vsst.num_entries = n_entries;
    vsst.level = target_level;
    vsst.size_bytes = VirtualSST::EstimateSize(n_entries, avg_entry_size);

    result.push_back(std::move(vsst));
  }

  return result;
}

std::vector<uint64_t> MaterializeKeys(const VirtualSST& vsst) {
  std::vector<uint64_t> keys;
  keys.reserve(vsst.num_entries);

  for (uint64_t pos = 0; pos < vsst.num_entries; pos++) {
    uint64_t key = vsst.plr_model.Inverse(static_cast<double>(pos));
    // Clamp to valid range.
    key = std::max(key, vsst.key_min);
    key = std::min(key, vsst.key_max);
    keys.push_back(key);
  }

  // Ensure strictly increasing (PLR inverse might produce duplicates).
  for (size_t i = 1; i < keys.size(); i++) {
    if (keys[i] <= keys[i - 1]) {
      keys[i] = keys[i - 1] + 1;
    }
  }

  return keys;
}

std::vector<VirtualSST> VirtualCompact(
    const std::vector<const VirtualSST*>& inputs,
    uint64_t target_sst_size,
    uint64_t avg_entry_size,
    int output_level) {
  if (inputs.empty()) return {};

  // Gather info for N-way merge.
  std::vector<const PLRModel*> models;
  std::vector<uint64_t> num_entries;
  std::vector<uint64_t> key_mins;
  std::vector<uint64_t> key_maxs;
  models.reserve(inputs.size());
  num_entries.reserve(inputs.size());
  key_mins.reserve(inputs.size());
  key_maxs.reserve(inputs.size());

  uint64_t total_entries = 0;
  uint64_t global_min = std::numeric_limits<uint64_t>::max();
  uint64_t global_max = 0;

  for (const auto* vsst : inputs) {
    models.push_back(&vsst->plr_model);
    num_entries.push_back(vsst->num_entries);
    key_mins.push_back(vsst->key_min);
    key_maxs.push_back(vsst->key_max);
    total_entries += vsst->num_entries;
    global_min = std::min(global_min, vsst->key_min);
    global_max = std::max(global_max, vsst->key_max);
  }

  // N-way PLR merge.
  PLRModel merged = NWayMergePLR(models, num_entries, key_mins, key_maxs);

  // Split into output SSTs.
  return SplitIntoSSTs(merged, total_entries, target_sst_size, avg_entry_size,
                       global_min, global_max, output_level);
}

}  // namespace ROCKSDB_NAMESPACE
