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
                                      int target_level,
                                      const std::vector<uint64_t>& grandparent_boundaries) {
  std::vector<VirtualSST> result;

  if (total_entries == 0 || plr.Empty()) return result;

  uint64_t keys_per_sst = target_sst_size / avg_entry_size;
  if (keys_per_sst == 0) keys_per_sst = 1;

  // Build split positions.
  // Convert grandparent boundary keys to positions for split decisions.
  std::vector<uint64_t> gp_positions;
  if (!grandparent_boundaries.empty() && target_level > 0) {
    for (uint64_t bkey : grandparent_boundaries) {
      if (bkey <= global_min || bkey >= global_max) continue;
      double pos_d = plr.Predict(bkey);
      if (pos_d > 0 && pos_d < static_cast<double>(total_entries)) {
        gp_positions.push_back(static_cast<uint64_t>(std::round(pos_d)));
      }
    }
    std::sort(gp_positions.begin(), gp_positions.end());
    gp_positions.erase(
        std::unique(gp_positions.begin(), gp_positions.end()),
        gp_positions.end());
  }

  // Build split positions by scanning size-based boundaries and grandparent
  // boundaries together. At each grandparent boundary, split if current file
  // is >= 50% of target size (matching RocksDB's ShouldStopBefore heuristic).
  std::vector<uint64_t> split_positions;
  {
    uint64_t last_split = 0;
    uint64_t half_sst = keys_per_sst / 2;
    uint64_t next_size_split = keys_per_sst;
    size_t gp_idx = 0;

    while (next_size_split < total_entries || gp_idx < gp_positions.size()) {
      // Find the next candidate: either size-based or grandparent boundary.
      uint64_t next_gp = (gp_idx < gp_positions.size())
                             ? gp_positions[gp_idx]
                             : total_entries;

      if (next_size_split <= next_gp && next_size_split < total_entries) {
        // Size-based split comes first (or at same point).
        split_positions.push_back(next_size_split);
        last_split = next_size_split;
        next_size_split = last_split + keys_per_sst;
        // Skip grandparent boundaries we've passed.
        while (gp_idx < gp_positions.size() &&
               gp_positions[gp_idx] <= last_split) {
          gp_idx++;
        }
      } else if (next_gp < total_entries) {
        // Grandparent boundary: split here if current file >= 50% of target.
        if (next_gp - last_split >= half_sst) {
          split_positions.push_back(next_gp);
          last_split = next_gp;
          next_size_split = last_split + keys_per_sst;
        }
        gp_idx++;
      } else {
        break;
      }
    }
  }

  // Build SSTs from split positions.
  uint64_t num_ssts = split_positions.size() + 1;
  result.reserve(num_ssts);

  for (uint64_t i = 0; i < num_ssts; i++) {
    uint64_t pos_start = (i == 0) ? 0 : split_positions[i - 1];
    uint64_t pos_end = (i == num_ssts - 1) ? total_entries
                                            : split_positions[i];

    if (pos_start >= total_entries || pos_end <= pos_start) continue;

    uint64_t key_start = (i == 0) ? global_min
                                  : plr.Inverse(static_cast<double>(pos_start));
    uint64_t key_end = (i == num_ssts - 1)
                           ? global_max
                           : plr.Inverse(static_cast<double>(pos_end));

    // Ensure non-overlapping ranges between adjacent SSTs.
    if (i < num_ssts - 1 && key_end > key_start) {
      key_end = key_end - 1;
    }
    if (key_end < key_start) key_end = key_start;

    // Extract sub-PLR with local rank starting at 0.
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
  if (vsst.num_entries == 0 || vsst.plr_model.Empty()) return keys;
  keys.reserve(vsst.num_entries);

  // Walk segments linearly since positions are sequential (0, 1, 2, ...).
  // Much faster than calling Inverse() with its per-call search.
  const auto& segments = vsst.plr_model.Segments();
  size_t seg_idx = 0;

  for (uint64_t pos = 0; pos < vsst.num_entries; pos++) {
    double position = static_cast<double>(pos);

    // Advance segment if current one's position range is exceeded.
    while (seg_idx + 1 < segments.size()) {
      double pos_end = segments[seg_idx].slope *
                           static_cast<double>(segments[seg_idx].key_end) +
                       segments[seg_idx].intercept;
      if (position <= pos_end) break;
      seg_idx++;
    }

    const auto& seg = segments[seg_idx];
    uint64_t key;
    if (std::abs(seg.slope) < 1e-15) {
      key = (seg.key_start + seg.key_end) / 2;
    } else {
      double key_d = (position - seg.intercept) / seg.slope;
      key_d = std::max(key_d, static_cast<double>(seg.key_start));
      key_d = std::min(key_d, static_cast<double>(seg.key_end));
      key = static_cast<uint64_t>(std::round(key_d));
    }

    key = std::max(key, vsst.key_min);
    key = std::min(key, vsst.key_max);
    keys.push_back(key);
  }

  // Ensure strictly increasing. Clamp to key_max.
  for (size_t i = 1; i < keys.size(); i++) {
    if (keys[i] <= keys[i - 1]) {
      keys[i] = keys[i - 1] + 1;
    }
  }
  // If +1 accumulation pushed past key_max, cap and deduplicate.
  if (!keys.empty() && keys.back() > vsst.key_max) {
    for (size_t i = keys.size(); i > 0; i--) {
      if (keys[i - 1] > vsst.key_max) {
        keys[i - 1] = vsst.key_max;
      } else {
        break;
      }
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
