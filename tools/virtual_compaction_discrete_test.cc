// Standalone CPU-only regression tests for the discrete CDF contract.
// Compile against matching current sources or a fully rebuilt current library.
// Do not link this source against the historical pre-certificate static library.
#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

#include "db/virtual_compaction/discrete_cdf.h"
#ifndef VCOMP_DISCRETE_CDF_ONLY
#include "db/virtual_compaction/discrete_merge.h"
#include "db/virtual_compaction/virtual_sst.h"
#endif

namespace {
namespace r = ROCKSDB_NAMESPACE;
using Keys = std::vector<uint64_t>;
using Wide = unsigned __int128;
constexpr uint64_t kMax = std::numeric_limits<uint64_t>::max();
uint64_t checks = 0;
uint64_t pipeline_rounds = 0;

void Check(bool ok, const std::string& detail) {
  ++checks;
  if (!ok) throw std::runtime_error(detail);
}

void OK(const r::Status& status, const std::string& detail) {
  Check(status.ok(), detail + ": " + status.ToString());
}

void Unique(Keys* keys) {
  std::sort(keys->begin(), keys->end());
  keys->erase(std::unique(keys->begin(), keys->end()), keys->end());
}

Keys Union(const std::vector<Keys>& inputs) {
  Keys result;
  for (const auto& keys : inputs) result.insert(result.end(), keys.begin(), keys.end());
  Unique(&result);
  return result;
}

bool Strict(const Keys& keys) {
  return std::adjacent_find(keys.begin(), keys.end(),
                           [](uint64_t a, uint64_t b) { return a >= b; }) == keys.end();
}

Keys SelectAll(const r::DiscreteCDF& cdf) {
  Check(cdf.Count() <= 100000, "test must not enumerate a large certificate");
  uint64_t cell_sum = 0;
  for (const auto& cell : cdf.Cells()) {
    Check(cell.num_entries > 0 && cell.prefix_entries == cell_sum,
          "cell mass and prefix are consistent");
    Check(cell.key_min <= cell.key_max &&
          Wide{cell.num_entries} <= Wide{cell.key_max} - cell.key_min + 1,
          "selected cell capacity");
    cell_sum += cell.num_entries;
  }
  Check(cell_sum == cdf.Count(), "cell mass sum");
  Keys result;
  auto cursor = cdf.NewCursor();
  uint64_t key = 0;
  while (cursor.Next(&key)) result.push_back(key);
  OK(cursor.status(), "cursor status");
  Check(result.size() == cdf.Count(), "cursor count");
  Check(Strict(result), "cursor strict order");
  for (uint64_t i = 0; i < result.size(); ++i) {
    uint64_t checked = 0;
    OK(cdf.Select(i, &checked), "checked Select");
    Check(checked == result[i] && cdf.Select(i) == result[i], "cursor/Select equality");
    Check(cdf.CountLessThan(result[i]) == i, "rank strictly before selected key");
    Check(cdf.CountThrough(result[i]) == i + 1, "rank through selected key");
  }
  Check(!cursor.Next(&key), "cursor remains exhausted");
  return result;
}

void CheckSlices(const r::DiscreteCDF& cdf, const Keys& expected) {
  for (uint64_t first = 0; first <= expected.size(); ++first) {
    for (uint64_t count = 0; count <= expected.size() - first; ++count) {
      r::DiscreteCDF slice;
      OK(cdf.Slice(first, count, &slice), "checked Slice");
      const Keys wanted(expected.begin() + first, expected.begin() + first + count);
      Check(SelectAll(slice) == wanted, "slice keeps original selection phase");
      Check(SelectAll(cdf.Slice(first, count)) == wanted, "unchecked Slice equality");
      if (count > 1) {
        const auto nested = slice.Slice(1, count - 1);
        Check(SelectAll(nested) == Keys(wanted.begin() + 1, wanted.end()),
              "nested slice composition");
      }
    }
  }
}

void IndependentCDFTests() {
  r::DiscreteCDF cdf;
  OK(cdf.Assign({{0, 9, 3}}), "sparse interval");
  Check(SelectAll(cdf) == Keys({3, 6, 9}), "floor-CDF sparse exact reference");
  CheckSlices(cdf, {3, 6, 9});
  Check(cdf.CountLessThan(0) == 0 && cdf.CountThrough(2) == 0 &&
        cdf.CountThrough(5) == 1 && cdf.CountThrough(kMax) == 3,
        "floor-CDF gap/outside rank");

  OK(cdf.Assign({{0, 0, 1}, {7, 11, 3}, {kMax - 1, kMax, 2}}), "multi-cell endpoints");
  const Keys multi = {0, 8, 10, 11, kMax - 1, kMax};
  Check(SelectAll(cdf) == multi, "inclusive cells at uint64 endpoints");
  CheckSlices(cdf, multi);
  auto old_cursor = cdf.NewCursor();
  OK(cdf.Assign({{42, 42, 1}}), "replace certificate");
  Keys cursor_keys;
  uint64_t key = 0;
  while (old_cursor.Next(&key)) cursor_keys.push_back(key);
  OK(old_cursor.status(), "retained cursor status");
  Check(cursor_keys == multi, "cursor owns immutable prior storage");

  // Rejections must not overwrite the previous successful certificate/output.
  for (const auto& invalid : std::vector<std::vector<r::DiscreteCDF::Interval>>{
           {{5, 4, 1}}, {{9, 9, 2}}, {{2, 4, 1}, {4, 7, 1}},
           {{8, 8, 1}, {1, 1, 1}}, {{0, kMax - 1, kMax}, {kMax, kMax, 1}}}) {
    Check(!cdf.Assign(invalid).ok(), "invalid/capacity/overlap/count-overflow rejected");
    Check(SelectAll(cdf) == Keys({42}), "failed Assign preserves object");
  }
  key = 91;
  Check(!cdf.Select(1, &key).ok() && key == 91, "out-of-range Select preserves output");
  r::DiscreteCDF output;
  OK(output.Assign({{81, 81, 1}}), "failed Slice output fixture");
  Check(!cdf.Slice(2, 0, &output).ok() && SelectAll(output) == Keys({81}),
        "invalid Slice preserves output");
  Check(!cdf.Slice(1, kMax, &output).ok(), "Slice count overflow rejected");
  OK(cdf.Slice(0, 1, &cdf), "in-place Slice");
  Check(SelectAll(cdf) == Keys({42}), "in-place Slice preserves keys");

  OK(cdf.Assign({{0, kMax, 1}}), "full uint64 domain singleton");
  Check(cdf.Select(0) == kMax, "2^64 width arithmetic");
  OK(cdf.Assign({{0, kMax, 2}}), "full uint64 domain pair");
  Check(SelectAll(cdf) == Keys({(uint64_t{1} << 63) - 1, kMax}), "full-width pair reference");
  OK(cdf.Assign({{0, kMax, kMax}}), "largest representable count");
  for (uint64_t rank : Keys({0, 1, (uint64_t{1} << 63), kMax - 1})) {
    const Wide span = Wide{1} << 64;
    const uint64_t expected = static_cast<uint64_t>(((Wide{rank} + 1) * span + kMax - 1) / kMax - 1);
    const auto selected = cdf.Select(rank);
    Check(selected == expected, "uint128 rank product reference");
    Check(cdf.CountThrough(selected) == rank + 1, "large-count rank identity");
  }
  Check(cdf.CountThrough(kMax) == kMax, "largest count endpoint");
  Check(SelectAll(cdf.Slice(kMax - 2, 2)) == Keys({kMax - 1, kMax}),
        "cursor slice near maximum original rank");

  // An independent floor-CDF oracle across deterministic, feasible intervals.
  std::mt19937_64 random(0x5eed);
  for (unsigned trial = 0; trial < 128; ++trial) {
    const uint64_t lo = random() % 1000;
    const uint64_t width = 1 + random() % 73;
    const uint64_t count = 1 + random() % width;
    OK(cdf.Assign({{lo, lo + width - 1, count}}), "random feasible cell");
    Keys expected;
    for (uint64_t x = 0; x < width; ++x) {
      const uint64_t before = x * count / width;
      const uint64_t through = (x + 1) * count / width;
      if (through > before) expected.push_back(lo + x);
      Check(cdf.CountThrough(lo + x) == through, "independent floor-CDF enumeration");
    }
    Check(SelectAll(cdf) == expected, "random independent select oracle");
    if (count > 2) {
      const auto sliced = cdf.Slice(1, count - 2);
      Check(SelectAll(sliced) == Keys(expected.begin() + 1, expected.end() - 1), "random sliced phase");
    }
  }
  OK(cdf.Assign({}), "empty certificate");
  Check(cdf.Empty() && cdf.Count() == 0 && SelectAll(cdf).empty(), "empty semantics");
  std::cout << "{\"record_type\":\"cdf_tests\",\"status\":\"PASS\"}\n";
}

#ifndef VCOMP_DISCRETE_CDF_ONLY
std::vector<Keys> Dataset(const std::string& name) {
  if (name == "dense1") return {{0}};
  if (name == "dense3") return {{0, 1, 2}};
  if (name == "dense5") return {{0, 1, 2, 3, 4}};
  if (name == "max_singleton") return {{kMax}};
  if (name == "endpoint_overlap") return {{0, 1, kMax - 1, kMax}, {0, 3, kMax}};
  if (name == "max_dense5") return {{kMax - 4, kMax - 3, kMax - 2, kMax - 1, kMax}};
  std::vector<Keys> result(4);
  if (name == "disjoint") {
    for (uint64_t i = 0; i < 256; ++i) result[i % 4].push_back(i);
  } else if (name == "complete_overlap") {
    for (uint64_t i = 0; i < 128; ++i)
      for (auto& keys : result) keys.push_back(i * i * 3);
  } else if (name == "hot") {
    for (uint64_t i = 0; i < 256; ++i) {
      const uint64_t key = i < 64 ? i : 1000 + (i - 64) * 17;
      if (i < 64) for (auto& keys : result) keys.push_back(key);
      else if (i < 128) {
        result[i % 4].push_back(key);
        result[(i + 1) % 4].push_back(key);
      } else result[i % 4].push_back(key);
    }
  } else if (name == "random_gapped") {
    std::mt19937_64 random(0xcdf20260908ULL);
    uint64_t key = 0;
    for (uint64_t i = 0; i < 256; ++i) {
      key += 1 + random() % 4096;
      result[i % 4].push_back(key);
      if (i % 3 == 0) result[(i + 1) % 4].push_back(key);
    }
  } else throw std::runtime_error("unknown dataset: " + name);
  for (auto& keys : result) Unique(&keys);
  return result;
}

std::vector<r::VirtualSST> Describe(const std::vector<Keys>& inputs, size_t samples) {
  std::vector<r::VirtualSST> result;
  for (const auto& keys : inputs) {
    Check(!keys.empty() && Strict(keys), "valid fixture input");
    r::VirtualSST file{};
    file.key_min = keys.front();
    file.key_max = keys.back();
    file.num_entries = file.size_bytes = keys.size();
    file.level = 0;
    file.plr_model = r::GreedyPLRFit(keys, 8);
    file.kmv_sketch = r::BuildKMVSketchFromSortedKeys(keys, samples);
    file.kmv_ranges = r::BuildKMVRangeSketchesFromSortedKeys(keys, samples, 8);
    OK(r::CertifyVirtualSST(&file), "flush descriptor certification");
    Check(file.num_entries == keys.size(), "certification preserves known flush count");
    const auto generated = r::MaterializeKeys(file);
    Check(generated.size() == keys.size() && Strict(generated), "certified flush materialization");
    if (file.kmv_sketch.complete) Check(generated == keys, "complete certified flush key identity");
    result.push_back(std::move(file));
  }
  return result;
}

Keys Witnesses(const std::vector<r::VirtualSST>& files, bool extrema) {
  Keys witnesses;
  for (const auto& file : files) {
    if (extrema) {
      witnesses.push_back(file.key_min);
      witnesses.push_back(file.key_max);
    }
    for (const auto& sample : file.kmv_sketch.samples) witnesses.push_back(sample.key);
    for (const auto& range : file.kmv_ranges)
      for (const auto& sample : range.sketch.samples) witnesses.push_back(sample.key);
  }
  Unique(&witnesses);
  return witnesses;
}

void BuilderContractTests() {
  auto files = Describe({{0, 1, 2, 3, 4}}, 4096);
  std::vector<const r::VirtualSST*> pointers = {&files.front()};
  r::PLRModel output;
  uint64_t accepted = 91;
  for (uint64_t request : Keys({0, 2, 5, kMax})) {
    OK(r::BuildDiscreteMergeModel(pointers, request, &output, &accepted), "builder target projection");
    Check(accepted == 5 && output.DiscreteModel() != nullptr &&
          SelectAll(*output.DiscreteModel()) == Keys({0, 1, 2, 3, 4}),
          "projected target protects witnesses and respects support capacity");
  }
  r::DiscreteCDF sentinel;
  OK(sentinel.Assign({{777, 777, 1}}), "builder failure output sentinel");
  output.SetDiscreteModel(sentinel);
  accepted = 91;
  auto unchanged = [&]() {
    Check(accepted == 91 && output.DiscreteModel() != nullptr &&
          SelectAll(*output.DiscreteModel()) == Keys({777}), "failed builder preserves outputs");
  };
  Check(!r::BuildDiscreteMergeModel(pointers, 5, nullptr, &accepted).ok(), "builder null model rejected");
  unchanged();
  Check(!r::BuildDiscreteMergeModel(pointers, 5, &output, nullptr).ok(), "builder null count rejected");
  unchanged();
  Check(!r::BuildDiscreteMergeModel({nullptr}, 5, &output, &accepted).ok(), "builder null descriptor rejected");
  unchanged();
  for (unsigned variant = 0; variant < 5; ++variant) {
    auto invalid = files.front();
    if (variant == 0) { invalid.key_min = 10; invalid.key_max = 9; }
    if (variant == 1) invalid.kmv_sketch.samples.push_back({99, 123});
    if (variant == 2) invalid.kmv_ranges.front().key_max = 99;
    if (variant == 3) invalid.kmv_ranges.front().sketch.samples.push_back({99, 123});
    if (variant == 4) invalid.num_entries = 1;
    Check(!r::BuildDiscreteMergeModel({&invalid}, 5, &output, &accepted).ok(),
          "invalid bounds/sample/witness count rejected");
    unchanged();
  }
  auto sparse = Describe({{0, 100}}, 4096);
  Check(!r::BuildDiscreteMergeModel({&sparse.front()}, 5, &output, &accepted).ok(),
        "projected target above input descriptor sum rejected");
  unchanged();
  auto huge = files.front();
  huge.key_min = 0; huge.key_max = kMax; huge.num_entries = kMax;
  Check(!r::BuildDiscreteMergeModel({&huge, &huge}, kMax, &output, &accepted).ok(),
        "input descriptor sum overflow rejected");
  unchanged();
  OK(r::BuildDiscreteMergeModel({}, 25, &output, &accepted), "empty merge projection");
  Check(accepted == 0 && output.DiscreteModel() != nullptr && output.DiscreteModel()->Empty(),
        "empty input produces empty certificate");
  std::cout << "{\"record_type\":\"builder_tests\",\"status\":\"PASS\"}\n";
}

void Pipeline(const std::string& name, size_t samples, uint64_t target, bool gp) {
  const auto actual = Dataset(name);
  const auto truth = Union(actual);
  auto files = Describe(actual, samples);
  const bool complete = std::all_of(files.begin(), files.end(),
      [](const r::VirtualSST& file) { return file.kmv_sketch.complete; });
  std::vector<uint64_t> cuts;
  if (gp) {
    cuts = {0, kMax};
    for (size_t i = 0; i < truth.size(); i += std::max<size_t>(1, truth.size() / 7)) cuts.push_back(truth[i]);
    Unique(&cuts);
  }
  for (unsigned round = 1; round <= 4; ++round) {
    std::vector<const r::VirtualSST*> pointers;
    uint64_t input_count = 0;
    for (const auto& file : files) { pointers.push_back(&file); input_count += file.num_entries; }
    const auto witnesses = Witnesses(files, true);
    uint64_t accepted = 0;
    r::Status merge_status;
    auto merged = r::NWayMergeKMVRangeAware(pointers, &accepted, samples, &merge_status);
    OK(merge_status, "merge status");
    const auto* certificate = merged.DiscreteModel();
    Check(certificate != nullptr, "merged certificate present");
    Check(certificate->Count() == accepted, "accepted count matches certificate");
    const auto merged_keys = SelectAll(*certificate);
    Check(accepted <= input_count, "dedup target cannot exceed input count sum");
    Check(std::includes(merged_keys.begin(), merged_keys.end(), witnesses.begin(), witnesses.end()),
          "merged materialized membership retains every input sample/extremum witness");
    if (complete) Check(merged_keys == truth, "complete merge exactly preserves original key union");
    auto outputs = r::SplitIntoSSTs(merged, accepted, target, 1, truth.front(), truth.back(), 1, cuts, &pointers, samples);
    Keys concatenated;
    uint64_t sum = 0;
    for (size_t index = 0; index < outputs.size(); ++index) {
      const auto& file = outputs[index];
      Check(file.num_entries > 0 && file.key_min <= file.key_max, "nonempty feasible split");
      Check(Wide{file.num_entries} <= Wide{file.key_max} - file.key_min + 1, "inclusive child capacity");
      Check(file.level == 1, "requested output level");
      if (index) Check(outputs[index - 1].key_max < file.key_min, "strictly nonoverlapping siblings");
      const auto* child = file.plr_model.DiscreteModel();
      Check(child != nullptr && child->Count() == file.num_entries, "child certificate count");
      auto keys = r::MaterializeKeys(file);
      Check(keys == SelectAll(*child), "actual MaterializeKeys equals certificate Select/cursor");
      Check(keys.size() == file.num_entries && Strict(keys), "actual materializer count and strict order");
      Check(keys.front() == file.key_min && keys.back() == file.key_max, "actual extrema equal descriptor bounds");
      uint64_t range_sum = 0;
      for (const auto& range : file.kmv_ranges) {
        Check(range.key_min >= file.key_min && range.key_max <= file.key_max && range.key_min <= range.key_max,
              "child bucket in descriptor bounds");
        const uint64_t exact = std::upper_bound(keys.begin(), keys.end(), range.key_max) -
                               std::lower_bound(keys.begin(), keys.end(), range.key_min);
        Check(exact == range.num_entries, "bucket count agrees with selected keys");
        range_sum += range.num_entries;
        if (complete) {
          Check(range.sketch.complete, "complete range sketches remain complete");
          Keys bucket_samples;
          for (const auto& sample : range.sketch.samples) bucket_samples.push_back(sample.key);
          Unique(&bucket_samples);
          const Keys bucket_truth(std::lower_bound(keys.begin(), keys.end(), range.key_min),
                                  std::upper_bound(keys.begin(), keys.end(), range.key_max));
          Check(bucket_samples == bucket_truth, "complete bucket sample IDs equal selected keys");
        }
      }
      Check(range_sum == file.num_entries, "range bucket entry sum equals child count");
      if (complete) {
        Check(file.kmv_sketch.complete, "complete global sketches remain complete");
        Keys global_samples;
        for (const auto& sample : file.kmv_sketch.samples) global_samples.push_back(sample.key);
        Unique(&global_samples);
        Check(global_samples == keys, "complete global sample IDs equal selected keys");
      }
      concatenated.insert(concatenated.end(), keys.begin(), keys.end());
      sum += file.num_entries;
    }
    Check(sum == accepted, "split descriptor sum preserves accepted target");
    Check(concatenated == merged_keys, "split concatenation exactly equals unsplit selection sequence");
    if (complete) {
      Check(concatenated == truth, "complete actual materialization preserves original union");
      Check(Witnesses(outputs, false) == truth, "complete output sketches preserve all original sample IDs");
    }
    const uint64_t missing = std::count_if(truth.begin(), truth.end(), [&](uint64_t k) {
      return !std::binary_search(concatenated.begin(), concatenated.end(), k);
    });
    std::cout << "{\"record_type\":\"pipeline\",\"case\":\"" << name
              << "\",\"samples\":" << samples << ",\"complete_input_control\":" << (complete ? "true" : "false")
              << ",\"target_file_entries\":" << target << ",\"grandparent_cuts\":" << cuts.size()
              << ",\"round\":" << round << ",\"original_union\":" << truth.size()
              << ",\"input_descriptor_sum\":" << input_count << ",\"accepted_target\":" << accepted
              << ",\"split_descriptor_sum\":" << sum << ",\"generated_distinct\":" << concatenated.size()
              << ",\"input_witness_union\":" << witnesses.size() << ",\"output_sample_union\":" << Witnesses(outputs, false).size()
              << ",\"missing_original_keys\":" << missing << ",\"invented_keys\":" << concatenated.size() - (truth.size() - missing)
              << ",\"status\":\"PASS\"}\n";
    ++pipeline_rounds;
    files = std::move(outputs);
  }
}
#endif  // VCOMP_DISCRETE_CDF_ONLY

}  // namespace

int main(int argc, char** argv) {
  bool cdf_only = false;
  bool pipeline_only = false;
  if (argc == 2 && std::string(argv[1]) == "--cdf-only") cdf_only = true;
  else if (argc == 2 && std::string(argv[1]) == "--pipeline-only") pipeline_only = true;
  else if (argc != 1) { std::cerr << "usage: " << argv[0] << " [--cdf-only|--pipeline-only]\n"; return 2; }
  setenv("VCOMP_KMV_ENABLED", "1", 1);
  setenv("VCOMP_DISCRETE_CDF_ENABLED", "1", 1);
  std::string context = "independent CDF";
  try {
    if (!pipeline_only) IndependentCDFTests();
#ifndef VCOMP_DISCRETE_CDF_ONLY
    if (!cdf_only) {
      context = "builder contracts";
      BuilderContractTests();
      for (const std::string name : {"dense1", "dense3", "dense5", "max_singleton", "max_dense5",
                                      "endpoint_overlap", "random_gapped", "disjoint", "hot", "complete_overlap"}) {
        for (uint64_t target : {1, 2, 7, 31}) for (bool gp : {false, true}) {
          context = name + " complete target=" + std::to_string(target) + " gp=" + std::to_string(gp);
          Pipeline(name, 4096, target, gp);
        }
      }
      for (const std::string name : {"random_gapped", "disjoint", "hot", "complete_overlap"}) {
        for (size_t samples : {8, 32}) for (bool gp : {false, true}) {
          context = name + " budget=" + std::to_string(samples) + " gp=" + std::to_string(gp);
          Pipeline(name, samples, 31, gp);
        }
      }
    }
#endif
    std::cout << "{\"record_type\":\"summary\",\"status\":\"PASS\",\"checks\":" << checks
              << ",\"pipeline_rounds\":" << pipeline_rounds << "}\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "FAIL [" << context << "]: " << error.what() << '\n';
    return 1;
  }
}
