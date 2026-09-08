// Copyright (c) 2011-present, Facebook, Inc. All rights reserved.
// This source code is licensed under both the GPLv2 (found in the
// COPYING file in the root directory) and Apache 2.0 License
// (found in the LICENSE.Apache file in the root directory).

#include "db/virtual_compaction/discrete_cdf.h"

#include <algorithm>
#include <cassert>
#include <limits>
#include <utility>

namespace ROCKSDB_NAMESPACE {

namespace {
using Wide = unsigned __int128;

Wide Span(uint64_t key_min, uint64_t key_max) {
  return static_cast<Wide>(key_max) - static_cast<Wide>(key_min) + 1;
}
}  // namespace

const std::vector<DiscreteCDF::Cell>& DiscreteCDF::Cells() const {
  static const std::vector<Cell> empty;
  return cells_ ? *cells_ : empty;
}

uint64_t DiscreteCDF::SelectInCell(const Cell& cell, uint64_t local_rank) {
  const Wide rank = static_cast<Wide>(cell.origin_rank_begin) + local_rank;
  const Wide width = Span(cell.origin_key_min, cell.origin_key_max);
  // floor((x-1)/m) equals ceil(x/m)-1 for positive x. The product fits:
  // rank+1 <= m <= UINT64_MAX and width <= 2^64.
  const Wide offset = ((rank + 1) * width - 1) / cell.origin_num_entries;
  return static_cast<uint64_t>(static_cast<Wide>(cell.origin_key_min) + offset);
}

Status DiscreteCDF::Assign(const std::vector<Interval>& intervals) {
  auto cells = std::make_shared<std::vector<Cell>>();
  cells->reserve(intervals.size());
  Wide total = 0;
  bool have_previous = false;
  uint64_t previous_max = 0;
  for (const auto& interval : intervals) {
    if (interval.key_max < interval.key_min) {
      return Status::InvalidArgument("DiscreteCDF: reversed interval bounds");
    }
    if (have_previous && interval.key_min <= previous_max) {
      return Status::InvalidArgument(
          "DiscreteCDF: intervals overlap or are unordered");
    }
    have_previous = true;
    previous_max = interval.key_max;
    if (static_cast<Wide>(interval.num_entries) >
        Span(interval.key_min, interval.key_max)) {
      return Status::InvalidArgument(
          "DiscreteCDF: mass exceeds interval capacity");
    }
    if (interval.num_entries == 0) continue;
    const Wide next_total = total + interval.num_entries;
    if (next_total > std::numeric_limits<uint64_t>::max()) {
      return Status::InvalidArgument(
          "DiscreteCDF: total cardinality overflows uint64");
    }
    Cell cell;
    cell.num_entries = interval.num_entries;
    cell.prefix_entries = static_cast<uint64_t>(total);
    cell.origin_key_min = interval.key_min;
    cell.origin_key_max = interval.key_max;
    cell.origin_num_entries = interval.num_entries;
    cell.key_min = SelectInCell(cell, 0);
    cell.key_max = SelectInCell(cell, interval.num_entries - 1);
    cells->push_back(cell);
    total = next_total;
  }
  cells_ = std::move(cells);
  count_ = static_cast<uint64_t>(total);
  return Status::OK();
}

Status DiscreteCDF::Select(uint64_t rank, uint64_t* key) const {
  if (key == nullptr) {
    return Status::InvalidArgument("DiscreteCDF: null Select output");
  }
  if (rank >= count_) {
    return Status::InvalidArgument("DiscreteCDF: rank is outside the CDF");
  }
  const auto& cells = Cells();
  size_t lo = 0;
  size_t hi = cells.size();
  while (lo < hi) {
    const size_t mid = lo + (hi - lo) / 2;
    const auto& cell = cells[mid];
    if (rank >= cell.prefix_entries + cell.num_entries) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  assert(lo < cells.size());
  *key = SelectInCell(cells[lo], rank - cells[lo].prefix_entries);
  return Status::OK();
}

uint64_t DiscreteCDF::Select(uint64_t rank) const {
  uint64_t key = 0;
  Status status = Select(rank, &key);
  assert(status.ok());
  (void)status;
  return key;
}

uint64_t DiscreteCDF::CountBeforeEdge(Wide edge) const {
  const auto& cells = Cells();
  size_t lo = 0;
  size_t hi = cells.size();
  while (lo < hi) {
    const size_t mid = lo + (hi - lo) / 2;
    if (static_cast<Wide>(cells[mid].key_max) < edge) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  if (lo == cells.size()) return count_;
  const auto& cell = cells[lo];
  if (edge <= static_cast<Wide>(cell.key_min)) return cell.prefix_entries;
  const Wide width = Span(cell.origin_key_min, cell.origin_key_max);
  const Wide origin_count =
      (static_cast<Wide>(cell.origin_num_entries) *
       (edge - static_cast<Wide>(cell.origin_key_min))) / width;
  const Wide begin = cell.origin_rank_begin;
  const Wide end = begin + cell.num_entries;
  const Wide clipped = std::max(begin, std::min(end, origin_count));
  return cell.prefix_entries + static_cast<uint64_t>(clipped - begin);
}

uint64_t DiscreteCDF::CountLessThan(uint64_t key) const {
  return CountBeforeEdge(static_cast<Wide>(key));
}

uint64_t DiscreteCDF::CountThrough(uint64_t key) const {
  return CountBeforeEdge(static_cast<Wide>(key) + 1);
}

Status DiscreteCDF::Slice(uint64_t first_rank, uint64_t count,
                          DiscreteCDF* output) const {
  if (output == nullptr) {
    return Status::InvalidArgument("DiscreteCDF: null Slice output");
  }
  if (first_rank > count_ || count > count_ - first_rank) {
    return Status::InvalidArgument("DiscreteCDF: slice is outside the CDF");
  }
  DiscreteCDF result;
  if (count == 0) {
    *output = std::move(result);
    return Status::OK();
  }
  auto sliced = std::make_shared<std::vector<Cell>>();
  const auto& cells = Cells();
  const uint64_t end_rank = first_rank + count;
  uint64_t prefix = 0;
  size_t lo = 0;
  size_t hi = cells.size();
  while (lo < hi) {
    const size_t mid = lo + (hi - lo) / 2;
    if (cells[mid].prefix_entries + cells[mid].num_entries <= first_rank) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  for (size_t i = lo; i < cells.size(); ++i) {
    const auto& cell = cells[i];
    const uint64_t cell_end = cell.prefix_entries + cell.num_entries;
    if (cell.prefix_entries >= end_rank) break;
    const uint64_t begin = std::max(first_rank, cell.prefix_entries);
    const uint64_t end = std::min(end_rank, cell_end);
    Cell child = cell;
    child.origin_rank_begin += begin - cell.prefix_entries;
    child.num_entries = end - begin;
    child.prefix_entries = prefix;
    child.key_min = SelectInCell(child, 0);
    child.key_max = SelectInCell(child, child.num_entries - 1);
    sliced->push_back(child);
    prefix += child.num_entries;
  }
  assert(prefix == count);
  result.cells_ = std::move(sliced);
  result.count_ = count;
  *output = std::move(result);
  return Status::OK();
}

DiscreteCDF DiscreteCDF::Slice(uint64_t first_rank, uint64_t count) const {
  DiscreteCDF result;
  Status status = Slice(first_rank, count, &result);
  assert(status.ok());
  (void)status;
  return result;
}

void DiscreteCDF::Cursor::InitializeCell() {
  const auto& cell = (*cells_)[cell_index_];
  const Wide width = Span(cell.origin_key_min, cell.origin_key_max);
  const Wide numerator =
      (static_cast<Wide>(cell.origin_rank_begin) + 1) * width - 1;
  current_key_ = static_cast<uint64_t>(
      static_cast<Wide>(cell.origin_key_min) + numerator / cell.origin_num_entries);
  remainder_ = static_cast<uint64_t>(numerator % cell.origin_num_entries);
  step_quotient_ = width / cell.origin_num_entries;
  step_remainder_ = static_cast<uint64_t>(width % cell.origin_num_entries);
  emitted_in_cell_ = 0;
  initialized_ = true;
}

bool DiscreteCDF::Cursor::Next(uint64_t* key) {
  if (!status_.ok()) return false;
  if (key == nullptr) {
    status_ = Status::InvalidArgument("DiscreteCDF: null Cursor output");
    return false;
  }
  if (!cells_ || cell_index_ == cells_->size()) return false;
  if (!initialized_) InitializeCell();
  const auto& cell = (*cells_)[cell_index_];
  *key = current_key_;
  ++emitted_in_cell_;
  if (emitted_in_cell_ == cell.num_entries) {
    ++cell_index_;
    initialized_ = false;
  } else {
    Wide remainder = static_cast<Wide>(remainder_) + step_remainder_;
    const bool carry = remainder >= cell.origin_num_entries;
    if (carry) remainder -= cell.origin_num_entries;
    remainder_ = static_cast<uint64_t>(remainder);
    const Wide next = static_cast<Wide>(current_key_) + step_quotient_ + carry;
    assert(next <= cell.key_max);
    current_key_ = static_cast<uint64_t>(next);
  }
  return true;
}

}  // namespace ROCKSDB_NAMESPACE
