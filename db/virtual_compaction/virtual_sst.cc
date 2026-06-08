//  Copyright (c) 2011-present, Facebook, Inc.  All rights reserved.
//  This source code is licensed under both the GPLv2 (found in the
//  COPYING file in the root directory) and Apache 2.0 License
//  (found in the LICENSE.Apache file in the root directory).

#include "db/virtual_compaction/virtual_sst.h"

#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdlib>
#include <iterator>
#include <limits>

namespace ROCKSDB_NAMESPACE {

namespace {

constexpr size_t kDefaultKMVSamples = 512;
constexpr size_t kDefaultKMVRangeBuckets = 8;

uint64_t KMVHash(uint64_t x) {
  // SplitMix64 finalizer. It is fast, deterministic, and gives stable
  // uniformly distributed fingerprints for integer keys.
  x += 0x9e3779b97f4a7c15ULL;
  x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
  x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
  return x ^ (x >> 31);
}

void SortUniqueByKey(std::vector<KMVSample>* samples) {
  if (samples->empty()) return;

  std::sort(samples->begin(), samples->end(),
            [](const KMVSample& a, const KMVSample& b) {
              if (a.key != b.key) return a.key < b.key;
              return a.hash < b.hash;
            });
  samples->erase(std::unique(samples->begin(), samples->end(),
                             [](const KMVSample& a, const KMVSample& b) {
                               return a.key == b.key;
                             }),
                 samples->end());
}

void SortByHashAndTrim(std::vector<KMVSample>* samples, size_t max_samples) {
  if (samples->empty()) return;

  std::sort(samples->begin(), samples->end(),
            [](const KMVSample& a, const KMVSample& b) {
              if (a.hash != b.hash) return a.hash < b.hash;
              return a.key < b.key;
            });
  if (max_samples > 0 && samples->size() > max_samples) {
    samples->resize(max_samples);
  }
}

uint64_t MaxHash() {
  return std::numeric_limits<uint64_t>::max();
}

long double ThetaFraction(uint64_t theta_hash) {
  const long double domain =
      static_cast<long double>(std::numeric_limits<uint64_t>::max()) + 1.0L;
  return (static_cast<long double>(theta_hash) + 1.0L) / domain;
}

KMVSketch BuildKMVSketchFromSortedKeyRange(
    std::vector<uint64_t>::const_iterator begin,
    std::vector<uint64_t>::const_iterator end,
    size_t max_samples) {
  KMVSketch sketch;
  if (begin == end) return sketch;

  auto sample_less = [](const KMVSample& a, const KMVSample& b) {
    if (a.hash != b.hash) return a.hash < b.hash;
    return a.key < b.key;
  };
  sketch.samples.reserve(
      std::min(static_cast<size_t>(std::distance(begin, end)), max_samples));
  uint64_t unique_count = 0;
  uint64_t last_key = 0;
  bool have_last = false;
  for (auto it = begin; it != end; ++it) {
    uint64_t key = *it;
    if (have_last && key == last_key) continue;
    have_last = true;
    last_key = key;
    unique_count++;

    KMVSample sample{key, KMVHash(key)};
    if (sketch.samples.size() < max_samples) {
      sketch.samples.push_back(sample);
      std::push_heap(sketch.samples.begin(), sketch.samples.end(),
                     sample_less);
    } else if (sample_less(sample, sketch.samples.front())) {
      std::pop_heap(sketch.samples.begin(), sketch.samples.end(),
                    sample_less);
      sketch.samples.back() = sample;
      std::push_heap(sketch.samples.begin(), sketch.samples.end(),
                     sample_less);
    }
  }

  sketch.complete = unique_count <= max_samples;
  SortByHashAndTrim(&sketch.samples, max_samples);
  sketch.theta_hash = sketch.complete || sketch.samples.empty()
                          ? MaxHash()
                          : sketch.samples.back().hash;
  return sketch;
}

}  // namespace

size_t VirtualSSTKMVSamples() {
  const char* value = std::getenv("VCOMP_KMV_SAMPLES");
  if (value == nullptr || *value == '\0') {
    return kDefaultKMVSamples;
  }
  char* end = nullptr;
  unsigned long long parsed = std::strtoull(value, &end, 10);
  if (end == value || parsed == 0) {
    return kDefaultKMVSamples;
  }
  return static_cast<size_t>(parsed);
}

size_t VirtualSSTKMVRangeBuckets() {
  const char* value = std::getenv("VCOMP_KMV_RANGE_BUCKETS");
  if (value == nullptr || *value == '\0') {
    return kDefaultKMVRangeBuckets;
  }
  char* end = nullptr;
  unsigned long long parsed = std::strtoull(value, &end, 10);
  if (end == value || parsed == 0) {
    return kDefaultKMVRangeBuckets;
  }
  return static_cast<size_t>(parsed);
}

KMVSketch BuildKMVSketchFromSortedKeys(const std::vector<uint64_t>& sorted_keys,
                                       size_t max_samples) {
  if (sorted_keys.empty()) return KMVSketch();
  if (max_samples == 0) max_samples = VirtualSSTKMVSamples();
  return BuildKMVSketchFromSortedKeyRange(sorted_keys.begin(), sorted_keys.end(),
                                          max_samples);
}

std::vector<KMVRangeSketch> BuildKMVRangeSketchesFromSortedKeys(
    const std::vector<uint64_t>& sorted_keys,
    size_t max_samples,
    size_t max_ranges) {
  std::vector<KMVRangeSketch> ranges;
  if (sorted_keys.empty()) return ranges;
  if (max_samples == 0) max_samples = VirtualSSTKMVSamples();
  if (max_ranges == 0) max_ranges = VirtualSSTKMVRangeBuckets();

  size_t range_count = std::min(max_ranges, sorted_keys.size());
  if (range_count == 0) return ranges;
  size_t samples_per_range = std::max<size_t>(1, max_samples / range_count);
  ranges.reserve(range_count);

  for (size_t i = 0; i < range_count; i++) {
    size_t begin_idx = (sorted_keys.size() * i) / range_count;
    size_t end_idx = (sorted_keys.size() * (i + 1)) / range_count;
    if (begin_idx >= end_idx) continue;

    KMVRangeSketch range;
    range.key_min = sorted_keys[begin_idx];
    range.key_max = sorted_keys[end_idx - 1];
    range.num_entries = end_idx - begin_idx;
    range.sketch = BuildKMVSketchFromSortedKeyRange(
        sorted_keys.begin() + begin_idx, sorted_keys.begin() + end_idx,
        samples_per_range);
    ranges.push_back(std::move(range));
  }
  return ranges;
}

uint64_t EstimateKMVUnionEntries(const std::vector<const VirtualSST*>& inputs,
                                 uint64_t naive_entries,
                                 size_t max_samples) {
  if (inputs.empty() || naive_entries == 0) return naive_entries;
  if (max_samples == 0) max_samples = VirtualSSTKMVSamples();

  std::vector<KMVSample> merged_samples;
  bool complete = true;
  size_t reserve = 0;
  uint64_t theta_hash = MaxHash();
  for (const auto* input : inputs) {
    if (input == nullptr ||
        (input->kmv_sketch.samples.empty() && !input->kmv_sketch.complete)) {
      return naive_entries;
    }
    complete = complete && input->kmv_sketch.complete;
    theta_hash = std::min(theta_hash, input->kmv_sketch.theta_hash);
    reserve += input->kmv_sketch.samples.size();
  }
  merged_samples.reserve(reserve);
  for (const auto* input : inputs) {
    for (const auto& sample : input->kmv_sketch.samples) {
      if (sample.hash <= theta_hash) {
        merged_samples.push_back(sample);
      }
    }
  }
  SortUniqueByKey(&merged_samples);

  if (complete) {
    return std::min<uint64_t>(naive_entries, merged_samples.size());
  }
  if (merged_samples.empty()) {
    return naive_entries;
  }

  const long double theta = ThetaFraction(theta_hash);
  if (theta <= 0.0L) return naive_entries;

  const long double estimate =
      static_cast<long double>(merged_samples.size()) / theta;
  if (estimate >= static_cast<long double>(naive_entries)) {
    return naive_entries;
  }
  return std::max<uint64_t>(
      1, static_cast<uint64_t>(std::ceil(estimate)));
}

uint64_t EstimateKMVUnionEntriesForRange(
    const std::vector<const VirtualSST*>& inputs, uint64_t key_min,
    uint64_t key_max) {
  if (inputs.empty() || key_max < key_min) return 0;

  struct SketchPiece {
    const KMVSketch* sketch;
    uint64_t piece_min;
    uint64_t piece_max;
    uint64_t entries;
  };
  std::vector<KMVSample> merged_samples;
  bool complete = true;
  uint64_t theta_hash = MaxHash();
  uint64_t input_entries_cap = 0;
  size_t reserve = 0;
  std::vector<SketchPiece> pieces;
  for (const auto* input : inputs) {
    if (input == nullptr || input->key_max < key_min ||
        input->key_min > key_max) {
      continue;
    }
    if (!input->kmv_ranges.empty()) {
      for (const auto& range : input->kmv_ranges) {
        if (range.key_max < key_min || range.key_min > key_max) continue;
        pieces.push_back(SketchPiece{&range.sketch, range.key_min,
                                     range.key_max, range.num_entries});
      }
    } else {
      pieces.push_back(SketchPiece{&input->kmv_sketch, input->key_min,
                                   input->key_max, input->num_entries});
    }
  }
  if (pieces.empty()) return 0;

  long double density_estimate = 0.0L;
  for (const auto& piece : pieces) {
    if (piece.sketch == nullptr) continue;
    complete = complete && piece.sketch->complete;
    theta_hash = std::min(theta_hash, piece.sketch->theta_hash);
    input_entries_cap += piece.entries;
    reserve += piece.sketch->samples.size();

    uint64_t overlap_min = std::max(piece.piece_min, key_min);
    uint64_t overlap_max = std::min(piece.piece_max, key_max);
    if (overlap_max >= overlap_min && piece.entries > 0) {
      unsigned __int128 piece_span =
          static_cast<unsigned __int128>(piece.piece_max) -
          static_cast<unsigned __int128>(piece.piece_min) + 1;
      unsigned __int128 overlap_span =
          static_cast<unsigned __int128>(overlap_max) -
          static_cast<unsigned __int128>(overlap_min) + 1;
      if (piece_span > 0) {
        density_estimate +=
            static_cast<long double>(piece.entries) *
            static_cast<long double>(overlap_span) /
            static_cast<long double>(piece_span);
      }
    }
  }
  if (input_entries_cap == 0) return 0;

  merged_samples.reserve(reserve);
  for (const auto& piece : pieces) {
    if (piece.sketch == nullptr) continue;
    for (const auto& sample : piece.sketch->samples) {
      if (sample.key >= key_min && sample.key <= key_max &&
          sample.hash <= theta_hash) {
        merged_samples.push_back(sample);
      }
    }
  }
  SortUniqueByKey(&merged_samples);

  if (complete) {
    return std::min<uint64_t>(input_entries_cap, merged_samples.size());
  }
  if (merged_samples.empty()) {
    return std::min<uint64_t>(
        input_entries_cap,
        static_cast<uint64_t>(std::ceil(density_estimate)));
  }

  const long double theta = ThetaFraction(theta_hash);
  if (theta <= 0.0L) return 0;
  const long double estimate =
      static_cast<long double>(merged_samples.size()) / theta;
  return std::min<uint64_t>(
      input_entries_cap,
      std::max<uint64_t>(1, static_cast<uint64_t>(std::ceil(estimate))));
}

PLRModel NWayMergeKMVRangeAware(const std::vector<const VirtualSST*>& inputs,
                                uint64_t* adjusted_total,
                                size_t kmv_samples) {
  if (kmv_samples == 0) kmv_samples = VirtualSSTKMVSamples();
  if (adjusted_total != nullptr) *adjusted_total = 0;
  if (inputs.empty()) return PLRModel();

  const size_t N = inputs.size();
  uint64_t naive_entries = 0;
  for (const auto* input : inputs) {
    if (input == nullptr) continue;
    naive_entries += input->num_entries;
  }
  uint64_t kmv_total_entries =
      EstimateKMVUnionEntries(inputs, naive_entries, kmv_samples);

  std::vector<uint64_t> breakpoints;
  size_t total_bp = 0;
  for (const auto* input : inputs) {
    if (input == nullptr) continue;
    total_bp += input->plr_model.NumSegments() * 2 + 2;
  }
  breakpoints.reserve(total_bp);
  for (const auto* input : inputs) {
    if (input == nullptr) continue;
    breakpoints.push_back(input->key_min);
    breakpoints.push_back(input->key_max);
    for (const auto& seg : input->plr_model.Segments()) {
      breakpoints.push_back(seg.key_start);
      breakpoints.push_back(seg.key_end);
    }
  }
  std::sort(breakpoints.begin(), breakpoints.end());
  breakpoints.erase(std::unique(breakpoints.begin(), breakpoints.end()),
                    breakpoints.end());
  if (breakpoints.empty()) return PLRModel();
  if (breakpoints.size() == 1) {
    if (adjusted_total != nullptr) *adjusted_total = kmv_total_entries;
    return PLRModel({PLRSegment{breakpoints[0], breakpoints[0], 0.0,
                                static_cast<double>(kmv_total_entries) / 2.0}});
  }

  std::vector<size_t> model_order(N);
  for (size_t j = 0; j < N; j++) model_order[j] = j;
  std::sort(model_order.begin(), model_order.end(), [&](size_t a, size_t b) {
    if (inputs[a] == nullptr) return false;
    if (inputs[b] == nullptr) return true;
    return inputs[a]->key_min < inputs[b]->key_min;
  });

  std::vector<size_t> seg_idx(N, 0);
  std::vector<size_t> active;
  active.reserve(N);
  std::vector<PLRSegment> merged;
  merged.reserve(breakpoints.size() - 1);
  size_t next_model = 0;
  long double raw_total_entries = 0.0L;

  for (size_t i = 0; i + 1 < breakpoints.size(); i++) {
    uint64_t k_start = breakpoints[i];
    uint64_t k_end = breakpoints[i + 1];
    uint64_t k_mid = k_start + (k_end - k_start) / 2;

    while (next_model < N) {
      size_t j = model_order[next_model];
      if (inputs[j] == nullptr) {
        next_model++;
        continue;
      }
      if (inputs[j]->key_min > k_mid) break;
      active.push_back(j);
      next_model++;
    }

    size_t write = 0;
    for (size_t r = 0; r < active.size(); r++) {
      size_t j = active[r];
      if (inputs[j] == nullptr || inputs[j]->key_max < k_mid) {
        continue;
      }
      active[write++] = j;
    }
    active.resize(write);

    double slope_sum = 0.0;
    std::vector<const VirtualSST*> interval_inputs;
    interval_inputs.reserve(active.size());
    for (size_t j : active) {
      const auto& segs = inputs[j]->plr_model.Segments();
      while (seg_idx[j] + 1 < segs.size() &&
             segs[seg_idx[j]].key_end < k_mid) {
        seg_idx[j]++;
      }
      if (segs.empty()) continue;
      const auto& seg = segs[seg_idx[j]];
      if (k_mid < seg.key_start || k_mid > seg.key_end) {
        continue;
      }
      slope_sum += std::max(seg.slope, 0.0);
      interval_inputs.push_back(inputs[j]);
    }

    uint64_t interval_entries =
        EstimateKMVUnionEntriesForRange(interval_inputs, k_start, k_end);
    raw_total_entries += static_cast<long double>(interval_entries);

    const long double width =
        std::max<long double>(1.0L, static_cast<long double>(k_end - k_start));
    const long double naive_mass =
        std::max<long double>(0.0L, static_cast<long double>(slope_sum) * width);
    double slope = 0.0;
    if (interval_entries > 0) {
      if (slope_sum > 0.0 && naive_mass > 0.0L) {
        slope = slope_sum * static_cast<double>(
                                static_cast<long double>(interval_entries) /
                                naive_mass);
      } else {
        slope = static_cast<double>(static_cast<long double>(interval_entries) /
                                    width);
      }
    }
    merged.push_back({k_start, k_end, slope, 0.0});
  }

  if (kmv_total_entries > 0 && raw_total_entries > 0.0L) {
    const double scale =
        static_cast<double>(static_cast<long double>(kmv_total_entries) /
                            raw_total_entries);
    for (auto& seg : merged) {
      seg.slope *= scale;
    }
  } else if (kmv_total_entries > 0 && !merged.empty()) {
    const long double width = std::max<long double>(
        1.0L, static_cast<long double>(breakpoints.back() - breakpoints.front()));
    const double slope =
        static_cast<double>(static_cast<long double>(kmv_total_entries) / width);
    for (auto& seg : merged) {
      seg.slope = slope;
    }
  }

  double cumulative_pos = 0.0;
  for (auto& seg : merged) {
    seg.intercept =
        cumulative_pos - seg.slope * static_cast<double>(seg.key_start);
    cumulative_pos +=
        seg.slope * static_cast<double>(seg.key_end - seg.key_start);
  }

  if (merged.size() > 1) {
    std::vector<PLRSegment> compacted;
    compacted.push_back(merged[0]);
    for (size_t i = 1; i < merged.size(); i++) {
      auto& prev = compacted.back();
      const auto& cur = merged[i];
      if (std::abs(prev.slope - cur.slope) < 1e-12 &&
          std::abs(prev.intercept - cur.intercept) < 1e-9) {
        prev.key_end = cur.key_end;
      } else {
        compacted.push_back(cur);
      }
    }
    merged = std::move(compacted);
  }

  if (adjusted_total != nullptr) *adjusted_total = kmv_total_entries;
  return PLRModel(std::move(merged));
}

KMVSketch MergeKMVSketchesForRange(const std::vector<const VirtualSST*>& inputs,
                                   uint64_t key_min, uint64_t key_max,
                                   size_t max_samples) {
  KMVSketch sketch;
  if (inputs.empty() || key_max < key_min) return sketch;
  if (max_samples == 0) max_samples = VirtualSSTKMVSamples();

  struct SketchPiece {
    const KMVSketch* sketch;
  };
  std::vector<SketchPiece> pieces;
  size_t reserve = 0;
  bool complete = true;
  uint64_t theta_hash = MaxHash();
  for (const auto* input : inputs) {
    if (input == nullptr || input->key_max < key_min ||
        input->key_min > key_max) {
      continue;
    }
    if (!input->kmv_ranges.empty()) {
      for (const auto& range : input->kmv_ranges) {
        if (range.key_max < key_min || range.key_min > key_max) continue;
        pieces.push_back(SketchPiece{&range.sketch});
      }
    } else {
      pieces.push_back(SketchPiece{&input->kmv_sketch});
    }
  }
  if (pieces.empty()) return sketch;
  for (const auto& piece : pieces) {
    if (piece.sketch == nullptr) continue;
    complete = complete && piece.sketch->complete;
    theta_hash = std::min(theta_hash, piece.sketch->theta_hash);
    reserve += piece.sketch->samples.size();
  }
  sketch.samples.reserve(reserve);
  for (const auto& piece : pieces) {
    if (piece.sketch == nullptr) continue;
    for (const auto& sample : piece.sketch->samples) {
      if (sample.key >= key_min && sample.key <= key_max &&
          sample.hash <= theta_hash) {
        sketch.samples.push_back(sample);
      }
    }
  }
  sketch.complete = complete;
  sketch.theta_hash = complete ? MaxHash() : theta_hash;
  SortUniqueByKey(&sketch.samples);
  bool trimmed = max_samples > 0 && sketch.samples.size() > max_samples;
  if (sketch.complete && trimmed) {
    sketch.complete = false;
  }
  SortByHashAndTrim(&sketch.samples, max_samples);
  if (sketch.complete) {
    sketch.theta_hash = MaxHash();
  } else if (trimmed && !sketch.samples.empty()) {
    sketch.theta_hash = sketch.samples.back().hash;
  }
  return sketch;
}

std::vector<KMVRangeSketch> MergeKMVRangeSketchesForOutput(
    const std::vector<const VirtualSST*>& inputs,
    uint64_t key_min,
    uint64_t key_max,
    uint64_t num_entries,
    size_t max_samples,
    size_t max_ranges) {
  std::vector<KMVRangeSketch> ranges;
  if (inputs.empty() || num_entries == 0 || key_max < key_min) return ranges;
  if (max_samples == 0) max_samples = VirtualSSTKMVSamples();
  if (max_ranges == 0) max_ranges = VirtualSSTKMVRangeBuckets();

  size_t range_count =
      static_cast<size_t>(std::min<uint64_t>(max_ranges, num_entries));
  if (range_count == 0) return ranges;
  size_t samples_per_range = std::max<size_t>(1, max_samples / range_count);
  ranges.reserve(range_count);

  unsigned __int128 span =
      static_cast<unsigned __int128>(key_max) -
      static_cast<unsigned __int128>(key_min) + 1;
  for (size_t i = 0; i < range_count; i++) {
    unsigned __int128 start_off = (span * i) / range_count;
    unsigned __int128 end_off = (span * (i + 1)) / range_count;
    uint64_t range_min = key_min + static_cast<uint64_t>(start_off);
    uint64_t range_max =
        key_min + static_cast<uint64_t>(end_off == 0 ? 0 : end_off - 1);
    if (range_max < range_min) range_max = range_min;

    KMVRangeSketch range;
    range.key_min = range_min;
    range.key_max = range_max;
    range.num_entries =
        EstimateKMVUnionEntriesForRange(inputs, range_min, range_max);
    range.sketch = MergeKMVSketchesForRange(inputs, range_min, range_max,
                                            samples_per_range);
    if (range.num_entries > 0 || !range.sketch.samples.empty()) {
      ranges.push_back(std::move(range));
    }
  }
  return ranges;
}

PLRModel ScalePLRPositions(const PLRModel& plr, double scale) {
  if (plr.Empty() || std::abs(scale - 1.0) < 1e-15) return plr;
  std::vector<PLRSegment> segments;
  segments.reserve(plr.NumSegments());
  for (const auto& seg : plr.Segments()) {
    PLRSegment scaled = seg;
    scaled.slope *= scale;
    scaled.intercept *= scale;
    segments.push_back(scaled);
  }
  return PLRModel(std::move(segments));
}

std::vector<VirtualSST> SplitIntoSSTs(const PLRModel& plr,
                                      uint64_t total_entries,
                                      uint64_t target_sst_size,
                                      uint64_t avg_entry_size,
                                      uint64_t global_min,
                                      uint64_t global_max,
                                      int target_level,
                                      const std::vector<uint64_t>& grandparent_boundaries,
                                      const std::vector<const VirtualSST*>* kmv_inputs,
                                      size_t kmv_samples) {
  std::vector<VirtualSST> result;

  if (total_entries == 0 || plr.Empty()) return result;

  // Intra-L0 (target_level == 0): baseline RocksDB never splits L0 outputs.
  // See CompactionOutputs::ShouldStopBefore in
  // db/compaction/compaction_outputs.cc:
  //     if (compaction_->output_level() == 0) return false;
  // Without this, vcomp's intra-L0 would re-split N inputs back into ~N
  // 64MB outputs (no consolidation), and downstream L0→L1 picks would be
  // small. Matching baseline means emitting a single VirtualSST per
  // intra-L0 compaction, regardless of total size.
  if (target_level == 0) {
    VirtualSST vsst;
    vsst.plr_model = plr;
    vsst.key_min = global_min;
    vsst.key_max = global_max;
    vsst.num_entries = total_entries;
    vsst.level = 0;
    vsst.size_bytes = VirtualSST::EstimateSize(total_entries, avg_entry_size);
    if (kmv_inputs != nullptr) {
      vsst.kmv_sketch = MergeKMVSketchesForRange(*kmv_inputs, global_min,
                                                 global_max, kmv_samples);
      vsst.kmv_ranges = MergeKMVRangeSketchesForOutput(
          *kmv_inputs, global_min, global_max, total_entries, kmv_samples,
          /*max_ranges=*/0);
    }
    result.push_back(std::move(vsst));
    return result;
  }

  // Non-L0 output: match baseline's max_output_file_size policy. On
  // non-bottom levels with grandparents, files may grow up to 2 * target
  // before the size-based hard cut, while the dynamic threshold at GP
  // boundaries still uses target_sst_size. See
  // Compaction::max_output_file_size_ computation.
  bool has_grandparents =
      !grandparent_boundaries.empty() && target_level > 0;
  uint64_t max_sst_size =
      has_grandparents ? 2 * target_sst_size : target_sst_size;

  uint64_t keys_per_sst = max_sst_size / avg_entry_size;
  if (keys_per_sst == 0) keys_per_sst = 1;

  // Build split positions.
  // Convert grandparent boundary keys to positions for split decisions.
  // Do NOT dedup: two adjacent grandparent files contribute boundaries
  // (largest of file i, smallest of file i+1) that often round to the same
  // integer position but represent two distinct boundary transitions in
  // baseline's state machine. Dropping one halves the `switched` counter
  // and makes the dynamic threshold grow too slowly, producing files that
  // are smaller than baseline.
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
  }

  // Build split positions by scanning size-based boundaries and grandparent
  // boundaries together. Mirrors RocksDB's CompactionOutputs::ShouldStopBefore
  // dynamic threshold: at each grandparent boundary, pre-cut when the current
  // file's bytes are >= target_sst_size * (50 + 5*switched)%, capped at 90%,
  // where `switched` counts GP boundaries crossed since the last cut and
  // resets on every new output file.
  std::vector<uint64_t> split_positions;
  {
    uint64_t last_split = 0;
    uint64_t next_size_split = keys_per_sst;
    uint64_t switched = 0;  // GP boundaries crossed since last cut
    size_t gp_idx = 0;

    while (next_size_split < total_entries || gp_idx < gp_positions.size()) {
      // Find the next candidate: either size-based or grandparent boundary.
      uint64_t next_gp = (gp_idx < gp_positions.size())
                             ? gp_positions[gp_idx]
                             : total_entries;

      if (next_size_split <= next_gp && next_size_split < total_entries) {
        // Size-based hard cut.
        split_positions.push_back(next_size_split);
        last_split = next_size_split;
        next_size_split = last_split + keys_per_sst;
        switched = 0;
        // Skip grandparent boundaries we've passed.
        while (gp_idx < gp_positions.size() &&
               gp_positions[gp_idx] <= last_split) {
          gp_idx++;
        }
      } else if (next_gp < total_entries) {
        // GP boundary: count it, then evaluate dynamic threshold in BYTES.
        switched++;
        uint64_t cur_bytes = (next_gp - last_split) * avg_entry_size;
        uint64_t pct = 50 + std::min<uint64_t>(switched * 5, 40);
        uint64_t threshold_bytes = (target_sst_size * pct) / 100;
        if (cur_bytes >= threshold_bytes) {
          split_positions.push_back(next_gp);
          last_split = next_gp;
          next_size_split = last_split + keys_per_sst;
          switched = 0;
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
    uint64_t key_end =
        (i == num_ssts - 1)
            ? global_max
            : plr.Inverse(static_cast<double>(pos_end - 1));

    // File metadata should describe the actual keys in this output run, not a
    // gapless partition of the key space. Sparse random keys can have large
    // gaps between adjacent output files, and claiming those gaps as part of
    // the previous file creates false overlap with lower levels.
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
    if (kmv_inputs != nullptr) {
      vsst.kmv_sketch = MergeKMVSketchesForRange(*kmv_inputs, key_start,
                                                 key_end, kmv_samples);
      vsst.kmv_ranges = MergeKMVRangeSketchesForOutput(
          *kmv_inputs, key_start, key_end, n_entries, kmv_samples,
          /*max_ranges=*/0);
    }

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

  std::vector<const VirtualSST*> input_ptrs = inputs;
  uint64_t kmv_entries = 0;
  PLRModel merged = NWayMergeKMVRangeAware(input_ptrs, &kmv_entries);
  if (kmv_entries > total_entries && kmv_entries > 0) {
    double scale = static_cast<double>(total_entries) /
                   static_cast<double>(kmv_entries);
    merged = ScalePLRPositions(merged, scale);
    kmv_entries = total_entries;
  }
  total_entries = kmv_entries;

  // Split into output SSTs.
  return SplitIntoSSTs(merged, total_entries, target_sst_size, avg_entry_size,
                       global_min, global_max, output_level,
                       /*grandparent_boundaries=*/{}, &input_ptrs);
}

}  // namespace ROCKSDB_NAMESPACE
