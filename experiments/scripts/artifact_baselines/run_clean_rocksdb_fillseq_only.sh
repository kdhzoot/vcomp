#!/usr/bin/env bash
# Build, settle, validate, and preserve a sequentially populated clean-RocksDB
# database. This is phase 1 of the validated fillseq+overwrite runner without
# the overwrite phase.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

CLEAN_DB_BENCH="${CLEAN_DB_BENCH:-${BASELINE_DB_BENCH}}"
CLEAN_ROOT="${CLEAN_ROOT:-${REPO_ROOT}/../rocksdb}"
EXPECTED_BINARY_SHA256="${EXPECTED_BINARY_SHA256:-289f4783ed194761c98e740b9def72a68950c2d31bd172804f10fbd30e314259}"
TARGET_GIB="${TARGET_GIB:-1000}"
KEY_SIZE="${KEY_SIZE:-24}"
VALUE_SIZE="${VALUE_SIZE:-1000}"
NUM_KEYS="${NUM_KEYS:-}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
RUN_ROOT="${RUN_ROOT:-${ARTIFACT_ROOT}/log_loads/paper_clean_rocksdb_fillseq_only_${TARGET_GIB}gib_${RUN_ID}}"
DB_DIR="${DB_DIR:-${VCOMP_DB_ROOT%/}/paper_clean_rocksdb_fillseq_only_${TARGET_GIB}gib_${RUN_ID}}"

BG_JOBS="${BG_JOBS:-48}"
SUBCOMPACTIONS="${SUBCOMPACTIONS:-1}"
WRITE_BUFFER_SIZE="${WRITE_BUFFER_SIZE:-67108864}"
MAX_WRITE_BUFFER_NUMBER="${MAX_WRITE_BUFFER_NUMBER:-2}"
MIN_WRITE_BUFFER_NUMBER_TO_MERGE="${MIN_WRITE_BUFFER_NUMBER_TO_MERGE:-1}"
REPORT_INTERVAL_SECONDS="${REPORT_INTERVAL_SECONDS:-10}"
READS="${READS:-10000}"
DRY_RUN="${DRY_RUN:-1}"
CLEAN_RELEASE_BUILD_CONFIRMED="${CLEAN_RELEASE_BUILD_CONFIRMED:-0}"

BENCHMARKS="fillseq,flush,compact0,waitforcompaction,stats,levelstats"
MEMTABLE_REP="vector"
BATCH_SIZE=1
THREADS=1

require_executable "${CLEAN_DB_BENCH}" "clean RocksDB db_bench"
require_positive_uint TARGET_GIB
require_positive_uint KEY_SIZE
require_positive_uint VALUE_SIZE
require_positive_uint BG_JOBS
require_positive_uint SUBCOMPACTIONS
require_positive_uint WRITE_BUFFER_SIZE
require_positive_uint MAX_WRITE_BUFFER_NUMBER
require_positive_uint MIN_WRITE_BUFFER_NUMBER_TO_MERGE
require_positive_uint REPORT_INTERVAL_SECONDS
require_positive_uint READS
[[ "${DRY_RUN}" == "0" || "${DRY_RUN}" == "1" ]] || \
  die "DRY_RUN must be 0 or 1: ${DRY_RUN}"
[[ "${CLEAN_RELEASE_BUILD_CONFIRMED}" == "0" || \
   "${CLEAN_RELEASE_BUILD_CONFIRMED}" == "1" ]] || \
  die "CLEAN_RELEASE_BUILD_CONFIRMED must be 0 or 1"

KV_SIZE=$((KEY_SIZE + VALUE_SIZE))
if [[ -z "${NUM_KEYS}" ]]; then
  NUM_KEYS=$((TARGET_GIB * 1024 * 1024 * 1024 / KV_SIZE))
fi
require_positive_uint NUM_KEYS
LOGICAL_BYTES=$((NUM_KEYS * KV_SIZE))

check_db_bench_interface() {
  local help_text flag benchmark
  help_text="$("${CLEAN_DB_BENCH}" --help 2>&1 || true)"
  for flag in statistics stats_interval_seconds stats_per_interval \
      report_interval_seconds report_file enable_index_compression bloom_bits \
      disable_wal max_background_jobs subcompactions write_buffer_size num \
      max_write_buffer_number min_write_buffer_number_to_merge \
      writes reads key_size value_size batch_size threads memtablerep \
      allow_concurrent_memtable_write seed db use_existing_db use_direct_reads \
      use_direct_io_for_flush_and_compaction compression_type benchmarks; do
    [[ "${help_text}" == *"-${flag}"* ]] || \
      die "clean db_bench does not expose --${flag}: ${CLEAN_DB_BENCH}"
  done
  for benchmark in fillseq flush compact0 waitforcompaction readrandom stats \
      levelstats; do
    [[ "${help_text}" == *"${benchmark}"* ]] || \
      die "clean db_bench does not expose benchmark '${benchmark}'"
  done
}

print_command() {
  printf '%q ' "$@"
  printf '\n'
}

write_command_file() {
  local output="$1"
  shift
  { printf '#!/usr/bin/env bash\n'; print_command "$@"; } > "${output}"
  chmod a-w "${output}"
}

benchmark_seconds() {
  awk '$1 == "fillseq" && $2 == ":" {for (i=1;i<=NF;i++) if ($i=="seconds") v=$(i-1)} END{print v}' "$1"
}

benchmark_operations() {
  awk '$1 == "fillseq" && $2 == ":" {for (i=1;i<=NF;i++) if ($i=="operations;") v=$(i-1)} END{print v}' "$1"
}

final_wait_finished() {
  local final_state
  final_state="$(awk '/^waitforcompaction\(.*\): (active|finished)/ {v=$0} END{print v}' "$1")"
  [[ "${final_state}" == *": finished"* ]]
}

declare -a LOAD_CMD REOPEN_CMD
LOAD_CMD=(
  "${CLEAN_DB_BENCH}"
  --statistics=1
  --stats_interval_seconds=60
  --stats_per_interval=1
  --report_interval_seconds="${REPORT_INTERVAL_SECONDS}"
  --report_file="${RUN_ROOT}/fillseq.rep"
  --enable_index_compression=false
  --bloom_bits=10
  --disable_wal=true
  --max_background_jobs="${BG_JOBS}"
  --subcompactions="${SUBCOMPACTIONS}"
  --write_buffer_size="${WRITE_BUFFER_SIZE}"
  --max_write_buffer_number="${MAX_WRITE_BUFFER_NUMBER}"
  --min_write_buffer_number_to_merge="${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}"
  --num="${NUM_KEYS}"
  --writes="${NUM_KEYS}"
  --key_size="${KEY_SIZE}"
  --value_size="${VALUE_SIZE}"
  --batch_size="${BATCH_SIZE}"
  --threads="${THREADS}"
  --memtablerep="${MEMTABLE_REP}"
  --allow_concurrent_memtable_write=false
  --seed=12345678
  --db="${DB_DIR}"
  --use_direct_reads=true
  --use_direct_io_for_flush_and_compaction=true
  --compression_type=none
  --benchmarks="${BENCHMARKS}"
)

REOPEN_CMD=(
  "${CLEAN_DB_BENCH}"
  --use_existing_db=true
  --write_buffer_size="${WRITE_BUFFER_SIZE}"
  --max_write_buffer_number="${MAX_WRITE_BUFFER_NUMBER}"
  --min_write_buffer_number_to_merge="${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}"
  --num="${NUM_KEYS}"
  --reads="${READS}"
  --key_size="${KEY_SIZE}"
  --value_size="${VALUE_SIZE}"
  --threads=1
  --memtablerep="${MEMTABLE_REP}"
  --allow_concurrent_memtable_write=false
  --seed=12345678
  --db="${DB_DIR}"
  --use_direct_reads=true
  --use_direct_io_for_flush_and_compaction=true
  --compression_type=none
  --benchmarks=waitforcompaction,readrandom,stats,levelstats
)

check_db_bench_interface

printf 'Clean db_bench:      %s\n' "${CLEAN_DB_BENCH}"
printf 'Target:              %s GiB (%s logical bytes)\n' \
  "${TARGET_GIB}" "${LOGICAL_BYTES}"
printf 'Records:             %s\n' "${NUM_KEYS}"
printf 'KV:                  %s B key + %s B value\n' \
  "${KEY_SIZE}" "${VALUE_SIZE}"
printf 'Engine:              jobs=%s subcompactions=%s memtable=%s\n' \
  "${BG_JOBS}" "${SUBCOMPACTIONS}" "${MEMTABLE_REP}"
printf 'Write buffers:       %s x %s bytes; min_merge=%s\n' \
  "${MAX_WRITE_BUFFER_NUMBER}" "${WRITE_BUFFER_SIZE}" \
  "${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}"
printf 'I/O:                 direct reads and direct flush/compaction\n'

if [[ "${DRY_RUN}" == "1" ]]; then
  printf '\nDRY_RUN=1; no DB or log directories will be created.\n'
  printf '[load]\n'
  print_command "${LOAD_CMD[@]}"
  printf '[reopen validation]\n'
  print_command "${REOPEN_CMD[@]}"
  exit 0
fi

[[ "${CLEAN_RELEASE_BUILD_CONFIRMED}" == "1" ]] || die \
  "Refusing a run with an unconfirmed clean RocksDB release build"
actual_binary_sha="$(sha256sum "${CLEAN_DB_BENCH}" | awk '{print $1}')"
[[ "${actual_binary_sha}" == "${EXPECTED_BINARY_SHA256}" ]] || \
  die "clean db_bench SHA-256 mismatch: ${actual_binary_sha}"
if pgrep -x db_bench >/dev/null 2>&1 || \
    pgrep -x titandb_bench >/dev/null 2>&1 || \
    pgrep -x ycsbc >/dev/null 2>&1; then
  die "Another DB benchmark is running"
fi
[[ ! -e "${RUN_ROOT}" ]] || die "Log output already exists: ${RUN_ROOT}"
[[ ! -e "${DB_DIR}" ]] || die "DB output already exists: ${DB_DIR}"
mkdir -p "${RUN_ROOT}/raw" "${RUN_ROOT}/runner_snapshot" "${DB_DIR}"

cp -- "$0" "${RUN_ROOT}/runner_snapshot/run_clean_rocksdb_fillseq_only.sh"
chmod a-w "${RUN_ROOT}/runner_snapshot/run_clean_rocksdb_fillseq_only.sh"
sha256sum "${CLEAN_DB_BENCH}" \
  "${RUN_ROOT}/runner_snapshot/run_clean_rocksdb_fillseq_only.sh" \
  > "${RUN_ROOT}/SHA256SUMS"
git -C "${CLEAN_ROOT}" rev-parse HEAD > "${RUN_ROOT}/clean_commit.txt"
git -C "${CLEAN_ROOT}" status --short > "${RUN_ROOT}/clean_git_status.txt"
git -C "${CLEAN_ROOT}" diff > "${RUN_ROOT}/clean.patch"
{
  date -u
  uname -a
  "${CLEAN_DB_BENCH}" --version 2>&1 | head -1
  free -h
  swapon --show
  df -h "${DB_DIR}"
  cat /proc/mdstat
} > "${RUN_ROOT}/environment.txt"
write_command_file "${RUN_ROOT}/raw/load_cmd.sh" "${LOAD_CMD[@]}"
write_command_file "${RUN_ROOT}/raw/reopen_cmd.sh" "${REOPEN_CMD[@]}"

printf 'system\tstatus\ttarget_gib\tnum_keys\telapsed_sec\tfillseq_benchmark_sec\tpeak_rss_kb\tfinal_db_bytes\tvalidation_elapsed_sec\tread_found\tread_requested\tlog_dir\tdb_dir\n' \
  > "${RUN_ROOT}/summary.tsv"

IOSTAT_PID=""
cleanup_iostat() {
  stop_process "${IOSTAT_PID:-}"
  IOSTAT_PID=""
}
trap cleanup_iostat EXIT
trap 'cleanup_iostat; exit 130' INT
trap 'cleanup_iostat; exit 143' TERM

drop_page_cache
cat /proc/diskstats > "${RUN_ROOT}/raw/diskstats.start"
cat /proc/stat > "${RUN_ROOT}/raw/procstat.start"
cat /proc/vmstat > "${RUN_ROOT}/raw/vmstat.start"
if command -v iostat >/dev/null 2>&1; then
  iostat -dx 1 > "${RUN_ROOT}/raw/iostat.log" &
  IOSTAT_PID=$!
fi

start_ts="$(date +%s)"
printf '%s\n' "${start_ts}" > "${RUN_ROOT}/raw/start_epoch.txt"
printf '[clean_rocksdb_fillseq_only] command: '
print_command "${LOAD_CMD[@]}"
set +e
/usr/bin/time -v -o "${RUN_ROOT}/raw/time.out" \
  "${LOAD_CMD[@]}" > "${RUN_ROOT}/fillseq.out" 2>&1
load_rc=$?
set -e
end_ts="$(date +%s)"
cleanup_iostat
elapsed_sec=$((end_ts - start_ts))
printf '%s\n' "${end_ts}" > "${RUN_ROOT}/raw/end_epoch.txt"
printf '%s\n' "${elapsed_sec}" > "${RUN_ROOT}/raw/elapsed_sec.txt"
printf '%s\n' "${load_rc}" > "${RUN_ROOT}/raw/load_exit_code.txt"
cat /proc/diskstats > "${RUN_ROOT}/raw/diskstats.end"
cat /proc/stat > "${RUN_ROOT}/raw/procstat.end"
cat /proc/vmstat > "${RUN_ROOT}/raw/vmstat.end"
[[ "${load_rc}" -eq 0 ]] || die "fillseq load failed: ${load_rc}"

if rg -n 'Corruption:|Segmentation fault|Assertion .*failed|FATAL|Out of memory' \
    "${RUN_ROOT}/fillseq.out" > "${RUN_ROOT}/validation_errors.txt"; then
  die "fillseq load emitted a fatal validation pattern"
fi
final_wait_finished "${RUN_ROOT}/fillseq.out" || \
  die "fillseq load did not reach a settled boundary"
options_file="$(find "${DB_DIR}" -maxdepth 1 -type f -name 'OPTIONS-*' \
  -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
[[ -n "${options_file}" ]] || die "fillseq OPTIONS file is missing"
for expected_option in \
    "write_buffer_size=${WRITE_BUFFER_SIZE}" \
    "max_write_buffer_number=${MAX_WRITE_BUFFER_NUMBER}" \
    "min_write_buffer_number_to_merge=${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}" \
    'allow_concurrent_memtable_write=false'; do
  rg -q "${expected_option}" "${options_file}" || \
    die "effective fillseq option missing: ${expected_option}"
done
fillseq_ops="$(benchmark_operations "${RUN_ROOT}/fillseq.out")"
[[ "${fillseq_ops}" == "${NUM_KEYS}" ]] || \
  die "fillseq operation mismatch: ${fillseq_ops} != ${NUM_KEYS}"
fillseq_sec="$(benchmark_seconds "${RUN_ROOT}/fillseq.out")"

drop_page_cache
validation_start="$(date +%s)"
set +e
"${REOPEN_CMD[@]}" > "${RUN_ROOT}/reopen.out" 2>&1
reopen_rc=$?
set -e
validation_end="$(date +%s)"
validation_elapsed=$((validation_end - validation_start))
printf '%s\n' "${reopen_rc}" > "${RUN_ROOT}/raw/reopen_exit_code.txt"
printf '%s\n' "${validation_elapsed}" > "${RUN_ROOT}/raw/validation_elapsed_sec.txt"
[[ "${reopen_rc}" -eq 0 ]] || die "reopen validation failed: ${reopen_rc}"
final_wait_finished "${RUN_ROOT}/reopen.out" || \
  die "reopen validation did not settle"
read_found="$(awk '/readrandom.*found/ {for(i=1;i<=NF;i++) if($i=="of") {gsub(/\(/,"",$(i-1)); v=$(i-1)}} END{print v}' "${RUN_ROOT}/reopen.out")"
[[ "${read_found}" == "${READS}" ]] || \
  die "reopen validation found ${read_found}/${READS} keys"

peak_rss_kb="$(extract_peak_rss_kb "${RUN_ROOT}/raw/time.out")"
final_db_bytes="$(du -sb "${DB_DIR}" | awk '{print $1}')"
summary_fields=(
  clean_rocksdb_fillseq_only ok "${TARGET_GIB}" "${NUM_KEYS}"
  "${elapsed_sec}" "${fillseq_sec}" "${peak_rss_kb}" "${final_db_bytes}"
  "${validation_elapsed}" "${read_found}" "${READS}" "${RUN_ROOT}" "${DB_DIR}"
)
(IFS=$'\t'; printf '%s\n' "${summary_fields[*]}") >> "${RUN_ROOT}/summary.tsv"
printf 'completed_utc=%s\n' "$(date -u '+%FT%TZ')" > "${RUN_ROOT}/COMPLETED"
printf '[clean_rocksdb_fillseq_only] completed: elapsed=%ss fillseq=%ss read=%s/%s DB=%s\n' \
  "${elapsed_sec}" "${fillseq_sec}" "${read_found}" "${READS}" "${DB_DIR}"
