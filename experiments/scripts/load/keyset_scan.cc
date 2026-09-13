// Reads every SST of a db_bench DB and records which generator key ids it holds.
// Read-only: files are opened through SstFileReader, nothing is written to the DB.
#include <atomic>
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <string>
#include <thread>
#include <vector>

#include "rocksdb/options.h"
#include "rocksdb/sst_file_reader.h"

using ROCKSDB_NAMESPACE::Options;
using ROCKSDB_NAMESPACE::ReadOptions;
using ROCKSDB_NAMESPACE::SstFileReader;

// db_bench GenerateKeyFromInt with keys_per_prefix_ == 0 writes the key id as
// eight big-endian bytes and pads the rest with '0'.
static inline uint64_t KeyId(const char* p) {
  uint64_t v = 0;
  for (int i = 0; i < 8; i++) v = (v << 8) | static_cast<unsigned char>(p[i]);
  return v;
}

int main(int argc, char** argv) {
  if (argc < 4) {
    fprintf(stderr, "usage: %s <db_dir> <key_domain> <out.bitmap> [threads]\n", argv[0]);
    return 2;
  }
  const std::string dir = argv[1];
  const uint64_t domain = strtoull(argv[2], nullptr, 10);
  const std::string out = argv[3];
  const int threads = argc > 4 ? atoi(argv[4]) : 48;

  std::vector<std::string> files;
  DIR* d = opendir(dir.c_str());
  if (!d) { perror("opendir"); return 1; }
  for (dirent* e; (e = readdir(d));) {
    std::string n = e->d_name;
    if (n.size() > 4 && n.compare(n.size() - 4, 4, ".sst") == 0)
      files.push_back(dir + "/" + n);
  }
  closedir(d);
  fprintf(stderr, "%zu sst files, domain %lu, %d threads\n", files.size(), domain, threads);

  const uint64_t words = (domain + 63) / 64;
  std::vector<std::atomic<uint64_t>> bits(words);
  for (auto& w : bits) w.store(0, std::memory_order_relaxed);

  std::atomic<size_t> next{0};
  std::atomic<uint64_t> entries{0}, outside{0};
  std::atomic<int> failed{0};
  std::vector<std::thread> pool;
  for (int t = 0; t < threads; t++) {
    pool.emplace_back([&] {
      Options opts;
      for (size_t i = next.fetch_add(1); i < files.size(); i = next.fetch_add(1)) {
        SstFileReader r(opts);
        auto s = r.Open(files[i]);
        if (!s.ok()) { failed.fetch_add(1); continue; }
        std::unique_ptr<ROCKSDB_NAMESPACE::Iterator> it(r.NewIterator(ReadOptions()));
        uint64_t n = 0, bad = 0;
        for (it->SeekToFirst(); it->Valid(); it->Next()) {
          auto k = it->key();
          if (k.size() < 8) { bad++; continue; }
          uint64_t id = KeyId(k.data());
          if (id >= domain) { bad++; continue; }
          bits[id >> 6].fetch_or(uint64_t{1} << (id & 63), std::memory_order_relaxed);
          n++;
        }
        entries.fetch_add(n);
        outside.fetch_add(bad);
        size_t done = i + 1;
        if (done % 1000 == 0) fprintf(stderr, "  %zu/%zu files\n", done, files.size());
      }
    });
  }
  for (auto& th : pool) th.join();

  uint64_t present = 0;
  std::vector<uint64_t> plain(words);
  for (uint64_t i = 0; i < words; i++) {
    plain[i] = bits[i].load(std::memory_order_relaxed);
    present += __builtin_popcountll(plain[i]);
  }
  FILE* f = fopen(out.c_str(), "wb");
  fwrite(plain.data(), sizeof(uint64_t), words, f);
  fclose(f);
  printf("{\"db\":\"%s\",\"files\":%zu,\"open_failed\":%d,\"entries\":%lu,"
         "\"keys_outside_domain\":%lu,\"distinct_keys\":%lu,\"domain\":%lu,"
         "\"coverage_pct\":%.4f}\n",
         dir.c_str(), files.size(), failed.load(), entries.load(), outside.load(),
         present, domain, 100.0 * present / domain);
  return 0;
}
