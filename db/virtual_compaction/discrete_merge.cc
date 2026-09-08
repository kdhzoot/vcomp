// Copyright (c) 2011-present, Facebook, Inc. All rights reserved.
// This source code is licensed under both the GPLv2 (found in the
// COPYING file in the root directory) and Apache 2.0 License
// (found in the LICENSE.Apache file in the root directory).

#include "db/virtual_compaction/discrete_merge.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <utility>

#include "db/virtual_compaction/discrete_cdf.h"
#include "db/virtual_compaction/plr_model.h"
#include "db/virtual_compaction/virtual_sst.h"

namespace ROCKSDB_NAMESPACE {
namespace {
using Wide = unsigned __int128;

struct BaseInterval {
  Wide begin;
  Wide end;
  long double weight;
  bool active;
};

struct Part {
  Wide begin;
  Wide end;
  long double weight;
  uint64_t mass;
  bool witness;
  bool saturated = false;
};

// Allocate free mass. Saturation is metadata-sized work. The final correction
// visits intervals, never individual keys, even if numerical residuals are large.
Status Allocate(std::vector<Part>* parts, uint64_t free_mass) {
  std::vector<size_t> active;
  active.reserve(parts->size());
  for (size_t i = 0; i < parts->size(); ++i) {
    if (!(*parts)[i].witness && (*parts)[i].begin < (*parts)[i].end) {
      active.push_back(i);
    }
  }
  uint64_t remaining = free_mass;
  while (remaining > 0) {
    if (active.empty()) {
      return Status::InvalidArgument("Discrete merge: insufficient free capacity");
    }
    long double weight_sum = 0.0L;
    for (size_t i : active) weight_sum += (*parts)[i].weight;
    if (weight_sum == 0.0L) {
      // Samples can provide no weight in still-admissible support. This is an
      // explicit capacity-proportional allocation policy, not a legacy model.
      for (size_t i : active) {
        auto& part = (*parts)[i];
        part.weight = static_cast<long double>(part.end - part.begin);
        weight_sum += part.weight;
      }
    }
    if (!(weight_sum > 0.0L) || !std::isfinite(weight_sum)) {
      return Status::InvalidArgument("Discrete merge: invalid allocation weights");
    }

    Wide saturation_mass = 0;
    std::vector<size_t> saturating;
    for (size_t i : active) {
      const auto& part = (*parts)[i];
      const Wide capacity = part.end - part.begin;
      const long double quota = static_cast<long double>(remaining) *
                                (part.weight / weight_sum);
      if (capacity <= remaining &&
          quota >= static_cast<long double>(capacity)) {
        saturating.push_back(i);
        saturation_mass += capacity;
      }
    }
    if (!saturating.empty() && saturation_mass <= remaining) {
      for (size_t i : saturating) {
        auto& part = (*parts)[i];
        part.mass = static_cast<uint64_t>(part.end - part.begin);
        part.saturated = true;
      }
      remaining -= static_cast<uint64_t>(saturation_mass);
      active.erase(std::remove_if(active.begin(), active.end(),
                                  [&](size_t i) { return (*parts)[i].saturated; }),
                   active.end());
      continue;
    }

    struct Remainder {
      size_t index;
      long double fraction;
    };
    std::vector<Remainder> remainders;
    remainders.reserve(active.size());
    Wide allocated = 0;
    for (size_t i : active) {
      auto& part = (*parts)[i];
      const Wide capacity = part.end - part.begin;
      const Wide limit = std::min<Wide>(capacity, remaining);
      const long double quota = std::min(
          static_cast<long double>(capacity),
          static_cast<long double>(remaining) * (part.weight / weight_sum));
      if (!std::isfinite(quota) || quota < 0.0L) {
        return Status::InvalidArgument("Discrete merge: invalid fractional quota");
      }
      part.mass = quota >= static_cast<long double>(limit)
                      ? static_cast<uint64_t>(limit)
                      : static_cast<uint64_t>(std::floor(quota));
      allocated += part.mass;
      remainders.push_back({i, quota - static_cast<long double>(part.mass)});
    }

    const bool adding = allocated <= remaining;
    Wide residual = adding ? static_cast<Wide>(remaining) - allocated
                           : allocated - remaining;
    std::sort(remainders.begin(), remainders.end(),
              [&](const Remainder& a, const Remainder& b) {
                if (a.fraction != b.fraction) {
                  return adding ? a.fraction > b.fraction
                                : a.fraction < b.fraction;
                }
                return (*parts)[a.index].begin < (*parts)[b.index].begin;
              });
    // Largest-remainder rounding normally needs at most one unit per cell.
    for (const auto& r : remainders) {
      if (residual == 0) break;
      auto& part = (*parts)[r.index];
      const Wide capacity = part.end - part.begin;
      if (adding && static_cast<Wide>(part.mass) < capacity) {
        ++part.mass;
        --residual;
      } else if (!adding && part.mass > 0) {
        --part.mass;
        --residual;
      }
    }
    // Correct any larger floating-point residual using bounded integer chunks.
    for (const auto& r : remainders) {
      if (residual == 0) break;
      auto& part = (*parts)[r.index];
      const Wide available = adding ? part.end - part.begin - part.mass
                                    : static_cast<Wide>(part.mass);
      const uint64_t amount = static_cast<uint64_t>(std::min(residual, available));
      if (adding) {
        part.mass += amount;
      } else {
        part.mass -= amount;
      }
      residual -= amount;
    }
    if (residual != 0) {
      return Status::InvalidArgument("Discrete merge: integer residual is infeasible");
    }
    remaining = 0;
  }
  Wide assigned = 0;
  for (const auto& part : *parts) {
    if (!part.witness) assigned += part.mass;
    if (static_cast<Wide>(part.mass) > part.end - part.begin) {
      return Status::InvalidArgument("Discrete merge: allocation exceeds capacity");
    }
  }
  if (assigned != free_mass) {
    return Status::InvalidArgument("Discrete merge: allocation count mismatch");
  }
  return Status::OK();
}
}  // namespace

Status BuildDiscreteMergeModel(const std::vector<const VirtualSST*>& inputs,
                               uint64_t requested_total, PLRModel* output,
                               uint64_t* accepted_total) {
  if (output == nullptr || accepted_total == nullptr) {
    return Status::InvalidArgument("Discrete merge: null output argument");
  }
  std::vector<const VirtualSST*> nonempty;
  std::vector<Wide> edges;
  std::vector<uint64_t> witnesses;
  Wide input_sum = 0;
  for (const auto* input : inputs) {
    if (input == nullptr) {
      return Status::InvalidArgument("Discrete merge: null input descriptor");
    }
    if (input->num_entries == 0) continue;
    if (input->key_max < input->key_min) {
      return Status::InvalidArgument("Discrete merge: reversed input bounds");
    }
    input_sum += input->num_entries;
    if (input_sum > std::numeric_limits<uint64_t>::max()) {
      return Status::InvalidArgument("Discrete merge: input count overflows uint64");
    }
    nonempty.push_back(input);
    edges.push_back(input->key_min);
    edges.push_back(static_cast<Wide>(input->key_max) + 1);
    witnesses.push_back(input->key_min);
    witnesses.push_back(input->key_max);
    for (const auto& sample : input->kmv_sketch.samples) {
      if (sample.key < input->key_min || sample.key > input->key_max) {
        return Status::InvalidArgument("Discrete merge: global sample outside input");
      }
      witnesses.push_back(sample.key);
    }
    for (const auto& range : input->kmv_ranges) {
      if (range.key_max < range.key_min || range.key_min < input->key_min ||
          range.key_max > input->key_max) {
        return Status::InvalidArgument("Discrete merge: invalid range bucket bounds");
      }
      edges.push_back(range.key_min);
      edges.push_back(static_cast<Wide>(range.key_max) + 1);
      for (const auto& sample : range.sketch.samples) {
        if (sample.key < range.key_min || sample.key > range.key_max) {
          return Status::InvalidArgument("Discrete merge: sample outside range bucket");
        }
        witnesses.push_back(sample.key);
      }
    }
    if (input->plr_model.DiscreteModel() == nullptr) {
      for (const auto& segment : input->plr_model.Segments()) {
        if (segment.key_end < segment.key_start) {
          return Status::InvalidArgument("Discrete merge: reversed PLR segment");
        }
        const uint64_t begin = std::max(segment.key_start, input->key_min);
        const uint64_t end = std::min(segment.key_end, input->key_max);
        if (begin <= end) {
          edges.push_back(begin);
          edges.push_back(static_cast<Wide>(end) + 1);
        }
      }
    }
  }
  std::sort(edges.begin(), edges.end());
  edges.erase(std::unique(edges.begin(), edges.end()), edges.end());
  std::sort(witnesses.begin(), witnesses.end());
  witnesses.erase(std::unique(witnesses.begin(), witnesses.end()), witnesses.end());
  std::sort(nonempty.begin(), nonempty.end(),
            [](const VirtualSST* a, const VirtualSST* b) {
              return a->key_min < b->key_min;
            });

  std::vector<BaseInterval> bases;
  if (!edges.empty()) bases.reserve(edges.size() - 1);
  std::vector<const VirtualSST*> active;
  size_t next_input = 0;
  Wide total_capacity = 0;
  for (size_t i = 0; i + 1 < edges.size(); ++i) {
    const Wide begin = edges[i];
    const Wide end = edges[i + 1];
    while (next_input < nonempty.size() &&
           static_cast<Wide>(nonempty[next_input]->key_min) <= begin) {
      active.push_back(nonempty[next_input++]);
    }
    active.erase(std::remove_if(active.begin(), active.end(),
                                [&](const VirtualSST* input) {
                                  return static_cast<Wide>(input->key_max) + 1 <= begin;
                                }),
                 active.end());
    const bool supported = !active.empty();
    uint64_t estimate = 0;
    if (supported) {
      total_capacity += end - begin;
      estimate = EstimateKMVUnionEntriesForRange(
          active, static_cast<uint64_t>(begin), static_cast<uint64_t>(end - 1));
    }
    bases.push_back({begin, end, static_cast<long double>(estimate), supported});
  }

  const Wide witness_count = witnesses.size();
  if (witness_count > total_capacity) {
    return Status::InvalidArgument("Discrete merge: witnesses exceed support capacity");
  }
  const Wide target = std::max(witness_count,
                               std::min<Wide>(requested_total, total_capacity));
  if (target > input_sum || target > std::numeric_limits<uint64_t>::max()) {
    return Status::InvalidArgument("Discrete merge: projected count exceeds input sum");
  }

  std::vector<Part> parts;
  parts.reserve(bases.size() + witnesses.size() * 2);
  size_t witness_index = 0;
  for (const auto& base : bases) {
    Wide begin = base.begin;
    const Wide span = base.end - base.begin;
    auto add_free = [&](Wide first, Wide last) {
      if (first < last) {
        parts.push_back({first, last,
                         base.weight * (static_cast<long double>(last - first) /
                                        static_cast<long double>(span)),
                         0, false});
      }
    };
    while (witness_index < witnesses.size() &&
           static_cast<Wide>(witnesses[witness_index]) < base.end) {
      const Wide key = witnesses[witness_index++];
      if (!base.active || key < base.begin) {
        return Status::InvalidArgument("Discrete merge: witness outside supported partition");
      }
      add_free(begin, key);
      parts.push_back({key, key + 1, 0.0L, 1, true});
      begin = key + 1;
    }
    if (base.active) add_free(begin, base.end);
  }
  if (witness_index != witnesses.size()) {
    return Status::InvalidArgument("Discrete merge: unassigned witness");
  }
  Status status = Allocate(&parts, static_cast<uint64_t>(target - witness_count));
  if (!status.ok()) return status;
  std::vector<DiscreteCDF::Interval> intervals;
  intervals.reserve(parts.size());
  Wide assigned = 0;
  for (const auto& part : parts) {
    assigned += part.mass;
    if (part.witness && part.mass != 1) {
      return Status::InvalidArgument("Discrete merge: witness allocation changed");
    }
    intervals.push_back({static_cast<uint64_t>(part.begin),
                         static_cast<uint64_t>(part.end - 1), part.mass});
  }
  if (assigned != target) {
    return Status::InvalidArgument("Discrete merge: final count mismatch");
  }
  DiscreteCDF cdf;
  status = cdf.Assign(intervals);
  if (!status.ok()) return status;
  std::vector<PLRSegment> segments;
  segments.reserve(bases.size());
  for (const auto& base : bases) {
    if (!base.active) continue;
    const uint64_t begin = static_cast<uint64_t>(base.begin);
    const uint64_t end = static_cast<uint64_t>(base.end - 1);
    const uint64_t y_begin = cdf.CountLessThan(begin);
    const uint64_t y_end = cdf.CountLessThan(end);
    const double slope = begin == end
                             ? 0.0
                             : static_cast<double>(
                                   static_cast<long double>(y_end - y_begin) /
                                   static_cast<long double>(end - begin));
    segments.push_back({begin, end, slope,
                       static_cast<double>(y_begin) -
                           slope * static_cast<double>(begin)});
  }
  PLRModel result(std::move(segments));
  result.SetDiscreteModel(std::move(cdf));
  *output = std::move(result);
  *accepted_total = static_cast<uint64_t>(target);
  return Status::OK();
}

}  // namespace ROCKSDB_NAMESPACE
