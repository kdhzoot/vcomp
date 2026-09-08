#!/usr/bin/env bash
# Run the paper loading workload with DiffKV's patched titandb_bench. Common
# RocksDB/db_bench settings match the existing 1 TiB Figure 3 load; only the
# Titan/DiffKV-specific settings come from the author artifact and ATC'21
# paper. The timed boundary includes fillrandom, synchronous flush, compact0,
# LSM settling, and TitanDB destruction (which joins its background workers).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

DIFFKV_ROOT="${DIFFKV_ROOT:-${ARTIFACT_ROOT}/external_baselines/diffkv}"
DIFFKV_DB_BENCH="${DIFFKV_DB_BENCH:-${DIFFKV_ROOT}/build-paper-release/titandb_bench}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
TARGET_GIB="${TARGET_GIB:-1000}"
RUN_ROOT="${RUN_ROOT:-${ARTIFACT_ROOT}/log_loads/paper_diffkv_${TARGET_GIB}gib_${RUN_ID}}"
DB_DIR="${DB_DIR:-${VCOMP_DB_ROOT%/}/paper_diffkv_${TARGET_GIB}gib_${RUN_ID}}"

KEY_SIZE="${KEY_SIZE:-24}"
VALUE_SIZE="${VALUE_SIZE:-1000}"
NUM_KEYS="${NUM_KEYS:-}"
BG_JOBS="${BG_JOBS:-48}"
SUBCOMPACTIONS="${SUBCOMPACTIONS:-1}"
WRITE_BUFFER_SIZE="${WRITE_BUFFER_SIZE:-67108864}"
TARGET_FILE_SIZE_BASE="${TARGET_FILE_SIZE_BASE:-67108864}"
MAX_BYTES_FOR_LEVEL_BASE="${MAX_BYTES_FOR_LEVEL_BASE:-268435456}"
REPORT_INTERVAL_SECONDS="${REPORT_INTERVAL_SECONDS:-10}"
READS="${READS:-10000}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-86400}"
USE_DIRECT_READS="${USE_DIRECT_READS:-true}"
USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION="${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION:-true}"
DRY_RUN="${DRY_RUN:-1}"

# DiffKV defaults from the author YCSB-C adapter/config and Sec. 5 of the
# paper. These remain explicit in the saved command and effective-option dump.
TITAN_MIN_BLOB_SIZE=128
TITAN_MID_BLOB_SIZE=8192
TITAN_BLOB_FILE_TARGET_SIZE=8388608
TITAN_MIN_GC_BATCH_SIZE=33554432
TITAN_MAX_GC_BATCH_SIZE=67108864
TITAN_GC_RATIO=0.3
TITAN_MAX_BACKGROUND_GC=8
TITAN_MAX_SORTED_RUNS=10
TITAN_BLOCK_WRITE_SIZE=0

BENCHMARKS="fillrandom,flush,compact0,waitforcompaction,stats,levelstats"
MEMTABLE_REP="skip_list"
BATCH_SIZE=1
THREADS=1

require_executable "${DIFFKV_DB_BENCH}" "DiffKV titandb_bench"
require_positive_uint TARGET_GIB
require_positive_uint KEY_SIZE
require_positive_uint VALUE_SIZE
require_positive_uint BG_JOBS
require_positive_uint SUBCOMPACTIONS
require_positive_uint WRITE_BUFFER_SIZE
require_positive_uint TARGET_FILE_SIZE_BASE
require_positive_uint MAX_BYTES_FOR_LEVEL_BASE
require_positive_uint REPORT_INTERVAL_SECONDS
require_positive_uint READS
require_positive_uint WAIT_TIMEOUT_SECONDS
[[ "${DRY_RUN}" == "0" || "${DRY_RUN}" == "1" ]] || \
  die "DRY_RUN must be 0 or 1: ${DRY_RUN}"
for io_flag in USE_DIRECT_READS USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION; do
  [[ "${!io_flag}" == "true" || "${!io_flag}" == "false" ]] || \
    die "${io_flag} must be true or false: ${!io_flag}"
done

KV_SIZE=$((KEY_SIZE + VALUE_SIZE))
if [[ -z "${NUM_KEYS}" ]]; then
  NUM_KEYS=$((TARGET_GIB * 1024 * 1024 * 1024 / KV_SIZE))
fi
require_positive_uint NUM_KEYS
LOGICAL_BYTES=$((NUM_KEYS * KV_SIZE))

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

check_interface() {
  local help_text flag
  help_text="$("${DIFFKV_DB_BENCH}" --help 2>&1 || true)"
  for flag in statistics stats_interval_seconds stats_per_interval \
      report_interval_seconds report_file enable_index_compression bloom_bits \
      disable_wal max_background_jobs subcompactions write_buffer_size \
      target_file_size_base max_bytes_for_level_base num reads key_size \
      value_size batch_size threads memtablerep seed db use_existing_db \
      use_direct_reads use_direct_io_for_flush_and_compaction compression_type \
      use_titan titan_min_blob_size titan_mid_blob_size titan_sep_before_flush \
      titan_level_merge titan_range_merge titan_lazy_merge \
      titan_max_sorted_runs titan_blob_file_target_size \
      titan_min_gc_batch_size titan_max_gc_batch_size \
      titan_blob_file_discardable_ratio titan_disable_background_gc \
      titan_max_background_gc titan_block_write_size \
      wait_for_compaction_timeout_sec benchmarks; do
    [[ "${help_text}" == *"-${flag}"* ]] || \
      die "DiffKV titandb_bench does not expose --${flag}"
  done
}

declare -a COMMON_CMD LOAD_CMD REOPEN_CMD
build_common_command() {
  local report_file="$1"
  COMMON_CMD=(
    "${DIFFKV_DB_BENCH}"
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
    --target_file_size_base="${TARGET_FILE_SIZE_BASE}"
    --max_bytes_for_level_base="${MAX_BYTES_FOR_LEVEL_BASE}"
    --num="${NUM_KEYS}"
    --key_size="${KEY_SIZE}"
    --value_size="${VALUE_SIZE}"
    --batch_size="${BATCH_SIZE}"
    --threads="${THREADS}"
    --memtablerep="${MEMTABLE_REP}"
    --seed=12345678
    --db="${DB_DIR}"
    --use_direct_reads="${USE_DIRECT_READS}"
    --use_direct_io_for_flush_and_compaction="${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}"
    --compression_type=none
    --use_titan=true
    --titan_min_blob_size="${TITAN_MIN_BLOB_SIZE}"
    --titan_mid_blob_size="${TITAN_MID_BLOB_SIZE}"
    --titan_sep_before_flush=true
    --titan_level_merge=true
    --titan_range_merge=true
    --titan_lazy_merge=true
    --titan_max_sorted_runs="${TITAN_MAX_SORTED_RUNS}"
    --titan_blob_file_target_size="${TITAN_BLOB_FILE_TARGET_SIZE}"
    --titan_min_gc_batch_size="${TITAN_MIN_GC_BATCH_SIZE}"
    --titan_max_gc_batch_size="${TITAN_MAX_GC_BATCH_SIZE}"
    --titan_blob_file_discardable_ratio="${TITAN_GC_RATIO}"
    --titan_disable_background_gc=false
    --titan_max_background_gc="${TITAN_MAX_BACKGROUND_GC}"
    --titan_block_write_size="${TITAN_BLOCK_WRITE_SIZE}"
    --wait_for_compaction_timeout_sec="${WAIT_TIMEOUT_SECONDS}"
  )
}

build_load_command() {
  build_common_command "${RUN_ROOT}/report.rep"
  LOAD_CMD=("${COMMON_CMD[@]}" --benchmarks="${BENCHMARKS}")
}

build_reopen_command() {
  build_common_command "${RUN_ROOT}/reopen.rep"
  REOPEN_CMD=(
    "${COMMON_CMD[@]}"
    --use_existing_db=true
    --reads="${READS}"
    --benchmarks=waitforcompaction,readrandom,readseq,stats,levelstats
  )
}

check_effective_options() {
  local output="$1"
  local pattern
  for pattern in \
      'min_blob_size:             128' \
      'mid_blob_size:             8192' \
      'sep_before_flush:          true' \
      'level_merge:               true' \
      'range_merge:               true' \
      'lazy_merge:                true' \
      'max_sorted_runs:           10' \
      'blob_file_target_size:     8388608' \
      'min_gc_batch_size:         33554432' \
      'max_gc_batch_size:         67108864' \
      'blob_discardable_ratio:    0.300' \
      'disable_background_gc:     false' \
      'max_background_gc:         8' \
      'block_write_size:          0' \
      'dynamic_level_bytes:       true' \
      'dynamic_base_level:        4'; do
    rg -Fq "${pattern}" "${output}" || \
      die "effective DiffKV option missing from output: ${pattern}"
  done
}

build_load_command
build_reopen_command
check_interface

printf 'DiffKV binary:       %s\n' "${DIFFKV_DB_BENCH}"
printf 'Target:              %s GiB (%s logical bytes)\n' \
  "${TARGET_GIB}" "${LOGICAL_BYTES}"
printf 'Records:             %s\n' "${NUM_KEYS}"
printf 'KV:                  %s B key + %s B value\n' "${KEY_SIZE}" "${VALUE_SIZE}"
printf 'RocksDB common:      jobs=%s, subcompactions=%s, memtable=%s\n' \
  "${BG_JOBS}" "${SUBCOMPACTIONS}" "${MEMTABLE_REP}"
printf 'I/O policy:          direct_reads=%s, direct_flush_compaction=%s\n' \
  "${USE_DIRECT_READS}" "${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}"
printf 'DiffKV:              thresholds=128/8192, vTable=8MiB, GC=0.3/8, sorted-runs=10\n'

if [[ "${DRY_RUN}" == "1" ]]; then
  printf '\nDRY_RUN=1; no DB or log directories will be created.\n'
  printf '[load]\n'
  print_command "${LOAD_CMD[@]}"
  printf '[reopen validation]\n'
  print_command "${REOPEN_CMD[@]}"
  exit 0
fi

if pgrep -x db_bench >/dev/null 2>&1 || \
    pgrep -x titandb_bench >/dev/null 2>&1; then
  die "Another db_bench/titandb_bench is running"
fi
[[ ! -e "${RUN_ROOT}" ]] || die "Log output already exists: ${RUN_ROOT}"
[[ ! -e "${DB_DIR}" ]] || die "DB output already exists: ${DB_DIR}"
mkdir -p "${RUN_ROOT}/raw" "${RUN_ROOT}/runner_snapshot" "${DB_DIR}"

cp -- "$0" "${RUN_ROOT}/runner_snapshot/run_diffkv_db_bench_load.sh"
chmod a-w "${RUN_ROOT}/runner_snapshot/run_diffkv_db_bench_load.sh"
write_command_file "${RUN_ROOT}/raw/load_cmd.sh" "${LOAD_CMD[@]}"
write_command_file "${RUN_ROOT}/raw/reopen_cmd.sh" "${REOPEN_CMD[@]}"
sha256sum "${DIFFKV_DB_BENCH}" \
  "${RUN_ROOT}/runner_snapshot/run_diffkv_db_bench_load.sh" \
  > "${RUN_ROOT}/SHA256SUMS"
git -C "${DIFFKV_ROOT}" rev-parse HEAD > "${RUN_ROOT}/diffkv_commit.txt"
git -C "${DIFFKV_ROOT}" submodule status --recursive \
  > "${RUN_ROOT}/diffkv_submodules.txt"
git -C "${DIFFKV_ROOT}" status --short > "${RUN_ROOT}/diffkv_git_status.txt"
git -C "${DIFFKV_ROOT}" diff > "${RUN_ROOT}/diffkv.patch"
cp -- "${DIFFKV_ROOT}/README.md" "${RUN_ROOT}/README.diffkv.md"
cp -- "${DIFFKV_ROOT}/bench_tools/YCSB-C/configDir/diffkv_config.ini" \
  "${RUN_ROOT}/diffkv_config.ini"
{
  date -u
  uname -a
  gcc-11 --version | head -1
  g++-11 --version | head -1
  free -h
  swapon --show
  df -h "${DB_DIR}"
  cat /proc/mdstat
} > "${RUN_ROOT}/environment.txt"

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
set +e
/usr/bin/time -v -o "${RUN_ROOT}/raw/time.out" \
  "${LOAD_CMD[@]}" > "${RUN_ROOT}/bench.out" 2>&1
load_rc=$?
set -e
end_ts="$(date +%s)"
cleanup_iostat

printf '%s\n' "${end_ts}" > "${RUN_ROOT}/raw/end_epoch.txt"
elapsed_sec=$((end_ts - start_ts))
printf '%s\n' "${elapsed_sec}" > "${RUN_ROOT}/raw/elapsed_sec.txt"
cat /proc/diskstats > "${RUN_ROOT}/raw/diskstats.end"
cat /proc/stat > "${RUN_ROOT}/raw/procstat.end"
cat /proc/vmstat > "${RUN_ROOT}/raw/vmstat.end"
printf '%s\n' "${load_rc}" > "${RUN_ROOT}/raw/load_exit_code.txt"
[[ "${load_rc}" -eq 0 ]] || die "DiffKV load failed with exit code ${load_rc}"

if rg -n 'Corruption:|Segmentation fault|Assertion .*failed|FATAL|Out of memory' \
    "${RUN_ROOT}/bench.out" > "${RUN_ROOT}/validation_errors.txt"; then
  die "DiffKV load emitted a fatal validation pattern"
fi
if rg -q 'WARNING: (Optimization is disabled|Assertions are enabled)' \
    "${RUN_ROOT}/bench.out"; then
  die "DiffKV performance binary is not an assertion-free optimized build"
fi
check_effective_options "${RUN_ROOT}/bench.out"
rg -q '^waitforcompaction\(.*\): finished$' "${RUN_ROOT}/bench.out" || \
  die "DiffKV load did not reach the settled LSM boundary"

drop_page_cache
reopen_start="$(date +%s)"
set +e
/usr/bin/time -v -o "${RUN_ROOT}/raw/reopen_time.out" \
  "${REOPEN_CMD[@]}" > "${RUN_ROOT}/reopen.out" 2>&1
reopen_rc=$?
set -e
reopen_end="$(date +%s)"
printf '%s\n' "${reopen_rc}" > "${RUN_ROOT}/raw/reopen_exit_code.txt"
[[ "${reopen_rc}" -eq 0 ]] || die "DiffKV reopen validation failed: ${reopen_rc}"
check_effective_options "${RUN_ROOT}/reopen.out"

read_counts="$(sed -n 's/.*(\([0-9][0-9]*\) of \([0-9][0-9]*\) found).*/\1 \2/p' \
  "${RUN_ROOT}/reopen.out" | tail -1)"
read_found="${read_counts%% *}"
read_requested="${read_counts##* }"
[[ "${read_requested}" == "${READS}" ]] || \
  die "DiffKV random-read count mismatch: ${read_counts:-missing}"
# Old db_bench starts reopen reads at a different PRNG position from writes.
# For N random writes into N keys, an independent random lookup should hit
# about 1-exp(-1) = 63.2%. Keep a deliberately wide corruption-detection gate.
min_random_hits=$((READS * 50 / 100))
max_random_hits=$((READS * 75 / 100))
[[ "${read_found}" -ge "${min_random_hits}" && \
   "${read_found}" -le "${max_random_hits}" ]] || \
  die "DiffKV random-read hit count is outside the occupancy gate: ${read_counts}"
# This old reporter omits the operation count for readseq. The patched method
# exits non-zero unless it reads exactly --reads entries and its iterator ends
# cleanly, so a reported readseq line plus reopen_rc=0 is the proof.
rg -q '^readseq[[:space:]]*:' "${RUN_ROOT}/reopen.out" || \
  die "DiffKV sequential reopen verification result is missing"
readseq_ops="${READS}"

peak_rss_kb="$(extract_peak_rss_kb "${RUN_ROOT}/raw/time.out")"
final_db_bytes="$(du -sb "${DB_DIR}" | awk '{print $1}')"
fillrandom_sec="$(awk '$1 == "fillrandom" && $2 == ":" {for (i=1;i<=NF;i++) if ($i=="seconds") v=$(i-1)} END{print v}' "${RUN_ROOT}/bench.out")"
reopen_elapsed_sec=$((reopen_end - reopen_start))

printf 'system\tstatus\ttarget_gib\tnum_keys\telapsed_sec\tfillrandom_sec\tpeak_rss_kb\tfinal_db_bytes\treopen_elapsed_sec\trandom_read_found\trandom_read_requested\tsequential_reads_verified\tuse_direct_reads\tuse_direct_io_for_flush_and_compaction\tlog_dir\tdb_dir\n' \
  > "${RUN_ROOT}/summary.tsv"
printf 'diffkv\tok\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "${TARGET_GIB}" "${NUM_KEYS}" "${elapsed_sec}" "${fillrandom_sec}" \
  "${peak_rss_kb}" "${final_db_bytes}" "${reopen_elapsed_sec}" \
  "${read_found}" "${read_requested}" "${readseq_ops}" \
  "${USE_DIRECT_READS}" "${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}" \
  "${RUN_ROOT}" "${DB_DIR}" \
  >> "${RUN_ROOT}/summary.tsv"

printf 'DiffKV completed: elapsed=%ss, fillrandom=%ss, random=%s/%s, sequential=%s/%s, DB=%s bytes\n' \
  "${elapsed_sec}" "${fillrandom_sec}" "${read_found}" "${read_requested}" \
  "${readseq_ops}" "${READS}" "${final_db_bytes}"
