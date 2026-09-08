//  Copyright (c) 2011-present, Facebook, Inc.  All rights reserved.
//  This source code is licensed under both the GPLv2 (found in the
//  COPYING file in the root directory) and Apache 2.0 License
//  (found in the LICENSE.Apache file in the root directory).
#pragma once

#include <cstdint>
#include <limits>
#include <memory>
#include <vector>

#include "db/virtual_compaction/discrete_cdf.h"
#include "rocksdb/rocksdb_namespace.h"

namespace ROCKSDB_NAMESPACE {

// A single linear segment in a Piecewise Linear Regression model.
// Represents: pos(key) = slope * key + intercept, for key in [key_start, key_end].
struct PLRSegment {
  uint64_t key_start;
  uint64_t key_end;
  double slope;
  double intercept;
};

// Piecewise Linear Regression model that approximates the CDF of keys
// in a sorted sequence. Each segment maps key → rank (position).
//
// Predict(key): returns the estimated rank of a key.
// Inverse(rank): returns the estimated key at a given rank.
class PLRModel {
 public:
  PLRModel() = default;
  explicit PLRModel(std::vector<PLRSegment> segments)
      : segments_(std::move(segments)) {}

  // Predict the rank (position) of a key. O(log S) via binary search.
  // Returns 0 for keys below range, total_entries for keys above range.
  double Predict(uint64_t key) const;

  // Inverse: given a rank (position), return the estimated key.
  // key = (rank - intercept) / slope
  uint64_t Inverse(double position) const;

  // Return the segment that covers the given key.
  // If key is out of range, returns the first or last segment.
  const PLRSegment& GetSegmentAt(uint64_t key) const;

  size_t NumSegments() const { return segments_.size(); }
  bool Empty() const {
    return discrete_model_ ? discrete_model_->Empty() : segments_.empty();
  }

  const std::vector<PLRSegment>& Segments() const { return segments_; }

  // Exact count/select certificate for the reconstructed model. It does not
  // claim exact membership of the original logical input keys.
  const DiscreteCDF* DiscreteModel() const { return discrete_model_.get(); }
  void SetDiscreteModel(DiscreteCDF model) {
    discrete_model_ = std::make_shared<const DiscreteCDF>(std::move(model));
  }

  // Key range of the entire model.
  uint64_t KeyMin() const;
  uint64_t KeyMax() const;

 private:
  std::vector<PLRSegment> segments_;
  std::shared_ptr<const DiscreteCDF> discrete_model_;
};

// Build a PLR model from sorted keys using the Greedy-PLR algorithm.
// Keys must be sorted in ascending order.
// error_bound: maximum allowed rank prediction error (δ).
PLRModel GreedyPLRFit(const std::vector<uint64_t>& sorted_keys,
                      double error_bound);

// Merge N PLR models into one.
// The merged model represents the CDF of the merged sorted sequence.
// pos_merged(x) = sum of pos_i(x) for all models.
//
// When dedup=true, applies probabilistic dedup correction per breakpoint:
//   adjusted_slope = 1 - Π(1 - slope_i)   (inclusion-exclusion)
// This estimates unique key density assuming independent random placement.
// If adjusted_total is non-null, the estimated unique entry count is stored.
PLRModel NWayMergePLR(const std::vector<const PLRModel*>& models,
                      const std::vector<uint64_t>& num_entries,
                      const std::vector<uint64_t>& key_mins,
                      const std::vector<uint64_t>& key_maxs,
                      bool dedup = false,
                      uint64_t* adjusted_total = nullptr);

}  // namespace ROCKSDB_NAMESPACE
