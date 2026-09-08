#pragma once

#include <string>
#include <vector>

#include "core/db.h"
#include "titan/db.h"

namespace ycsbc {

// Minimal YCSB adapter for artifact validation. It preserves the artifact's
// Titan/DiffKV options and flushes once after all YCSB worker futures join so
// the database survives process restart without racing active writers.
class TitanDBSmoke : public DB {
 public:
  TitanDBSmoke(const char* dbfilename, const std::string& config_file_path);
  ~TitanDBSmoke() override;

  void Close() override;
  int Read(const std::string& table, const std::string& key,
           const std::vector<std::string>* fields,
           std::vector<KVPair>& result) override;
  int Scan(const std::string& table, const std::string& key, int len,
           const std::vector<std::string>* fields,
           std::vector<std::vector<KVPair>>& result) override;
  int Insert(const std::string& table, const std::string& key,
             std::vector<KVPair>& values) override;
  int Update(const std::string& table, const std::string& key,
             std::vector<KVPair>& values) override;
  int Delete(const std::string& table, const std::string& key) override;

 private:
  rocksdb::titandb::TitanDB* db_{nullptr};
};

}  // namespace ycsbc
