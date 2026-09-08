// Repeated-merge closure control; shares the separately archived oracle helpers.
// Only library descriptors are carried forward. Original keys remain read-only
// oracle state. No new keys, DB, SST, or storage workload are introduced.
#define main SingleStageAccuracyProbeMain
#include "virtual_compaction_accuracy_probe.cc"
#undef main

namespace {
void SketchState(Record* record, const std::string& prefix,
                  const std::vector<r::VirtualSST>& descriptors, const Keys& truth) {
  uint64_t entries=0, global_samples=0, range_samples=0, range_entries=0;
  uint64_t empty_global=0,incomplete_global=0,empty_range=0,incomplete_range=0,ranges=0;
  Keys retained_samples;
  std::vector<bool> covered(truth.size(),false);
  for(const auto& d:descriptors) {
    entries+=d.num_entries;global_samples+=d.kmv_sketch.samples.size();
    empty_global+=d.kmv_sketch.samples.empty();incomplete_global+=!d.kmv_sketch.complete;
    for(const auto& sample:d.kmv_sketch.samples)retained_samples.push_back(sample.key);
    for(const auto& range:d.kmv_ranges) {
      ++ranges;range_samples+=range.sketch.samples.size();range_entries+=range.num_entries;
      empty_range+=range.sketch.samples.empty();incomplete_range+=!range.sketch.complete;
    }
    auto begin=std::lower_bound(truth.begin(),truth.end(),d.key_min);
    auto end=std::upper_bound(truth.begin(),truth.end(),d.key_max);
    if(begin<=end)for(auto it=begin;it!=end;++it)covered[it-truth.begin()]=true;
  }
  Unique(&retained_samples);
  const auto covered_count=std::count(covered.begin(),covered.end(),true);
  record->Number(prefix+"_descriptor_files",descriptors.size());record->Number(prefix+"_descriptor_entries",entries);
  record->Number(prefix+"_global_sample_entries",global_samples);record->Number(prefix+"_global_sample_distinct_union",retained_samples.size());
  record->Number(prefix+"_global_empty_sketches",empty_global);record->Number(prefix+"_global_incomplete_sketches",incomplete_global);
  record->Number(prefix+"_range_sketches",ranges);record->Number(prefix+"_range_sample_entries",range_samples);
  record->Number(prefix+"_range_estimated_entry_sum",range_entries);record->Number(prefix+"_range_empty_sketches",empty_range);
  record->Number(prefix+"_range_incomplete_sketches",incomplete_range);
  record->Number(prefix+"_original_keys_covered_by_any_descriptor_range",covered_count);
  record->Number(prefix+"_original_keys_outside_all_descriptor_ranges",truth.size()-covered_count);
}
void Chain(const std::string& name,size_t samples) {
  const auto initial=Dataset(name);const auto truth=Union(initial);
  auto state=Describe(initial,8,samples);
  for(unsigned int round=1;round<=4;++round) {
    const auto pointers=Pointers(state);
    uint64_t input_sum=0,minimum=UINT64_MAX,maximum=0;
    for(const auto& d:state){input_sum+=d.num_entries;minimum=std::min(minimum,d.key_min);maximum=std::max(maximum,d.key_max);}
    const auto estimate=r::EstimateKMVUnionEntries(pointers,input_sum,samples);
    uint64_t target=0;const auto merged=r::NWayMergeKMVRangeAware(pointers,&target,samples);
    auto outputs=r::SplitIntoSSTs(merged,target,512,1,minimum,maximum,1,{},&pointers,samples);
    Record record;record.Text("record_type","fixed_original_set_remerge");record.Text("case",name);
    record.Number("round",round);record.Number("kmv_samples",samples);record.Number("range_buckets",8);
    record.Number("initial_plr_error",8);record.Number("sst_target_entries",512);record.Number("grandparent_boundaries",0);
    record.Number("original_input_entry_sum",16384);record.Number("true_original_union",truth.size());
    record.Number("isolated_union_estimate",estimate);record.Number("merged_target",target);
    record.Number("this_round_entry_drop",input_sum-target);
    record.Number("target_minus_true_union",static_cast<int64_t>(target)-static_cast<int64_t>(truth.size()));
    record.Text("carried_state","raw SplitIntoSSTs descriptors, never synthetic materialized keys");
    SketchState(&record,"input",state,truth);SketchState(&record,"output",outputs,truth);
    MaterializedMetrics(&record,"raw_outputs",outputs,truth);
    MaterializedMetrics(&record,"trimmed_outputs_diagnostic_only",TrimSameLevel(outputs),truth);
    record.Print();state=std::move(outputs);
  }
}
}  // namespace
int main() {
  setenv("VCOMP_KMV_ENABLED","1",1);
  for(const auto& name:std::vector<std::string>{"disjoint_interleaved","hot_shared_core","gapped_50"})
    for(size_t samples:{512,2048,16384})Chain(name,samples);
  return 0;
}
