//  Copyright (c) 2011-present, Facebook, Inc.  All rights reserved.
//  This source code is licensed under both the GPLv2 (found in the
//  COPYING file in the root directory) and Apache 2.0 License
//  (found in the LICENSE.Apache file in the root directory).
#pragma once

#include <cstdint>
#include <memory>
#include <string>

#include "db/dbformat.h"
#include "rocksdb/env.h"
#include "rocksdb/slice.h"

namespace ROCKSDB_NAMESPACE {

class Compaction;
class CompactionState;
class FileMetaData;
class SubcompactionState;
struct CompactionJobStats;

// CompactionTraceLogger creates a per-compaction-job log file and records
// detailed information about the compaction: job metadata, input/output file
// details, and every key-value pair processed.
//
// Enabled when DBOptions::compaction_trace_dir is non-empty. Log files are
// written as: <compaction_trace_dir>/compaction_trace_job_<job_id>.log
class CompactionTraceLogger {
 public:
  // Returns nullptr if trace_dir is empty (tracing disabled).
  static std::unique_ptr<CompactionTraceLogger> Create(
      const std::string& trace_dir, Env* env, int job_id);

  ~CompactionTraceLogger();

  // Log compaction job header: job id, column family, levels, reason, etc.
  void LogJobStart(const Compaction* compaction, int job_id);

  // Log header/footer for per-input-file key dump.
  void LogInputFileKeysHeader(int level, uint64_t file_number);
  void LogInputFileKeysFooter(uint64_t num_keys);

  // Log a single key from an input file (before merge).
  void LogInputKey(const ParsedInternalKey& ikey);

  // Log output file information after compaction completes.
  void LogOutputFiles(CompactionState* compact);

  // Log compaction job completion with final stats.
  void LogJobEnd(const CompactionJobStats* stats);

  // Flush and close the log file.
  void Close();

 private:
  CompactionTraceLogger(std::unique_ptr<WritableFile> file, int job_id);

  void Write(const std::string& data);

  static const char* ValueTypeName(ValueType type);
  static std::string SliceToHex(const Slice& s, size_t max_len = 256);

  std::unique_ptr<WritableFile> file_;
  int job_id_;
  bool closed_ = false;
};

}  // namespace ROCKSDB_NAMESPACE
