// Independent streaming VLOADTR1 verifier: exact distinct key IDs via bitset.
#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
void Require(bool ok, const std::string& message) {
  if (!ok) throw std::runtime_error(message);
}
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
uint64_t LE(const unsigned char* p, size_t n = 8) {
  uint64_t v = 0;
  for (size_t i = 0; i < n; ++i) v |= uint64_t(p[i]) << (8 * i);
  return v;
}
void Write(const std::string& path, const std::string& json) {
  if (!path.empty()) {
    std::ofstream out(path);
    Require(bool(out), "cannot open output: " + path);
    out << json << '\n';
    out.close();
    Require(bool(out), "cannot write output: " + path);
  }
  std::cout << json << '\n';
}
}  // namespace

int main(int argc, char** argv) {
  std::string output;
  try {
    std::map<std::string, std::string> args;
    for (int i = 1; i < argc; i += 2) {
      const std::string key = argv[i];
      Require(i + 1 < argc, "missing value for " + key);
      Require(key == "--trace" || key == "--output", "unknown argument: " + key);
      Require(args.emplace(key, argv[i + 1]).second, "duplicate argument: " + key);
    }
    Require(args.count("--trace") && args.count("--output") && !args.at("--output").empty(),
            "required: --trace PATH --output PATH");
    // Keep output unset until it is known not to alias the input. The catch
    // block also writes a report, so rejecting a collision after accepting
    // output would overwrite the trace with the error report.
    const auto trace_path = std::filesystem::canonical(args.at("--trace"));
    const auto output_path = std::filesystem::weakly_canonical(args.at("--output"));
    Require(trace_path != output_path, "trace and output paths must differ");
    if (std::filesystem::exists(output_path))
      Require(!std::filesystem::equivalent(trace_path, output_path),
              "trace and output must not refer to the same file");
    output = args.at("--output");
    std::ifstream in(args.at("--trace"), std::ios::binary);
    Require(bool(in), "cannot open trace");
    std::array<unsigned char, 88> h{};
    in.read(reinterpret_cast<char*>(h.data()), h.size());
    Require(in.gcount() == 88 && !in.bad(), "truncated/unreadable 88-byte header");
    Require(std::string(reinterpret_cast<char*>(h.data()), 8) == "VLOADTR1", "invalid magic");
    Require(LE(h.data() + 8, 4) == 1 && LE(h.data() + 12, 4) == 88,
            "requires VLOADTR1 version 1, exactly 88-byte header");
    const auto records = LE(h.data() + 16), domain = LE(h.data() + 24);
    const auto expected = LE(h.data() + 32), key_size = LE(h.data() + 40, 4);
    const auto value_size = LE(h.data() + 44, 4), flags = LE(h.data() + 72);
    const double ratio = std::bit_cast<double>(LE(h.data() + 48));
    const double alpha = std::bit_cast<double>(LE(h.data() + 56));
    Require(domain > 0 && expected <= domain && expected <= records,
            "invalid header cardinalities");
    Require(key_size >= 8 && value_size > 0, "invalid key/value sizes for prefix-free uint64 encoding");
    Require(std::isfinite(ratio) && ratio >= 0 && ratio <= 1 &&
                std::isfinite(alpha) && alpha >= 0, "invalid ratio/Zipf fields");
    Require((flags & ~uint64_t(3)) == 0 && (flags & 1) != 0 && LE(h.data() + 80) == 0,
            "unsupported flags/reserved header fields");
    const auto wanted_ratio = records ? double(expected) / double(records) : 0;
    Require(std::abs(ratio - wanted_ratio) <= 1e-12, "header unique ratio mismatch");
    Require(records <= (std::numeric_limits<uint64_t>::max() - 88) / 8,
            "trace byte size overflow");
    const uint64_t words = domain / 64 + (domain % 64 != 0);
    Require(words <= std::numeric_limits<size_t>::max() / sizeof(uint64_t), "bitset too large");
    std::vector<uint64_t> bits(static_cast<size_t>(words), 0);
    constexpr size_t kChunkRecords = 1 << 20;
    std::vector<unsigned char> chunk(kChunkRecords * 8);
    uint64_t seen = 0, unique = 0;
    while (seen < records) {
      const size_t n = static_cast<size_t>(std::min<uint64_t>(kChunkRecords, records - seen));
      in.read(reinterpret_cast<char*>(chunk.data()), n * 8);
      Require(static_cast<size_t>(in.gcount()) == n * 8 && !in.bad(), "truncated/unreadable records");
      for (size_t i = 0; i < n; ++i) {
        const uint64_t id = LE(chunk.data() + i * 8);
        if (id >= domain)
          throw std::runtime_error("record ID outside key domain at record " + std::to_string(seen + i));
        const auto mask = uint64_t(1) << (id & 63);
        auto& word = bits[id >> 6];
        if (!(word & mask)) { word |= mask; ++unique; }
      }
      seen += n;
    }
    char extra;
    in.get(extra);
    Require(!in.bad(), "I/O error while checking EOF");
    Require(in.eof(), "unexpected trailing trace bytes");
    const bool matches = unique == expected;
    std::ostringstream json;
    json << std::boolalpha << "{\"status\":" << Quote(matches ? "ok" : "invalid")
         << ",\"trace\":" << Quote(args.at("--trace"))
         << ",\"records\":" << seen << ",\"key_domain\":" << domain
         << ",\"key_size\":" << key_size << ",\"value_size\":" << value_size
         << ",\"header_unique_keys\":" << expected << ",\"exact_unique_keys\":" << unique
         << ",\"unique_count_matches\":" << matches << ",\"byte_size\":" << 88 + records * 8
         << ",\"bitset_bytes\":" << words * 8 << '}';
    Write(output, json.str());
    return matches ? 0 : 1;
  } catch (const std::exception& error) {
    const auto json = "{\"status\":\"error\",\"error\":" + Quote(error.what()) + '}';
    std::cerr << error.what() << '\n';
    try { Write(output, json); } catch (...) { std::cerr << "cannot write error report\n"; }
    return 1;
  }
}
