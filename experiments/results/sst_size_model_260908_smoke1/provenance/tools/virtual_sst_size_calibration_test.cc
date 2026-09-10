// In-memory SST-size calibration regression/measurement tool. Link against
// librocksdb; this program neither opens a DB nor creates host files.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include "db/virtual_compaction/sst_size_calibration.h"
#include "rocksdb/convenience.h"
#include "rocksdb/env.h"
#include "rocksdb/filter_policy.h"
#include "rocksdb/listener.h"
#include "rocksdb/statistics.h"
#include "rocksdb/table.h"

namespace {
namespace r = ROCKSDB_NAMESPACE;
constexpr uint64_t kMiB = uint64_t{1} << 20;
size_t checks = 0;
size_t heldout_cases = 0;

void Require(bool condition, const std::string& message) {
  ++checks;
  if (!condition) throw std::runtime_error(message);
}

void Check(const r::Status& status, const std::string& message) {
  Require(status.ok(), message + ": " + status.ToString());
}

using Populate = std::function<r::Status(r::SstFileWriter*, uint64_t, uint64_t)>;

Populate MakePopulate(size_t key_bytes, size_t value_bytes) {
  return [=](r::SstFileWriter* writer, uint64_t entries, uint64_t stride) {
    // Fresh per invocation, with 50% repeated value bytes. Neither key nor
    // value generation depends on calibration order or any external RNG.
    uint64_t state = 0x782159cc7439ULL;
    std::string key(key_bytes, '0');
    std::string value(value_bytes, '\0');
    for (uint64_t i = 0; i < entries; ++i) {
      const uint64_t id = i * stride;
      for (size_t j = 0; j < 8; ++j) {
        key[j] = static_cast<char>(id >> (8 * (7 - j)));
      }
      const size_t random_bytes = (value_bytes + 1) / 2;
      for (size_t j = 0; j < random_bytes; ++j) {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        value[j] = static_cast<char>(state & 0xff);
      }
      for (size_t j = random_bytes; j < value_bytes; ++j) {
        value[j] = value[j - random_bytes];
      }
      r::Status status = writer->Put(key, value);
      if (!status.ok()) return status;
    }
    return r::Status::OK();
  };
}

r::Options MakeOptions(unsigned block_bytes, unsigned restart,
                       unsigned bloom_bits, r::CompressionType compression) {
  r::Options options;
  options.compression = compression;
  r::BlockBasedTableOptions table;
  table.format_version = 7;
  table.block_size = block_bytes;
  table.block_restart_interval = restart;
  table.enable_index_compression = false;
  if (bloom_bits != 0) {
    table.filter_policy.reset(r::NewBloomFilterPolicy(bloom_bits, false));
  }
  options.table_factory.reset(r::NewBlockBasedTableFactory(table));
  return options;
}

uint64_t MeasureHeldout(r::Options options, const Populate& populate,
                       uint64_t entries, uint64_t stride) {
  std::unique_ptr<r::Env> env(r::NewMemEnv(r::Env::Default()));
  options.env = env.get();
  r::ExternalSstFileInfo info;
  {
    r::SstFileWriter writer(r::EnvOptions(), options, nullptr, false);
    Check(writer.Open("/heldout.sst"), "open held-out in-memory SST");
    Check(populate(&writer, entries, stride), "populate held-out SST");
    Check(writer.Finish(&info), "finish held-out SST");
  }
  uint64_t size = 0;
  Check(env->GetFileSize("/heldout.sst", &size), "measure held-out size");
  Require(size == info.file_size && info.num_entries == entries,
          "held-out final size/count match");
  Check(env->DeleteFile("/heldout.sst"), "delete private held-out SST");
  return size;
}

void MeasurementCase(size_t key_bytes, size_t value_bytes, unsigned block_bytes,
                     unsigned restart, unsigned bloom_bits,
                     r::CompressionType compression) {
  const uint64_t logical = key_bytes + value_bytes;
  const uint64_t domain = uint64_t{1} << 40;
  const auto options = MakeOptions(block_bytes, restart, bloom_bits, compression);
  const auto populate = MakePopulate(key_bytes, value_bytes);
  r::SSTSizeModel model;
  std::vector<r::SSTSizeCalibrationSample> samples;
  Check(r::CalibrateSSTSizeModel(options, logical, domain, 64 * kMiB, populate,
                                 &model, &samples),
        "calibrate representative table options");
  Require(model.Valid() && samples.size() == 4, "two dense/sparse sample pairs");
  for (const auto& sample : samples) {
    Require(sample.entries <= 262144 && sample.entries >= 1,
            "bounded sample entry count");
    Require(static_cast<unsigned __int128>(sample.entries - 1) * sample.stride <
                domain,
            "sample keys stay inside domain");
    Require(model.Estimate(sample.entries) >= sample.file_bytes,
            "model envelope covers its calibration samples");
  }
  const uint64_t budget = 64 * kMiB;
  const uint64_t capacity = model.MaxEntries(budget);
  Require(model.Estimate(capacity) <= budget &&
              model.Estimate(capacity + 1) > budget,
          "physical byte budget uses the model's exact inverse");
  const uint64_t heldout_entries = 12 * kMiB / logical;
  for (uint64_t stride : {uint64_t{1}, samples.back().stride}) {
    const uint64_t actual = MeasureHeldout(options, populate, heldout_entries, stride);
    const uint64_t predicted = model.Estimate(heldout_entries);
    const double relative_error =
        static_cast<double>(predicted) / static_cast<double>(actual) - 1.0;
    // A loose regression threshold on these fixtures, not a guaranteed bound
    // for arbitrary compression, keys, table options, or file sizes.
    Require(std::abs(relative_error) <= 0.15,
            "held-out representative fixture error exceeds 15%");
    std::cout << "{\"record_type\":\"heldout\",\"kv_bytes\":" << logical
              << ",\"key_bytes\":" << key_bytes << ",\"block_bytes\":" << block_bytes
              << ",\"restart\":" << restart << ",\"bloom_bits\":" << bloom_bits
              << ",\"compression\":" << static_cast<unsigned>(compression)
              << ",\"entries\":" << heldout_entries << ",\"stride\":" << stride
              << ",\"actual_bytes\":" << actual << ",\"estimated_bytes\":" << predicted
              << ",\"relative_error\":" << relative_error << "}\n";
    ++heldout_cases;
  }
}

class CountListener : public r::EventListener {
 public:
  bool ShouldBeNotifiedOnFileIO() override { return true; }
  void OnFileWriteFinish(const r::FileOperationInfo&) override { ++writes; }
  uint64_t writes = 0;
};

void Contracts() {
  auto options = MakeOptions(4096, 16, 10, r::kNoCompression);
  const auto populate = MakePopulate(24, 1000);
  auto model = r::SSTSizeModel::Logical(77);
  std::vector<r::SSTSizeCalibrationSample> samples = {{7, 11, 13}};
  const auto unchanged = [&] {
    Require(model.Estimate(5) == 385 && samples.size() == 1 &&
                samples[0].entries == 7 && samples[0].stride == 11 &&
                samples[0].file_bytes == 13,
            "failed calibration preserves caller outputs");
  };
  for (uint64_t bad_size : {uint64_t{0}, 64 * kMiB + 1}) {
    Require(!r::CalibrateSSTSizeModel(options, bad_size, 1000, kMiB, populate,
                                     &model, &samples).ok(),
            "reject invalid logical entry size");
    unchanged();
  }
  for (uint64_t bad_domain : {uint64_t{0}, uint64_t{1}}) {
    Require(!r::CalibrateSSTSizeModel(options, 1024, bad_domain, kMiB, populate,
                                     &model, &samples).ok(),
            "reject domain smaller than two keys");
    unchanged();
  }
  Require(!r::CalibrateSSTSizeModel(options, 1024, 1000, 0, populate,
                                   &model, &samples).ok(), "reject zero target");
  unchanged();
  Require(!r::CalibrateSSTSizeModel(options, 1024, 1000, kMiB, {},
                                   &model, &samples).ok(), "reject missing populate");
  unchanged();
  Require(!r::CalibrateSSTSizeModel(options, 1024, 1000, kMiB, populate,
                                   nullptr, &samples).ok(), "reject missing output");
  unchanged();
  Require(!r::CalibrateSSTSizeModel(
               options, 1024, 1000, kMiB,
               [](r::SstFileWriter*, uint64_t, uint64_t) {
                 return r::Status::IOError("injected populate failure");
               }, &model, &samples).ok(), "propagate populate failure");
  unchanged();
  Require(!r::CalibrateSSTSizeModel(
               options, 1024, 1000, kMiB,
               [populate](r::SstFileWriter* writer, uint64_t n, uint64_t stride) {
                 return populate(writer, n - 1, stride);
               }, &model, &samples).ok(), "reject short sample");
  unchanged();

  std::unique_ptr<r::Env> caller_env(r::NewMemEnv(r::Env::Default()));
  options.env = caller_env.get();
  options.statistics = r::CreateDBStatistics();
  auto listener = std::make_shared<CountListener>();
  options.listeners.push_back(listener);
  options.use_direct_reads = true;
  options.use_direct_io_for_flush_and_compaction = true;
  options.allow_mmap_reads = true;
  options.allow_mmap_writes = true;
  const auto stats_before = options.statistics->ToString();
  Check(r::CalibrateSSTSizeModel(options, 1024, 2, kMiB, populate, &model, &samples),
        "small-domain calibration disables direct/mmap in private Env");
  Require(samples.size() == 2 && samples[0].entries == 1 && samples[1].entries == 2,
          "small domain uses one nonduplicated dense pair");
  Require(listener->writes == 0 && stats_before == options.statistics->ToString(),
          "caller listeners/statistics untouched");
  Require(options.env == caller_env.get() && options.use_direct_reads &&
              options.use_direct_io_for_flush_and_compaction &&
              options.allow_mmap_reads && options.allow_mmap_writes,
          "caller options remain unchanged");
  Require(caller_env->FileExists("/sst-size-calibration/sample-0.sst").IsNotFound(),
          "calibration did not create files in caller Env");
  Check(r::CalibrateSSTSizeModel(MakeOptions(4096, 16, 0, r::kNoCompression),
                                 1024, std::numeric_limits<uint64_t>::max(),
                                 64 * kMiB, populate, &model, nullptr),
        "UINT64_MAX domain and optional sample output");
}

}  // namespace

int main() {
  try {
    Contracts();
    for (const auto& kv : {std::pair<size_t, size_t>{48, 43}, {24, 1000}}) {
      for (unsigned block : {4096U, 16384U}) {
        for (unsigned restart : {1U, 16U}) {
          for (unsigned bloom : {0U, 10U}) {
            MeasurementCase(kv.first, kv.second, block, restart, bloom,
                            r::kNoCompression);
          }
        }
      }
    }
    for (auto compression : r::GetSupportedCompressions()) {
      if (compression == r::kSnappyCompression || compression == r::kLZ4Compression ||
          compression == r::kZSTD) {
        MeasurementCase(24, 1000, 4096, 16, 10, compression);
      }
    }
    std::cout << "{\"record_type\":\"summary\",\"status\":\"PASS\",\"checks\":"
              << checks << ",\"heldout_cases\":" << heldout_cases << "}\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "FAIL: " << error.what() << '\n';
    return 1;
  }
}
