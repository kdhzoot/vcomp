#include <algorithm>
#include <cstdint>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

#include "core/utils.h"
#include "db/leveldb_config.h"
#include "rocksdb/cache.h"
#include "rocksdb/filter_policy.h"
#include "rocksdb/statistics.h"
#include "rocksdb/table.h"
#include "titan/db.h"

namespace {

rocksdb::titandb::TitanOptions LoadOptions(const std::string& config_path) {
  ycsbc::ConfigLevelDB config(config_path);
  rocksdb::titandb::TitanOptions options;
  rocksdb::BlockBasedTableOptions table_options;

  options.create_if_missing = false;
  options.write_buffer_size = config.getMemtable();
  options.disable_background_gc = false;
  options.compaction_pri = rocksdb::kMinOverlappingRatio;
  options.max_bytes_for_level_base = config.getMemtable();
  options.target_file_size_base = 16 << 20;
  options.statistics = rocksdb::CreateDBStatistics();
  if (!config.getCompression()) options.compression = rocksdb::kNoCompression;
  if (config.getBloomBits() > 0) {
    table_options.filter_policy.reset(
        rocksdb::NewBloomFilterPolicy(config.getBloomBits()));
  }
  options.min_gc_batch_size = 32 << 20;
  options.max_gc_batch_size = 64 << 20;
  options.max_sorted_runs = config.getMaxSortedRuns();
  table_options.block_cache = rocksdb::NewLRUCache(config.getBlockCache());
  options.table_factory.reset(
      rocksdb::NewBlockBasedTableFactory(table_options));
  options.blob_file_target_size = 8 << 20;
  options.level_merge = config.getLevelMerge();
  options.range_merge = config.getRangeMerge();
  options.lazy_merge = config.getLazyMerge();
  options.max_background_gc = config.getGCThreads();
  options.block_write_size = config.getBlockWriteSize();
  options.blob_file_discardable_ratio = config.getGCRatio();
  if (options.level_merge) {
    options.base_level_for_dynamic_level_bytes = 4;
    options.level_compaction_dynamic_level_bytes = true;
  }
  options.intra_compact_small_l0 = config.getIntraCompaction();
  options.sep_before_flush = config.getSepBeforeFlush();
  if (config.getTiered()) {
    options.compaction_style = rocksdb::kCompactionStyleUniversal;
  }
  options.max_background_jobs = config.getNumThreads();
  options.disable_auto_compactions = config.getNoCompaction();
  options.mid_blob_size = config.getMidThresh();
  options.min_blob_size = config.getSmallThresh();
  return options;
}

std::string YcsbKey(uint64_t key_number) {
  return std::string("user") + std::to_string(utils::Hash(key_number));
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 4) {
    std::cerr << "usage: verify_diffkv_smoke DB_PATH CONFIG_PATH RECORD_COUNT\n";
    return 2;
  }

  const std::string db_path = argv[1];
  const std::string config_path = argv[2];
  const uint64_t record_count = std::stoull(argv[3]);
  if (record_count == 0) return 2;

  auto options = LoadOptions(config_path);
  rocksdb::titandb::TitanDB* raw_db = nullptr;
  rocksdb::Status status =
      rocksdb::titandb::TitanDB::Open(options, db_path, &raw_db);
  if (!status.ok()) {
    std::cerr << "open failed: " << status.ToString() << "\n";
    return 1;
  }
  std::unique_ptr<rocksdb::titandb::TitanDB> db(raw_db);

  std::vector<uint64_t> samples = {0, 1, record_count / 4,
                                   record_count / 2,
                                   (record_count * 3) / 4,
                                   record_count - 1};
  const uint64_t stride = std::max<uint64_t>(1, record_count / 251);
  for (uint64_t key = 0; key < record_count; key += stride) {
    samples.push_back(key);
  }

  uint64_t verified = 0;
  for (uint64_t key_number : samples) {
    std::string value;
    status = db->Get(rocksdb::ReadOptions(), YcsbKey(key_number), &value);
    if (!status.ok()) {
      std::cerr << "get failed for key_number=" << key_number << ": "
                << status.ToString() << "\n";
      return 1;
    }
    if (value.size() != 1000) {
      std::cerr << "unexpected value length for key_number=" << key_number
                << ": " << value.size() << "\n";
      return 1;
    }
    ++verified;
  }

  status = db->Close();
  if (!status.ok()) {
    std::cerr << "close failed: " << status.ToString() << "\n";
    return 1;
  }
  db.release();
  std::cout << "verified_records=" << verified << " value_bytes=1000\n";
  return 0;
}
