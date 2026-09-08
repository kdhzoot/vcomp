// Small sequence-number regression fixture and independent clean RocksDB reader.
// Build with VCOMP_SEQUENCE_PRODUCER against vcomp, or without it against clean
// RocksDB. Never points at an existing experiment DB and never calls DestroyDB.
#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <list>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

#include "rocksdb/db.h"
#include "rocksdb/filter_policy.h"
#include "rocksdb/metadata.h"
#include "rocksdb/options.h"
#include "rocksdb/sst_file_writer.h"
#include "rocksdb/table.h"

#ifdef VCOMP_SEQUENCE_PRODUCER
#include "db/column_family.h"
#include "db/db_impl/db_impl.h"
#include "db/version_edit.h"
#include "db/version_set.h"
#include "file/filename.h"
#include "monitoring/instrumented_mutex.h"
#endif

namespace {
namespace r = ROCKSDB_NAMESPACE;
using Map = std::map<std::string, std::string>;

void Require(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}
void Check(const r::Status& status, const std::string& message) {
  Require(status.ok(), message + ": " + status.ToString());
}
std::string Key(uint64_t id) {
  std::string key(24, '0');
  for (unsigned int i = 0; i < 8; ++i)
    key[i] = static_cast<char>((id >> (8 * (7 - i))) & 0xff);
  return key;
}
Map Expected() {
  return {{Key(1), "new1"}, {Key(2), "old2"}, {Key(4), "new4"},
          {Key(7), "old7"}, {Key(9), "new9"}};
}
r::Options Options() {
  r::Options options;
  options.create_if_missing = false;
  options.error_if_exists = false;
  options.disable_auto_compactions = true;
  options.num_levels = 7;
  options.max_background_jobs = 2;
  options.compression = r::kNoCompression;
  options.use_direct_reads = true;
  options.use_direct_io_for_flush_and_compaction = true;
  r::BlockBasedTableOptions table;
  table.format_version = 6;
  table.enable_index_compression = false;
  table.filter_policy.reset(r::NewBloomFilterPolicy(10, false));
  options.table_factory.reset(r::NewBlockBasedTableFactory(table));
  return options;
}
struct Args {
  std::filesystem::path db;
  std::string scenario;
  bool check_only = false;
};
Args Parse(int argc, char** argv) {
  Args args;
  std::set<std::string> seen;
  for (int i = 1; i < argc; ++i) {
    const std::string flag = argv[i];
    Require(seen.insert(flag).second, "duplicate argument: " + flag);
    if (flag == "--check-only") {
      args.check_only = true;
      continue;
    }
    Require((flag == "--db" || flag == "--case") && i + 1 < argc,
            "usage: --db /work/vcomp/exp/fidelity_seqfix_20260908.../CASE --case levels|l0 [--check-only]");
    if (flag == "--db") args.db = argv[++i];
    else args.scenario = argv[++i];
  }
  Require(!args.db.empty(), "missing --db");
  Require(args.scenario == "levels" || args.scenario == "l0", "case must be levels or l0");
  args.db = std::filesystem::weakly_canonical(args.db);
  const auto relative = args.db.lexically_relative("/work/vcomp/exp");
  Require(!relative.empty(), "DB must be below /work/vcomp/exp");
  const auto first = relative.begin()->string();
  Require(first.rfind("fidelity_seqfix_20260908", 0) == 0 &&
              std::distance(relative.begin(), relative.end()) >= 2,
          "DB must be a fresh case directory below /work/vcomp/exp/fidelity_seqfix_20260908...");
  return args;
}

void Verify(r::DB* db, const Map& expected, const r::Snapshot* snapshot,
            const std::string& stage) {
  r::ReadOptions ro;
  ro.snapshot = snapshot;
  ro.verify_checksums = true;
  ro.total_order_seek = true;
  auto iterator = std::unique_ptr<r::Iterator>(db->NewIterator(ro));
  auto wanted = expected.begin();
  for (iterator->SeekToFirst(); iterator->Valid(); iterator->Next()) {
    Require(wanted != expected.end(), stage + ": forward has extra/duplicate key");
    Require(iterator->key().ToString() == wanted->first &&
                iterator->value().ToString() == wanted->second,
            stage + ": forward key/value mismatch");
    ++wanted;
  }
  Check(iterator->status(), stage + ": forward iterator status");
  Require(wanted == expected.end(), stage + ": forward missing key");
  auto reverse_wanted = expected.rbegin();
  for (iterator->SeekToLast(); iterator->Valid(); iterator->Prev()) {
    Require(reverse_wanted != expected.rend(), stage + ": reverse has extra/duplicate key");
    Require(iterator->key().ToString() == reverse_wanted->first &&
                iterator->value().ToString() == reverse_wanted->second,
            stage + ": reverse key/value mismatch");
    ++reverse_wanted;
  }
  Check(iterator->status(), stage + ": reverse iterator status");
  Require(reverse_wanted == expected.rend(), stage + ": reverse missing key");
  // Exercise direction changes around the overlapping key in the live view.
  iterator->Seek(Key(4));
  if (expected.count(Key(4))) {
    Require(iterator->Valid() && iterator->key().ToString() == Key(4) &&
                iterator->value().ToString() == expected.at(Key(4)), stage + ": Seek(4) mismatch");
    iterator->Next();
    Require(iterator->Valid() && iterator->key().ToString() == Key(7), stage + ": Seek/Next mismatch");
    iterator->Prev();
    Require(iterator->Valid() && iterator->key().ToString() == Key(4) &&
                iterator->value().ToString() == expected.at(Key(4)), stage + ": Next/Prev mismatch");
  }
  Check(iterator->status(), stage + ": direction-change status");
  std::vector<std::string> owned;
  for (uint64_t id : {9, 4, 8, 1, 2, 7, 10}) owned.push_back(Key(id));
  std::vector<r::Slice> keys;
  for (const auto& key : owned) keys.emplace_back(key);
  std::vector<std::string> values;
  const auto statuses = db->MultiGet(ro, keys, &values);
  Require(statuses.size() == keys.size() && values.size() == keys.size(), stage + ": MultiGet size mismatch");
  for (size_t i = 0; i < keys.size(); ++i) {
    const auto found = expected.find(owned[i]);
    std::string value;
    const auto status = db->Get(ro, keys[i], &value);
    if (found == expected.end()) {
      Require(status.IsNotFound() && statuses[i].IsNotFound(), stage + ": missing-key Get/MultiGet mismatch");
    } else {
      Check(status, stage + ": Get");
      Check(statuses[i], stage + ": MultiGet");
      Require(value == found->second && values[i] == found->second, stage + ": Get/MultiGet wrong winner");
    }
  }
  std::cout << "PASS " << stage << " visible_keys=" << expected.size() << '\n';
}

void VerifyFileMetadata(r::DB* db, const std::string& scenario) {
  std::vector<r::LiveFileMetaData> files;
  db->GetLiveFilesMetaData(&files);
  Require(files.size() == 2, "fixture must have exactly two SSTs before mutations");
  std::set<uint64_t> sequences;
  uint64_t old_sequence = 0, new_sequence = 0;
  for (const auto& file : files) {
    Require(file.smallest_seqno > 0 && file.smallest_seqno == file.largest_seqno,
            "each materialized file must have one positive global sequence");
    Require(sequences.insert(file.smallest_seqno).second, "file sequence numbers must differ");
    Require(db->GetLatestSequenceNumber() >= file.largest_seqno, "runtime/manifest sequence below SST sequence");
    const bool newer = file.smallestkey == Key(1);
    Require(file.smallestkey == Key(newer ? 1 : 2) && file.largestkey == Key(newer ? 9 : 7),
            "file key bounds changed");
    Require(file.level == (scenario == "l0" ? 0 : (newer ? 1 : 2)), "fixture level placement changed");
    if (newer) new_sequence = file.smallest_seqno;
    else old_sequence = file.smallest_seqno;
  }
  Require(new_sequence > old_sequence && old_sequence > 0, "newer file must have higher positive sequence");
}

#ifdef VCOMP_SEQUENCE_PRODUCER
void Produce(const Args& args) {
  Require(!args.check_only, "producer does not accept --check-only");
  Require(!std::filesystem::exists(args.db), "producer requires a new DB path");
  std::filesystem::create_directories(args.db.parent_path());
  auto options = Options();
  options.create_if_missing = true;
  options.error_if_exists = true;
  std::unique_ptr<r::DB> db;
  Check(r::DB::Open(options, args.db.string(), &db), "create fixture DB");
  auto* impl = static_cast<r::DBImpl*>(db->GetRootDB());
  auto* cfd = static_cast<r::ColumnFamilyHandleImpl*>(db->DefaultColumnFamily())->cfd();
  auto* versions = impl->GetVersionSet();
  const r::Snapshot* before = db->GetSnapshot();
  Require(before != nullptr, "empty snapshot missing");
  auto retained_iterator = std::unique_ptr<r::Iterator>(db->NewIterator(r::ReadOptions()));
  retained_iterator->SeekToFirst();
  Require(!retained_iterator->Valid(), "initial DB must be empty");
  Check(db->PauseBackgroundWork(), "pause background work");
  std::unique_ptr<std::list<uint64_t>::iterator> pending;
  {
    r::InstrumentedMutexLock lock(impl->mutex());
    pending = impl->CaptureVirtualCompactionMaterializationOutputs();
  }
  r::VersionEdit edit;
  const uint64_t old_epoch = cfd->NewEpochNumber();
  const uint64_t new_epoch = cfd->NewEpochNumber();
  // Create/add the newer file first: ordering must follow levels/epochs rather
  // than file-number order or caller's vector iteration order.
  for (bool newer : {true, false}) {
    const Map contents = newer ? Map{{Key(1), "new1"}, {Key(4), "new4"}, {Key(9), "new9"}}
                               : Map{{Key(2), "old2"}, {Key(4), "old4"}, {Key(7), "old7"}};
    const uint64_t number = versions->NewFileNumber();
    const auto path = r::TableFileName(cfd->ioptions().cf_paths, number, 0u);
    r::EnvOptions env_options;
    env_options.use_direct_writes = true;
    r::SstFileWriter writer(env_options, options);
    Check(writer.Open(path), "open external SST");
    for (const auto& [key, value] : contents) Check(writer.Put(key, value), "write external SST key");
    Check(writer.Finish(), "finish external SST");
    const r::InternalKey smallest(r::Slice(contents.begin()->first), 0, r::kTypeValue);
    const r::InternalKey largest(r::Slice(contents.rbegin()->first), 0, r::kTypeValue);
    edit.AddFile(args.scenario == "l0" ? 0 : (newer ? 1 : 2), number, 0,
                 std::filesystem::file_size(path), smallest, largest, 0, 0, false,
                 r::Temperature::kUnknown, r::kInvalidBlobFileNumber, 0, 0,
                 newer ? new_epoch : old_epoch, "", "", r::UniqueId64x2{}, 0, 0, true);
  }
  Check(impl->InstallVirtualCompactionMaterialization(&edit), "install materialization");
  {
    r::InstrumentedMutexLock lock(impl->mutex());
    impl->ReleaseVirtualCompactionMaterializationOutputs(pending);
  }
  Require(versions->LastSequence() > 0 &&
              versions->LastSequence() == versions->LastPublishedSequence() &&
              versions->LastSequence() == versions->LastAllocatedSequence(),
          "all three runtime sequence counters must advance together");
  VerifyFileMetadata(db.get(), args.scenario);
  Verify(db.get(), Expected(), nullptr, "producer_after_install");
  Verify(db.get(), {}, before, "producer_preinstall_snapshot");
  retained_iterator->SeekToFirst();
  Require(!retained_iterator->Valid(), "retained preinstall iterator saw future data");
  Check(retained_iterator->status(), "retained iterator status");
  retained_iterator.reset();
  db->ReleaseSnapshot(before);
  Check(db->ContinueBackgroundWork(), "resume background work");
  const auto latest = db->GetLatestSequenceNumber();
  db.reset();
  std::cout << "{\"status\":\"ok\",\"role\":\"producer\",\"case\":\"" << args.scenario
            << "\",\"fixture_visible_keys\":5,\"fixture_physical_entries\":6,\"latest_sequence\":"
            << latest << "}\n";
}
#else
void Read(const Args& args) {
  Require(std::filesystem::is_directory(args.db), "reader requires existing fixture DB");
  const auto options = Options();
  std::unique_ptr<r::DB> db;
  if (args.check_only) Check(r::DB::OpenForReadOnly(options, args.db.string(), &db), "clean readonly open");
  else Check(r::DB::Open(options, args.db.string(), &db), "clean open");
  VerifyFileMetadata(db.get(), args.scenario);
  const Map original = Expected();
  Verify(db.get(), original, nullptr, "clean_reopen_initial");
  if (!args.check_only) {
    const auto initial_sequence = db->GetLatestSequenceNumber();
    db.reset();
    Check(r::DB::Open(options, args.db.string(), &db), "second clean reopen");
    Require(db->GetLatestSequenceNumber() == initial_sequence, "sequence lost across reopen");
    Verify(db.get(), original, nullptr, "clean_reopen_again");
    const r::Snapshot* snapshot = db->GetSnapshot();
    Require(snapshot != nullptr, "snapshot missing");
    Map updated = original;
    updated[Key(4)] = "post4";
    updated[Key(10)] = "post10";
    Check(db->Put(r::WriteOptions(), Key(4), "post4"), "post-load overwrite");
    Require(db->GetLatestSequenceNumber() > initial_sequence, "post-Put did not exceed materialized sequence");
    Check(db->Put(r::WriteOptions(), Key(10), "post10"), "post-load insert");
    Verify(db.get(), updated, nullptr, "after_post_put");
    Verify(db.get(), original, snapshot, "snapshot_after_post_put");
    r::FlushOptions flush;
    flush.wait = true;
    Check(db->Flush(flush), "flush with snapshot");
    r::CompactRangeOptions compact;
    compact.exclusive_manual_compaction = true;
    Check(db->CompactRange(compact, nullptr, nullptr), "compact with snapshot");
    Verify(db.get(), updated, nullptr, "after_compaction");
    Verify(db.get(), original, snapshot, "snapshot_after_compaction");
    db->ReleaseSnapshot(snapshot);
    db.reset();
    Check(r::DB::Open(options, args.db.string(), &db), "post-compaction reopen");
    Verify(db.get(), updated, nullptr, "post_compaction_reopen");
  }
  db.reset();
  std::cout << "{\"status\":\"ok\",\"role\":\"clean_reader\",\"case\":\"" << args.scenario
            << "\",\"check_only\":" << (args.check_only ? "true" : "false") << "}\n";
}
#endif
}  // namespace

int main(int argc, char** argv) {
  try {
    const Args args = Parse(argc, argv);
#ifdef VCOMP_SEQUENCE_PRODUCER
    Produce(args);
#else
    Read(args);
#endif
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "FAIL: " << error.what() << '\n';
    return 1;
  }
}
