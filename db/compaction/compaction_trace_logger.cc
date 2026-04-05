//  Copyright (c) 2011-present, Facebook, Inc.  All rights reserved.
//  This source code is licensed under both the GPLv2 (found in the
//  COPYING file in the root directory) and Apache 2.0 License
//  (found in the LICENSE.Apache file in the root directory).

#include "db/compaction/compaction_trace_logger.h"

#include <cinttypes>
#include <cstdio>
#include <ctime>

#include "db/compaction/compaction.h"
#include "db/compaction/compaction_outputs.h"
#include "db/compaction/compaction_state.h"
#include "db/compaction/subcompaction_state.h"
#include "db/version_edit.h"
#include "rocksdb/compaction_job_stats.h"

namespace ROCKSDB_NAMESPACE {

std::unique_ptr<CompactionTraceLogger> CompactionTraceLogger::Create(
    const std::string& trace_dir, Env* env, int job_id) {
  if (trace_dir.empty() || env == nullptr) {
    return nullptr;
  }

  // Ensure the trace directory exists
  env->CreateDirIfMissing(trace_dir);

  std::string fname =
      trace_dir + "/compaction_trace_job_" + std::to_string(job_id) + ".log";
  std::unique_ptr<WritableFile> file;
  Status s = env->NewWritableFile(fname, &file, EnvOptions());
  if (!s.ok()) {
    return nullptr;
  }

  return std::unique_ptr<CompactionTraceLogger>(
      new CompactionTraceLogger(std::move(file), job_id));
}

CompactionTraceLogger::CompactionTraceLogger(
    std::unique_ptr<WritableFile> file, int job_id)
    : file_(std::move(file)), job_id_(job_id) {}

CompactionTraceLogger::~CompactionTraceLogger() { Close(); }

void CompactionTraceLogger::Write(const std::string& data) {
  if (file_ && !closed_) {
    file_->Append(data);
  }
}

void CompactionTraceLogger::Close() {
  if (file_ && !closed_) {
    file_->Flush();
    file_->Close();
    closed_ = true;
  }
}

const char* CompactionTraceLogger::ValueTypeName(ValueType type) {
  switch (type) {
    case kTypeDeletion:
      return "Delete";
    case kTypeValue:
      return "Put";
    case kTypeMerge:
      return "Merge";
    case kTypeSingleDeletion:
      return "SingleDelete";
    case kTypeRangeDeletion:
      return "RangeDelete";
    case kTypeBlobIndex:
      return "BlobIndex";
    case kTypeWideColumnEntity:
      return "WideColumnEntity";
    case kTypeDeletionWithTimestamp:
      return "DeleteWithTS";
    case kTypeValuePreferredSeqno:
      return "ValuePreferredSeqno";
    default:
      return "Unknown";
  }
}

std::string CompactionTraceLogger::SliceToHex(const Slice& s,
                                              size_t max_len) {
  std::string result;
  size_t len = std::min(s.size(), max_len);
  result.reserve(len * 2);
  for (size_t i = 0; i < len; i++) {
    char buf[3];
    snprintf(buf, sizeof(buf), "%02X",
             static_cast<unsigned char>(s.data()[i]));
    result.append(buf);
  }
  if (s.size() > max_len) {
    result.append("...");
  }
  return result;
}


void CompactionTraceLogger::LogJobStart(const Compaction* compaction,
                                        int job_id) {
  if (!compaction) return;

  // Timestamp
  auto now = std::time(nullptr);
  char time_buf[64];
  std::strftime(time_buf, sizeof(time_buf), "%Y-%m-%d %H:%M:%S",
                std::localtime(&now));

  std::string buf;
  buf.reserve(4096);

  buf.append("========================================\n");
  buf.append("COMPACTION TRACE LOG\n");
  buf.append("========================================\n");
  buf.append("Timestamp: ");
  buf.append(time_buf);
  buf.append("\n");
  buf.append("Job ID: ");
  buf.append(std::to_string(job_id));
  buf.append("\n");

  const auto* cfd = compaction->column_family_data();
  if (cfd) {
    buf.append("Column Family: ");
    buf.append(cfd->GetName());
    buf.append(" (ID: ");
    buf.append(std::to_string(cfd->GetID()));
    buf.append(")\n");
  }

  buf.append("Compaction Reason: ");
  buf.append(
      GetCompactionReasonString(compaction->compaction_reason()));
  buf.append("\n");

  buf.append("Input Level(s): ");
  for (size_t i = 0; i < compaction->num_input_levels(); i++) {
    if (i > 0) buf.append(", ");
    buf.append("L");
    buf.append(std::to_string(compaction->level(i)));
  }
  buf.append("\n");

  buf.append("Output Level: L");
  buf.append(std::to_string(compaction->output_level()));
  buf.append("\n");

  buf.append("Is Manual: ");
  buf.append(compaction->is_manual_compaction() ? "true" : "false");
  buf.append("\n");

  buf.append("Is Full: ");
  buf.append(compaction->is_full_compaction() ? "true" : "false");
  buf.append("\n");

  buf.append("Output Compression: ");
  buf.append(CompressionTypeToString(compaction->output_compression()));
  buf.append("\n");

  buf.append("Target File Size: ");
  buf.append(std::to_string(compaction->target_output_file_size()));
  buf.append("\n");

  buf.append("Max Output File Size: ");
  buf.append(std::to_string(compaction->max_output_file_size()));
  buf.append("\n");

  buf.append("Total Input Size: ");
  buf.append(std::to_string(compaction->CalculateTotalInputSize()));
  buf.append(" bytes\n");

  buf.append("Score: ");
  char score_buf[32];
  snprintf(score_buf, sizeof(score_buf), "%.2f", compaction->score());
  buf.append(score_buf);
  buf.append("\n");

  // Input files per level
  buf.append("\n--- INPUT FILES ---\n");
  for (size_t lvl = 0; lvl < compaction->num_input_levels(); lvl++) {
    int level = compaction->level(lvl);
    size_t num_files = compaction->num_input_files(lvl);
    buf.append("Level ");
    buf.append(std::to_string(level));
    buf.append(" (");
    buf.append(std::to_string(num_files));
    buf.append(" files):\n");

    for (size_t i = 0; i < num_files; i++) {
      const FileMetaData* f = compaction->input(lvl, i);
      buf.append("  File #");
      buf.append(std::to_string(f->fd.GetNumber()));
      buf.append("\n");

      buf.append("    Size: ");
      buf.append(std::to_string(f->fd.GetFileSize()));
      buf.append(" bytes\n");

      buf.append("    Smallest Key: ");
      buf.append(SliceToHex(f->smallest.user_key()));
      buf.append("\n");

      buf.append("    Largest Key: ");
      buf.append(SliceToHex(f->largest.user_key()));
      buf.append("\n");

      buf.append("    Seq Range: [");
      buf.append(std::to_string(f->fd.smallest_seqno));
      buf.append(", ");
      buf.append(std::to_string(f->fd.largest_seqno));
      buf.append("]\n");

      buf.append("    Entries: ");
      buf.append(std::to_string(f->num_entries));
      buf.append(" (deletions: ");
      buf.append(std::to_string(f->num_deletions));
      buf.append(", range_deletions: ");
      buf.append(std::to_string(f->num_range_deletions));
      buf.append(")\n");

      buf.append("    Raw Key Size: ");
      buf.append(std::to_string(f->raw_key_size));
      buf.append(", Raw Value Size: ");
      buf.append(std::to_string(f->raw_value_size));
      buf.append("\n");

      buf.append("    Epoch: ");
      buf.append(std::to_string(f->epoch_number));
      buf.append("\n");
    }
  }

  Write(buf);
}

void CompactionTraceLogger::LogInputFileKeysHeader(int level,
                                                    uint64_t file_number) {
  std::string buf;
  buf.append("\n  --- File #");
  buf.append(std::to_string(file_number));
  buf.append(" (L");
  buf.append(std::to_string(level));
  buf.append(") Keys ---\n");
  Write(buf);
}

void CompactionTraceLogger::LogInputFileKeysFooter(uint64_t num_keys) {
  std::string buf;
  buf.append("  Total Keys in File: ");
  buf.append(std::to_string(num_keys));
  buf.append("\n");
  Write(buf);
}

void CompactionTraceLogger::LogInputKey(const ParsedInternalKey& ikey) {
  std::string buf;
  buf.reserve(128);
  buf.append("  [");
  buf.append(std::to_string(ikey.sequence));
  buf.append("] ");
  buf.append(ValueTypeName(ikey.type));
  buf.append(" | ");
  buf.append(SliceToHex(ikey.user_key));
  buf.append("\n");
  Write(buf);
}

void CompactionTraceLogger::LogOutputFiles(CompactionState* compact) {
  if (!compact) return;

  const Compaction* compaction = compact->compaction;
  std::string buf;
  buf.reserve(4096);

  buf.append("\n--- OUTPUT FILES ---\n");

  int total_output_files = 0;
  uint64_t total_output_bytes = 0;

  for (size_t sc = 0; sc < compact->sub_compact_states.size(); sc++) {
    auto& sub = compact->sub_compact_states[sc];

    buf.append("Subcompaction ");
    buf.append(std::to_string(sc));
    buf.append(" (sub_job_id: ");
    buf.append(std::to_string(sub.sub_job_id));
    buf.append(", status: ");
    buf.append(sub.status.ToString());
    buf.append("):\n");

    // Helper lambda to log output files
    auto log_output_files = [&](const std::vector<CompactionOutputs::Output>& outputs,
                                const std::string& level_label) {
      if (outputs.empty()) return;
      buf.append("  ");
      buf.append(level_label);
      buf.append(":\n");
      for (const auto& out : outputs) {
        const auto& meta = out.meta;
        buf.append("    File #");
        buf.append(std::to_string(meta.fd.GetNumber()));
        buf.append("\n");

        buf.append("      Size: ");
        buf.append(std::to_string(meta.fd.GetFileSize()));
        buf.append(" bytes\n");

        buf.append("      Smallest Key: ");
        buf.append(SliceToHex(meta.smallest.user_key()));
        buf.append("\n");

        buf.append("      Largest Key: ");
        buf.append(SliceToHex(meta.largest.user_key()));
        buf.append("\n");

        buf.append("      Seq Range: [");
        buf.append(std::to_string(meta.fd.smallest_seqno));
        buf.append(", ");
        buf.append(std::to_string(meta.fd.largest_seqno));
        buf.append("]\n");

        // Use table_properties for accurate stats (FileMetaData stats
        // are not populated until Install(), after Run() completes)
        if (out.table_properties) {
          const auto& tp = *out.table_properties;
          buf.append("      Entries: ");
          buf.append(std::to_string(tp.num_entries));
          buf.append(" (deletions: ");
          buf.append(std::to_string(tp.num_deletions));
          buf.append(", range_deletions: ");
          buf.append(std::to_string(tp.num_range_deletions));
          buf.append(")\n");

          buf.append("      Raw Key Size: ");
          buf.append(std::to_string(tp.raw_key_size));
          buf.append(", Raw Value Size: ");
          buf.append(std::to_string(tp.raw_value_size));
          buf.append("\n");

          buf.append("      Data Size: ");
          buf.append(std::to_string(tp.data_size));
          buf.append(", Index Size: ");
          buf.append(std::to_string(tp.index_size));
          buf.append(", Filter Size: ");
          buf.append(std::to_string(tp.filter_size));
          buf.append("\n");
        } else {
          buf.append("      Entries: ");
          buf.append(std::to_string(meta.num_entries));
          buf.append("\n");
        }

        total_output_files++;
        total_output_bytes += meta.fd.GetFileSize();
      }
    };

    // Normal output files
    const auto& outputs = sub.Outputs(false)->GetOutputs();
    log_output_files(outputs,
                     "Output Level L" +
                         std::to_string(compaction->output_level()));

    // Proximal level output files (if per-key placement is supported)
    if (compaction->SupportsPerKeyPlacement()) {
      const auto& prox_outputs = sub.Outputs(true)->GetOutputs();
      log_output_files(prox_outputs,
                       "Proximal Level L" +
                           std::to_string(compaction->GetProximalLevel()));
    }
  }

  buf.append("\nTotal Output Files: ");
  buf.append(std::to_string(total_output_files));
  buf.append("\nTotal Output Size: ");
  buf.append(std::to_string(total_output_bytes));
  buf.append(" bytes\n");

  Write(buf);
}

void CompactionTraceLogger::LogJobEnd(const CompactionJobStats* stats) {
  std::string buf;
  buf.reserve(1024);

  buf.append("\n--- COMPACTION SUMMARY ---\n");

  if (stats) {
    buf.append("Elapsed Time: ");
    buf.append(std::to_string(stats->elapsed_micros));
    buf.append(" us\n");

    buf.append("CPU Time: ");
    buf.append(std::to_string(stats->cpu_micros));
    buf.append(" us\n");

    buf.append("Input Records: ");
    buf.append(std::to_string(stats->num_input_records));
    buf.append("\n");

    buf.append("Output Records: ");
    buf.append(std::to_string(stats->num_output_records));
    buf.append("\n");

    buf.append("Input Files: ");
    buf.append(std::to_string(stats->num_input_files));
    buf.append("\n");

    buf.append("Output Files: ");
    buf.append(std::to_string(stats->num_output_files));
    buf.append("\n");

    buf.append("Total Input Bytes: ");
    buf.append(std::to_string(stats->total_input_bytes));
    buf.append("\n");

    buf.append("Total Output Bytes: ");
    buf.append(std::to_string(stats->total_output_bytes));
    buf.append("\n");

    buf.append("Records Replaced: ");
    buf.append(std::to_string(stats->num_records_replaced));
    buf.append("\n");

    buf.append("Expired Deletions: ");
    buf.append(std::to_string(stats->num_expired_deletion_records));
    buf.append("\n");

    buf.append("Input Raw Key Bytes: ");
    buf.append(std::to_string(stats->total_input_raw_key_bytes));
    buf.append("\n");

    buf.append("Input Raw Value Bytes: ");
    buf.append(std::to_string(stats->total_input_raw_value_bytes));
    buf.append("\n");

    if (stats->file_write_nanos > 0 || stats->file_fsync_nanos > 0) {
      buf.append("File Write Time: ");
      buf.append(std::to_string(stats->file_write_nanos));
      buf.append(" ns\n");

      buf.append("File Fsync Time: ");
      buf.append(std::to_string(stats->file_fsync_nanos));
      buf.append(" ns\n");
    }
  }

  buf.append("========================================\n");
  buf.append("END OF COMPACTION TRACE\n");
  buf.append("========================================\n");

  Write(buf);
}

}  // namespace ROCKSDB_NAMESPACE
