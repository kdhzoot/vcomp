//  Copyright (c) 2011-present, Facebook, Inc.  All rights reserved.
//  This source code is licensed under both the GPLv2 (found in the
//  COPYING file in the root directory) and Apache 2.0 License
//  (found in the LICENSE.Apache file in the root directory).

#include "db/virtual_compaction/plr_model.h"

#include <algorithm>
#include <cassert>
#include <cmath>
#include <set>

namespace ROCKSDB_NAMESPACE {

// ── PLRModel ────────────────────────────────────────────────────────────────

double PLRModel::Predict(uint64_t key) const {
  if (segments_.empty()) return 0.0;
  const auto& seg = GetSegmentAt(key);
  return seg.slope * static_cast<double>(key) + seg.intercept;
}

uint64_t PLRModel::Inverse(double position) const {
  if (segments_.empty()) return 0;

  // For each segment, compute position range [pos_start, pos_end].
  // Find the segment whose range is closest to the target position.
  // This avoids the gap problem where position falls between two segments.

  size_t best_seg = 0;
  double best_dist = std::numeric_limits<double>::infinity();

  for (size_t i = 0; i < segments_.size(); i++) {
    const auto& seg = segments_[i];
    double pos_start = seg.slope * static_cast<double>(seg.key_start) +
                       seg.intercept;
    double pos_end = seg.slope * static_cast<double>(seg.key_end) +
                     seg.intercept;

    double lo = std::min(pos_start, pos_end);
    double hi = std::max(pos_start, pos_end);

    if (position >= lo && position <= hi) {
      // Exact match — use this segment.
      best_seg = i;
      break;
    }

    // Track closest segment by distance to its range.
    double dist = (position < lo) ? (lo - position) : (position - hi);
    if (dist < best_dist) {
      best_dist = dist;
      best_seg = i;
    }
  }

  const auto& seg = segments_[best_seg];
  if (std::abs(seg.slope) < 1e-15) {
    return (seg.key_start + seg.key_end) / 2;
  }
  double key_d = (position - seg.intercept) / seg.slope;
  key_d = std::max(key_d, static_cast<double>(seg.key_start));
  key_d = std::min(key_d, static_cast<double>(seg.key_end));
  return static_cast<uint64_t>(std::round(key_d));
}

const PLRSegment& PLRModel::GetSegmentAt(uint64_t key) const {
  assert(!segments_.empty());

  // Binary search for the segment covering 'key'.
  size_t lo = 0, hi = segments_.size();
  while (lo < hi) {
    size_t mid = lo + (hi - lo) / 2;
    if (segments_[mid].key_end < key) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }

  if (lo >= segments_.size()) return segments_.back();
  return segments_[lo];
}

uint64_t PLRModel::KeyMin() const {
  if (segments_.empty()) return 0;
  return segments_.front().key_start;
}

uint64_t PLRModel::KeyMax() const {
  if (segments_.empty()) return 0;
  return segments_.back().key_end;
}

// ── GreedyPLRFit ────────────────────────────────────────────────────────────

PLRModel GreedyPLRFit(const std::vector<uint64_t>& sorted_keys,
                      double error_bound) {
  std::vector<PLRSegment> segments;
  if (sorted_keys.empty()) return PLRModel(std::move(segments));

  const size_t n = sorted_keys.size();
  size_t seg_start = 0;

  while (seg_start < n) {
    // Single remaining point.
    if (seg_start == n - 1) {
      segments.push_back({sorted_keys[seg_start], sorted_keys[seg_start],
                          0.0, static_cast<double>(seg_start)});
      break;
    }

    double x0 = static_cast<double>(sorted_keys[seg_start]);
    double y0 = static_cast<double>(seg_start);

    // Slope bounds (shrinking cone).
    double s_lo = -std::numeric_limits<double>::infinity();
    double s_hi = std::numeric_limits<double>::infinity();

    size_t seg_end = seg_start;

    for (size_t i = seg_start + 1; i < n; i++) {
      double dx = static_cast<double>(sorted_keys[i]) - x0;
      double dy = static_cast<double>(i) - y0;

      if (dx <= 0) {
        // Duplicate key — extend without narrowing cone.
        seg_end = i;
        continue;
      }

      double new_s_lo = (dy - error_bound) / dx;
      double new_s_hi = (dy + error_bound) / dx;

      if (new_s_lo > s_hi || new_s_hi < s_lo) {
        // Can't extend — close segment at seg_end.
        break;
      }

      s_lo = std::max(s_lo, new_s_lo);
      s_hi = std::min(s_hi, new_s_hi);
      seg_end = i;
    }

    // Use midpoint of feasible slope range.
    double slope;
    if (std::isinf(s_lo) && std::isinf(s_hi)) {
      slope = 0.0;
    } else if (std::isinf(s_lo)) {
      slope = s_hi;
    } else if (std::isinf(s_hi)) {
      slope = s_lo;
    } else {
      slope = (s_lo + s_hi) / 2.0;
    }
    double intercept = y0 - slope * x0;

    segments.push_back({sorted_keys[seg_start], sorted_keys[seg_end],
                        slope, intercept});
    seg_start = seg_end + 1;
  }

  return PLRModel(std::move(segments));
}

// ── NWayMergePLR ────────────────────────────────────────────────────────────

PLRModel NWayMergePLR(const std::vector<const PLRModel*>& models,
                      const std::vector<uint64_t>& num_entries,
                      const std::vector<uint64_t>& key_mins,
                      const std::vector<uint64_t>& key_maxs) {
  assert(models.size() == num_entries.size());
  assert(models.size() == key_mins.size());
  assert(models.size() == key_maxs.size());

  const size_t N = models.size();
  if (N == 0) return PLRModel();

  // Step 1: Collect all breakpoints from all models.
  std::set<uint64_t> bp_set;
  for (size_t j = 0; j < N; j++) {
    bp_set.insert(key_mins[j]);
    bp_set.insert(key_maxs[j]);
    for (const auto& seg : models[j]->Segments()) {
      bp_set.insert(seg.key_start);
      bp_set.insert(seg.key_end);
    }
  }

  std::vector<uint64_t> breakpoints(bp_set.begin(), bp_set.end());
  // Already sorted by std::set.

  if (breakpoints.size() < 2) {
    // All models cover a single key — degenerate case.
    double total = 0;
    for (size_t j = 0; j < N; j++) total += num_entries[j];
    PLRSegment seg = {breakpoints[0], breakpoints[0], 0.0, total / 2.0};
    return PLRModel({seg});
  }

  // Step 2: For each sub-interval, sum slope/intercept from all models.
  std::vector<PLRSegment> merged;
  merged.reserve(breakpoints.size() - 1);

  for (size_t i = 0; i + 1 < breakpoints.size(); i++) {
    uint64_t k_start = breakpoints[i];
    uint64_t k_end = breakpoints[i + 1];
    // Representative key in this interval.
    uint64_t k_mid = k_start + (k_end - k_start) / 2;

    double slope_sum = 0.0;
    double intercept_sum = 0.0;

    for (size_t j = 0; j < N; j++) {
      if (k_mid < key_mins[j]) {
        // Before this model's range — contributes 0.
        continue;
      } else if (k_mid > key_maxs[j]) {
        // After this model's range — all entries are before this key.
        intercept_sum += static_cast<double>(num_entries[j]);
      } else {
        // Within range — add segment's slope/intercept.
        const auto& seg = models[j]->GetSegmentAt(k_mid);
        slope_sum += seg.slope;
        intercept_sum += seg.intercept;
      }
    }

    merged.push_back({k_start, k_end, slope_sum, intercept_sum});
  }

  return PLRModel(std::move(merged));
}

}  // namespace ROCKSDB_NAMESPACE
