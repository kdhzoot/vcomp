//  Copyright (c) 2011-present, Facebook, Inc.  All rights reserved.
//  This source code is licensed under both the GPLv2 (found in the
//  COPYING file in the root directory) and Apache 2.0 License
//  (found in the LICENSE.Apache file in the root directory).

#include "db/virtual_compaction/virtual_sst.h"

#include <algorithm>
#include <cstdlib>
#include <limits>
#include <numeric>
#include <string>
#include <vector>

#include "db/virtual_compaction/virtual_sst_registry.h"
#include "port/stack_trace.h"
#include "test_util/testharness.h"

namespace ROCKSDB_NAMESPACE {
namespace {

class ScopedEnvVar {
 public:
  ScopedEnvVar(const char* name, const char* value) : name_(name) {
    const char* old_value = std::getenv(name);
    if (old_value != nullptr) {
      had_old_value_ = true;
      old_value_ = old_value;
    }
    setenv(name, value, 1);
  }

  ~ScopedEnvVar() {
    if (had_old_value_) {
      setenv(name_.c_str(), old_value_.c_str(), 1);
    } else {
      unsetenv(name_.c_str());
    }
  }

 private:
  std::string name_;
  std::string old_value_;
  bool had_old_value_ = false;
};

VirtualSST MakeVirtualSST(std::vector<uint64_t> keys, int level = 0,
                          size_t kmv_samples = 64,
                          size_t kmv_range_buckets = 4) {
  std::sort(keys.begin(), keys.end());
  keys.erase(std::unique(keys.begin(), keys.end()), keys.end());

  VirtualSST vsst;
  vsst.plr_model = GreedyPLRFit(keys, 0.0);
  vsst.kmv_sketch = BuildKMVSketchFromSortedKeys(keys, kmv_samples);
  vsst.kmv_ranges = BuildKMVRangeSketchesFromSortedKeys(
      keys, kmv_samples, kmv_range_buckets);
  vsst.key_min = keys.empty() ? 0 : keys.front();
  vsst.key_max = keys.empty() ? 0 : keys.back();
  vsst.num_entries = keys.size();
  vsst.level = level;
  vsst.size_bytes = keys.size();
  return vsst;
}

std::vector<uint64_t> KeyRange(uint64_t begin, uint64_t end) {
  std::vector<uint64_t> keys(end - begin);
  std::iota(keys.begin(), keys.end(), begin);
  return keys;
}

TEST(VirtualSSTTest, KMVRuntimeSwitch) {
  {
    ScopedEnvVar enabled("VCOMP_KMV_ENABLED", "true");
    ASSERT_TRUE(VirtualSSTKMVEnabled());
  }
  {
    ScopedEnvVar disabled("VCOMP_KMV_ENABLED", "off");
    ASSERT_FALSE(VirtualSSTKMVEnabled());
  }
}

TEST(VirtualSSTTest, PhysicalSizeModelControlsSplitAndRegistration) {
  auto input = MakeVirtualSST(KeyRange(0, 100));
  ASSERT_OK(CertifyVirtualSST(&input));
  SSTSizeModel physical;
  ASSERT_TRUE(physical.AddCalibration(10, 1100, 20, 2100));
  const auto output = SplitIntoSSTs(input.plr_model, 100, 1000, 50, 0, 99, 1,
                                  {}, nullptr, 0, &physical);
  ASSERT_EQ(output.size(), 12U);  // Nine entries per full file, not twenty.
  uint64_t count = 0;
  for (const auto& file : output) {
    count += file.num_entries;
    ASSERT_EQ(file.size_bytes, physical.Estimate(file.num_entries));
    ASSERT_LE(file.size_bytes, 1000U);
    ASSERT_EQ(MaterializeKeys(file).size(), file.num_entries);
  }
  ASSERT_EQ(count, 100U);

  const auto l0 = SplitIntoSSTs(input.plr_model, 100, 1000, 50, 0, 99, 0,
                              {}, nullptr, 0, &physical);
  ASSERT_EQ(l0.size(), 1U);
  ASSERT_EQ(l0[0].size_bytes, 10100U);

  VirtualSSTRegistry registry;
  registry.SetAvgEntrySize(50);
  registry.SetSSTSizeModel(physical);
  ASSERT_EQ(registry.GetAvgEntrySize(), 50U);  // Logical input batching unchanged.
  ASSERT_EQ(registry.GetSSTSizeModel().Estimate(10), 1100U);
}

TEST(VirtualSSTTest, PhysicalBytesDriveGrandparentThreshold) {
  auto input = MakeVirtualSST(KeyRange(0, 100));
  ASSERT_OK(CertifyVirtualSST(&input));
  SSTSizeModel physical;
  ASSERT_TRUE(physical.AddCalibration(10, 1100, 20, 2100));
  const auto output = SplitIntoSSTs(input.plr_model, 100, 1000, 50, 0, 99, 1,
                                  {5}, nullptr, 0, &physical);
  ASSERT_FALSE(output.empty());
  // 100 + 5*100 = 600 bytes crosses the first GP threshold (550 bytes).
  ASSERT_EQ(output.front().num_entries, 5U);
  ASSERT_EQ(output.front().size_bytes, 600U);
  const auto legacy = SplitIntoSSTs(input.plr_model, 100, 1000, 50, 0, 99, 1,
                                  {5});
  ASSERT_EQ(legacy.front().num_entries, 40U);  // 250 logical bytes did not cross.
}

TEST(VirtualSSTTest, CompleteKMVSketchDeduplicatesExactUnion) {
  auto left = MakeVirtualSST(KeyRange(0, 32));
  auto right = MakeVirtualSST(KeyRange(16, 48));
  std::vector<const VirtualSST*> inputs{&left, &right};

  ASSERT_TRUE(left.kmv_sketch.complete);
  ASSERT_TRUE(right.kmv_sketch.complete);
  ASSERT_EQ(48U, EstimateKMVUnionEntries(inputs, 64, 64));

  uint64_t merged_entries = 0;
  PLRModel merged = NWayMergeKMVRangeAware(inputs, &merged_entries, 64);
  ASSERT_FALSE(merged.Empty());
  ASSERT_EQ(48U, merged_entries);

  double previous_position = 0.0;
  for (uint64_t key = 0; key < 48; ++key) {
    const double position = merged.Predict(key);
    ASSERT_GE(position, previous_position);
    previous_position = position;
  }
}

TEST(VirtualSSTTest, IncompleteKMVEstimateIsBoundedByInputEntries) {
  auto left = MakeVirtualSST(KeyRange(0, 1000), 0, 64, 8);
  auto right = MakeVirtualSST(KeyRange(500, 1500), 0, 64, 8);
  std::vector<const VirtualSST*> inputs{&left, &right};

  ASSERT_FALSE(left.kmv_sketch.complete);
  ASSERT_FALSE(right.kmv_sketch.complete);
  const uint64_t estimate = EstimateKMVUnionEntries(inputs, 2000, 64);
  ASSERT_GT(estimate, 0U);
  ASSERT_LE(estimate, 2000U);
}

TEST(VirtualSSTTest, SplitPreservesEntryCountAndOrderedRanges) {
  const auto keys = KeyRange(0, 100);
  const auto source = MakeVirtualSST(keys);
  auto outputs =
      SplitIntoSSTs(source.plr_model, source.num_entries,
                    /*target_sst_size=*/25, /*avg_entry_size=*/1,
                    source.key_min, source.key_max, /*target_level=*/1);

  ASSERT_EQ(4U, outputs.size());
  uint64_t total_entries = 0;
  for (size_t i = 0; i < outputs.size(); ++i) {
    ASSERT_EQ(1, outputs[i].level);
    ASSERT_LE(outputs[i].num_entries, 25U);
    ASSERT_LE(outputs[i].key_min, outputs[i].key_max);
    if (i > 0) {
      ASSERT_LT(outputs[i - 1].key_max, outputs[i].key_min);
    }
    total_entries += outputs[i].num_entries;
  }
  ASSERT_EQ(source.num_entries, total_entries);
}

TEST(VirtualSSTTest, L0SplitProducesSingleOutput) {
  const auto source = MakeVirtualSST(KeyRange(0, 100));
  auto outputs =
      SplitIntoSSTs(source.plr_model, source.num_entries,
                    /*target_sst_size=*/10, /*avg_entry_size=*/1,
                    source.key_min, source.key_max, /*target_level=*/0);

  ASSERT_EQ(1U, outputs.size());
  ASSERT_EQ(source.num_entries, outputs.front().num_entries);
  ASSERT_EQ(source.key_min, outputs.front().key_min);
  ASSERT_EQ(source.key_max, outputs.front().key_max);
}

TEST(VirtualSSTTest, MaterializedKeysAreStrictlyIncreasingAndBounded) {
  const auto source = MakeVirtualSST(KeyRange(100, 200));
  auto keys = MaterializeKeys(source);

  ASSERT_EQ(source.num_entries, keys.size());
  ASSERT_EQ(source.key_min, keys.front());
  ASSERT_EQ(source.key_max, keys.back());
  for (size_t i = 1; i < keys.size(); ++i) {
    ASSERT_LT(keys[i - 1], keys[i]);
  }
}

TEST(VirtualSSTRegistryTest, HandleRemainsValidAfterRemoval) {
  VirtualSSTRegistry registry;
  registry.Register(7, MakeVirtualSST(KeyRange(10, 20), 2));

  auto handle = registry.Lookup(7);
  ASSERT_NE(nullptr, handle);
  registry.Remove(7);

  ASSERT_EQ(nullptr, registry.Lookup(7));
  ASSERT_EQ(10U, handle->key_min);
  ASSERT_EQ(19U, handle->key_max);
  ASSERT_EQ(2, handle->level);
}

TEST(VirtualSSTRegistryTest, SnapshotIsStableAcrossReplacement) {
  VirtualSSTRegistry registry;
  registry.Register(11, MakeVirtualSST(KeyRange(0, 10)));
  auto snapshot = registry.GetSnapshot();
  ASSERT_EQ(1U, snapshot.size());

  registry.Register(11, MakeVirtualSST(KeyRange(100, 120), 3));
  auto current = registry.Lookup(11);
  ASSERT_NE(nullptr, current);
  ASSERT_EQ(100U, current->key_min);
  ASSERT_EQ(0U, snapshot.front().second->key_min);
}

TEST(VirtualSSTRegistryTest, BatchLookupPreservesInputOrderAndMissingEntries) {
  VirtualSSTRegistry registry;
  registry.Register(1, MakeVirtualSST(KeyRange(10, 20)));
  registry.Register(3, MakeVirtualSST(KeyRange(30, 40)));

  auto handles = registry.LookupMany({3, 2, 1});
  ASSERT_EQ(3U, handles.size());
  ASSERT_EQ(30U, handles[0]->key_min);
  ASSERT_EQ(nullptr, handles[1]);
  ASSERT_EQ(10U, handles[2]->key_min);
}

}  // namespace
}  // namespace ROCKSDB_NAMESPACE

int main(int argc, char** argv) {
  ROCKSDB_NAMESPACE::port::InstallStackTraceHandler();
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
