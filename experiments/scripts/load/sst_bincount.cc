// Scans every live SST of a db_bench DB and counts its entries per level and
// per key-id bin, so the entries a real LSM holds in a key range can be
// compared with what F2Load's descriptors plan for it. Opens the DB read-only
// for the file list and reads each file with SstFileReader.
//
// usage: sst_bincount <db_dir> <bin_width> <out_prefix> [ids]
//   <out_prefix>.files.tsv  level, name, entries, size, smallest_id, largest_id
//   <out_prefix>.bins.tsv   level, bin, entries
//   <out_prefix>.ids        with "ids": every entry's key id, uint64 little-endian
#include <cinttypes>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include "rocksdb/db.h"
#include "rocksdb/metadata.h"
#include "rocksdb/options.h"
#include "rocksdb/sst_file_reader.h"

using ROCKSDB_NAMESPACE::DB;
using ROCKSDB_NAMESPACE::Iterator;
using ROCKSDB_NAMESPACE::LiveFileMetaData;
using ROCKSDB_NAMESPACE::Options;
using ROCKSDB_NAMESPACE::ReadOptions;
using ROCKSDB_NAMESPACE::SstFileReader;

// db_bench GenerateKeyFromInt with keys_per_prefix_ == 0 writes the key id as
// eight big-endian bytes and pads the rest with '0'.
static uint64_t KeyId(const char* p, size_t n) {
  uint64_t v = 0;
  for (size_t i = 0; i < 8 && i < n; i++) v = (v << 8) | static_cast<unsigned char>(p[i]);
  return v;
}

int main(int argc, char** argv) {
  if (argc < 4) {
    fprintf(stderr, "usage: %s <db_dir> <bin_width> <out_prefix>\n", argv[0]);
    return 2;
  }
  const uint64_t bin = strtoull(argv[2], nullptr, 10);
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
  delete db;

  std::string prefix = argv[3];
  FILE* ff = fopen((prefix + ".files.tsv").c_str(), "w");
  fprintf(ff, "level\tname\tentries\tsize\tsmallest_id\tlargest_id\n");
  std::map<std::pair<int, uint64_t>, uint64_t> bins;
  FILE* fi = (argc > 4 && std::string(argv[4]) == "ids") ? fopen((prefix + ".ids").c_str(), "wb") : nullptr;
  for (const auto& f : files) {
    SstFileReader reader(opts);
    s = reader.Open(std::string(argv[1]) + f.name);
    if (!s.ok()) {
      fprintf(stderr, "open %s: %s\n", f.name.c_str(), s.ToString().c_str());
      return 1;
    }
    ReadOptions ro;
    ro.verify_checksums = false;
    ro.fill_cache = false;
    std::unique_ptr<Iterator> it(reader.NewIterator(ro));
    uint64_t n = 0, lo = UINT64_MAX, hi = 0;
    for (it->SeekToFirst(); it->Valid(); it->Next()) {
      const uint64_t id = KeyId(it->key().data(), it->key().size());
      bins[{f.level, id / bin}]++;
      if (fi != nullptr) fwrite(&id, sizeof(id), 1, fi);
      if (id < lo) lo = id;
      if (id > hi) hi = id;
      n++;
    }
    if (!it->status().ok()) {
      fprintf(stderr, "scan %s: %s\n", f.name.c_str(), it->status().ToString().c_str());
      return 1;
    }
    fprintf(ff, "%d\t%s\t%" PRIu64 "\t%zu\t%" PRIu64 "\t%" PRIu64 "\n", f.level, f.name.c_str(), n,
            f.size, lo, hi);
  }
  fclose(ff);
  if (fi != nullptr) fclose(fi);
  FILE* fb = fopen((prefix + ".bins.tsv").c_str(), "w");
  fprintf(fb, "level\tbin\tentries\n");
  for (const auto& [k, v] : bins) fprintf(fb, "%d\t%" PRIu64 "\t%" PRIu64 "\n", k.first, k.second, v);
  fclose(fb);
  fprintf(stderr, "%zu files scanned\n", files.size());
  return 0;
}
