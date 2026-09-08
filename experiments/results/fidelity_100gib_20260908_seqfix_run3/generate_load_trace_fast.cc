// Standalone fast generator for the VLOADTR1 binary load trace format.
//
// It intentionally has no RocksDB dependency so it can be compiled directly:
//   g++ -O3 -std=c++17 tools/generate_load_trace_fast.cc -o /tmp/generate_load_trace_fast

#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

constexpr char kMagic[8] = {'V', 'L', 'O', 'A', 'D', 'T', 'R', '1'};
constexpr uint32_t kVersion = 1;
constexpr uint32_t kHeaderSize = 88;
constexpr uint64_t kFlagExactUniqueCount = 1ULL << 0;
constexpr uint64_t kFlagAffineKeyMapping = 1ULL << 1;

struct Args {
  std::string output;
  uint64_t num_records = 0;
  bool have_num_records = false;
  double target_db_gb = 0.0;
  bool have_target_db_gb = false;
  uint32_t key_size = 24;
  uint32_t value_size = 1000;
  uint64_t key_domain = 0;
  bool have_key_domain = false;
  double unique_ratio = 0.6321205588285577;
  uint64_t unique_count = 0;
  bool have_unique_count = false;
  double zipf_alpha = 0.0;
  uint64_t seed = 12345678;
  uint64_t chunk_records = 4ULL * 1024ULL * 1024ULL;
  uint64_t progress_every = 0;
  bool force = false;
};

class SplitMix64 {
 public:
  explicit SplitMix64(uint64_t seed) : state_(seed) {}

  uint64_t Next() {
    uint64_t z = (state_ += 0x9e3779b97f4a7c15ULL);
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
  }

  double UniformDouble() {
    return (Next() >> 11) * (1.0 / 9007199254740992.0);
  }

  uint64_t Bounded(uint64_t bound) {
    if (bound <= 1) {
      return 0;
    }
    return Next() % bound;
  }

 private:
  uint64_t state_;
};

[[noreturn]] void Usage(const char* argv0) {
  std::cerr
      << "Usage: " << argv0 << " OUTPUT (--num N | --target-db-gb GB) [options]\n"
      << "Options:\n"
      << "  --key-size N              default 24\n"
      << "  --value-size N            default 1000\n"
      << "  --key-domain N            default num_records\n"
      << "  --unique-ratio R          default 0.6321205588\n"
      << "  --unique-count N          overrides --unique-ratio\n"
      << "  --zipf-alpha A            0 = uniform repeats\n"
      << "  --seed N                  default 12345678\n"
      << "  --chunk-records N         default 4194304\n"
      << "  --progress-every N        0 disables progress\n"
      << "  --force\n";
  std::exit(2);
}

uint64_t ParseU64(const std::string& s) {
  if (s.empty()) {
    throw std::invalid_argument("empty integer");
  }
  char suffix = s.back();
  double mult = 1.0;
  std::string num = s;
  if (suffix == 'k' || suffix == 'K') {
    mult = 1e3;
    num.pop_back();
  } else if (suffix == 'm' || suffix == 'M') {
    mult = 1e6;
    num.pop_back();
  } else if (suffix == 'g' || suffix == 'G') {
    mult = 1e9;
    num.pop_back();
  } else if (suffix == 't' || suffix == 'T') {
    mult = 1e12;
    num.pop_back();
  }
  char* end = nullptr;
  double v = std::strtod(num.c_str(), &end);
  if (end == nullptr || *end != '\0' || v < 0.0) {
    throw std::invalid_argument("bad integer: " + s);
  }
  return static_cast<uint64_t>(v * mult);
}

double ParseDouble(const std::string& s) {
  char* end = nullptr;
  double v = std::strtod(s.c_str(), &end);
  if (end == nullptr || *end != '\0') {
    throw std::invalid_argument("bad double: " + s);
  }
  return v;
}

Args ParseArgs(int argc, char** argv) {
  if (argc < 2) {
    Usage(argv[0]);
  }
  Args args;
  args.output = argv[1];
  for (int i = 2; i < argc; ++i) {
    std::string opt = argv[i];
    auto need_value = [&](const char* name) -> std::string {
      if (i + 1 >= argc) {
        throw std::invalid_argument(std::string("missing value for ") + name);
      }
      return argv[++i];
    };
    if (opt == "--num") {
      args.num_records = ParseU64(need_value("--num"));
      args.have_num_records = true;
    } else if (opt == "--target-db-gb") {
      args.target_db_gb = ParseDouble(need_value("--target-db-gb"));
      args.have_target_db_gb = true;
    } else if (opt == "--key-size") {
      args.key_size = static_cast<uint32_t>(ParseU64(need_value("--key-size")));
    } else if (opt == "--value-size") {
      args.value_size =
          static_cast<uint32_t>(ParseU64(need_value("--value-size")));
    } else if (opt == "--key-domain") {
      args.key_domain = ParseU64(need_value("--key-domain"));
      args.have_key_domain = true;
    } else if (opt == "--unique-ratio") {
      args.unique_ratio = ParseDouble(need_value("--unique-ratio"));
    } else if (opt == "--unique-count") {
      args.unique_count = ParseU64(need_value("--unique-count"));
      args.have_unique_count = true;
    } else if (opt == "--zipf-alpha") {
      args.zipf_alpha = ParseDouble(need_value("--zipf-alpha"));
    } else if (opt == "--seed") {
      args.seed = ParseU64(need_value("--seed"));
    } else if (opt == "--chunk-records") {
      args.chunk_records = ParseU64(need_value("--chunk-records"));
    } else if (opt == "--progress-every") {
      args.progress_every = ParseU64(need_value("--progress-every"));
    } else if (opt == "--force") {
      args.force = true;
    } else {
      throw std::invalid_argument("unknown option: " + opt);
    }
  }
  if (!args.have_num_records && !args.have_target_db_gb) {
    throw std::invalid_argument("one of --num or --target-db-gb is required");
  }
  if (args.unique_ratio < 0.0 || args.unique_ratio > 1.0) {
    throw std::invalid_argument("--unique-ratio must be in [0,1]");
  }
  if (args.zipf_alpha < 0.0) {
    throw std::invalid_argument("--zipf-alpha must be non-negative");
  }
  if (args.key_size + args.value_size == 0) {
    throw std::invalid_argument("key-size + value-size must be positive");
  }
  return args;
}

void WriteU32(FILE* f, uint32_t v) {
  if (std::fwrite(&v, sizeof(v), 1, f) != 1) {
    throw std::runtime_error("short header write");
  }
}

void WriteU64(FILE* f, uint64_t v) {
  if (std::fwrite(&v, sizeof(v), 1, f) != 1) {
    throw std::runtime_error("short header write");
  }
}

void WriteDouble(FILE* f, double v) {
  if (std::fwrite(&v, sizeof(v), 1, f) != 1) {
    throw std::runtime_error("short header write");
  }
}

uint64_t ChooseCoprimeMultiplier(uint64_t modulus, SplitMix64* rng) {
  if (modulus <= 1) {
    return 0;
  }
  uint64_t candidate = rng->Bounded(modulus - 1) + 1;
  if ((candidate & 1ULL) == 0) {
    ++candidate;
  }
  while (std::gcd(candidate, modulus) != 1) {
    candidate += 2;
    if (candidate >= modulus) {
      candidate = 1;
    }
  }
  return candidate;
}

uint64_t SampleExistingRank(uint64_t existing, double alpha, SplitMix64* rng) {
  if (existing <= 1) {
    return 0;
  }
  if (alpha <= 0.0) {
    return rng->Bounded(existing);
  }
  const double u = rng->UniformDouble();
  uint64_t rank = 1;
  if (std::abs(alpha - 1.0) < 1e-9) {
    rank = static_cast<uint64_t>(std::exp(u * std::log(existing)));
  } else {
    const double one_minus_alpha = 1.0 - alpha;
    const double high = std::pow(static_cast<double>(existing),
                                 one_minus_alpha);
    rank = static_cast<uint64_t>(
        std::pow(u * (high - 1.0) + 1.0, 1.0 / one_minus_alpha));
  }
  if (rank == 0) {
    rank = 1;
  }
  if (rank > existing) {
    rank = existing;
  }
  return rank - 1;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    Args args = ParseArgs(argc, argv);
    if (!args.have_num_records) {
      const double bytes = args.target_db_gb * 1024.0 * 1024.0 * 1024.0;
      args.num_records = static_cast<uint64_t>(
          bytes / static_cast<double>(args.key_size + args.value_size));
    }
    if (!args.have_key_domain) {
      args.key_domain = args.num_records;
    }
    if (args.num_records > 0 && args.key_domain == 0) {
      throw std::invalid_argument("key-domain must be positive");
    }

    uint64_t unique_count = 0;
    if (args.num_records > 0) {
      unique_count = args.have_unique_count
                         ? args.unique_count
                         : static_cast<uint64_t>(
                               std::llround(args.num_records *
                                            args.unique_ratio));
      if (unique_count == 0) {
        unique_count = 1;
      }
      if (unique_count > args.num_records) {
        unique_count = args.num_records;
      }
      if (unique_count > args.key_domain) {
        unique_count = args.key_domain;
      }
    }
    const double effective_unique_ratio =
        args.num_records == 0
            ? 0.0
            : static_cast<double>(unique_count) /
                  static_cast<double>(args.num_records);

    std::filesystem::path out(args.output);
    if (std::filesystem::exists(out) && !args.force) {
      throw std::runtime_error("output exists: " + args.output);
    }
    if (!out.parent_path().empty()) {
      std::filesystem::create_directories(out.parent_path());
    }

    FILE* f = std::fopen(args.output.c_str(), "wb");
    if (f == nullptr) {
      throw std::runtime_error("open failed: " + args.output + ": " +
                               std::strerror(errno));
    }

    SplitMix64 mapper_rng(args.seed ^ 0x9e3779b97f4a7c15ULL);
    const uint64_t multiplier =
        ChooseCoprimeMultiplier(args.key_domain, &mapper_rng);
    const uint64_t offset =
        args.key_domain > 0 ? mapper_rng.Bounded(args.key_domain) : 0;

    std::fwrite(kMagic, 1, sizeof(kMagic), f);
    WriteU32(f, kVersion);
    WriteU32(f, kHeaderSize);
    WriteU64(f, args.num_records);
    WriteU64(f, args.key_domain);
    WriteU64(f, unique_count);
    WriteU32(f, args.key_size);
    WriteU32(f, args.value_size);
    WriteDouble(f, effective_unique_ratio);
    WriteDouble(f, args.zipf_alpha);
    WriteU64(f, args.seed);
    WriteU64(f, kFlagExactUniqueCount | kFlagAffineKeyMapping);
    WriteU64(f, 0);

    SplitMix64 rng(args.seed);
    std::vector<uint64_t> chunk;
    chunk.reserve(args.chunk_records);
    uint64_t introduced = 0;

    auto dense_to_key_id = [&](uint64_t rank) -> uint64_t {
      if (args.key_domain <= 1) {
        return 0;
      }
      return (multiplier * rank + offset) % args.key_domain;
    };

    for (uint64_t i = 0; i < args.num_records; ++i) {
      const uint64_t remaining_ops = args.num_records - i;
      const uint64_t remaining_new =
          unique_count > introduced ? unique_count - introduced : 0;
      uint64_t dense_rank = 0;
      if (remaining_new == 0) {
        dense_rank = SampleExistingRank(introduced, args.zipf_alpha, &rng);
      } else if (remaining_new >= remaining_ops || introduced == 0) {
        dense_rank = introduced++;
      } else {
        const double p = static_cast<double>(remaining_new) /
                         static_cast<double>(remaining_ops);
        if (rng.UniformDouble() < p) {
          dense_rank = introduced++;
        } else {
          dense_rank = SampleExistingRank(introduced, args.zipf_alpha, &rng);
        }
      }

      chunk.push_back(dense_to_key_id(dense_rank));
      if (chunk.size() >= args.chunk_records) {
        if (std::fwrite(chunk.data(), sizeof(uint64_t), chunk.size(), f) !=
            chunk.size()) {
          throw std::runtime_error("short data write");
        }
        chunk.clear();
      }

      if (args.progress_every > 0 && (i + 1) % args.progress_every == 0) {
        std::cerr << "[progress] " << (i + 1) << "/" << args.num_records
                  << "\n";
      }
    }
    if (!chunk.empty() &&
        std::fwrite(chunk.data(), sizeof(uint64_t), chunk.size(), f) !=
            chunk.size()) {
      throw std::runtime_error("short data write");
    }
    if (std::fclose(f) != 0) {
      throw std::runtime_error("close failed: " + std::string(std::strerror(errno)));
    }

    std::cout << "output\t" << args.output << "\n"
              << "num_records\t" << args.num_records << "\n"
              << "key_domain\t" << args.key_domain << "\n"
              << "unique_count\t" << unique_count << "\n"
              << "unique_ratio\t" << effective_unique_ratio << "\n"
              << "zipf_alpha\t" << args.zipf_alpha << "\n"
              << "key_size\t" << args.key_size << "\n"
              << "value_size\t" << args.value_size << "\n"
              << "file_bytes\t" << (kHeaderSize + args.num_records * 8ULL)
              << "\n";
  } catch (const std::exception& e) {
    std::cerr << "[ERROR] " << e.what() << "\n";
    return 1;
  }
  return 0;
}
