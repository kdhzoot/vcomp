#include "titandb_db_smoke.h"

#include <cstdlib>
#include <iostream>
#include <limits>

#include "db/leveldb_config.h"
#include "rocksdb/cache.h"
#include "rocksdb/filter_policy.h"
#include "rocksdb/statistics.h"
#include "rocksdb/table.h"

namespace ycsbc {

namespace {

uint32_t EnvUint(const char* name, uint32_t fallback) {
  const char* text = std::getenv(name);
  if (text == nullptr || *text == '\0') return fallback;
  const unsigned long value = std::stoul(text);
  if (value == 0 || value > std::numeric_limits<uint32_t>::max()) {
    std::cerr << "invalid " << name << "=" << text << "\n";
    std::abort();
  }
  return static_cast<uint32_t>(value);
}

}  // namespace

TitanDBSmoke::TitanDBSmoke(const char* dbfilename,
                           const std::string& config_file_path) {
  ConfigLevelDB config(config_file_path);
  rocksdb::titandb::TitanOptions options;
  rocksdb::BlockBasedTableOptions table_options;

  options.create_if_missing = true;
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
  options.max_background_jobs =
      EnvUint("DIFFKV_BG_JOBS", config.getNumThreads());
  options.max_subcompactions = EnvUint("DIFFKV_SUBCOMPACTIONS", 1);
  options.disable_auto_compactions = config.getNoCompaction();
  options.mid_blob_size = config.getMidThresh();
  options.min_blob_size = config.getSmallThresh();

  rocksdb::Status status =
      rocksdb::titandb::TitanDB::Open(options, dbfilename, &db_);
  if (!status.ok()) {
    std::cerr << "Can't open TitanDB: " << status.ToString() << "\n";
    std::exit(1);
  }
}

void TitanDBSmoke::Close() {
  // YCSB-C calls Close() once per worker on a single shared DB. Flushing here
  // races with workers that are still inserting. The destructor runs after all
  // worker futures have joined and performs the one required synchronous flush.
}

TitanDBSmoke::~TitanDBSmoke() {
  if (db_ == nullptr) return;
  rocksdb::FlushOptions options;
  options.wait = true;
  rocksdb::Status status = db_->Flush(options);
  if (!status.ok()) {
    std::cerr << "TitanDB flush failed: " << status.ToString() << "\n";
    std::abort();
  }
  delete db_;
  db_ = nullptr;
}

int TitanDBSmoke::Read(const std::string&, const std::string& key,
                       const std::vector<std::string>*,
                       std::vector<KVPair>& result) {
  std::string value;
  rocksdb::Status status = db_->Get(rocksdb::ReadOptions(), key, &value);
  if (!status.ok()) return -1;
  result.emplace_back("field0", std::move(value));
  return DB::kOK;
}

int TitanDBSmoke::Scan(const std::string&, const std::string& key, int len,
                       const std::vector<std::string>*,
                       std::vector<std::vector<KVPair>>& result) {
  std::unique_ptr<rocksdb::Iterator> iterator(
      db_->NewIterator(rocksdb::ReadOptions()));
  iterator->Seek(key);
  for (int i = 0; i < len && iterator->Valid(); ++i, iterator->Next()) {
    result.push_back({{"field0", iterator->value().ToString()}});
  }
  return iterator->status().ok() ? DB::kOK : -1;
}

int TitanDBSmoke::Insert(const std::string&, const std::string& key,
                         std::vector<KVPair>& values) {
  for (const KVPair& pair : values) {
    rocksdb::Status status =
        db_->Put(rocksdb::WriteOptions(), key, pair.second);
    if (!status.ok()) {
      std::cerr << "insert failed: " << status.ToString() << "\n";
      return -1;
    }
  }
  return DB::kOK;
}

int TitanDBSmoke::Update(const std::string& table, const std::string& key,
                         std::vector<KVPair>& values) {
  return Insert(table, key, values);
}

int TitanDBSmoke::Delete(const std::string&, const std::string& key) {
  rocksdb::Status status = db_->Delete(rocksdb::WriteOptions(), key);
  return status.ok() ? DB::kOK : -1;
}

}  // namespace ycsbc
