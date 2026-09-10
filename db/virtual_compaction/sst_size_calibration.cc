// Copyright (c) 2011-present, Facebook, Inc. All rights reserved.
// This source code is licensed under both the GPLv2 (found in the
// COPYING file in the root directory) and Apache 2.0 License
// (found in the LICENSE.Apache file in the root directory).

#include "db/virtual_compaction/sst_size_calibration.h"

#include <algorithm>
#include <memory>
#include <string>
#include <utility>

#include "rocksdb/env.h"

namespace ROCKSDB_NAMESPACE {
namespace {

constexpr uint64_t kMiB = uint64_t{1} << 20;
constexpr uint64_t kMaxSampleEntries = 262144;
constexpr uint64_t kMaxLogicalEntryBytes = 64 * kMiB;

Status MeasureSample(
    const Options& options, Env* private_env, uint64_t entries, uint64_t stride,
    size_t ordinal,
    const std::function<Status(SstFileWriter*, uint64_t, uint64_t)>& populate,
    SSTSizeCalibrationSample* result) {
  // The pathname belongs only to a newly allocated in-memory Env. It cannot
  // refer to an existing host file, including when calibration runs in parallel.
  const std::string path =
      "/sst-size-calibration/sample-" + std::to_string(ordinal) + ".sst";
  EnvOptions env_options(options);
  env_options.use_direct_reads = false;
  env_options.use_direct_writes = false;
  env_options.use_mmap_reads = false;
  env_options.use_mmap_writes = false;
  ExternalSstFileInfo info;
  Status status;
  bool opened = false;
  {
    SstFileWriter writer(env_options, options, /*column_family=*/nullptr,
                         /*invalidate_page_cache=*/false);
    status = writer.Open(path);
    opened = status.ok();
    if (status.ok()) status = populate(&writer, entries, stride);
    if (status.ok()) status = writer.Finish(&info);
    if (status.ok() &&
        (info.num_entries != entries || info.num_range_del_entries != 0 ||
         info.file_size == 0)) {
      status = Status::Corruption("SST size calibration: sample count/size mismatch");
    }
    if (status.ok()) {
      uint64_t actual_file_bytes = 0;
      status = private_env->GetFileSize(path, &actual_file_bytes);
      if (status.ok() && actual_file_bytes != info.file_size) {
        status = Status::Corruption("SST size calibration: finished size mismatch");
      }
    }
  }  // Close/abandon the writer before removing its private in-memory file.
  if (opened) {
    Status cleanup = private_env->DeleteFile(path);
    if (!cleanup.ok() && status.ok()) status = cleanup;
  }
  if (!status.ok()) return status;
  *result = {entries, stride, info.file_size};
  return Status::OK();
}

}  // namespace

Status CalibrateSSTSizeModel(
    const Options& options, uint64_t logical_entry_bytes, uint64_t key_domain,
    uint64_t target_sst_bytes,
    const std::function<Status(SstFileWriter*, uint64_t, uint64_t)>& populate,
    SSTSizeModel* out, std::vector<SSTSizeCalibrationSample>* samples) {
  if (out == nullptr || !populate || logical_entry_bytes == 0 ||
      logical_entry_bytes > kMaxLogicalEntryBytes || key_domain < 2 ||
      target_sst_bytes == 0) {
    return Status::InvalidArgument("SST size calibration: invalid input or entry exceeds 64 MiB");
  }
  const uint64_t n2 = std::min(
      {std::max(uint64_t{2}, 8 * kMiB / logical_entry_bytes),
       kMaxSampleEntries, key_domain});
  const uint64_t n1 = std::min(
      std::max(uint64_t{1}, 4 * kMiB / logical_entry_bytes), n2 / 2);
  const uint64_t typical_entries =
      std::max(uint64_t{1}, target_sst_bytes / logical_entry_bytes);
  const uint64_t stride = std::max(
      uint64_t{1},
      (key_domain - 1) / std::max(n2 - 1, typical_entries - 1));

  std::unique_ptr<Env> private_env(NewMemEnv(Env::Default()));
  if (!private_env) {
    return Status::IOError("SST size calibration: cannot allocate private Env");
  }
  Status status = private_env->CreateDirIfMissing("/sst-size-calibration");
  if (!status.ok()) return status;
  Options isolated(options);
  isolated.env = private_env.get();
  isolated.statistics.reset();
  isolated.info_log.reset();
  isolated.listeners.clear();
  isolated.rate_limiter.reset();
  isolated.sst_file_manager.reset();
  isolated.use_direct_reads = false;
  isolated.use_direct_io_for_flush_and_compaction = false;
  isolated.allow_mmap_reads = false;
  isolated.allow_mmap_writes = false;

  // Do not seed this with Logical(): its envelope line would prevent the
  // calibrated model from representing compression below logical KV bytes.
  SSTSizeModel calibrated;
  std::vector<SSTSizeCalibrationSample> measured;
  for (uint64_t key_stride : {uint64_t{1}, stride}) {
    if (!measured.empty() && key_stride == 1) continue;
    SSTSizeCalibrationSample first;
    SSTSizeCalibrationSample second;
    status = MeasureSample(isolated, private_env.get(), n1, key_stride,
                           measured.size(), populate, &first);
    if (!status.ok()) return status;
    status = MeasureSample(isolated, private_env.get(), n2, key_stride,
                           measured.size() + 1, populate, &second);
    if (!status.ok()) return status;
    if (!calibrated.AddCalibration(n1, first.file_bytes, n2, second.file_bytes)) {
      return Status::InvalidArgument("SST size calibration: invalid measured size slope");
    }
    measured.push_back(first);
    measured.push_back(second);
  }
  if (!calibrated.Valid()) {
    return Status::Corruption("SST size calibration: empty model");
  }
  *out = std::move(calibrated);
  if (samples != nullptr) *samples = std::move(measured);
  return Status::OK();
}

}  // namespace ROCKSDB_NAMESPACE
