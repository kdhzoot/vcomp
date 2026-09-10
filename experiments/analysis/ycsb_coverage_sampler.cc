// CPU-only query coverage analysis for the vcomp-prof db_bench YCSB C port.
// No RocksDB dependency; no database is opened and no files are written.
//
// Source semantics audited against vcomp-prof/tools/db_bench_tool.cc:
//   ThreadState / RunBenchmark: seed + 1, ..., seed + threads.
//   YCSBWorkload: op chooser, SelectDB, then key chooser consume THREE draws.
//   YCSBScrambledZipfianGenerator / YCSBZipfianGenerator / YCSB_FNVHash64.
//   util/random.h: Random64::Next() is std::mt19937_64::operator()().
//   GenerateKeyFromInt: no second hash; 8 big-endian key bytes, then ASCII '0'.
// This retains the port's 10^10+1 constructor size vs 10^10 rank scale.
//
// Build only this analysis helper, not db_bench:
//   g++-11 -std=c++17 -O2 ycsb_coverage_sampler.cc -o /an/artifact/path/sampler
// Input TSV: system level file_index key_lo key_hi (inclusive integer bounds).
// The four-column variant system level min_key max_key is also accepted.
// An optional header and # comments are accepted. Use --ranges - for stdin.
// Example options: --domain 1048576000 --samples-per-thread 100000
//                  --threads 48 --seed 87654321 --ranges ranges.tsv
//
// Output replays equal-length initial prefixes of the original C workers. It
// estimates query-weighted coverage, NOT an exact duration-weighted replay:
// the original final per-worker operation counts are not supplied/assumed.
// Range membership is a candidate-file test, not a successful Get or exact
// physical read count (Bloom decisions, cache hits and file-picker rules differ).

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {
using Interval = std::pair<uint64_t, uint64_t>;

struct FileRange {
  uint64_t index;
  uint64_t minimum;
  uint64_t maximum;
  uint64_t covered_queries = 0;
};

struct Group {
  std::string system;
  uint64_t level = 0;
  uint64_t input_ranges = 0;
  std::vector<Interval> ranges;
  std::vector<FileRange> files;
  bool nonoverlapping_files = true;
  uint64_t covered_keys = 0;
  uint64_t covered_queries = 0;
  std::vector<uint64_t> per_thread_covered;
};

uint64_t ParseUInt(const std::string& text) {
  if (text.empty() || text.find_first_not_of("0123456789") != std::string::npos) {
    throw std::runtime_error("invalid unsigned integer: " + text);
  }
  size_t used = 0;
  const auto value = std::stoull(text, &used);
  if (used != text.size()) throw std::runtime_error("invalid integer: " + text);
  return value;
}

std::string JsonString(const std::string& text) {
  std::ostringstream out;
  out << '"';
  for (unsigned char c : text) {
    if (c == '"' || c == '\\') {
      out << '\\' << c;
    } else if (c < 0x20) {
      out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
          << static_cast<unsigned int>(c) << std::dec;
    } else {
      out << c;
    }
  }
  out << '"';
  return out.str();
}

uint64_t FNVHash64(uint64_t value) {
  uint64_t hash = 0xCBF29CE484222325ULL;
  for (int i = 0; i < 8; ++i) {
    hash ^= value & 0xff;
    value >>= 8;
    hash *= 1099511628211ULL;
  }
  return hash;
}

// Uses the same double expressions and unsigned hash as the measured port.
class CKeyGenerator {
 public:
  CKeyGenerator(uint64_t seed, uint64_t domain)
      : random_(seed), domain_(domain) {
    const double zeta2theta = 1.0 / std::pow(1.0, kTheta) +
                              1.0 / std::pow(2.0, kTheta);
    eta_ = (1.0 - std::pow(2.0 / (kRankDomain + 1), 1.0 - kTheta)) /
           (1.0 - zeta2theta / kZetan);
  }

  uint64_t Next() {
    random_();  // YCSBDiscreteGenerator: still consumed with read weight 1.0.
    random_();  // SelectDBWithCfh: still consumed with exactly one database.
    const double u = static_cast<double>(random_()) / 18446744073709551616.0;
    const double uz = u * kZetan;
    uint64_t rank;
    if (uz < 1.0) {
      rank = 0;
    } else if (uz < 1.0 + std::pow(0.5, kTheta)) {
      rank = 1;
    } else {
      rank = static_cast<uint64_t>(
          kRankDomain * std::pow(eta_ * u - eta_ + 1, 1.0 / (1.0 - kTheta)));
    }
    return FNVHash64(rank) % domain_;
  }

 private:
  static constexpr double kTheta = 0.99;
  static constexpr double kZetan = 26.46902820178302;
  static constexpr uint64_t kRankDomain = 10000000000ULL;
  std::mt19937_64 random_;
  uint64_t domain_;
  double eta_;
};

void UnionRanges(Group* group) {
  std::sort(group->ranges.begin(), group->ranges.end());
  std::vector<Interval> merged;
  for (const auto& interval : group->ranges) {
    if (merged.empty() || interval.first > merged.back().second + 1) {
      merged.push_back(interval);
    } else {
      merged.back().second = std::max(merged.back().second, interval.second);
    }
  }
  group->ranges = std::move(merged);
  for (const auto& interval : group->ranges) {
    group->covered_keys += interval.second - interval.first + 1;
  }
  std::sort(group->files.begin(), group->files.end(),
            [](const FileRange& a, const FileRange& b) { return a.minimum < b.minimum; });
  uint64_t largest_maximum = 0;
  for (size_t i = 0; i < group->files.size(); ++i) {
    if (i > 0 && group->files[i].minimum <= largest_maximum) {
      group->nonoverlapping_files = false;
    }
    largest_maximum = std::max(largest_maximum, group->files[i].maximum);
  }
}

bool Contains(const Group& group, uint64_t key) {
  auto it = std::upper_bound(
      group.ranges.begin(), group.ranges.end(), key,
      [](uint64_t value, const Interval& interval) { return value < interval.first; });
  return it != group.ranges.begin() && key <= std::prev(it)->second;
}

void CountFileHits(Group* group, uint64_t key) {
  if (group->nonoverlapping_files) {
    auto it = std::upper_bound(
        group->files.begin(), group->files.end(), key,
        [](uint64_t value, const FileRange& file) { return value < file.minimum; });
    if (it != group->files.begin()) {
      auto& file = *std::prev(it);
      if (key <= file.maximum) ++file.covered_queries;
    }
  } else {
    // L0 can overlap. Count every containing file, but only one union hit.
    for (auto& file : group->files) {
      if (file.minimum > key) break;
      if (key <= file.maximum) ++file.covered_queries;
    }
  }
}

void Usage() {
  std::cerr << "Usage: ycsb_coverage_sampler --domain N --samples-per-thread N "
               "[--threads 48] [--seed 87654321] [--ranges file.tsv|-]\n"
               "TSV columns: system level file_index key_lo key_hi (inclusive),\n"
               "or system level min_key max_key.\n";
}
}  // namespace

int main(int argc, char** argv) {
  try {
    uint64_t domain = 0, samples = 0, threads = 48, seed = 87654321;
    std::string ranges_path = "-";
    for (int i = 1; i < argc; ++i) {
      const std::string arg(argv[i]);
      if (arg == "--help") {
        Usage();
        return 0;
      }
      if (i + 1 >= argc) throw std::runtime_error("missing argument: " + arg);
      const std::string value(argv[++i]);
      if (arg == "--domain") domain = ParseUInt(value);
      else if (arg == "--samples-per-thread") samples = ParseUInt(value);
      else if (arg == "--threads") threads = ParseUInt(value);
      else if (arg == "--seed") seed = ParseUInt(value);
      else if (arg == "--ranges") ranges_path = value;
      else throw std::runtime_error("unknown option: " + arg);
    }
    if (domain == 0 || domain > static_cast<uint64_t>(std::numeric_limits<int64_t>::max()) ||
        samples == 0 || threads == 0 || threads > 4096 ||
        samples > std::numeric_limits<uint64_t>::max() / threads ||
        seed > std::numeric_limits<uint64_t>::max() - threads) {
      throw std::runtime_error("invalid domain/sample/thread/seed bounds");
    }
    std::ifstream file;
    std::istream* input = &std::cin;
    if (ranges_path != "-") {
      file.open(ranges_path);
      if (!file) throw std::runtime_error("cannot open ranges: " + ranges_path);
      input = &file;
    }
    std::map<std::pair<std::string, uint64_t>, Group> groups;
    std::string line;
    uint64_t line_number = 0;
    while (std::getline(*input, line)) {
      ++line_number;
      const auto first = line.find_first_not_of(" \t\r");
      if (first == std::string::npos || line[first] == '#') continue;
      std::istringstream row(line);
      std::vector<std::string> columns;
      std::string column;
      while (row >> column) columns.push_back(column);
      if (columns.size() != 4 && columns.size() != 5) {
        throw std::runtime_error("expected four or five TSV columns at line " + std::to_string(line_number));
      }
      if (columns[0] == "system" && columns[1] == "level") continue;
      const std::string& system = columns[0];
      const uint64_t level = ParseUInt(columns[1]);
      const size_t bounds_offset = columns.size() - 2;
      const uint64_t minimum = ParseUInt(columns[bounds_offset]);
      const uint64_t maximum = ParseUInt(columns[bounds_offset + 1]);
      if (minimum > maximum) throw std::runtime_error("inverted range at line " + std::to_string(line_number));
      auto& group = groups[{system, level}];
      group.system = system;
      group.level = level;
      const uint64_t file_index = columns.size() == 5 ? ParseUInt(columns[2]) : group.input_ranges;
      group.files.push_back({file_index, minimum, maximum, 0});
      ++group.input_ranges;
      // Clip to the actual query domain [0, domain-1], not a level-local span.
      if (minimum < domain) group.ranges.emplace_back(minimum, std::min(maximum, domain - 1));
    }
    if (groups.empty()) throw std::runtime_error("no input ranges");
    for (auto& entry : groups) {
      UnionRanges(&entry.second);
      entry.second.per_thread_covered.assign(threads, 0);
    }

    for (uint64_t tid = 0; tid < threads; ++tid) {
      CKeyGenerator generator(seed + tid + 1, domain);
      for (uint64_t i = 0; i < samples; ++i) {
        const uint64_t key = generator.Next();
        for (auto& entry : groups) {
          if (Contains(entry.second, key)) {
            ++entry.second.per_thread_covered[tid];
            CountFileHits(&entry.second, key);
          }
        }
      }
    }
    const uint64_t queries = samples * threads;
    std::cout << std::setprecision(12)
              << "{\n  \"schema_version\": 1,\n"
              << "  \"mode\": \"equal_length_ycsb_c_worker_prefixes\",\n"
              << "  \"domain\": " << domain << ",\n  \"seed\": " << seed
              << ",\n  \"threads\": " << threads
              << ",\n  \"samples_per_thread\": " << samples
              << ",\n  \"sampled_queries\": " << queries << ",\n  \"groups\": [\n";
    bool first_group = true;
    for (auto& entry : groups) {
      auto& group = entry.second;
      for (uint64_t count : group.per_thread_covered) group.covered_queries += count;
      if (!first_group) std::cout << ",\n";
      first_group = false;
      std::cout << "    {\"system\": " << JsonString(group.system)
                << ", \"level\": " << group.level
                << ", \"input_ranges\": " << group.input_ranges
                << ", \"union_ranges\": " << group.ranges.size()
                << ", \"covered_keys\": " << group.covered_keys
                << ", \"domain_coverage_fraction\": " << static_cast<double>(group.covered_keys) / domain
                << ", \"covered_queries\": " << group.covered_queries
                << ", \"query_coverage_fraction\": " << static_cast<double>(group.covered_queries) / queries
                << ", \"per_thread_covered_queries\": [";
      for (uint64_t tid = 0; tid < threads; ++tid) {
        if (tid) std::cout << ", ";
        std::cout << group.per_thread_covered[tid];
      }
      std::cout << "], \"files\": [";
      bool first_file = true;
      for (const auto& file_range : group.files) {
        if (!first_file) std::cout << ", ";
        first_file = false;
        std::cout << "{\"file_index\": " << file_range.index
                  << ", \"key_lo\": " << file_range.minimum
                  << ", \"key_hi\": " << file_range.maximum
                  << ", \"covered_queries\": " << file_range.covered_queries
                  << ", \"query_coverage_fraction\": "
                  << static_cast<double>(file_range.covered_queries) / queries << "}";
      }
      std::cout << "]}";
    }
    std::cout << "\n  ]\n}\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "coverage sampler error: " << error.what() << '\n';
    return 1;
  }
}
