// CPU-only exact-key oracle for exported virtual-compaction APIs.
// Does not open RocksDB, write SSTs, or modify any existing artifact.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <map>
#include <numeric>
#include <sstream>
#include <string>
#include <vector>

#include "db/virtual_compaction/virtual_sst.h"

namespace {
namespace r = ROCKSDB_NAMESPACE;
using Keys = std::vector<uint64_t>;
std::string Quote(const std::string& s) {
  std::string out = "\"";
  for (char c : s) { if (c == '\\' || c == '"') out += '\\'; out += c; }
  return out + '"';
}
struct Record {
  std::map<std::string, std::string> values;
  void Text(const std::string& key, const std::string& value) { values[key] = Quote(value); }
  template<class T> void Number(const std::string& key, T value) {
    std::ostringstream out; out.precision(17); out << value; values[key] = out.str();
  }
  void Print() const {
    std::cout << '{'; bool comma = false;
    for (const auto& [key, value] : values) {
      if (comma) std::cout << ','; comma = true;
      std::cout << Quote(key) << ':' << value;
    }
    std::cout << "}\n";
  }
};
void Unique(Keys* keys) {
  std::sort(keys->begin(), keys->end());
  keys->erase(std::unique(keys->begin(), keys->end()), keys->end());
}
Keys Union(const std::vector<Keys>& inputs) {
  Keys keys;
  for (const auto& in : inputs) keys.insert(keys.end(), in.begin(), in.end());
  Unique(&keys); return keys;
}
std::vector<Keys> Dataset(const std::string& name, uint64_t offset = 0) {
  if (name == "toy_dense3" || name == "toy_dense5") {
    Keys keys(name == "toy_dense3" ? 3 : 5);
    std::iota(keys.begin(), keys.end(), offset);
    return {keys};
  }
  constexpr uint64_t n = 4096;
  std::vector<Keys> inputs(4);
  if (name == "disjoint_interleaved") {
    for (uint64_t rank = 0; rank < 4*n; ++rank) inputs[rank % 4].push_back(offset + rank);
  } else if (name == "hot_shared_core") {
    for (uint64_t rank = 0; rank < 2*n; ++rank) {
      if (rank < n/2) {
        for (auto& input : inputs) input.push_back(offset + rank);
      } else if (rank < n) {
        const uint64_t key = offset + n/2 + (rank-n/2)*4;
        inputs[rank%4].push_back(key); inputs[(rank+1)%4].push_back(key);
      } else {
        inputs[rank%4].push_back(offset + 10240 + (rank-n)*32);
      }
    }
  } else {
    for (uint64_t rank = 0; rank < 2*n; ++rank) {
      const uint64_t key = offset + (name == "gapped_50" ? (rank/128)*4096 + (rank%128)*3 : rank);
      inputs[rank%4].push_back(key); inputs[(rank+1)%4].push_back(key);
    }
  }
  for (auto& input : inputs) Unique(&input);
  return inputs;
}
std::vector<r::VirtualSST> Describe(const std::vector<Keys>& inputs, double error, size_t samples) {
  std::vector<r::VirtualSST> outputs;
  for (const auto& input : inputs) {
    r::VirtualSST descriptor{};
    descriptor.key_min = input.front(); descriptor.key_max = input.back();
    descriptor.num_entries = input.size(); descriptor.level = 0;
    descriptor.size_bytes = input.size();
    descriptor.plr_model = r::GreedyPLRFit(input, error);
    descriptor.kmv_sketch = r::BuildKMVSketchFromSortedKeys(input, samples);
    descriptor.kmv_ranges = r::BuildKMVRangeSketchesFromSortedKeys(input, samples, 8);
    outputs.push_back(std::move(descriptor));
  }
  return outputs;
}
std::vector<const r::VirtualSST*> Pointers(const std::vector<r::VirtualSST>& inputs) {
  std::vector<const r::VirtualSST*> result;
  for (const auto& input : inputs) result.push_back(&input);
  return result;
}

// Count-only reference for the current db_bench streaming loop. Actual APIs
// supply descriptors/ranges/models; this reference performs no SST I/O. Its
// output is kept separate from MaterializeKeys(), whose tail is capped without
// deduplication. It is corroborating evidence, not a replacement implementation.
Keys StreamReference(const r::VirtualSST& descriptor) {
  Keys keys;
  if (descriptor.num_entries == 0 || descriptor.plr_model.Empty() ||
      descriptor.key_min > descriptor.key_max) return keys;
  const auto& segments = descriptor.plr_model.Segments();
  size_t segment = 0;
  for (uint64_t pos = 0; pos < descriptor.num_entries; ++pos) {
    double position = static_cast<double>(pos);
    while (segment + 1 < segments.size()) {
      double pos_end = segments[segment].slope * static_cast<double>(segments[segment].key_end) + segments[segment].intercept;
      if (position <= pos_end) break;
      ++segment;
    }
    const auto& s = segments[segment];
    uint64_t key = 0;
    if (std::abs(s.slope) < 1e-15) key = (s.key_start+s.key_end)/2;
    else {
      double key_d = (position-s.intercept)/s.slope;
      key_d = std::max(key_d,static_cast<double>(s.key_start));
      key_d = std::min(key_d,static_cast<double>(s.key_end));
      key = static_cast<uint64_t>(std::round(key_d));
    }
    key = std::max(key, descriptor.key_min); key = std::min(key, descriptor.key_max);
    if (!keys.empty() && key <= keys.back()) {
      if (keys.back() == UINT64_MAX) break;
      key = keys.back()+1;
    }
    if (key > descriptor.key_max) break;
    keys.push_back(key);
  }
  return keys;
}
std::vector<r::VirtualSST> TrimSameLevel(std::vector<r::VirtualSST> outputs) {
  std::sort(outputs.begin(), outputs.end(), [](const auto& a, const auto& b) {
    if (a.level != b.level) return a.level < b.level;
    if (a.key_min != b.key_min) return a.key_min < b.key_min;
    return a.key_max < b.key_max;
  });
  bool previous = false; int level = -1; uint64_t maximum = 0;
  for (auto& output : outputs) {
    if (!previous || output.level != level || output.level == 0) {
      level = output.level; maximum = output.key_max; previous = output.level != 0; continue;
    }
    if (output.key_min <= maximum) output.key_min = maximum == UINT64_MAX ? maximum : maximum+1;
    maximum = std::max(maximum, output.key_max);
  }
  return outputs;
}
void MaterializedMetrics(Record* record, const std::string& prefix,
                         const std::vector<r::VirtualSST>& outputs, const Keys& truth) {
  uint64_t planned=0, raw_rows=0, local_distinct=0, stream_rows=0, impossible=0, invalid=0;
  uint64_t raw_internal_duplicates=0, raw_stream_disagreements=0, overlaps=0;
  Keys all_stream, all_library;
  std::ostringstream ranges; ranges << '[';
  for (size_t i=0; i<outputs.size(); ++i) {
    const auto& output = outputs[i]; planned += output.num_entries;
    const bool bad = output.key_max < output.key_min;
    const uint64_t capacity = bad ? 0 : output.key_max-output.key_min+1;
    invalid += bad; impossible += output.num_entries > capacity;
    for (size_t j=0; j<i; ++j)
      overlaps += !bad && outputs[j].key_min<=outputs[j].key_max &&
                  std::max(output.key_min,outputs[j].key_min)<=std::min(output.key_max,outputs[j].key_max);
    Keys raw = bad ? Keys{} : r::MaterializeKeys(output);
    raw_rows += raw.size(); const auto raw_size=raw.size(); Unique(&raw);
    local_distinct += raw.size(); raw_internal_duplicates += raw_size-raw.size();
    all_library.insert(all_library.end(),raw.begin(),raw.end());
    auto stream=StreamReference(output); stream_rows += stream.size();
    raw_stream_disagreements += raw != stream;
    all_stream.insert(all_stream.end(),stream.begin(),stream.end());
    if (truth.size()<=5) {
      if (i) ranges << ',';
      ranges << "{\"minimum\":" << output.key_min << ",\"maximum\":" << output.key_max
             << ",\"planned\":" << output.num_entries << ",\"capacity\":" << capacity
             << ",\"stream_reference_keys\":[";
      for(size_t k=0;k<stream.size();++k){if(k)ranges<<',';ranges<<stream[k];}
      ranges << "]}";
    }
  }
  ranges << ']'; Unique(&all_stream); Unique(&all_library);
  uint64_t missing=0, invented=0, library_missing=0, library_invented=0;
  for(auto key:truth) missing += !std::binary_search(all_stream.begin(),all_stream.end(),key);
  for(auto key:all_stream) invented += !std::binary_search(truth.begin(),truth.end(),key);
  for(auto key:truth) library_missing += !std::binary_search(all_library.begin(),all_library.end(),key);
  for(auto key:all_library) library_invented += !std::binary_search(truth.begin(),truth.end(),key);
  record->Number(prefix+"_planned_entries",planned); record->Number(prefix+"_files",outputs.size());
  record->Number(prefix+"_invalid_ranges",invalid); record->Number(prefix+"_capacity_violations",impossible);
  record->Number(prefix+"_overlapping_range_pairs",overlaps); record->Number(prefix+"_library_raw_vector_rows",raw_rows);
  record->Number(prefix+"_library_perfile_distinct_sum",local_distinct);
  record->Number(prefix+"_library_within_file_duplicates",raw_internal_duplicates);
  record->Number(prefix+"_library_distinct_union",all_library.size());
  record->Number(prefix+"_library_missing_original_keys",library_missing);
  record->Number(prefix+"_library_invented_keys",library_invented);
  record->Number(prefix+"_stream_reference_rows",stream_rows);
  record->Number(prefix+"_stream_reference_drop",planned-stream_rows);
  record->Number(prefix+"_stream_distinct_union",all_stream.size());
  record->Number(prefix+"_crossfile_duplicate_rows",stream_rows-all_stream.size());
  record->Number(prefix+"_missing_original_keys",missing); record->Number(prefix+"_invented_keys",invented);
  record->Number(prefix+"_library_unique_vs_stream_disagree_files",raw_stream_disagreements);
  if(truth.size()<=5)record->values[prefix+"_output_ranges"]=ranges.str();
}
void Pipeline(const std::string& name,size_t samples,double error,bool grandparents) {
  auto inputs=Dataset(name);const auto truth=Union(inputs);
  auto descriptors=Describe(inputs,error,samples);const auto pointers=Pointers(descriptors);
  uint64_t sum=0;size_t segments=0;bool complete=true;
  for(const auto& d:descriptors){sum+=d.num_entries;segments+=d.plr_model.NumSegments();complete &= d.kmv_sketch.complete;}
  const auto union_estimate=r::EstimateKMVUnionEntries(pointers,sum,samples);
  uint64_t target=0;auto merged=r::NWayMergeKMVRangeAware(pointers,&target,samples);
  const auto oracle=r::GreedyPLRFit(truth,error);
  const uint64_t sst_entries=truth.size()<=5?2:512;
  std::vector<uint64_t> boundaries;
  if(grandparents)for(size_t i=1;i<5;++i)boundaries.push_back(truth[(truth.size()*i)/5]);
  for(const auto& path:std::vector<std::string>{"kmv_merge","exact_union_refit_control"}) {
    const bool kmv=path=="kmv_merge";const auto& model=kmv?merged:oracle;
    const uint64_t current_target=kmv?target:truth.size();
    auto outputs=r::SplitIntoSSTs(model,current_target,sst_entries,1,truth.front(),truth.back(),1,
                                 boundaries,kmv?&pointers:nullptr,samples);
    Record row;row.Text("record_type","pipeline");row.Text("case",name);row.Text("path",path);
    row.Number("kmv_samples",samples);row.Number("kmv_range_buckets",8);row.Number("plr_error",error);
    row.Number("all_input_global_sketches_complete",complete);row.Number("input_files",inputs.size());
    row.Number("input_entry_sum",sum);row.Number("true_union",truth.size());row.Number("input_plr_segments",segments);
    row.Number("kmv_union_estimate",union_estimate);row.Number("merged_target",target);
    row.Number("kmv_union_error",static_cast<int64_t>(union_estimate)-static_cast<int64_t>(truth.size()));
    row.Number("this_path_target",current_target);row.Number("merged_segments",model.NumSegments());
    row.Number("sst_target_entries",sst_entries);row.Number("grandparent_boundaries",boundaries.size());
    if(truth.size()<=5 && !model.Empty())row.Number("first_segment_slope",model.Segments().front().slope);
    MaterializedMetrics(&row,"independent_inputs",descriptors,truth);
    r::VirtualSST unsplit{};unsplit.plr_model=model;unsplit.key_min=truth.front();unsplit.key_max=truth.back();
    unsplit.num_entries=current_target;unsplit.level=1;
    MaterializedMetrics(&row,"unsplit",{unsplit},truth);
    MaterializedMetrics(&row,"raw_split",outputs,truth);
    MaterializedMetrics(&row,"trimmed_split",TrimSameLevel(outputs),truth);
    row.Print();
  }
}
void OffsetSeries(const std::string& name,size_t samples) {
  for(uint64_t seed=0;seed<32;++seed) {
    const auto offset=seed*10000019ULL;
    const auto inputs=Dataset(name,offset);const auto truth=Union(inputs);
    std::vector<r::VirtualSST> descriptors;
    uint64_t sum=0;
    for(const auto& input:inputs) {
      r::VirtualSST d{};d.key_min=input.front();d.key_max=input.back();d.num_entries=input.size();
      d.kmv_sketch=r::BuildKMVSketchFromSortedKeys(input,samples);sum+=input.size();descriptors.push_back(std::move(d));
    }
    const auto pointers=Pointers(descriptors);const auto estimate=r::EstimateKMVUnionEntries(pointers,sum,samples);
    Record row;row.Text("record_type","single_merge_hash_offset");row.Text("case",name);
    row.Number("offset_index",seed);row.Number("key_offset",offset);row.Number("kmv_samples",samples);
    row.Number("input_entry_sum",sum);row.Number("true_union",truth.size());row.Number("estimate",estimate);
    row.Number("error",static_cast<int64_t>(estimate)-static_cast<int64_t>(truth.size()));
    row.Number("relative_error",static_cast<double>(estimate)/truth.size()-1.0);row.Print();
  }
}
}  // namespace
int main() {
  setenv("VCOMP_KMV_ENABLED","1",1);
  for(const auto& toy:std::vector<std::string>{"toy_dense3","toy_dense5"})Pipeline(toy,16384,0,false);
  for(const auto& name:std::vector<std::string>{"disjoint_interleaved","uniform_50","hot_shared_core","gapped_50"}) {
    for(size_t samples:{512,2048,16384})for(double error:{8.0,1.0})for(bool gp:{false,true})Pipeline(name,samples,error,gp);
    for(size_t samples:{512,2048})OffsetSeries(name,samples);
  }
  return 0;
}
