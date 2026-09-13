// Export and verify exact live membership without modifying a source database.
// Keys are headerless little-endian uint64 IDs, strictly increasing.
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include "rocksdb/db.h"
#include "rocksdb/iterator.h"
#include "rocksdb/options.h"
#include "rocksdb/utilities/options_util.h"

namespace {
void Require(bool ok, const std::string& message) {
  if (!ok) throw std::runtime_error(message);
}
void Check(const rocksdb::Status& status) {
  Require(status.ok(), status.ToString());
}
uint64_t Parse(const std::string& text) {
  Require(!text.empty() && text.find_first_not_of("0123456789") == std::string::npos,
          "invalid unsigned integer: " + text);
  return std::stoull(text);
}
using File = std::unique_ptr<FILE, decltype(&std::fclose)>;
File Open(const std::string& path, const char* mode) {
  FILE* file = std::fopen(path.c_str(), mode);
  Require(file != nullptr, "open " + path + ": " + std::strerror(errno));
  std::setvbuf(file, nullptr, _IOFBF, 1024 * 1024);
  return File(file, std::fclose);
}
void WriteId(FILE* file, uint64_t id) {
  unsigned char bytes[8];
  for (int i = 0; i < 8; ++i) bytes[i] = static_cast<unsigned char>(id >> (8*i));
  Require(std::fwrite(bytes, 1, 8, file) == 8, "key file write failed");
}
uint64_t ReadId(FILE* file) {
  unsigned char bytes[8];
  Require(std::fread(bytes, 1, 8, file) == 8, "key file ended early or read failed");
  uint64_t id = 0;
  for (int i = 0; i < 8; ++i) id |= static_cast<uint64_t>(bytes[i]) << (8*i);
  return id;
}
}

int main(int argc, char** argv) {
  try {
    Require(argc >= 2, "usage: sparse_keys export|verify --db PATH --keys PATH --domain N --key-size 24 --value-size 1000 --json PATH");
    const std::string mode = argv[1];
    Require(mode == "export" || mode == "verify", "unsupported mode");
    std::map<std::string, std::string> args;
    for (int i = 2; i < argc; i += 2) {
      Require(i + 1 < argc, "missing flag value");
      const std::string name = argv[i];
      Require(name == "--db" || name == "--keys" || name == "--domain" ||
              name == "--key-size" || name == "--value-size" || name == "--json",
              "unknown flag: " + name);
      Require(args.emplace(name, argv[i+1]).second, "duplicate flag: " + name);
    }
    for (const auto* name : {"--db", "--keys", "--domain", "--key-size", "--value-size", "--json"})
      Require(args.count(name) == 1, std::string("required flag: ") + name);
    const uint64_t domain = Parse(args.at("--domain"));
    const uint64_t key_size = Parse(args.at("--key-size"));
    const uint64_t value_size = Parse(args.at("--value-size"));
    Require(domain > 0 && key_size == 24 && value_size == 1000,
            "only positive domain, key24/value1000 are supported");
    Require(!std::filesystem::exists(args.at("--json")), "JSON output exists");
    uint64_t expected = 0;
    if (mode == "verify") {
      const auto bytes = std::filesystem::file_size(args.at("--keys"));
      Require(bytes > 0 && bytes % 8 == 0, "invalid key file length");
      expected = bytes / 8;
      Require(expected <= domain, "key file exceeds domain");
    }
    auto keys = Open(args.at("--keys"), mode == "export" ? "wbx" : "rb");
    const auto started = std::chrono::steady_clock::now();
    rocksdb::ConfigOptions config;
    rocksdb::DBOptions db_options;
    std::vector<rocksdb::ColumnFamilyDescriptor> families;
    Check(rocksdb::LoadLatestOptions(config, args.at("--db"), &db_options, &families));
    Require(families.size() == 1 && families[0].name == rocksdb::kDefaultColumnFamilyName,
            "only default column family is supported");
    rocksdb::Options options(db_options, families[0].options);
    options.create_if_missing = false;
    options.disable_auto_compactions = true;
    options.use_direct_reads = true;
    options.max_open_files = 512;
    std::unique_ptr<rocksdb::DB> db;
    Check(rocksdb::DB::OpenForReadOnly(options, args.at("--db"), &db));
    rocksdb::ReadOptions reads;
    reads.fill_cache = false;
    reads.verify_checksums = true;
    reads.readahead_size = 4 * 1024 * 1024;
    std::unique_ptr<rocksdb::Iterator> it(db->NewIterator(reads));
    uint64_t count = 0, previous = 0;
    for (it->SeekToFirst(); it->Valid(); it->Next()) {
      const auto key = it->key();
      Require(key.size() == key_size, "unexpected key size");
      Require(it->value().size() == value_size, "unexpected value size");
      uint64_t id = 0;
      for (int i = 0; i < 8; ++i) id = (id << 8) | static_cast<unsigned char>(key[i]);
      for (size_t i = 8; i < key.size(); ++i) Require(key[i] == '0', "unexpected key padding");
      Require(id < domain && (count == 0 || id > previous), "key domain/order violation");
      if (mode == "export") WriteId(keys.get(), id);
      else Require(count < expected && ReadId(keys.get()) == id,
                   "membership mismatch at record " + std::to_string(count));
      previous = id;
      ++count;
      if (count % 10000000 == 0) std::cerr << "processed " << count << " live keys\n";
    }
    Check(it->status());
    Require(count > 0, "database is empty");
    if (mode == "verify") {
      Require(count == expected, "database has fewer keys than key file");
      Require(std::fgetc(keys.get()) == EOF && !std::ferror(keys.get()), "extra key bytes or read error");
    }
    Require(std::fclose(keys.release()) == 0, "key file close failed");
    const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count();
    auto output = Open(args.at("--json"), "wx");
    Require(std::fprintf(output.get(),
      "{\"status\":\"ok\",\"mode\":\"%s\",\"unique_count\":%llu,\"key_domain\":%llu,\"keys_bytes\":%llu,\"key_size\":24,\"value_size\":1000,\"elapsed_sec\":%.6f}\n",
      mode.c_str(), static_cast<unsigned long long>(count), static_cast<unsigned long long>(domain),
      static_cast<unsigned long long>(count*8), elapsed) > 0, "JSON write failed");
    Require(std::fclose(output.release()) == 0, "JSON close failed");
    std::cout << mode << " ok, unique_count=" << count << " elapsed_sec=" << elapsed << '\n';
  } catch (const std::exception& error) {
    std::cerr << "ERROR: " << error.what() << '\n';
    return 1;
  }
}
