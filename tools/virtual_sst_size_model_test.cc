// Copyright (c) 2011-present, Facebook, Inc. All rights reserved.
// This source code is licensed under both the GPLv2 (found in the
// COPYING file in the root directory) and Apache 2.0 License
// (found in the LICENSE.Apache file in the root directory).

// Standalone arithmetic tests; no RocksDB library, gtest, or database I/O.
// Compile with -std=c++17 -O2 -Wall -Wextra -I. -Iinclude.
// Only this translation unit is needed; no additional libraries are linked.
#include "db/virtual_compaction/sst_size_model.h"

#include <cstdio>
#include <cstdlib>
#include <limits>

namespace {

using ROCKSDB_NAMESPACE::SSTSizeModel;

void Check(bool condition, const char* expression, int line) {
  if (!condition) {
    std::fprintf(stderr, "FAIL line %d: %s\n", line, expression);
    std::exit(1);
  }
}

#define CHECK(expression) Check((expression), #expression, __LINE__)

constexpr uint64_t kMax = std::numeric_limits<uint64_t>::max();

void CheckInverse(const SSTSizeModel& model, uint64_t target) {
  const uint64_t count = model.MaxEntries(target);
  CHECK(model.Estimate(count) <= target);
  if (count != kMax) CHECK(model.Estimate(count + 1) > target);
}

void TestLogicalAndInvalid() {
  SSTSizeModel empty;
  CHECK(!empty.Valid());
  CHECK(empty.Estimate(0) == 0);
  CHECK(empty.Estimate(123) == 0);
  CHECK(empty.MaxEntries(kMax) == 0);
  CHECK(!SSTSizeModel::Logical(0).Valid());

  const auto model = SSTSizeModel::Logical(91);
  CHECK(model.Valid());
  CHECK(model.Estimate(0) == 0);
  CHECK(model.Estimate(10) == 910);
  CHECK(model.MaxEntries(909) == 9);
  CHECK(model.MaxEntries(910) == 10);
  CHECK(model.Estimate(kMax) == kMax);
  CHECK(model.MaxEntries(kMax) == kMax);
  CheckInverse(model, 0);
  CheckInverse(model, kMax - 1);
}

void TestAffineAndFractional() {
  SSTSizeModel affine;
  CHECK(affine.AddCalibration(10, 125, 20, 225));  // 10*n + 25
  CHECK(affine.Estimate(0) == 0);
  CHECK(affine.Estimate(1) == 35);
  CHECK(affine.Estimate(10) == 125);
  CHECK(affine.Estimate(20) == 225);
  CHECK(affine.Estimate(100) == 1025);
  CHECK(affine.MaxEntries(24) == 0);
  CHECK(affine.MaxEntries(25) == 0);
  CHECK(affine.MaxEntries(34) == 0);
  CHECK(affine.MaxEntries(35) == 1);
  for (uint64_t target = 0; target < 1200; ++target) {
    CheckInverse(affine, target);
  }

  SSTSizeModel fractional;
  CHECK(fractional.AddCalibration(1, 4, 3, 7));  // 1.5*n + ceil(2.5)
  CHECK(fractional.Estimate(1) == 5);
  CHECK(fractional.Estimate(2) == 6);
  CHECK(fractional.Estimate(3) == 8);
  CHECK(fractional.Estimate(100) == 153);
  CHECK(fractional.Estimate(1) >= 4);
  CHECK(fractional.Estimate(3) >= 7);
  for (uint64_t target = 0; target < 1200; ++target) {
    CheckInverse(fractional, target);
  }

  SSTSizeModel negative_intercept;
  CHECK(negative_intercept.AddCalibration(10, 5, 20, 15));
  CHECK(negative_intercept.Estimate(1) == 1);  // slope 1, intercept clamped
  CHECK(negative_intercept.Estimate(10) >= 5);
  CHECK(negative_intercept.Estimate(20) >= 15);

  const uint64_t original = affine.Estimate(100);
  CHECK(!affine.AddCalibration(0, 10, 2, 20));
  CHECK(!affine.AddCalibration(2, 10, 2, 20));
  CHECK(!affine.AddCalibration(3, 10, 2, 20));
  CHECK(!affine.AddCalibration(1, 20, 2, 20));
  CHECK(!affine.AddCalibration(1, 30, 2, 20));
  CHECK(affine.Estimate(100) == original);
}

void TestEnvelope() {
  SSTSizeModel envelope;
  CHECK(envelope.AddCalibration(1, 101, 2, 102));  // n + 100
  CHECK(envelope.AddCalibration(1, 3, 2, 6));      // 3*n
  CHECK(envelope.Estimate(1) == 101);
  CHECK(envelope.Estimate(50) == 150);
  CHECK(envelope.Estimate(100) == 300);
  CHECK(envelope.MaxEntries(149) == 49);
  CHECK(envelope.MaxEntries(150) == 50);
  CHECK(envelope.MaxEntries(300) == 100);
  for (uint64_t target = 0; target < 1200; ++target) {
    CheckInverse(envelope, target);
  }
}

void TestOverflowAndSmallSlope() {
  // Products in fitting, evaluating, and inverting exceed 64 bits.
  SSTSizeModel large;
  CHECK(large.AddCalibration(1, kMax - 1, 2, kMax));
  CHECK(large.Estimate(1) == kMax - 1);
  CHECK(large.Estimate(2) == kMax);
  CHECK(large.Estimate(kMax) == kMax);
  CHECK(large.MaxEntries(kMax - 2) == 0);
  CHECK(large.MaxEntries(kMax - 1) == 1);
  CHECK(large.MaxEntries(kMax) == kMax);
  CheckInverse(large, kMax - 1);

  SSTSizeModel steep;
  CHECK(steep.AddCalibration(kMax - 1, 1, kMax, kMax));
  CHECK(steep.Estimate(1) == kMax - 1);
  CHECK(steep.Estimate(2) == kMax);
  CheckInverse(steep, kMax - 1);
  CheckInverse(steep, kMax);

  SSTSizeModel shallow;
  CHECK(shallow.AddCalibration(1, 1, kMax, 2));
  CHECK(shallow.Estimate(1) == 2);
  CHECK(shallow.Estimate(kMax - 1) == 2);
  CHECK(shallow.Estimate(kMax) == 3);
  CHECK(shallow.MaxEntries(0) == 0);
  CHECK(shallow.MaxEntries(1) == 0);
  CHECK(shallow.MaxEntries(2) == kMax - 1);
  CHECK(shallow.MaxEntries(3) == kMax);
  CheckInverse(shallow, 0);
  CheckInverse(shallow, 1);
  CheckInverse(shallow, 2);
  CheckInverse(shallow, 3);
  CheckInverse(shallow, kMax - 1);

  SSTSizeModel intercept_product;
  CHECK(intercept_product.AddCalibration(1, kMax - 2, kMax, kMax - 1));
  CHECK(intercept_product.Estimate(1) >= kMax - 2);
  CHECK(intercept_product.Estimate(kMax) == kMax);
  CheckInverse(intercept_product, kMax - 1);
  CheckInverse(intercept_product, kMax);
}

void TestSmallCalibrationProperties() {
  // Exhaustively cover rounded positive/zero/negative intercepts and slopes
  // above and below one, independently of any particular SST configuration.
  for (uint64_t n1 = 1; n1 <= 5; ++n1) {
    for (uint64_t n2 = n1 + 1; n2 <= 7; ++n2) {
      for (uint64_t bytes1 = 0; bytes1 <= 9; ++bytes1) {
        for (uint64_t bytes2 = bytes1 + 1; bytes2 <= 11; ++bytes2) {
          SSTSizeModel model;
          CHECK(model.AddCalibration(n1, bytes1, n2, bytes2));
          CHECK(model.Valid());
          CHECK(model.Estimate(n1) >= bytes1);
          CHECK(model.Estimate(n2) >= bytes2);
          for (uint64_t value = 0; value < 100; ++value) {
            CHECK(model.Estimate(value) <= model.Estimate(value + 1));
            CheckInverse(model, value);
          }
        }
      }
    }
  }
}

}  // namespace

int main() {
  TestLogicalAndInvalid();
  TestAffineAndFractional();
  TestEnvelope();
  TestOverflowAndSmallSlope();
  TestSmallCalibrationProperties();
  std::puts("SSTSizeModel standalone tests: PASS");
  return 0;
}
