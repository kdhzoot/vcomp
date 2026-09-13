// MANIFEST를 순서대로 재생해 compaction trace를 텍스트로 출력한다.
#include <cstdio>
#include <cstdint>
#include <cinttypes>
#include <string>
#include "db/version_edit.h"
#include "db/log_reader.h"
#include "file/sequence_file_reader.h"
#include "rocksdb/env.h"
#include "rocksdb/file_system.h"
#include "rocksdb/options.h"

namespace rocksdb {
namespace {
uint64_t KeyId(const std::string& k) {
  uint64_t v = 0;
  if (k.size() >= 8)
    for (int i = 0; i < 8; i++) v = (v << 8) | static_cast<uint8_t>(k[i]);
  return v;
}
}  // namespace
}  // namespace rocksdb

int main(int argc, char** argv) {
  using namespace rocksdb;
  if (argc < 2) { fprintf(stderr, "usage: mdump MANIFEST\n"); return 1; }
  Env* env = Env::Default();
  const auto& fs = env->GetFileSystem();
  std::unique_ptr<SequentialFileReader> reader;
  Status s = SequentialFileReader::Create(fs, argv[1], FileOptions(), &reader,
                                          nullptr, nullptr);
  if (!s.ok()) { fprintf(stderr, "open: %s\n", s.ToString().c_str()); return 1; }
  log::Reader lr(nullptr, std::move(reader), nullptr, true, 0);
  Slice record;
  std::string scratch;
  uint64_t edit_no = 0;
  while (lr.ReadRecord(&record, &scratch)) {
    VersionEdit edit;
    if (!edit.DecodeFrom(record).ok()) continue;
    const auto& dels = edit.GetDeletedFiles();
    const auto& adds = edit.GetNewFiles();
    if (dels.empty() && adds.empty()) continue;
    printf("EDIT %" PRIu64 "\n", edit_no++);
    for (const auto& d : dels) printf("  DEL %d %" PRIu64 "\n", d.first, d.second);
    for (const auto& a : adds) {
      const FileMetaData& m = a.second;
      printf("  ADD %d %" PRIu64 " %" PRIu64 " %" PRIu64 " %" PRIu64 " %" PRIu64 "\n",
             a.first, m.fd.GetNumber(), m.fd.GetFileSize(),
             KeyId(m.smallest.user_key().ToString()),
             KeyId(m.largest.user_key().ToString()), m.num_entries);
    }
  }
  return 0;
}
