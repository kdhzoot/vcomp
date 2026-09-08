// Focused real-compaction fixtures for the opt-in accuracy capture collector.
// Every invocation creates a fresh DB below /work; no existing DB is removed.
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>

#include "rocksdb/db.h"
#include "rocksdb/options.h"

namespace r = ROCKSDB_NAMESPACE;
namespace fs = std::filesystem;

void Require(bool yes, const std::string& message) {
  if (!yes) throw std::runtime_error(message);
}
void Check(const r::Status& status) {
  Require(status.ok(), status.ToString());
}
std::string Key(uint64_t value, bool bad_padding = false) {
  std::string key(24, '0');
  for (size_t i = 0; i < 8; ++i) key[i] = char(value >> (56 - 8 * i));
  if (bad_padding) key[12] = 'x';
  return key;
}

int main(int argc, char** argv) {
  try {
    std::map<std::string, std::string> args;
    for (int i = 1; i < argc; i += 2) {
      Require(i + 1 < argc, "missing argument value");
      Require(args.emplace(argv[i], argv[i + 1]).second, "duplicate argument");
    }
    Require(args.size() == 3 && args.count("--db") &&
                args.count("--capture") && args.count("--case"),
            "usage: --db NEW_PATH --capture NEW_PATH --case put|delete|padding|snapshot");
    const fs::path dbpath = fs::absolute(args.at("--db")).lexically_normal();
    const fs::path capture = fs::absolute(args.at("--capture")).lexically_normal();
    const std::string kind = args.at("--case");
    Require(kind == "put" || kind == "delete" || kind == "padding" ||
                kind == "snapshot", "invalid case");
    Require(dbpath.string().rfind("/work/vcomp/exp/real_input_accuracy_", 0) == 0 &&
                capture.string().rfind("/work/vcomp/exp/real_input_accuracy_", 0) == 0,
            "fixture paths must use fresh /work/vcomp/exp/real_input_accuracy_* directories");
    Require(!fs::exists(dbpath) && !fs::exists(capture), "fixture path already exists");
    fs::create_directories(dbpath.parent_path());
    fs::create_directories(capture);
    Require(setenv("VCOMP_ACCURACY_CAPTURE_DIR", capture.c_str(), 1) == 0,
            "set capture environment failed");

    r::Options options;
    options.create_if_missing = true;
    options.error_if_exists = true;
    options.use_virtual_compaction = false;
    options.disable_auto_compactions = true;
    options.write_buffer_size = 1024 * 1024;
    options.target_file_size_base = 64 * 1024;
    options.max_bytes_for_level_base = 256 * 1024;
    options.max_background_jobs = 2;
    options.max_subcompactions = 1;
    options.compression = r::kNoCompression;
    r::DB* raw = nullptr;
    Check(r::DB::Open(options, dbpath.string(), &raw));
    std::unique_ptr<r::DB> db(raw);
    r::WriteOptions write;
    write.disableWAL = true;
    r::FlushOptions flush;
    flush.wait = true;
    const std::string value(43, 'v');
    for (uint64_t i = 0; i < 2048; ++i)
      Check(db->Put(write, Key(2 * i), value));
    Check(db->Flush(flush));
    const r::Snapshot* snapshot = kind == "snapshot" ? db->GetSnapshot() : nullptr;
    for (uint64_t i = 1024; i < 3072; ++i)
      Check(db->Put(write, Key(i, kind == "padding" && i == 1025), value));
    if (kind == "delete") Check(db->Delete(write, Key(2048)));
    Check(db->Flush(flush));
    r::CompactRangeOptions compact;
    compact.change_level = true;
    compact.target_level = 1;
    compact.bottommost_level_compaction = r::BottommostLevelCompaction::kForce;
    Check(db->CompactRange(compact, nullptr, nullptr));
    if (snapshot) db->ReleaseSnapshot(snapshot);
    uint64_t unique = 0;
    std::unique_ptr<r::Iterator> it(db->NewIterator(r::ReadOptions()));
    for (it->SeekToFirst(); it->Valid(); it->Next()) ++unique;
    Check(it->status());
    it.reset();
    if (kind == "put" || kind == "snapshot") Require(unique == 3072, "unexpected reference unique count");
    if (kind == "delete") Require(unique == 3071, "unexpected delete reference unique count");
    std::cout << "{\"case\":\"" << kind << "\",\"reference_unique\":" << unique
              << ",\"status\":\"ok\"}\n";
    return 0;
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
