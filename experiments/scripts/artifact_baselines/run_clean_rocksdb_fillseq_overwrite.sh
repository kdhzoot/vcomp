#!/usr/bin/env bash
# Build a sequentially populated DB with clean RocksDB, then issue random
# overwrite operations equal to 10% of the record count over the same key
# space. db_bench has one process-wide --writes flag, so the phases are two
# invocations against the same DB.

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
OVERWRITE_PERCENT="${OVERWRITE_PERCENT:-10}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
RUN_ROOT="${RUN_ROOT:-${ARTIFACT_ROOT}/log_loads/paper_clean_rocksdb_fillseq_overwrite_${TARGET_GIB}gib_${RUN_ID}}"
DB_DIR="${DB_DIR:-${VCOMP_DB_ROOT%/}/paper_clean_rocksdb_fillseq_overwrite_${TARGET_GIB}gib_${RUN_ID}}"

BG_JOBS="${BG_JOBS:-48}"
SUBCOMPACTIONS="${SUBCOMPACTIONS:-1}"
WRITE_BUFFER_SIZE="${WRITE_BUFFER_SIZE:-67108864}"
MAX_WRITE_BUFFER_NUMBER="${MAX_WRITE_BUFFER_NUMBER:-2}"
MIN_WRITE_BUFFER_NUMBER_TO_MERGE="${MIN_WRITE_BUFFER_NUMBER_TO_MERGE:-1}"
REPORT_INTERVAL_SECONDS="${REPORT_INTERVAL_SECONDS:-10}"
DRY_RUN="${DRY_RUN:-1}"
CLEAN_RELEASE_BUILD_CONFIRMED="${CLEAN_RELEASE_BUILD_CONFIRMED:-0}"

FILLSEQ_BENCHMARKS="fillseq,flush,compact0,waitforcompaction,stats,levelstats"
OVERWRITE_BENCHMARKS="waitforcompaction,overwrite,flush,compact0,waitforcompaction,stats,levelstats"
MEMTABLE_REP="vector"
BATCH_SIZE=1
THREADS=1
READS=10000

require_executable "${CLEAN_DB_BENCH}" "clean RocksDB db_bench"
require_positive_uint TARGET_GIB
require_positive_uint KEY_SIZE
require_positive_uint VALUE_SIZE
require_positive_uint OVERWRITE_PERCENT
require_positive_uint BG_JOBS
require_positive_uint SUBCOMPACTIONS
require_positive_uint WRITE_BUFFER_SIZE
require_positive_uint MAX_WRITE_BUFFER_NUMBER
require_positive_uint MIN_WRITE_BUFFER_NUMBER_TO_MERGE
require_positive_uint REPORT_INTERVAL_SECONDS
[[ "${OVERWRITE_PERCENT}" -le 100 ]] || \
  die "OVERWRITE_PERCENT must be at most 100: ${OVERWRITE_PERCENT}"
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
OVERWRITE_WRITES=$((NUM_KEYS * OVERWRITE_PERCENT / 100))
[[ "${OVERWRITE_WRITES}" -gt 0 ]] || die "Calculated overwrite count is zero"
FILL_LOGICAL_BYTES=$((NUM_KEYS * KV_SIZE))
OVERWRITE_LOGICAL_BYTES=$((OVERWRITE_WRITES * KV_SIZE))

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
  for benchmark in fillseq overwrite flush compact0 waitforcompaction \
      readrandom stats levelstats; do
    [[ "${help_text}" == *"${benchmark}"* ]] || \
      die "clean db_bench does not expose benchmark '${benchmark}'"
  done
}

print_command() {
  printf '%q ' "$@"
  printf '\n'
}

declare -a COMMON_CMD FILLSEQ_CMD OVERWRITE_CMD REOPEN_CMD
build_common_command() {
  local report_file="$1"
  COMMON_CMD=(
    "${CLEAN_DB_BENCH}"
    --statistics=1
    --stats_interval_seconds=60
    --stats_per_interval=1
    --report_interval_seconds="${REPORT_INTERVAL_SECONDS}"
    --report_file="${report_file}"
    --enable_index_compression=false
    --bloom_bits=10
    --disable_wal=true
    --max_background_jobs="${BG_JOBS}"
    --subcompactions="${SUBCOMPACTIONS}"
    --write_buffer_size="${WRITE_BUFFER_SIZE}"
    --max_write_buffer_number="${MAX_WRITE_BUFFER_NUMBER}"
    --min_write_buffer_number_to_merge="${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}"
    --num="${NUM_KEYS}"
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
  )
}

build_fillseq_command() {
  build_common_command "$1"
  FILLSEQ_CMD=(
    "${COMMON_CMD[@]}"
    --writes="${NUM_KEYS}"
    --benchmarks="${FILLSEQ_BENCHMARKS}"
  )
}

build_overwrite_command() {
  build_common_command "$1"
  OVERWRITE_CMD=(
    "${COMMON_CMD[@]}"
    --use_existing_db=true
    --writes="${OVERWRITE_WRITES}"
    --benchmarks="${OVERWRITE_BENCHMARKS}"
  )
}

build_reopen_command() {
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
}

write_command_file() {
  local output="$1"
  shift
  { printf '#!/usr/bin/env bash\n'; print_command "$@"; } > "${output}"
  chmod a-w "${output}"
}

benchmark_seconds() {
  local benchmark="$1"
  local output="$2"
  awk -v benchmark="${benchmark}" \
    '$1 == benchmark && $2 == ":" {for (i=1;i<=NF;i++) if ($i=="seconds") v=$(i-1)} END{print v}' \
    "${output}"
}

benchmark_operations() {
  local benchmark="$1"
  local output="$2"
  awk -v benchmark="${benchmark}" \
    '$1 == benchmark && $2 == ":" {for (i=1;i<=NF;i++) if ($i=="operations;") v=$(i-1)} END{print v}' \
    "${output}"
}

final_wait_finished() {
  local output="$1"
  local final_state
  final_state="$(awk '/^waitforcompaction\(.*\): (active|finished)/ {v=$0} END{print v}' "${output}")"
  [[ "${final_state}" == *": finished"* ]]
}

print_configuration() {
  printf 'Clean db_bench:      %s\n' "${CLEAN_DB_BENCH}"
  printf 'RocksDB version:     '
  "${CLEAN_DB_BENCH}" --version 2>&1 | head -1
  printf 'Target live data:    %s GiB logical\n' "${TARGET_GIB}"
  printf 'Records/key space:   %s\n' "${NUM_KEYS}"
  printf 'Fillseq writes:      %s (%s bytes)\n' "${NUM_KEYS}" "${FILL_LOGICAL_BYTES}"
  printf 'Overwrite writes:    %s (%s%%, %s bytes)\n' \
    "${OVERWRITE_WRITES}" "${OVERWRITE_PERCENT}" "${OVERWRITE_LOGICAL_BYTES}"
  printf 'KV size:             %s B key + %s B value = %s B\n' \
    "${KEY_SIZE}" "${VALUE_SIZE}" "${KV_SIZE}"
  printf 'Engine config:       jobs=%s, subcompactions=%s, memtable=%s\n' \
    "${BG_JOBS}" "${SUBCOMPACTIONS}" "${MEMTABLE_REP}"
  printf 'Write buffers:       %s x %s bytes; min_merge=%s\n' \
    "${MAX_WRITE_BUFFER_NUMBER}" "${WRITE_BUFFER_SIZE}" \
    "${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}"
}

check_db_bench_interface
build_fillseq_command "${RUN_ROOT}/fillseq.rep"
build_overwrite_command "${RUN_ROOT}/overwrite.rep"
build_reopen_command
print_configuration

if [[ "${DRY_RUN}" == "1" ]]; then
  printf '\nDRY_RUN=1; no DB or log directories will be created.\n'
  printf '\n[fillseq]\n'
  print_command "${FILLSEQ_CMD[@]}"
  printf '[overwrite]\n'
  print_command "${OVERWRITE_CMD[@]}"
  printf '[validation]\n'
  print_command "${REOPEN_CMD[@]}"
  exit 0
fi

[[ "${CLEAN_RELEASE_BUILD_CONFIRMED}" == "1" ]] || die \
  "Refusing a run with an unconfirmed clean RocksDB release build"
actual_binary_sha="$(sha256sum "${CLEAN_DB_BENCH}" | awk '{print $1}')"
[[ "${actual_binary_sha}" == "${EXPECTED_BINARY_SHA256}" ]] || \
  die "clean db_bench SHA-256 mismatch: ${actual_binary_sha}"
require_no_db_bench "clean RocksDB fillseq/overwrite run"
[[ ! -e "${RUN_ROOT}" ]] || die "Log output already exists: ${RUN_ROOT}"
[[ ! -e "${DB_DIR}" ]] || die "DB output already exists: ${DB_DIR}"
mkdir -p "${RUN_ROOT}/raw" "${RUN_ROOT}/runner_snapshot" "${DB_DIR}"

cp -- "$0" "${RUN_ROOT}/runner_snapshot/run_clean_rocksdb_fillseq_overwrite.sh"
chmod a-w "${RUN_ROOT}/runner_snapshot/run_clean_rocksdb_fillseq_overwrite.sh"
sha256sum "${CLEAN_DB_BENCH}" \
  "${RUN_ROOT}/runner_snapshot/run_clean_rocksdb_fillseq_overwrite.sh" \
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

write_command_file "${RUN_ROOT}/raw/fillseq_cmd.sh" "${FILLSEQ_CMD[@]}"
write_command_file "${RUN_ROOT}/raw/overwrite_cmd.sh" "${OVERWRITE_CMD[@]}"
write_command_file "${RUN_ROOT}/raw/reopen_cmd.sh" "${REOPEN_CMD[@]}"

printf 'system\tstatus\ttarget_gib\tnum_keys\toverwrite_writes\ttotal_elapsed_sec\tfillseq_wall_sec\tfillseq_benchmark_sec\toverwrite_wall_sec\toverwrite_benchmark_sec\tpeak_rss_kb\tfillseq_wait_state\toverwrite_wait_state\tfinal_db_bytes\tvalidation_elapsed_sec\tread_found\tread_requested\tlog_dir\tdb_dir\n' \
  > "${RUN_ROOT}/summary.tsv"

IOSTAT_PID=""
cleanup_iostat() {
  stop_process "${IOSTAT_PID:-}"
  IOSTAT_PID=""
}
trap cleanup_iostat EXIT
trap 'cleanup_iostat; exit 130' INT
trap 'cleanup_iostat; exit 143' TERM

run_phase() {
  local time_file="$1"
  local output_file="$2"
  shift 2
  set +e
  /usr/bin/time -v -o "${time_file}" "$@" > "${output_file}" 2>&1
  local rc=$?
  set -e
  return "${rc}"
}

drop_page_cache
cat /proc/diskstats > "${RUN_ROOT}/raw/diskstats.start"
cat /proc/stat > "${RUN_ROOT}/raw/procstat.start"
cat /proc/vmstat > "${RUN_ROOT}/raw/vmstat.start"
if command -v iostat >/dev/null 2>&1; then
  iostat -dx 1 > "${RUN_ROOT}/raw/iostat.log" &
  IOSTAT_PID=$!
fi

printf '\n[clean_rocksdb] starting at %s\n' "$(date -u '+%FT%TZ')"
total_start="$(date +%s)"
printf '%s\n' "${total_start}" > "${RUN_ROOT}/raw/start_epoch.txt"

printf '[clean_rocksdb] phase 1 fillseq command: '
print_command "${FILLSEQ_CMD[@]}"
fillseq_start="$(date +%s)"
if run_phase "${RUN_ROOT}/raw/fillseq_time.out" "${RUN_ROOT}/fillseq.out" \
    "${FILLSEQ_CMD[@]}"; then
  :
else
  rc=$?
  printf 'clean_rocksdb\tfailed:fillseq:%s\t%s\t%s\t%s\n' \
    "${rc}" "${TARGET_GIB}" "${NUM_KEYS}" "${OVERWRITE_WRITES}" \
    >> "${RUN_ROOT}/summary.tsv"
  exit "${rc}"
fi
fillseq_end="$(date +%s)"
fillseq_wall=$((fillseq_end - fillseq_start))
printf '%s\n' "${fillseq_wall}" > "${RUN_ROOT}/raw/fillseq_elapsed_sec.txt"
phase1_options="$(find "${DB_DIR}" -maxdepth 1 -type f -name 'OPTIONS-*' \
  -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
[[ -n "${phase1_options}" ]] || die "phase-1 OPTIONS file is missing"
cp -- "${phase1_options}" "${RUN_ROOT}/raw/phase1_OPTIONS.snapshot"

printf '[clean_rocksdb] phase 2 overwrite command: '
print_command "${OVERWRITE_CMD[@]}"
overwrite_start="$(date +%s)"
if run_phase "${RUN_ROOT}/raw/overwrite_time.out" "${RUN_ROOT}/overwrite.out" \
    "${OVERWRITE_CMD[@]}"; then
  :
else
  rc=$?
  printf 'clean_rocksdb\tfailed:overwrite:%s\t%s\t%s\t%s\n' \
    "${rc}" "${TARGET_GIB}" "${NUM_KEYS}" "${OVERWRITE_WRITES}" \
    >> "${RUN_ROOT}/summary.tsv"
  exit "${rc}"
fi
overwrite_end="$(date +%s)"
overwrite_wall=$((overwrite_end - overwrite_start))
printf '%s\n' "${overwrite_wall}" > "${RUN_ROOT}/raw/overwrite_elapsed_sec.txt"

total_end="$(date +%s)"
total_elapsed=$((total_end - total_start))
printf '%s\n' "${total_end}" > "${RUN_ROOT}/raw/end_epoch.txt"
printf '%s\n' "${total_elapsed}" > "${RUN_ROOT}/raw/total_elapsed_sec.txt"
cleanup_iostat
cat /proc/diskstats > "${RUN_ROOT}/raw/diskstats.end"
cat /proc/stat > "${RUN_ROOT}/raw/procstat.end"
cat /proc/vmstat > "${RUN_ROOT}/raw/vmstat.end"

if rg -n 'Corruption:|Segmentation fault|Assertion .*failed|FATAL|Out of memory' \
    "${RUN_ROOT}/fillseq.out" "${RUN_ROOT}/overwrite.out" \
    > "${RUN_ROOT}/validation_errors.txt"; then
  die "clean RocksDB emitted a fatal validation pattern"
fi
final_wait_finished "${RUN_ROOT}/fillseq.out" || \
  die "fillseq phase did not report a settled boundary"
final_wait_finished "${RUN_ROOT}/overwrite.out" || \
  die "overwrite phase did not report a settled boundary"
phase2_options="$(find "${DB_DIR}" -maxdepth 1 -type f -name 'OPTIONS-*' \
  -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
[[ -n "${phase2_options}" ]] || die "phase-2 OPTIONS file is missing"
cp -- "${phase2_options}" "${RUN_ROOT}/raw/phase2_OPTIONS.snapshot"
for options_snapshot in \
    "${RUN_ROOT}/raw/phase1_OPTIONS.snapshot" \
    "${RUN_ROOT}/raw/phase2_OPTIONS.snapshot"; do
  for expected_option in \
      "write_buffer_size=${WRITE_BUFFER_SIZE}" \
      "max_write_buffer_number=${MAX_WRITE_BUFFER_NUMBER}" \
      "min_write_buffer_number_to_merge=${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}" \
      'allow_concurrent_memtable_write=false'; do
    rg -q "${expected_option}" "${options_snapshot}" || \
      die "effective phase option missing: ${expected_option}"
  done
done

fillseq_sec="$(benchmark_seconds fillseq "${RUN_ROOT}/fillseq.out")"
overwrite_sec="$(benchmark_seconds overwrite "${RUN_ROOT}/overwrite.out")"
fillseq_ops="$(benchmark_operations fillseq "${RUN_ROOT}/fillseq.out")"
overwrite_ops="$(benchmark_operations overwrite "${RUN_ROOT}/overwrite.out")"
[[ "${fillseq_ops}" == "${NUM_KEYS}" ]] || \
  die "fillseq operation mismatch: ${fillseq_ops} != ${NUM_KEYS}"
[[ "${overwrite_ops}" == "${OVERWRITE_WRITES}" ]] || \
  die "overwrite operation mismatch: ${overwrite_ops} != ${OVERWRITE_WRITES}"

validation_start="$(date +%s)"
"${REOPEN_CMD[@]}" > "${RUN_ROOT}/reopen.out" 2>&1
validation_end="$(date +%s)"
validation_elapsed=$((validation_end - validation_start))
printf '%s\n' "${validation_elapsed}" > "${RUN_ROOT}/raw/validation_elapsed_sec.txt"
final_wait_finished "${RUN_ROOT}/reopen.out" || \
  die "reopen validation did not settle"
read_found="$(awk '/readrandom.*found/ {for(i=1;i<=NF;i++) if($i=="of") {gsub(/\(/,"",$(i-1)); v=$(i-1)}} END{print v}' "${RUN_ROOT}/reopen.out")"
read_requested="${READS}"
[[ "${read_found}" == "${read_requested}" ]] || \
  die "reopen validation found ${read_found}/${read_requested} keys"

fillseq_rss="$(extract_peak_rss_kb "${RUN_ROOT}/raw/fillseq_time.out")"
overwrite_rss="$(extract_peak_rss_kb "${RUN_ROOT}/raw/overwrite_time.out")"
peak_rss="$(awk -v a="${fillseq_rss:-0}" -v b="${overwrite_rss:-0}" \
  'BEGIN{print (a > b ? a : b)}')"
final_db_bytes="$(du -sb "${DB_DIR}" | awk '{print $1}')"

summary_fields=(
  clean_rocksdb ok "${TARGET_GIB}" "${NUM_KEYS}" "${OVERWRITE_WRITES}"
  "${total_elapsed}" "${fillseq_wall}" "${fillseq_sec}"
  "${overwrite_wall}" "${overwrite_sec}" "${peak_rss}"
  finished finished "${final_db_bytes}" "${validation_elapsed}"
  "${read_found}" "${read_requested}" "${RUN_ROOT}" "${DB_DIR}"
)
(IFS=$'\t'; printf '%s\n' "${summary_fields[*]}") >> "${RUN_ROOT}/summary.tsv"
printf 'completed_utc=%s\n' "$(date -u '+%FT%TZ')" > "${RUN_ROOT}/COMPLETED"
printf '[clean_rocksdb] completed: total=%ss fillseq=%ss overwrite=%ss read=%s/%s\n' \
  "${total_elapsed}" "${fillseq_wall}" "${overwrite_wall}" \
  "${read_found}" "${read_requested}"
