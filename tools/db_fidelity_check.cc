// Exact, read-only validation for prefix-free db_bench GenerateKeyFromInt keys.
// Build against the common baseline RocksDB headers and static library.
#include <cstdarg>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "rocksdb/db.h"
#include "rocksdb/env.h"
#include "rocksdb/metadata.h"
#include "rocksdb/table_properties.h"
#include "rocksdb/utilities/options_util.h"

namespace {
std::string Quote(const std::string& value) {
  std::ostringstream out;
  out << '"';
  constexpr char hex[] = "0123456789abcdef";
  for (unsigned char c : value) {
    if (c == '"' || c == '\\') out << '\\' << c;
    else if (c < 32) out << "\\u00" << hex[c >> 4] << hex[c & 15];
    else out << c;
  }
  return out.str() + '"';
}
uint64_t Number(const std::string& s) {
  if (s.empty() || s.find_first_not_of("0123456789") != std::string::npos)
    throw std::runtime_error("expected unsigned decimal integer: " + s);
  size_t n = 0;
  auto value = std::stoull(s, &n);
  if (n != s.size()) throw std::runtime_error("invalid integer");
  return value;
}
void Require(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}
void Check(const rocksdb::Status& s, const std::string& operation) {
  Require(s.ok(), operation + ": " + s.ToString());
}
void Write(const std::string& path, const std::string& json) {
  if (!path.empty()) {
    std::ofstream out(path);
    Require(bool(out), "cannot open JSON output: " + path);
    out << json << '\n';
    out.close();
    Require(bool(out), "cannot write JSON output: " + path);
  }
  std::cout << json << '\n';
}
class SilentLogger final : public rocksdb::Logger {
 public:
  void Logv(const char*, va_list) override {}
};
std::string SignedDelta(uint64_t actual, uint64_t expected) {
  return actual >= expected ? std::to_string(actual - expected)
                            : "-" + std::to_string(expected - actual);
}
}  // namespace

int main(int argc, char** argv) {
  std::string output;
  try {
    std::map<std::string, std::string> args;
    for (int i = 1; i < argc; i += 2) {
      const std::string key = argv[i];
      Require(i + 1 < argc, "missing value for " + key);
      Require(key == "--db" || key == "--key-size" || key == "--value-size" ||
                  key == "--key-domain" || key == "--expected-unique" ||
                  key == "--output", "unknown argument: " + key);
      Require(args.emplace(key, argv[i + 1]).second, "duplicate argument: " + key);
      if (key == "--output") output = argv[i + 1];
    }
    for (const auto& key : {"--db", "--key-size", "--value-size", "--key-domain",
                            "--expected-unique", "--output"})
      Require(args.count(key), std::string("required argument: ") + key);
    const auto key_size = Number(args.at("--key-size"));
    const auto value_size = Number(args.at("--value-size"));
    const auto domain = Number(args.at("--key-domain"));
    const auto expected = Number(args.at("--expected-unique"));
    Require(key_size >= 8 && key_size <= std::numeric_limits<uint32_t>::max(),
            "key-size must be >= 8 for unambiguous prefix-free uint64 keys");
    Require(value_size > 0 && value_size <= std::numeric_limits<uint32_t>::max(),
            "invalid value-size");
    Require(domain > 0 && expected <= domain, "invalid domain/expected unique");
    Require(!output.empty(), "empty output path");

    rocksdb::ConfigOptions config;
    config.ignore_unknown_options = true;  // F2-only options are not reader options.
    rocksdb::DBOptions dbo;
    std::vector<rocksdb::ColumnFamilyDescriptor> columns;
    Check(rocksdb::LoadLatestOptions(config, args.at("--db"), &dbo, &columns),
          "load persisted options");
    Require(columns.size() == 1 && columns[0].name == "default",
            "only the default column family is supported");
    Require(std::string(columns[0].options.comparator->Name()) ==
                rocksdb::BytewiseComparator()->Name(), "requires bytewise comparator");
    rocksdb::Options options(dbo, columns[0].options);
    options.create_if_missing = false;
    options.disable_auto_compactions = true;
    options.paranoid_checks = true;
    options.use_direct_reads = true;
    options.info_log = std::make_shared<SilentLogger>();
    std::unique_ptr<rocksdb::DB> db;
    Check(rocksdb::DB::OpenForReadOnly(options, args.at("--db"), &db), "readonly open");

    rocksdb::ReadOptions ro;
    ro.fill_cache = false;
    ro.verify_checksums = true;
    ro.total_order_seek = true;
    ro.readahead_size = 2 * 1024 * 1024;
    std::unique_ptr<rocksdb::Iterator> it(db->NewIterator(ro));
    uint64_t count = 0, key_bad = 0, value_bad = 0, encoding_bad = 0;
    uint64_t inside = 0, outside = 0, non_increasing = 0;
    std::string previous;
    for (it->SeekToFirst(); it->Valid(); it->Next()) {
      const auto key = it->key();
      if (count && rocksdb::Slice(previous).compare(key) >= 0) ++non_increasing;
      previous.assign(key.data(), key.size());
      ++count;
      if (key.size() != key_size) ++key_bad;
      if (it->value().size() != value_size) ++value_bad;
      bool encoding_ok = key.size() == key_size;
      uint64_t id = 0;
      if (key.size() >= 8) {
        for (size_t n = 0; n < 8; ++n)
          id = (id << 8) | static_cast<unsigned char>(key[n]);
        for (size_t n = 8; n < key.size(); ++n)
          if (key[n] != '0') encoding_ok = false;
      } else encoding_ok = false;
      if (!encoding_ok) ++encoding_bad;
      if (encoding_ok && id < domain) ++inside;
      else ++outside;
      if (count % 10000000 == 0)
        std::cerr << "scanned_live_keys=" << count << '\n';
    }
    Check(it->status(), "full iterator scan");
    it.reset();

    rocksdb::TablePropertiesCollection props;
    Check(db->GetPropertiesOfAllTables(&props), "SST properties");
    std::map<std::string, const rocksdb::TableProperties*> by_name;
    uint64_t entries = 0, deletions = 0;
    for (const auto& [path, property] : props) {
      const auto slash = path.find_last_of("/\\");
      const auto name = slash == std::string::npos ? path : path.substr(slash + 1);
      Require(by_name.emplace(name, property.get()).second, "duplicate SST property name");
      entries += property->num_entries;
      deletions += property->num_deletions;
    }
    rocksdb::ColumnFamilyMetaData metadata;
    db->GetColumnFamilyMetaData(&metadata);
    uint64_t pending = 0;
    Require(db->GetIntProperty("rocksdb.estimate-pending-compaction-bytes", &pending),
            "pending compaction property unavailable");
    std::ostringstream levels;
    uint64_t matched_files = 0;
    levels << '[';
    bool first = true;
    for (const auto& level : metadata.levels) {
      uint64_t level_entries = 0;
      for (const auto& file : level.files) {
        auto p = by_name.find(file.relative_filename);
        Require(p != by_name.end(), "missing properties for SST " + file.relative_filename);
        level_entries += p->second->num_entries;
        ++matched_files;
      }
      if (!first) levels << ',';
      first = false;
      levels << "{\"level\":" << level.level << ",\"sst_count\":" << level.files.size()
             << ",\"sst_bytes\":" << level.size << ",\"sst_entry_sum\":" << level_entries << '}';
    }
    levels << ']';
    Require(matched_files == props.size(), "metadata/properties SST count mismatch");
    const bool matches = count == expected && key_bad == 0 && value_bad == 0 &&
        encoding_bad == 0 && outside == 0 && non_increasing == 0;
    std::ostringstream json;
    json << std::boolalpha << "{\"status\":\"ok\",\"db\":" << Quote(args.at("--db"))
         << ",\"exact_unique_keys\":" << count << ",\"expected_unique_keys\":" << expected
         << ",\"delta\":" << SignedDelta(count, expected)
         << ",\"unique_count_matches\":" << (count == expected)
         << ",\"fidelity_matches\":" << matches
         << ",\"strict_increasing\":" << (non_increasing == 0)
         << ",\"non_increasing_count\":" << non_increasing
         << ",\"key_size\":" << key_size << ",\"value_size\":" << value_size
         << ",\"key_domain\":" << domain
         << ",\"key_size_mismatch_count\":" << key_bad
         << ",\"value_size_mismatch_count\":" << value_bad
         << ",\"key_encoding_mismatch_count\":" << encoding_bad
         << ",\"inside_domain_count\":" << inside << ",\"outside_domain_count\":" << outside
         << ",\"sst_count\":" << matched_files << ",\"sst_entry_sum\":" << entries
         << ",\"sst_deletion_sum\":" << deletions
         << ",\"estimated_pending_compaction_bytes\":" << pending
         << ",\"levels\":" << levels.str() << '}';
    Write(output, json.str());
    return 0;
  } catch (const std::exception& error) {
    const auto json = "{\"status\":\"error\",\"error\":" + Quote(error.what()) + '}';
    std::cerr << error.what() << '\n';
    try { Write(output, json); } catch (...) { std::cerr << "cannot write error report\n"; }
    return 1;
  }
}
