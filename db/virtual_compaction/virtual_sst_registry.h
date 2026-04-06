//  Copyright (c) 2011-present, Facebook, Inc.  All rights reserved.
//  This source code is licensed under both the GPLv2 (found in the
//  COPYING file in the root directory) and Apache 2.0 License
//  (found in the LICENSE.Apache file in the root directory).
#pragma once

#include <algorithm>
#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

#include "db/virtual_compaction/virtual_sst.h"

namespace ROCKSDB_NAMESPACE {

// Thread-safe registry mapping file_number -> VirtualSST (PLR model + metadata).
// Used to track virtual SSTs that exist in the VersionSet but have no
// corresponding data on disk.
class VirtualSSTRegistry {
 public:
  VirtualSSTRegistry() = default;

  void SetKeySize(uint32_t ks) { key_size_ = ks; }
  uint32_t GetKeySize() const { return key_size_; }

  void SetAvgEntrySize(uint64_t s) { avg_entry_size_ = s; }
  uint64_t GetAvgEntrySize() const { return avg_entry_size_; }

  void SetTargetSSTSize(uint64_t s) { target_sst_size_ = s; }
  uint64_t GetTargetSSTSize() const { return target_sst_size_; }

  // Encode uint64_t to user key string matching db_bench GenerateKeyFromInt
  // format (big-endian 8 bytes + '0' padding).
  std::string EncodeUserKey(uint64_t v) const {
    std::string key(key_size_, '0');
    int bytes_to_fill = std::min(static_cast<int>(key_size_), 8);
    for (int i = 0; i < bytes_to_fill; i++) {
      key[i] = static_cast<char>((v >> ((bytes_to_fill - i - 1) << 3)) & 0xFF);
    }
    return key;
  }

  // Register a VirtualSST with the given file number.
  void Register(uint64_t file_number, VirtualSST vsst) {
    std::lock_guard<std::mutex> lk(mu_);
    registry_[file_number] = std::move(vsst);
  }

  // Look up a VirtualSST by file number. Returns nullptr if not found.
  const VirtualSST* Lookup(uint64_t file_number) const {
    std::lock_guard<std::mutex> lk(mu_);
    auto it = registry_.find(file_number);
    if (it == registry_.end()) return nullptr;
    return &it->second;
  }

  // Remove a VirtualSST from the registry.
  void Remove(uint64_t file_number) {
    std::lock_guard<std::mutex> lk(mu_);
    registry_.erase(file_number);
  }

  // Check if a file number corresponds to a virtual SST.
  bool IsVirtual(uint64_t file_number) const {
    std::lock_guard<std::mutex> lk(mu_);
    return registry_.count(file_number) > 0;
  }

  // Get all registered file numbers and their VirtualSSTs.
  std::vector<std::pair<uint64_t, const VirtualSST*>> GetAll() const {
    std::lock_guard<std::mutex> lk(mu_);
    std::vector<std::pair<uint64_t, const VirtualSST*>> result;
    result.reserve(registry_.size());
    for (const auto& kv : registry_) {
      result.emplace_back(kv.first, &kv.second);
    }
    return result;
  }

  size_t Size() const {
    std::lock_guard<std::mutex> lk(mu_);
    return registry_.size();
  }

 private:
  mutable std::mutex mu_;
  std::unordered_map<uint64_t, VirtualSST> registry_;
  uint32_t key_size_ = 16;
  uint64_t avg_entry_size_ = 1048;
  uint64_t target_sst_size_ = 64ULL * 1024 * 1024;
};

}  // namespace ROCKSDB_NAMESPACE
