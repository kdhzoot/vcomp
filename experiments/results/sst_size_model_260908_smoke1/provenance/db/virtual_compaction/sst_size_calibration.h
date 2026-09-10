// Copyright (c) 2011-present, Facebook, Inc. All rights reserved.
// This source code is licensed under both the GPLv2 (found in the
// COPYING file in the root directory) and Apache 2.0 License
// (found in the LICENSE.Apache file in the root directory).
#pragma once

#include <cstdint>
#include <functional>
#include <vector>

#include "db/virtual_compaction/sst_size_model.h"
#include "rocksdb/options.h"
#include "rocksdb/sst_file_writer.h"
#include "rocksdb/status.h"

namespace ROCKSDB_NAMESPACE {

struct SSTSizeCalibrationSample {
  uint64_t entries = 0;
  uint64_t stride = 0;
  uint64_t file_bytes = 0;
};

// Fit an approximate physical-SST size model using bounded, private in-memory
// SSTs. `options` must already contain the same table/compression settings as
// materialization; only I/O and observer settings are isolated here. No DB is
// opened and no files are created in the caller's Env or filesystem.
//
// Each populate invocation must use a fresh, equivalent value generator and
// the materializer's key encoding, without advancing any loader RNG. It must
// Put exactly `entries` sorted keys with IDs i * key_stride, 0 <= i < entries;
// this helper owns Open/Finish. Samples cover dense and sparse key spacing.
//
// Calibration is not a proof that every future SST lies below the model.
// `out` and the optional `samples` are unchanged if any calibration fails.
Status CalibrateSSTSizeModel(
    const Options& options, uint64_t logical_entry_bytes, uint64_t key_domain,
    uint64_t target_sst_bytes,
    const std::function<Status(SstFileWriter*, uint64_t entries,
                               uint64_t key_stride)>& populate,
    SSTSizeModel* out, std::vector<SSTSizeCalibrationSample>* samples);

}  // namespace ROCKSDB_NAMESPACE
