#!/usr/bin/env bash
# Run RocksDB Integrated BlobDB with garbage collection enabled. The load
# path otherwise matches the clean vector-memtable baseline.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

CLEAN_ROOT="${CLEAN_ROOT:-${REPO_ROOT}/../rocksdb-f455-release}"
CLEAN_DB_BENCH="${CLEAN_DB_BENCH:-${CLEAN_ROOT}/db_bench}"
EXPECTED_COMMIT="${EXPECTED_COMMIT:-f455ab7bd6a8c67f00d48075bb310f131d9fae5f}"
EXPECTED_BINARY_SHA256="${EXPECTED_BINARY_SHA256:-8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b}"
TARGET_GIB="${TARGET_GIB:-1000}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
RUN_ROOT="${RUN_ROOT:-${ARTIFACT_ROOT}/log_loads/paper_rocksdb_blobdb_gc_${TARGET_GIB}gib_${RUN_ID}}"
DB_DIR="${DB_DIR:-${VCOMP_DB_ROOT%/}/paper_rocksdb_blobdb_gc_${TARGET_GIB}gib_${RUN_ID}}"
READS="${READS:-10000}"
WRITE_BUFFER_SIZE="${WRITE_BUFFER_SIZE:-67108864}"
MAX_WRITE_BUFFER_NUMBER="${MAX_WRITE_BUFFER_NUMBER:-2}"
MIN_WRITE_BUFFER_NUMBER_TO_MERGE="${MIN_WRITE_BUFFER_NUMBER_TO_MERGE:-1}"
ALLOW_CONCURRENT_MEMTABLE_WRITE="${ALLOW_CONCURRENT_MEMTABLE_WRITE:-true}"
USE_DIRECT_READS="${USE_DIRECT_READS:-true}"
USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION="${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION:-true}"
MIN_BLOB_SIZE="${MIN_BLOB_SIZE:-128}"
BLOB_FILE_SIZE="${BLOB_FILE_SIZE:-268435456}"
BLOB_COMPRESSION_TYPE="${BLOB_COMPRESSION_TYPE:-none}"
ENABLE_BLOB_GARBAGE_COLLECTION="${ENABLE_BLOB_GARBAGE_COLLECTION:-true}"
BLOB_GARBAGE_COLLECTION_AGE_CUTOFF="${BLOB_GARBAGE_COLLECTION_AGE_CUTOFF:-0.25}"
BLOB_GARBAGE_COLLECTION_FORCE_THRESHOLD="${BLOB_GARBAGE_COLLECTION_FORCE_THRESHOLD:-1.0}"
BLOB_COMPACTION_READAHEAD_SIZE="${BLOB_COMPACTION_READAHEAD_SIZE:-0}"
BLOB_FILE_STARTING_LEVEL="${BLOB_FILE_STARTING_LEVEL:-0}"
DRY_RUN="${DRY_RUN:-1}"

require_executable "${CLEAN_DB_BENCH}" "clean RocksDB db_bench"
require_dir "${CLEAN_ROOT}" "clean RocksDB source"
require_positive_uint TARGET_GIB
require_positive_uint READS
require_positive_uint WRITE_BUFFER_SIZE
require_positive_uint MAX_WRITE_BUFFER_NUMBER
require_positive_uint MIN_WRITE_BUFFER_NUMBER_TO_MERGE
[[ "${ALLOW_CONCURRENT_MEMTABLE_WRITE}" == "true" || \
   "${ALLOW_CONCURRENT_MEMTABLE_WRITE}" == "false" ]] || \
  die "ALLOW_CONCURRENT_MEMTABLE_WRITE must be true or false"
require_uint MIN_BLOB_SIZE
require_positive_uint BLOB_FILE_SIZE
require_uint BLOB_COMPACTION_READAHEAD_SIZE
require_uint BLOB_FILE_STARTING_LEVEL
[[ "${DRY_RUN}" == "0" || "${DRY_RUN}" == "1" ]] || die "DRY_RUN must be 0 or 1"
for io_flag in USE_DIRECT_READS USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION; do
  [[ "${!io_flag}" == "true" || "${!io_flag}" == "false" ]] || \
    die "${io_flag} must be true or false: ${!io_flag}"
done
[[ "${ENABLE_BLOB_GARBAGE_COLLECTION}" == "true" || \
   "${ENABLE_BLOB_GARBAGE_COLLECTION}" == "false" ]] || \
  die "ENABLE_BLOB_GARBAGE_COLLECTION must be true or false"

actual_commit="$(git -C "${CLEAN_ROOT}" rev-parse HEAD)"
[[ "${actual_commit}" == "${EXPECTED_COMMIT}" ]] || \
  die "clean RocksDB commit mismatch: ${actual_commit}"
actual_sha="$(sha256sum "${CLEAN_DB_BENCH}" | awk '{print $1}')"
[[ "${actual_sha}" == "${EXPECTED_BINARY_SHA256}" ]] || \
  die "clean db_bench SHA-256 mismatch: ${actual_sha}"

NUM_KEYS=$((TARGET_GIB * 1024 * 1024 * 1024 / 1024))
declare -a BLOB_ARGS REOPEN_CMD
BLOB_ARGS=(
  --enable_blob_files=true
  --min_blob_size="${MIN_BLOB_SIZE}"
  --blob_file_size="${BLOB_FILE_SIZE}"
  --blob_compression_type="${BLOB_COMPRESSION_TYPE}"
  --enable_blob_garbage_collection="${ENABLE_BLOB_GARBAGE_COLLECTION}"
  --blob_garbage_collection_age_cutoff="${BLOB_GARBAGE_COLLECTION_AGE_CUTOFF}"
  --blob_garbage_collection_force_threshold="${BLOB_GARBAGE_COLLECTION_FORCE_THRESHOLD}"
  --blob_compaction_readahead_size="${BLOB_COMPACTION_READAHEAD_SIZE}"
  --blob_file_starting_level="${BLOB_FILE_STARTING_LEVEL}"
)
REOPEN_CMD=(
  "${CLEAN_DB_BENCH}"
  --use_existing_db=true
  --statistics=1
  --max_background_jobs=48
  --write_buffer_size="${WRITE_BUFFER_SIZE}"
  --max_write_buffer_number="${MAX_WRITE_BUFFER_NUMBER}"
  --min_write_buffer_number_to_merge="${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}"
  --num="${NUM_KEYS}"
  --reads="${READS}"
  --key_size=24
  --value_size=1000
  --threads=1
  --memtablerep=vector
  --allow_concurrent_memtable_write="${ALLOW_CONCURRENT_MEMTABLE_WRITE}"
  --seed=12345678
  --db="${DB_DIR}"
  --use_direct_reads="${USE_DIRECT_READS}"
  --use_direct_io_for_flush_and_compaction="${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}"
  --compression_type=none
  "${BLOB_ARGS[@]}"
  --benchmarks=waitforcompaction,readrandom,stats,levelstats
)

print_command() {
  printf '%q ' "$@"
  printf '\n'
}

printf 'Clean db_bench: %s\n' "${CLEAN_DB_BENCH}"
printf 'Commit:         %s\n' "${actual_commit}"
printf 'SHA-256:        %s\n' "${actual_sha}"
printf 'Target:         %s GiB, %s records, vector memtable\n' "${TARGET_GIB}" "${NUM_KEYS}"
printf 'I/O policy:     direct_reads=%s, direct_flush_compaction=%s\n' \
  "${USE_DIRECT_READS}" "${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}"
printf 'BlobDB:         min_blob=%s, file_size=%s, GC=%s, age_cutoff=%s, force_threshold=%s, readahead=%s\n' \
  "${MIN_BLOB_SIZE}" "${BLOB_FILE_SIZE}" "${ENABLE_BLOB_GARBAGE_COLLECTION}" "${BLOB_GARBAGE_COLLECTION_AGE_CUTOFF}" \
  "${BLOB_GARBAGE_COLLECTION_FORCE_THRESHOLD}" "${BLOB_COMPACTION_READAHEAD_SIZE}"
printf 'Write buffers:  %s x %s bytes; min_merge=%s, concurrent=%s\n' \
  "${MAX_WRITE_BUFFER_NUMBER}" "${WRITE_BUFFER_SIZE}" \
  "${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}" "${ALLOW_CONCURRENT_MEMTABLE_WRITE}"

if [[ "${DRY_RUN}" == "1" ]]; then
  printf '[load harness]\n'
  printf 'MODE=baseline TARGET_DB_GB=%q DB_BENCH=%q DB_DIR=%q LOG_DIR=%q BG_JOBS=48 SUBCOMPACTIONS=1 MEMTABLE_REP=vector WRITE_BUFFER_SIZE=%q MAX_WRITE_BUFFER_NUMBER=%q MIN_WRITE_BUFFER_NUMBER_TO_MERGE=%q ALLOW_CONCURRENT_MEMTABLE_WRITE=%q USE_DIRECT_READS=%q USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION=%q ENABLE_BLOB_FILES=true MIN_BLOB_SIZE=%q BLOB_FILE_SIZE=%q BLOB_COMPRESSION_TYPE=%q ENABLE_BLOB_GARBAGE_COLLECTION=%q BLOB_GARBAGE_COLLECTION_AGE_CUTOFF=%q BLOB_GARBAGE_COLLECTION_FORCE_THRESHOLD=%q BLOB_COMPACTION_READAHEAD_SIZE=%q BLOB_FILE_STARTING_LEVEL=%q bash %q\n' \
    "${TARGET_GIB}" "${CLEAN_DB_BENCH}" "${DB_DIR}" "${RUN_ROOT}" \
    "${WRITE_BUFFER_SIZE}" "${MAX_WRITE_BUFFER_NUMBER}" \
    "${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}" "${ALLOW_CONCURRENT_MEMTABLE_WRITE}" \
    "${USE_DIRECT_READS}" "${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}" \
    "${MIN_BLOB_SIZE}" "${BLOB_FILE_SIZE}" "${BLOB_COMPRESSION_TYPE}" "${ENABLE_BLOB_GARBAGE_COLLECTION}" \
    "${BLOB_GARBAGE_COLLECTION_AGE_CUTOFF}" "${BLOB_GARBAGE_COLLECTION_FORCE_THRESHOLD}" \
    "${BLOB_COMPACTION_READAHEAD_SIZE}" "${BLOB_FILE_STARTING_LEVEL}" \
    "${REPO_ROOT}/experiments/scripts/load/load.sh"
  printf '[reopen validation]\n'
  print_command "${REOPEN_CMD[@]}"
  exit 0
fi

if pgrep -x db_bench >/dev/null 2>&1 || pgrep -x titandb_bench >/dev/null 2>&1; then
  die "Another db_bench/titandb_bench is running"
fi
[[ ! -e "${RUN_ROOT}" ]] || die "Log output already exists: ${RUN_ROOT}"
[[ ! -e "${DB_DIR}" ]] || die "DB output already exists: ${DB_DIR}"

MODE=baseline \
TARGET_DB_GB="${TARGET_GIB}" \
DB_ROOT="$(dirname "${DB_DIR}")" \
DB_DIR="${DB_DIR}" \
LOG_DIR="${RUN_ROOT}" \
DB_BENCH="${CLEAN_DB_BENCH}" \
BG_JOBS=48 \
SUBCOMPACTIONS=1 \
MEMTABLE_REP=vector \
WRITE_BUFFER_SIZE="${WRITE_BUFFER_SIZE}" \
MAX_WRITE_BUFFER_NUMBER="${MAX_WRITE_BUFFER_NUMBER}" \
MIN_WRITE_BUFFER_NUMBER_TO_MERGE="${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}" \
ALLOW_CONCURRENT_MEMTABLE_WRITE="${ALLOW_CONCURRENT_MEMTABLE_WRITE}" \
COMPRESSION_TYPE=none \
USE_DIRECT_READS="${USE_DIRECT_READS}" \
USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION="${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}" \
ENABLE_BLOB_FILES=true \
MIN_BLOB_SIZE="${MIN_BLOB_SIZE}" \
BLOB_FILE_SIZE="${BLOB_FILE_SIZE}" \
BLOB_COMPRESSION_TYPE="${BLOB_COMPRESSION_TYPE}" \
ENABLE_BLOB_GARBAGE_COLLECTION="${ENABLE_BLOB_GARBAGE_COLLECTION}" \
BLOB_GARBAGE_COLLECTION_AGE_CUTOFF="${BLOB_GARBAGE_COLLECTION_AGE_CUTOFF}" \
BLOB_GARBAGE_COLLECTION_FORCE_THRESHOLD="${BLOB_GARBAGE_COLLECTION_FORCE_THRESHOLD}" \
BLOB_COMPACTION_READAHEAD_SIZE="${BLOB_COMPACTION_READAHEAD_SIZE}" \
BLOB_FILE_STARTING_LEVEL="${BLOB_FILE_STARTING_LEVEL}" \
bash "${REPO_ROOT}/experiments/scripts/load/load.sh"

mkdir -p "${RUN_ROOT}/runner_snapshot"
cp -- "$0" "${RUN_ROOT}/runner_snapshot/run_clean_blobdb_gc_load.sh"
cp -- "${REPO_ROOT}/experiments/scripts/load/load.sh" "${RUN_ROOT}/runner_snapshot/load.sh"
chmod a-w "${RUN_ROOT}/runner_snapshot/"*.sh
sha256sum "${CLEAN_DB_BENCH}" "${RUN_ROOT}/runner_snapshot/"*.sh > "${RUN_ROOT}/SHA256SUMS"
git -C "${CLEAN_ROOT}" rev-parse HEAD > "${RUN_ROOT}/clean_commit.txt"
git -C "${CLEAN_ROOT}" status --short > "${RUN_ROOT}/clean_git_status.txt"
git -C "${CLEAN_ROOT}" diff > "${RUN_ROOT}/clean.patch"
{
  printf '#!/usr/bin/env bash\n'
  print_command "${REOPEN_CMD[@]}"
} > "${RUN_ROOT}/raw/reopen_cmd.sh"
chmod a-w "${RUN_ROOT}/raw/reopen_cmd.sh"

rg -q 'Memtablerep: VectorRepFactory' "${RUN_ROOT}/bench.out" || \
  die "BlobDB run did not activate VectorRepFactory"
if rg -q 'WARNING: (Optimization is disabled|Assertions are enabled)' "${RUN_ROOT}/bench.out"; then
  die "clean baseline binary is not an assertion-free optimized build"
fi
rg -q 'waitforcompaction\(.*\): finished with status \(OK\)' "${RUN_ROOT}/bench.out" || \
  die "BlobDB run did not reach the settled compaction boundary"

options_file="$(find "${DB_DIR}" -maxdepth 1 -type f -name 'OPTIONS-*' -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
[[ -n "${options_file}" ]] || die "BlobDB OPTIONS file is missing"
for expected_option in \
  "use_direct_io_for_flush_and_compaction=${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}" \
  "use_direct_reads=${USE_DIRECT_READS}" \
  "write_buffer_size=${WRITE_BUFFER_SIZE}" \
  "max_write_buffer_number=${MAX_WRITE_BUFFER_NUMBER}" \
  "min_write_buffer_number_to_merge=${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}" \
  "allow_concurrent_memtable_write=${ALLOW_CONCURRENT_MEMTABLE_WRITE}" \
  'enable_blob_files=true' \
  "min_blob_size=${MIN_BLOB_SIZE}" \
  "blob_file_size=${BLOB_FILE_SIZE}" \
  "enable_blob_garbage_collection=${ENABLE_BLOB_GARBAGE_COLLECTION}" \
  "blob_garbage_collection_age_cutoff=${BLOB_GARBAGE_COLLECTION_AGE_CUTOFF}" \
  "blob_compaction_readahead_size=${BLOB_COMPACTION_READAHEAD_SIZE}" \
  "blob_file_starting_level=${BLOB_FILE_STARTING_LEVEL}"; do
  rg -q "${expected_option}" "${options_file}" || \
    die "effective BlobDB option missing: ${expected_option}"
done

drop_page_cache
reopen_start="$(date +%s)"
set +e
/usr/bin/time -v -o "${RUN_ROOT}/raw/reopen_time.out" \
  "${REOPEN_CMD[@]}" > "${RUN_ROOT}/reopen.out" 2>&1
reopen_rc=$?
set -e
reopen_end="$(date +%s)"
printf '%s\n' "${reopen_rc}" > "${RUN_ROOT}/raw/reopen_exit_code.txt"
[[ "${reopen_rc}" -eq 0 ]] || die "BlobDB reopen failed: ${reopen_rc}"

read_counts="$(sed -n 's/.*(\([0-9][0-9]*\) of \([0-9][0-9]*\) found).*/\1 \2/p' \
  "${RUN_ROOT}/reopen.out" | tail -1)"
read_found="${read_counts%% *}"
read_requested="${read_counts##* }"
[[ "${read_found}" == "${READS}" && "${read_requested}" == "${READS}" ]] || \
  die "BlobDB reopen validation mismatch: ${read_counts:-missing}"

elapsed_sec="$(<"${RUN_ROOT}/raw/elapsed_sec.txt")"
peak_rss_kb="$(<"${RUN_ROOT}/raw/peak_rss_kb.txt")"
final_db_bytes="$(du -sb "${DB_DIR}" | awk '{print $1}')"
blob_file_count="$(find "${DB_DIR}" -maxdepth 1 -type f -name '*.blob' | wc -l)"
blob_file_bytes="$(find "${DB_DIR}" -maxdepth 1 -type f -name '*.blob' -printf '%s\n' | awk '{s += $1} END {print s + 0}')"
stat_count() {
  local ticker="$1"
  sed -n "s/^${ticker} COUNT : \([0-9][0-9]*\).*/\1/p" "${RUN_ROOT}/bench.out" | tail -1
}
gc_keys_relocated="$(stat_count 'rocksdb.blobdb.gc.num.keys.relocated')"
gc_bytes_relocated="$(stat_count 'rocksdb.blobdb.gc.bytes.relocated')"
blob_bytes_written="$(stat_count 'rocksdb.blobdb.blob.file.bytes.written')"
blob_bytes_read="$(stat_count 'rocksdb.blobdb.blob.file.bytes.read')"
gc_keys_relocated="${gc_keys_relocated:-0}"
gc_bytes_relocated="${gc_bytes_relocated:-0}"
blob_bytes_written="${blob_bytes_written:-0}"
blob_bytes_read="${blob_bytes_read:-0}"
if [[ "${ENABLE_BLOB_GARBAGE_COLLECTION}" == "false" ]]; then
  [[ "${gc_keys_relocated}" == "0" && "${gc_bytes_relocated}" == "0" ]] || \
    die "BlobDB GC-off run unexpectedly relocated data: keys=${gc_keys_relocated}, bytes=${gc_bytes_relocated}"
fi
reopen_elapsed_sec=$((reopen_end - reopen_start))
printf 'system\tstatus\ttarget_gib\tnum_keys\telapsed_sec\tpeak_rss_kb\tfinal_db_bytes\twrite_buffer_size\tmax_write_buffer_number\tmin_write_buffer_number_to_merge\tblob_file_count\tblob_file_bytes\tgc_enabled\tgc_keys_relocated\tgc_bytes_relocated\tblob_bytes_written\tblob_bytes_read\treopen_elapsed_sec\tread_found\tread_requested\tuse_direct_reads\tuse_direct_io_for_flush_and_compaction\tmin_blob_size\tblob_file_size\tgc_age_cutoff\tgc_force_threshold\tblob_compaction_readahead_size\tlog_dir\tdb_dir\n' \
  > "${RUN_ROOT}/validated_summary.tsv"
printf 'rocksdb_blobdb\tok\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "${TARGET_GIB}" "${NUM_KEYS}" "${elapsed_sec}" "${peak_rss_kb}" \
  "${final_db_bytes}" "${WRITE_BUFFER_SIZE}" "${MAX_WRITE_BUFFER_NUMBER}" \
  "${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}" "${blob_file_count}" "${blob_file_bytes}" \
  "${ENABLE_BLOB_GARBAGE_COLLECTION}" "${gc_keys_relocated}" "${gc_bytes_relocated}" "${blob_bytes_written}" "${blob_bytes_read}" \
  "${reopen_elapsed_sec}" \
  "${read_found}" "${read_requested}" "${USE_DIRECT_READS}" \
  "${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}" "${MIN_BLOB_SIZE}" "${BLOB_FILE_SIZE}" \
  "${BLOB_GARBAGE_COLLECTION_AGE_CUTOFF}" "${BLOB_GARBAGE_COLLECTION_FORCE_THRESHOLD}" \
  "${BLOB_COMPACTION_READAHEAD_SIZE}" "${RUN_ROOT}" "${DB_DIR}" \
  >> "${RUN_ROOT}/validated_summary.tsv"
touch "${RUN_ROOT}/VALIDATED"

printf 'RocksDB BlobDB completed: GC=%s, elapsed=%ss, reads=%s/%s, blob_files=%s (%s bytes)\n' \
  "${ENABLE_BLOB_GARBAGE_COLLECTION}" "${elapsed_sec}" "${read_found}" "${read_requested}" "${blob_file_count}" "${blob_file_bytes}"
