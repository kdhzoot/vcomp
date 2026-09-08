#include "db/virtual_compaction/virtual_sst.h"
#include <algorithm>
#include <fstream>
#include <iostream>
#include <map>
#include <numeric>
#include <random>
#include <set>
#include <stdexcept>
#include <string>

using namespace ROCKSDB_NAMESPACE;

struct Dataset {
  std::string name;
  std::vector<uint64_t> keys;
  size_t inputs;
};

VirtualSST Make(const std::vector<uint64_t>& keys, double error) {
  VirtualSST s{};
  s.key_min = keys.front();
  s.key_max = keys.back();
  s.num_entries = keys.size();
  s.size_bytes = keys.size();
  s.plr_model = GreedyPLRFit(keys, error);
  s.kmv_sketch = BuildKMVSketchFromSortedKeys(keys, 4096);
  s.kmv_ranges = BuildKMVRangeSketchesFromSortedKeys(keys, 4096, 8);
  if (!s.kmv_sketch.complete) throw std::runtime_error("incomplete global sketch");
  for (const auto& r : s.kmv_ranges)
    if (!r.sketch.complete) throw std::runtime_error("incomplete range sketch");
  return s;
}

int main(int argc, char** argv) {
  if (argc != 3) return 2;
  std::ofstream rows(argv[1]), files(argv[2]);
  if (!rows || !files) return 3;
  rows << "dataset\tplr_error\ttarget_entries\tinputs\ttruth_unique\tmerged_entries\tmerged_rank_min\tmerged_rank_max\toutputs\tsplit_entries\tcapacity_bad_files\tsibling_overlap_pairs\tmaterialized_unique\tlocal_duplicate_keys\tidentity_missing\tidentity_extra\ttruth_outside_output_ranges\trange_count_bad_files\trange_entries_sum\tbucket_overlap_pairs\tretained_unique_samples\n";
  files << "dataset\tplr_error\ttarget_entries\tfile\tmin\tmax\tentries\tcapacity\tmaterialized_unique\trange_entries_sum\tbucket_overlap_pairs\n";
  std::vector<Dataset> datasets;
  for (size_t n : {1, 3, 5}) {
    Dataset d{"dense_" + std::to_string(n), std::vector<uint64_t>(n), 1};
    std::iota(d.keys.begin(), d.keys.end(), 0);
    datasets.push_back(d);
  }
  datasets.push_back({"singleton_fallback", {0, 100, 200}, 3});
  for (size_t seed = 0; seed < 10; ++seed) {
    const size_t n = 49 + seed;
    std::mt19937_64 rng(2026090800ULL + seed);
    std::set<uint64_t> keys;
    while (keys.size() < n) keys.insert(rng() % (2 * n));
    datasets.push_back({"random_" + std::to_string(seed),
                        {keys.begin(), keys.end()}, 3});
    std::vector<uint64_t> gaps;
    for (size_t i = 0; i < n; ++i) {
      gaps.push_back((i / 13) * (100 + seed * 17) + i % 13);
    }
    datasets.push_back({"gapped_" + std::to_string(seed), gaps, 3});
  }
  for (const auto& d : datasets) {
    for (double error : {0.0, 8.0}) {
      std::vector<std::vector<uint64_t>> parts(d.inputs);
      for (size_t i = 0; i < d.keys.size(); ++i)
        parts[i % d.inputs].push_back(d.keys[i]);
      std::vector<VirtualSST> inputs;
      for (const auto& part : parts) inputs.push_back(Make(part, error));
      std::vector<const VirtualSST*> pointers;
      for (const auto& s : inputs) pointers.push_back(&s);
      uint64_t count = 0;
      auto model = NWayMergeKMVRangeAware(pointers, &count, 4096);
      if (count != d.keys.size()) throw std::runtime_error("exact union count mismatch");
      for (uint64_t target : {2, 7, 16}) {
        auto outputs = SplitIntoSSTs(model, count, target, 1,
            d.keys.front(), d.keys.back(), 1, {}, &pointers, 4096);
        uint64_t sum = 0, capacity_bad = 0, overlap = 0, duplicates = 0;
        uint64_t range_bad = 0, range_sum = 0, bucket_overlap = 0;
        std::set<uint64_t> materialized, retained;
        for (size_t i = 0; i < outputs.size(); ++i) {
          const auto& s = outputs[i];
          const uint64_t capacity = s.key_max - s.key_min + 1;
          sum += s.num_entries;
          capacity_bad += s.num_entries > capacity;
          for (size_t j = 0; j < i; ++j)
            overlap += outputs[j].key_max >= s.key_min &&
                       outputs[j].key_min <= s.key_max;
          auto values = MaterializeKeys(s);
          std::set<uint64_t> unique(values.begin(), values.end());
          duplicates += values.size() - unique.size();
          materialized.insert(unique.begin(), unique.end());
          uint64_t local_range_sum = 0, local_bucket_overlap = 0;
          for (size_t j = 0; j < s.kmv_ranges.size(); ++j) {
            const auto& r = s.kmv_ranges[j];
            local_range_sum += r.num_entries;
            for (size_t k = 0; k < j; ++k)
              local_bucket_overlap += s.kmv_ranges[k].key_max >= r.key_min &&
                                      s.kmv_ranges[k].key_min <= r.key_max;
          }
          for (const auto& x : s.kmv_sketch.samples) retained.insert(x.key);
          range_bad += local_range_sum != s.num_entries;
          range_sum += local_range_sum;
          bucket_overlap += local_bucket_overlap;
          files << d.name << '\t' << error << '\t' << target << '\t' << i
                << '\t' << s.key_min << '\t' << s.key_max << '\t' << s.num_entries
                << '\t' << capacity << '\t' << unique.size() << '\t' << local_range_sum
                << '\t' << local_bucket_overlap << '\n';
        }
        uint64_t missing = 0, extra = 0, outside = 0;
        std::set<uint64_t> truth(d.keys.begin(), d.keys.end());
        for (uint64_t k : truth) {
          missing += !materialized.count(k);
          bool covered = false;
          for (const auto& s : outputs)
            covered |= s.key_min <= k && k <= s.key_max;
          outside += !covered;
        }
        for (uint64_t k : materialized) extra += !truth.count(k);
        rows << d.name << '\t' << error << '\t' << target << '\t' << d.inputs
             << '\t' << truth.size() << '\t' << count
             << '\t' << model.Predict(d.keys.front())
             << '\t' << model.Predict(d.keys.back()) << '\t' << outputs.size()
             << '\t' << sum << '\t' << capacity_bad << '\t' << overlap
             << '\t' << materialized.size() << '\t' << duplicates
             << '\t' << missing << '\t' << extra << '\t' << outside
             << '\t' << range_bad << '\t' << range_sum << '\t' << bucket_overlap
             << '\t' << retained.size() << '\n';
      }
    }
  }
  rows.flush(); files.flush();
  return rows && files ? 0 : 4;
}
