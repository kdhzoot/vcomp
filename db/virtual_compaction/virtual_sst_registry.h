//  Copyright (c) 2011-present, Facebook, Inc.  All rights reserved.
//  This source code is licensed under both the GPLv2 (found in the
//  COPYING file in the root directory) and Apache 2.0 License
//  (found in the LICENSE.Apache file in the root directory).
#pragma once

#include <algorithm>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "db/virtual_compaction/virtual_sst.h"

namespace ROCKSDB_NAMESPACE {

// Thread-safe registry mapping file_number -> VirtualSST (PLR model + metadata).
// Used to track virtual SSTs that exist in the VersionSet but have no
// corresponding data on disk.
class VirtualSSTRegistry {
 public:
  using Handle = std::shared_ptr<const VirtualSST>;
  using Snapshot = std::vector<std::pair<uint64_t, Handle>>;

  VirtualSSTRegistry() = default;

  void SetKeySize(uint32_t ks) { key_size_ = ks; }
  uint32_t GetKeySize() const { return key_size_; }

  // Logical KV bytes determine input batching, independently of SST encoding.
  void SetAvgEntrySize(uint64_t s) {
    avg_entry_size_ = s;
    size_model_ = SSTSizeModel::Logical(s);
  }
  uint64_t GetAvgEntrySize() const { return avg_entry_size_; }

  // Configure once, before registering any files/background compactions.
  void SetSSTSizeModel(const SSTSizeModel& model) { size_model_ = model; }
  const SSTSizeModel& GetSSTSizeModel() const { return size_model_; }

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
    Handle handle = std::make_shared<const VirtualSST>(std::move(vsst));
    std::lock_guard<std::mutex> lk(mu_);
    registry_[file_number] = std::move(handle);
    retired_.erase(file_number);
  }

  // Returned handles keep the immutable metadata alive after the registry lock
  // is released, including when the file is concurrently removed or replaced.
  Handle Lookup(uint64_t file_number) const {
    std::lock_guard<std::mutex> lk(mu_);
    auto it = registry_.find(file_number);
    return it == registry_.end() ? nullptr : it->second;
  }

  // Look up a compaction input set under one registry lock. A missing file is
  // represented by a null handle at the corresponding input position.
  std::vector<Handle> LookupMany(
      const std::vector<uint64_t>& file_numbers) const {
    std::lock_guard<std::mutex> lk(mu_);
    std::vector<Handle> result;
    result.reserve(file_numbers.size());
    for (uint64_t file_number : file_numbers) {
      auto it = registry_.find(file_number);
      result.push_back(it == registry_.end() ? nullptr : it->second);
    }
    return result;
  }

  // Remove a VirtualSST from the registry.
  void Remove(uint64_t file_number) {
    std::lock_guard<std::mutex> lk(mu_);
    if (registry_.erase(file_number) > 0) {
      retired_.insert(file_number);
    }
  }

  bool ConsumeVirtualOrRetired(uint64_t file_number) {
    std::lock_guard<std::mutex> lk(mu_);
    if (registry_.erase(file_number) > 0) {
      return true;
    }
    return retired_.erase(file_number) > 0;
  }

  // Check if a file number corresponds to a virtual SST.
  bool IsVirtual(uint64_t file_number) const {
    std::lock_guard<std::mutex> lk(mu_);
    return registry_.count(file_number) > 0;
  }

  Snapshot GetSnapshot() const {
    std::lock_guard<std::mutex> lk(mu_);
    Snapshot result;
    result.reserve(registry_.size());
    for (const auto& kv : registry_) {
      result.emplace_back(kv.first, kv.second);
    }
    return result;
  }

  size_t Size() const {
    std::lock_guard<std::mutex> lk(mu_);
    return registry_.size();
  }

 private:
  mutable std::mutex mu_;
  std::unordered_map<uint64_t, Handle> registry_;
  std::unordered_set<uint64_t> retired_;
  uint32_t key_size_ = 16;
  uint64_t avg_entry_size_ = 1048;
  SSTSizeModel size_model_ = SSTSizeModel::Logical(1048);
  uint64_t target_sst_size_ = 64ULL * 1024 * 1024;
};

}  // namespace ROCKSDB_NAMESPACE
