#!/usr/bin/env python3
"""Build isolated sparse fillseq tools against existing frozen baseline objects.

Never invokes make: even make -n can rewrite make_config.mk via detection hooks.
The original db_bench source, objects, library and binary remain untouched.
"""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import time


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise RuntimeError(f'patch anchor occurs {text.count(before)} times: {before[:90]}')
    return text.replace(before, after, 1)


READER = r'''
// Isolated experimental adapter. The engine and Write path remain unchanged.
class FillseqSparseKeys {
 public:
  FillseqSparseKeys(const std::string& path, uint64_t expected, uint64_t domain)
      : file_(nullptr), expected_(expected), domain_(domain) {
    file_ = fopen(path.c_str(), "rb");
    if (file_ == nullptr) Fail("cannot open sorted key file");
    if (fseeko(file_, 0, SEEK_END) != 0) Fail("cannot seek sorted key file");
    const auto bytes = ftello(file_);
    if (bytes < 0 || static_cast<uint64_t>(bytes) / 8 != expected ||
        static_cast<uint64_t>(bytes) % 8 != 0 || expected == 0 || expected > domain)
      Fail("sorted key file length does not match --writes or domain");
    if (fseeko(file_, 0, SEEK_SET) != 0) Fail("cannot rewind sorted key file");
    setvbuf(file_, nullptr, _IOFBF, 1024 * 1024);
  }
  ~FillseqSparseKeys() { if (file_ != nullptr) fclose(file_); }
  uint64_t Next() {
    unsigned char bytes[8];
    if (count_ >= expected_ || fread(bytes, 1, 8, file_) != 8)
      Fail("sorted key file read failed or ended early");
    uint64_t id = 0;
    for (int i = 0; i < 8; ++i) id |= static_cast<uint64_t>(bytes[i]) << (8 * i);
    if (id >= domain_ || (count_ > 0 && id <= previous_))
      Fail("sorted key file must be strictly increasing and within --num");
    previous_ = id;
    ++count_;
    return id;
  }
  void Finish() {
    if (count_ != expected_ || fgetc(file_) != EOF || ferror(file_))
      Fail("sorted key file was not consumed exactly");
  }
 private:
  static void Fail(const char* message) {
    fprintf(stderr, "fillseq_sparse: %s\n", message);
    exit(1);
  }
  FILE* file_;
  uint64_t expected_, domain_, count_ = 0, previous_ = 0;
};

'''

INITIALIZE = r'''
    std::unique_ptr<FillseqSparseKeys> sparse_keys;
    if (!FLAGS_fillseq_sparse_keys.empty()) {
      if (write_mode != SEQUENTIAL || FLAGS_threads != 1 ||
          entries_per_batch_ != 1 || FLAGS_batch_size != 1 ||
          FLAGS_num <= 0 || FLAGS_writes <= 0 || writes_ <= 0 || num_ != FLAGS_num ||
          FLAGS_num_column_families != 1 || db_.db == nullptr ||
          keys_per_prefix_ != 0 || FLAGS_use_existing_keys ||
          key_size_ != 24 || FLAGS_value_size != 1000 ||
          FLAGS_value_size_distribution_type_e != kFixed ||
          use_blob_db_ || user_timestamp_size_ != 0 ||
          max_num_range_tombstones_ != 0 || writes_per_range_tombstone_ != 0 ||
          FLAGS_disposable_entries_batch_size != 0 ||
          FLAGS_persistent_entries_batch_size != 0) {
        fprintf(stderr, "fillseq_sparse requires single-thread batch1 sequential writes, "
                        "explicit --writes, key24/value1000, no prefixes, timestamp, "
                        "range deletions, disposable keys or BlobDB\n");
        ErrorExit();
      }
      sparse_keys.reset(new FillseqSparseKeys(FLAGS_fillseq_sparse_keys,
                                              writes_, FLAGS_num));
    }
'''


def patched_source(original):
    source = replace_once(original, 'DEFINE_int64(seed, 0,',
        'DEFINE_string(fillseq_sparse_keys, "",\n'
        '              "Sorted unique uint64 little-endian key file for fillseq; "\n'
        '              "--num remains key domain and --writes is file record count.");\n\n'
        'DEFINE_int64(seed, 0,')
    anchor = '  void DoWrite(ThreadState* thread, WriteMode write_mode) {\n'
    source = replace_once(source, anchor, READER + anchor + INITIALIZE)
    start = source.index(anchor)
    end = source.index('\n  Status DoDeterministicCompact(', start)
    function = source[start:end]
    # Keep the original random-key cases untouched. Only the common final
    # generator branch is adapted, and validation restricts it to SEQUENTIAL.
    function = replace_once(function,
        '        } else {\n          rand_num = key_gens[id]->Next();\n        }\n        GenerateKeyFromInt',
        '        } else {\n          rand_num = sparse_keys ? sparse_keys->Next() : key_gens[id]->Next();\n        }\n        GenerateKeyFromInt')
    function = replace_once(function, '    thread->stats.AddBytes(bytes);',
        '    if (sparse_keys) sparse_keys->Finish();\n    thread->stats.AddBytes(bytes);')
    return source[:start] + function + source[end:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--baseline-root', type=Path,
                        default=Path('/home/smrc/virtual_compaction/rocksdb-f455-release'))
    args = parser.parse_args()
    baseline = args.baseline_root.resolve()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source_path = baseline / 'tools/db_bench_tool.cc'
    helper = Path(__file__).with_name('fillseq_sparse_keys.cc').resolve()
    config = dict(line.split('=', 1) for line in (baseline / 'make_config.mk').read_text().splitlines()
                  if '=' in line)
    compiler = config.get('CXX', 'g++-11')
    if Path(compiler).name != 'g++-11':
        raise RuntimeError(f'expected frozen compiler g++-11, found {compiler}')
    platform = shlex.split(config['PLATFORM_CXXFLAGS'])
    libraries = shlex.split(config['PLATFORM_LDFLAGS'])
    required = ['-DGFLAGS=1', '-DSNAPPY', '-DZLIB', '-DTBB']
    if any(flag not in platform for flag in required):
        raise RuntimeError('baseline build feature flags differ from frozen configuration')
    original = source_path.read_text()
    modified = patched_source(original)
    (out / 'db_bench_tool.original.cc').write_text(original)
    (out / 'db_bench_tool.cc').write_text(modified)
    (out / 'db_bench_tool.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), modified.splitlines(True),
        fromfile='tools/db_bench_tool.cc', tofile='tools/db_bench_tool.cc')))
    shutil.copy2(helper, out / helper.name)
    shutil.copy2(Path(__file__), out / Path(__file__).name)
    shutil.copy2(baseline / 'make_config.mk', out / 'make_config.mk')
    inputs = [source_path, baseline / 'librocksdb.a', baseline / 'db_bench',
              baseline / 'tools/db_bench.o', baseline / 'tools/tool_hooks.o',
              baseline / 'tools/simulated_hybrid_file_system.o', baseline / 'test_util/testutil.o']
    hashes = {str(path): sha(path) for path in inputs}
    flags = ['-O2', '-g', '-DNDEBUG', '-fno-rtti', '-fno-omit-frame-pointer',
             '-momit-leaf-frame-pointer', '-I' + str(baseline), '-I' + str(baseline / 'include')] + platform
    commands = [
        [compiler] + flags + ['-c', str(out / 'db_bench_tool.cc'), '-o', str(out / 'db_bench_tool.o')],
        [compiler, str(baseline / 'tools/db_bench.o'), str(out / 'db_bench_tool.o'),
         str(baseline / 'tools/tool_hooks.o'), str(baseline / 'tools/simulated_hybrid_file_system.o'),
         str(baseline / 'test_util/testutil.o'), str(baseline / 'librocksdb.a'),
         '-o', str(out / 'db_bench')] + libraries,
        [compiler] + flags + [str(out / helper.name), str(baseline / 'librocksdb.a'),
                             '-o', str(out / 'sparse_keys')] + libraries,
    ]
    manifest = dict(status='building', started_epoch=time.time(), baseline_root=str(baseline),
                    input_sha256=hashes, commands=commands,
                    source_sha256=sha(out / 'db_bench_tool.cc'),
                    helper_source_sha256=sha(out / helper.name),
                    note='Existing release engine/objects; isolated benchmark adapter, no make invoked.')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    try:
        with (out / 'build.log').open('w') as log:
            for command in commands:
                log.write(shlex.join(command) + '\n')
                log.flush()
                subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT, check=True)
        for path, digest in hashes.items():
            if sha(path) != digest:
                raise RuntimeError('baseline build input changed: ' + path)
        manifest.update(status='ok', finished_epoch=time.time(),
                        binary_sha256={name: sha(out / name) for name in ['db_bench', 'sparse_keys']})
    except Exception as error:
        manifest.update(status='failed', error=str(error), finished_epoch=time.time())
        raise
    finally:
        (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
