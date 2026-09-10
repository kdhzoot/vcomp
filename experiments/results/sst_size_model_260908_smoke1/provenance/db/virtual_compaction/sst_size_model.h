// Copyright (c) 2011-present, Facebook, Inc. All rights reserved.
// This source code is licensed under both the GPLv2 (found in the
// COPYING file in the root directory) and Apache 2.0 License
// (found in the LICENSE.Apache file in the root directory).
#pragma once

#include <algorithm>
#include <cstdint>
#include <limits>
#include <vector>

#include "rocksdb/rocksdb_namespace.h"

namespace ROCKSDB_NAMESPACE {

// An upper envelope of two-point, rational affine SST size estimates. Each
// calibration contributes a per-entry slope plus nonnegative per-file bytes.
// Keeping the slope fractional avoids rounding every entry up to a whole byte.
// The model is immutable while used by virtual compaction workers: construct it
// before publishing it through the registry.
class SSTSizeModel {
 public:
  SSTSizeModel() = default;

  // Compatibility model for callers that know only logical bytes per entry.
  // Zero bytes per entry is invalid, rather than a zero-cost valid model.
  static SSTSizeModel Logical(uint64_t bytes_per_entry) {
    SSTSizeModel model;
    if (bytes_per_entry != 0) {
      model.lines_.push_back({bytes_per_entry, 1, 0});
    }
    return model;
  }

  // Fit a line through two completed sample files. Round a positive intercept
  // upward and clamp a negative intercept to zero, so both samples are covered.
  // Additional calibrations form an envelope, not an average. Invalid samples
  // leave the previously constructed model unchanged.
  bool AddCalibration(uint64_t n1, uint64_t bytes1, uint64_t n2,
                      uint64_t bytes2) {
    if (n1 == 0 || n2 <= n1 || bytes2 <= bytes1) return false;
    const uint64_t numerator = bytes2 - bytes1;
    const uint64_t denominator = n2 - n1;
    const Wide intercept_left = static_cast<Wide>(bytes1) * denominator;
    const Wide intercept_right = static_cast<Wide>(n1) * numerator;
    uint64_t fixed_bytes = 0;
    if (intercept_left > intercept_right) {
      const Wide difference = intercept_left - intercept_right;
      // The intercept is <= bytes1, so its rounded value fits uint64_t.
      fixed_bytes = static_cast<uint64_t>(difference / denominator +
                                         (difference % denominator != 0));
    }
    lines_.push_back({numerator, denominator, fixed_bytes});
    return true;
  }

  bool Valid() const { return !lines_.empty(); }

  // Empty descriptors cost zero. All nonempty estimates saturate at UINT64_MAX
  // instead of wrapping; an invalid model returns zero and must be rejected by
  // callers before it is used for planning.
  uint64_t Estimate(uint64_t entries) const {
    if (entries == 0) return 0;
    uint64_t result = 0;
    for (const auto& line : lines_) {
      const Wide product = static_cast<Wide>(entries) * line.numerator;
      const Wide variable_bytes = product / line.denominator +
                                  (product % line.denominator != 0);
      const uint64_t available = Max() - line.fixed_bytes;
      const uint64_t estimate = variable_bytes > available
                                    ? Max()
                                    : static_cast<uint64_t>(variable_bytes) +
                                          line.fixed_bytes;
      result = std::max(result, estimate);
    }
    return result;
  }

  // Largest representable count whose (saturating) estimate fits the target.
  // This is an exact inverse, including fractional slopes and file overhead.
  // At UINT64_MAX every saturated estimate fits, so the maximum count also fits.
  uint64_t MaxEntries(uint64_t target_bytes) const {
    if (!Valid()) return 0;
    if (target_bytes == Max()) return Max();
    uint64_t result = Max();
    for (const auto& line : lines_) {
      if (target_bytes < line.fixed_bytes) return 0;
      const Wide product = static_cast<Wide>(target_bytes - line.fixed_bytes) *
                           line.denominator;
      const Wide capacity = product / line.numerator;
      const uint64_t bounded = capacity > Max()
                                   ? Max()
                                   : static_cast<uint64_t>(capacity);
      result = std::min(result, bounded);
    }
    return result;
  }

 private:
  using Wide = unsigned __int128;

  struct Line {
    uint64_t numerator;
    uint64_t denominator;
    uint64_t fixed_bytes;
  };

  static constexpr uint64_t Max() {
    return std::numeric_limits<uint64_t>::max();
  }

  std::vector<Line> lines_;
};

}  // namespace ROCKSDB_NAMESPACE
