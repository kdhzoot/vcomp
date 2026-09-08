// Read-only distinct-key audit independent of iterator ordering or deduplication.
// Supports prefix-free db_bench keys: eight-byte big-endian ID, ASCII '0' pad.
#include <cerrno>
#include <cstdarg>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <fcntl.h>
#include <unistd.h>

#include "rocksdb/db.h"
#include "rocksdb/env.h"
#include "rocksdb/utilities/options_util.h"

namespace {
void Require(bool ok, const std::string& why) {
  if (!ok) throw std::runtime_error(why);
}
void Check(const rocksdb::Status& status, const std::string& operation) {
  Require(status.ok(), operation + ": " + status.ToString());
}
std::string Quote(const std::string& value) {
  constexpr char hex[] = "0123456789abcdef";
  std::ostringstream out;
  out << '"';
  for (unsigned char c : value) {
    if (c == '"' || c == '\\') out << '\\' << c;
    else if (c < 32) out << "\\u00" << hex[c >> 4] << hex[c & 15];
    else out << c;
  }
  return out.str() + '"';
}
std::string Hex(const rocksdb::Slice& key) {
  constexpr char hex[] = "0123456789abcdef";
  std::string result;
  result.reserve(key.size() * 2);
  for (size_t n = 0; n < key.size(); ++n) {
    const auto c = static_cast<unsigned char>(key[n]);
    result += hex[c >> 4]; result += hex[c & 15];
  }
  return result;
}
uint64_t Number(const std::string& text) {
  Require(!text.empty() && text.find_first_not_of("0123456789") == std::string::npos,
          "expected unsigned decimal integer: " + text);
  return std::stoull(text);
}
uint64_t Decode(const rocksdb::Slice& key) {
  uint64_t id = 0;
  for (size_t n = 0; n < 8; ++n)
    id = (id << 8) | static_cast<unsigned char>(key[n]);
  return id;
}
std::string IdOrNull(const rocksdb::Slice& key) {
  return key.size() >= 8 ? std::to_string(Decode(key)) : "null";
}
std::string Delta(uint64_t actual, uint64_t expected) {
  return actual >= expected ? std::to_string(actual - expected)
                            : "-" + std::to_string(expected - actual);
}
bool Within(const std::filesystem::path& path, const std::filesystem::path& root) {
  auto p = path.begin();
  for (auto r = root.begin(); r != root.end(); ++r, ++p)
    if (p == path.end() || *p != *r) return false;
  return true;
}
void WriteNew(const std::string& path, const std::string& json) {
  if (!path.empty()) {
    // Do not truncate any existing path, including hardlink/symlink aliases.
    const int fd = open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0664);
    Require(fd >= 0, "cannot exclusively create report: " + path + ": " + std::strerror(errno));
    const std::string data = json + '\n';
    size_t offset = 0;
    while (offset < data.size()) {
      const ssize_t n = write(fd, data.data() + offset, data.size() - offset);
      if (n < 0 && errno == EINTR) continue;
      if (n <= 0) {
        const auto error = std::string(std::strerror(errno));
        close(fd);
        throw std::runtime_error("report write failed: " + error);
      }
      offset += static_cast<size_t>(n);
    }
    Require(close(fd) == 0, "report close failed");
  }
  std::cout << json << '\n';
}
class SilentLogger final : public rocksdb::Logger {
 public:
  void Logv(const char*, va_list) override {}
};
}  // namespace

int main(int argc, char** argv) {
  std::string output;
  try {
    std::map<std::string, std::string> args;
    for (int i = 1; i < argc; i += 2) {
      const std::string key = argv[i];
      Require(i + 1 < argc, "missing value for " + key);
      Require(key == "--db" || key == "--key-size" || key == "--value-size" ||
                  key == "--key-domain" || key == "--expected-unique" || key == "--output",
              "unknown argument: " + key);
      Require(args.emplace(key, argv[i + 1]).second, "duplicate argument: " + key);
    }
    for (const auto& key : {"--db", "--key-size", "--value-size", "--key-domain",
                            "--expected-unique", "--output"})
      Require(args.count(key), std::string("required argument: ") + key);
    const auto key_size = Number(args.at("--key-size"));
    const auto value_size = Number(args.at("--value-size"));
    const auto domain = Number(args.at("--key-domain"));
    const auto expected = Number(args.at("--expected-unique"));
    Require(key_size >= 8 && key_size <= std::numeric_limits<uint32_t>::max(),
            "key-size must be >= 8 and fit uint32");
    Require(value_size > 0 && value_size <= std::numeric_limits<uint32_t>::max(),
            "value-size must be positive and fit uint32");
    Require(domain > 0 && expected <= domain, "invalid key domain/expected unique");
    const uint64_t words = domain / 64 + (domain % 64 != 0);
    constexpr uint64_t kMaxBitsetBytes = 256 * 1024 * 1024;
    Require(words <= kMaxBitsetBytes / sizeof(uint64_t), "key-domain exceeds 256-MiB bitset limit");
    Require(!args.at("--output").empty(), "empty output path");
    const auto db_path = std::filesystem::canonical(args.at("--db"));
    Require(std::filesystem::is_directory(db_path), "DB path is not a directory");
    const auto output_path = std::filesystem::weakly_canonical(args.at("--output"));
    Require(!Within(output_path, db_path), "report must be outside DB directory");
    Require(!std::filesystem::exists(output_path) &&
                !std::filesystem::is_symlink(std::filesystem::symlink_status(args.at("--output"))),
            "report must be a new file; existing files and aliases are rejected");
    // Error reports may use the path only after the protection above succeeds.
    output = output_path.string();

    rocksdb::ConfigOptions config;
    config.ignore_unknown_options = true;
    rocksdb::DBOptions dbo;
    std::vector<rocksdb::ColumnFamilyDescriptor> columns;
    Check(rocksdb::LoadLatestOptions(config, db_path.string(), &dbo, &columns), "load persisted options");
    Require(columns.size() == 1 && columns[0].name == "default", "requires default-only CF");
    Require(std::string(columns[0].options.comparator->Name()) ==
                rocksdb::BytewiseComparator()->Name(), "requires bytewise comparator");
    rocksdb::Options options(dbo, columns[0].options);
    options.create_if_missing = false;
    options.disable_auto_compactions = true;
    options.paranoid_checks = true;
    options.use_direct_reads = true;
    options.info_log = std::make_shared<SilentLogger>();
    std::unique_ptr<rocksdb::DB> db;
    Check(rocksdb::DB::OpenForReadOnly(options, db_path.string(), &db), "readonly open");
    rocksdb::ReadOptions ro;
    ro.fill_cache = false;
    ro.verify_checksums = true;
    ro.total_order_seek = true;
    ro.readahead_size = 2 * 1024 * 1024;
    std::unique_ptr<rocksdb::Iterator> it(db->NewIterator(ro));
    std::vector<uint64_t> bits(static_cast<size_t>(words), 0);
    uint64_t rows = 0, distinct = 0, duplicate = 0, equal = 0, decreasing = 0;
    uint64_t key_bad = 0, value_bad = 0, encoding_bad = 0, domain_bad = 0, valid = 0;
    std::string previous;
    std::vector<std::string> examples;
    for (it->SeekToFirst(); it->Valid(); it->Next()) {
      const auto key = it->key();
      if (rows) {
        const rocksdb::Slice prev(previous);
        const int comparison = prev.compare(key);
        if (comparison >= 0) {
          if (comparison == 0) ++equal; else ++decreasing;
          if (examples.size() < 8) {
            std::ostringstream example;
            example << "{\"previous_row\":" << rows << ",\"current_row\":" << rows + 1
                    << ",\"kind\":" << Quote(comparison == 0 ? "equal" : "decreasing")
                    << ",\"previous_key_id\":" << IdOrNull(prev)
                    << ",\"current_key_id\":" << IdOrNull(key)
                    << ",\"previous_key_hex\":" << Quote(Hex(prev))
                    << ",\"current_key_hex\":" << Quote(Hex(key)) << '}';
            examples.push_back(example.str());
          }
        }
      }
      previous.assign(key.data(), key.size());
      ++rows;
      if (key.size() != key_size) ++key_bad;
      bool encoded = key.size() == key_size;
      if (encoded) {
        for (size_t n = 8; n < key.size(); ++n)
          if (key[n] != '0') { encoded = false; break; }
      }
      if (!encoded) ++encoding_bad;
      if (encoded) {
        const uint64_t id = Decode(key);
        if (id >= domain) ++domain_bad;
        else {
          ++valid;
          const uint64_t mask = uint64_t(1) << (id & 63);
          auto& word = bits[id >> 6];
          if (word & mask) ++duplicate;
          else { word |= mask; ++distinct; }
        }
      }
      if (it->value().size() != value_size) ++value_bad;
      if (rows % 10000000 == 0)
        std::cerr << "iterator_rows=" << rows << " distinct_keys=" << distinct << '\n';
    }
    Check(it->status(), "full iterator scan");
    Require(valid == distinct + duplicate && rows == valid + encoding_bad + domain_bad,
            "internal audit accounting mismatch");
    const bool key_count_complete = encoding_bad == 0 && domain_bad == 0;
    const bool structural_valid = key_count_complete && value_bad == 0 && equal == 0 && decreasing == 0;
    std::ostringstream json;
    json << std::boolalpha << "{\"status\":\"ok\",\"db\":" << Quote(db_path.string())
         << ",\"iterator_rows\":" << rows << ",\"distinct_keys\":" << distinct
         << ",\"distinct_count_complete\":" << key_count_complete
         << ",\"duplicate_rows\":" << duplicate
         << ",\"equal_adjacent\":" << equal << ",\"decreasing_adjacent\":" << decreasing
         << ",\"strict_increasing\":" << (equal == 0 && decreasing == 0)
         << ",\"expected_unique_keys\":" << expected << ",\"distinct_delta\":" << Delta(distinct, expected)
         << ",\"unique_count_matches\":" << (key_count_complete && distinct == expected)
         << ",\"structural_valid\":" << structural_valid
         << ",\"key_size\":" << key_size << ",\"value_size\":" << value_size
         << ",\"key_domain\":" << domain << ",\"bitset_bytes\":" << words * 8
         << ",\"valid_key_rows\":" << valid
         << ",\"key_size_mismatch_count\":" << key_bad
         << ",\"value_size_mismatch_count\":" << value_bad
         << ",\"key_encoding_mismatch_count\":" << encoding_bad
         << ",\"outside_domain_count\":" << domain_bad
         << ",\"transition_examples\":[";
    for (size_t n = 0; n < examples.size(); ++n) {
      if (n) json << ',';
      json << examples[n];
    }
    json << "]}";
    WriteNew(output, json.str());
    return 0;
  } catch (const std::exception& error) {
    const auto json = "{\"status\":\"error\",\"error\":" + Quote(error.what()) + '}';
    std::cerr << error.what() << '\n';
    try { WriteNew(output, json); } catch (...) { std::cout << json << '\n'; }
    return 1;
  }
}
