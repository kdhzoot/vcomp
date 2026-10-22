// Prints every live SST of a db_bench DB with its level and the key ids of its
// smallest and largest key, as recorded in the MANIFEST. Opens the DB read-only
// and does not read SST contents.
#include <cinttypes>
#include <cstdio>
#include <string>
#include <vector>

#include "rocksdb/db.h"
#include "rocksdb/metadata.h"
#include "rocksdb/options.h"

using ROCKSDB_NAMESPACE::DB;
using ROCKSDB_NAMESPACE::LiveFileMetaData;
using ROCKSDB_NAMESPACE::Options;

// db_bench GenerateKeyFromInt with keys_per_prefix_ == 0 writes the key id as
// eight big-endian bytes and pads the rest with '0'.
static uint64_t KeyId(const std::string& k) {
  uint64_t v = 0;
  for (size_t i = 0; i < 8 && i < k.size(); i++)
    v = (v << 8) | static_cast<unsigned char>(k[i]);
  return v;
}

int main(int argc, char** argv) {
  if (argc < 2) {
    fprintf(stderr, "usage: %s <db_dir>\n", argv[0]);
    return 2;
  }
  Options opts;
  opts.max_open_files = 64;
  DB* db = nullptr;
  auto s = DB::OpenForReadOnly(opts, argv[1], &db);
  if (!s.ok()) {
    fprintf(stderr, "open: %s\n", s.ToString().c_str());
    return 1;
  }
  std::vector<LiveFileMetaData> files;
  db->GetLiveFilesMetaData(&files);
  printf("level\tname\tentries\tsize\tsmallest_id\tlargest_id\n");
  for (const auto& f : files) {
    printf("%d\t%s\t%" PRIu64 "\t%zu\t%" PRIu64 "\t%" PRIu64 "\n", f.level,
           f.name.c_str(), f.num_entries, f.size, KeyId(f.smallestkey),
           KeyId(f.largestkey));
  }
  delete db;
  return 0;
}
