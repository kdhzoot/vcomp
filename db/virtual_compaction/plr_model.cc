//  Copyright (c) 2011-present, Facebook, Inc.  All rights reserved.
//  This source code is licensed under both the GPLv2 (found in the
//  COPYING file in the root directory) and Apache 2.0 License
//  (found in the LICENSE.Apache file in the root directory).

#include "db/virtual_compaction/plr_model.h"

#include <algorithm>
#include <cassert>
#include <cmath>

namespace ROCKSDB_NAMESPACE {

// ── PLRModel ────────────────────────────────────────────────────────────────

double PLRModel::Predict(uint64_t key) const {
  if (discrete_model_) {
    return static_cast<double>(discrete_model_->CountLessThan(key));
  }
  if (segments_.empty()) return 0.0;

  auto eval = [](const PLRSegment& seg, uint64_t k) {
    return seg.slope * static_cast<double>(k) + seg.intercept;
  };

  // CDF semantics: gaps between sparse PLR segments contain no keys, so the
  // predicted rank must stay flat instead of extrapolating the next segment.
  size_t lo = 0, hi = segments_.size();
  while (lo < hi) {
    size_t mid = lo + (hi - lo) / 2;
    if (segments_[mid].key_end < key) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }

  if (lo >= segments_.size()) {
    return eval(segments_.back(), segments_.back().key_end);
  }

  const auto& seg = segments_[lo];
  if (key < seg.key_start) {
    if (lo == 0) return 0.0;
    const auto& prev = segments_[lo - 1];
    return eval(prev, prev.key_end);
  }

  return eval(seg, key);
}

uint64_t PLRModel::Inverse(double position) const {
  if (discrete_model_ && !discrete_model_->Empty()) {
    if (!(position > 0.0)) return discrete_model_->Select(0);
    const uint64_t last = discrete_model_->Count() - 1;
    if (static_cast<long double>(position) >= static_cast<long double>(last)) {
      return discrete_model_->Select(last);
    }
    return discrete_model_->Select(static_cast<uint64_t>(position));
  }
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
                      const std::vector<uint64_t>& key_maxs,
                      bool dedup,
                      uint64_t* adjusted_total) {
  assert(models.size() == num_entries.size());
  assert(models.size() == key_mins.size());
  assert(models.size() == key_maxs.size());

  const size_t N = models.size();
  if (N == 0) return PLRModel();

  // Step 1: Collect all breakpoints into a sorted unique vector.
  std::vector<uint64_t> breakpoints;
  {
    size_t total_bp = 0;
    for (size_t j = 0; j < N; j++) {
      total_bp += models[j]->NumSegments() * 2 + 2;
    }
    breakpoints.reserve(total_bp);
    for (size_t j = 0; j < N; j++) {
      breakpoints.push_back(key_mins[j]);
      breakpoints.push_back(key_maxs[j]);
      for (const auto& seg : models[j]->Segments()) {
        breakpoints.push_back(seg.key_start);
        breakpoints.push_back(seg.key_end);
      }
    }
    std::sort(breakpoints.begin(), breakpoints.end());
    breakpoints.erase(std::unique(breakpoints.begin(), breakpoints.end()),
                      breakpoints.end());
  }

  if (breakpoints.size() < 2) {
    double total = 0;
    for (size_t j = 0; j < N; j++) total += num_entries[j];
    PLRSegment seg = {breakpoints[0], breakpoints[0], 0.0, total / 2.0};
    return PLRModel({seg});
  }

  // Step 2: Sort models by key_min so we can track active set efficiently.
  // For each breakpoint interval, we only need to consider models whose
  // key range overlaps with the interval.

  // Sort model indices by key_min.
  std::vector<size_t> model_order(N);
  for (size_t j = 0; j < N; j++) model_order[j] = j;
  std::sort(model_order.begin(), model_order.end(),
            [&](size_t a, size_t b) { return key_mins[a] < key_mins[b]; });

  // Pre-compute total intercept contribution from all "finished" models
  // (models whose key_max < current breakpoint).
  // As we sweep breakpoints left-to-right, models transition:
  //   not-yet-started → active → finished
  // finished models contribute a constant num_entries[j] to intercept.

  std::vector<size_t> seg_idx(N, 0);
  std::vector<PLRSegment> merged;
  merged.reserve(breakpoints.size() - 1);

  size_t next_model = 0;  // Next model in model_order to activate.
  double finished_intercept = 0.0;
  // Track active models (those with key_min <= k_mid <= key_max).
  std::vector<size_t> active;
  active.reserve(N);

  for (size_t i = 0; i + 1 < breakpoints.size(); i++) {
    uint64_t k_start = breakpoints[i];
    uint64_t k_end = breakpoints[i + 1];
    uint64_t k_mid = k_start + (k_end - k_start) / 2;

    // Activate new models whose key_min <= k_mid.
    while (next_model < N &&
           key_mins[model_order[next_model]] <= k_mid) {
      active.push_back(model_order[next_model]);
      next_model++;
    }

    // Remove finished models (key_max < k_mid) from active list.
    size_t write = 0;
    for (size_t r = 0; r < active.size(); r++) {
      size_t j = active[r];
      if (key_maxs[j] < k_mid) {
        finished_intercept += static_cast<double>(num_entries[j]);
      } else {
        active[write++] = j;
      }
    }
    active.resize(write);

    // Sum slope/intercept from active segments only. A model can be active by
    // min/max while the current key interval falls into a sparse gap between
    // two PLR segments. That gap must contribute zero density; otherwise rank
    // mass is smeared into empty key space and output SST ranges become too
    // wide.
    double slope_sum = 0.0;
    double intercept_sum = finished_intercept;
    double dedup_prod = 1.0;

    for (size_t j : active) {
      const auto& segs = models[j]->Segments();
      while (seg_idx[j] + 1 < segs.size() &&
             segs[seg_idx[j]].key_end < k_mid) {
        seg_idx[j]++;
      }
      const auto& seg = segs[seg_idx[j]];
      if (k_mid < seg.key_start || k_mid > seg.key_end) {
        if (!dedup && k_mid > seg.key_end) {
          intercept_sum +=
              seg.slope * static_cast<double>(seg.key_end) + seg.intercept;
        } else if (!dedup && seg_idx[j] > 0) {
          const auto& prev = segs[seg_idx[j] - 1];
          intercept_sum += prev.slope * static_cast<double>(prev.key_end) +
                           prev.intercept;
        }
        continue;
      }
      slope_sum += seg.slope;
      intercept_sum += seg.intercept;
      if (dedup) {
        // Clamp slope to [0, 1] — it represents key density per unit key space.
        double s = std::min(std::max(seg.slope, 0.0), 1.0);
        dedup_prod *= (1.0 - s);
      }
    }

    if (dedup) {
      // Inclusion-exclusion: P(at least one) = 1 - Π(1 - P_i)
      double adj_slope = active.empty() ? 0.0 : 1.0 - dedup_prod;
      merged.push_back({k_start, k_end, adj_slope, 0.0});
    } else {
      merged.push_back({k_start, k_end, slope_sum, intercept_sum});
    }
  }

  // If dedup, recompute intercepts so the position function is continuous.
  // pos(k) = slope * k + intercept, starting from cumulative_pos = 0.
  if (dedup) {
    double cumulative_pos = 0.0;
    for (auto& seg : merged) {
      seg.intercept =
          cumulative_pos - seg.slope * static_cast<double>(seg.key_start);
      cumulative_pos +=
          seg.slope * static_cast<double>(seg.key_end - seg.key_start);
    }
    if (adjusted_total) {
      *adjusted_total = static_cast<uint64_t>(std::round(cumulative_pos));
    }
  }

  // Merge adjacent segments with identical slope/intercept.
  if (merged.size() > 1) {
    std::vector<PLRSegment> compacted;
    compacted.push_back(merged[0]);
    for (size_t i = 1; i < merged.size(); i++) {
      auto& prev = compacted.back();
      const auto& cur = merged[i];
      if (std::abs(prev.slope - cur.slope) < 1e-12 &&
          std::abs(prev.intercept - cur.intercept) < 1e-9) {
        // Same line — extend previous segment.
        prev.key_end = cur.key_end;
      } else {
        compacted.push_back(cur);
      }
    }
    merged = std::move(compacted);
  }

  return PLRModel(std::move(merged));
}

}  // namespace ROCKSDB_NAMESPACE
