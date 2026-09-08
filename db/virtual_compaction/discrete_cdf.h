// Copyright (c) 2011-present, Facebook, Inc. All rights reserved.
// This source code is licensed under both the GPLv2 (found in the
// COPYING file in the root directory) and Apache 2.0 License
// (found in the LICENSE.Apache file in the root directory).
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <utility>
#include <vector>

#include "rocksdb/status.h"

namespace ROCKSDB_NAMESPACE {

// A certificate for an ordered set of integer keys. For an origin interval
// [a, a+C), mass m, and integer edge b, its local count CDF is
// floor(m * (b-a) / C). Its rank-t key is a + ceil((t+1)*C/m) - 1.
// All arithmetic involving the edge UINT64_MAX+1 uses unsigned 128-bit values.
class DiscreteCDF {
 public:
  struct Interval {
    uint64_t key_min = 0;
    uint64_t key_max = 0;
    uint64_t num_entries = 0;
  };

  struct Cell {
    // Extrema and count of the keys selected by this (possibly sliced) cell.
    uint64_t key_min = 0;
    uint64_t key_max = 0;
    uint64_t num_entries = 0;
    uint64_t prefix_entries = 0;

    // The original rational CDF and its retained rank interval. Slicing never
    // rescales these origin parameters or loses the original floor phase.
    uint64_t origin_key_min = 0;
    uint64_t origin_key_max = 0;
    uint64_t origin_num_entries = 0;
    uint64_t origin_rank_begin = 0;
  };

  class Cursor {
   public:
    Cursor() = default;

    // Returns false at the end or on error. End-of-stream leaves status() OK.
    // The cursor owns an immutable storage snapshot and can outlive its CDF.
    // Initialization uses division per cell; advancement uses only
    // quotient/remainder addition, comparison, and subtraction per key.
    bool Next(uint64_t* key);
    const Status& status() const { return status_; }

   private:
    friend class DiscreteCDF;
    explicit Cursor(std::shared_ptr<const std::vector<Cell>> cells)
        : cells_(std::move(cells)) {}
    void InitializeCell();

    std::shared_ptr<const std::vector<Cell>> cells_;
    size_t cell_index_ = 0;
    uint64_t emitted_in_cell_ = 0;
    uint64_t current_key_ = 0;
    unsigned __int128 step_quotient_ = 0;
    uint64_t step_remainder_ = 0;
    uint64_t remainder_ = 0;
    bool initialized_ = false;
    Status status_;
  };

  DiscreteCDF() = default;
  DiscreteCDF(const DiscreteCDF&) = default;
  DiscreteCDF& operator=(const DiscreteCDF&) = default;
  DiscreteCDF(DiscreteCDF&& other) noexcept
      : cells_(std::move(other.cells_)),
        count_(std::exchange(other.count_, 0)) {}
  DiscreteCDF& operator=(DiscreteCDF&& other) noexcept {
    if (this != &other) {
      cells_ = std::move(other.cells_);
      count_ = std::exchange(other.count_, 0);
    }
    return *this;
  }

  // Input intervals must have valid, ordered, disjoint inclusive bounds,
  // including zero-mass intervals. Zero-mass intervals are then omitted.
  // Rejects impossible capacities and total cardinality above UINT64_MAX.
  // On failure the existing CDF is unchanged.
  Status Assign(const std::vector<Interval>& intervals);

  uint64_t Count() const { return count_; }
  bool Empty() const { return count_ == 0; }
  const std::vector<Cell>& Cells() const;

  // Checked overload leaves *key unchanged on failure. The convenience
  // overload requires rank < Count(); use the checked API for external input.
  Status Select(uint64_t rank, uint64_t* key) const;
  uint64_t Select(uint64_t rank) const;

  uint64_t CountLessThan(uint64_t key) const;
  uint64_t CountThrough(uint64_t key) const;

  // A zero-length slice is valid at any first_rank <= Count(). Invalid slices
  // leave *output unchanged. Aliasing output with this is supported.
  Status Slice(uint64_t first_rank, uint64_t count, DiscreteCDF* output) const;
  // Convenience overload requires a valid slice; it does not throw on error.
  DiscreteCDF Slice(uint64_t first_rank, uint64_t count) const;

  Cursor NewCursor() const { return Cursor(cells_); }

 private:
  static uint64_t SelectInCell(const Cell& cell, uint64_t local_rank);
  uint64_t CountBeforeEdge(unsigned __int128 edge) const;

  std::shared_ptr<const std::vector<Cell>> cells_;
  uint64_t count_ = 0;
};

}  // namespace ROCKSDB_NAMESPACE
