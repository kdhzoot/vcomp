#!/usr/bin/env bash
# Clean-RocksDB vector-memtable background-job sensitivity sweep.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

CLEAN_ROOT="${CLEAN_ROOT:-${REPO_ROOT}/../rocksdb-f455-release}"
CLEAN_DB_BENCH="${CLEAN_DB_BENCH:-${CLEAN_ROOT}/db_bench}"
EXPECTED_COMMIT="${EXPECTED_COMMIT:-f455ab7bd6a8c67f00d48075bb310f131d9fae5f}"
TARGET_GIB="${TARGET_GIB:-100}"
PILOT_GIB="${PILOT_GIB:-1}"
RUN_PILOT="${RUN_PILOT:-1}"
BG_JOBS_LIST="${BG_JOBS_LIST:-8 16 24}"
WRITE_BUFFER_SIZE="${WRITE_BUFFER_SIZE:-67108864}"
MAX_WRITE_BUFFER_NUMBER="${MAX_WRITE_BUFFER_NUMBER:-8}"
MIN_WRITE_BUFFER_NUMBER_TO_MERGE="${MIN_WRITE_BUFFER_NUMBER_TO_MERGE:-1}"
ALLOW_CONCURRENT_MEMTABLE_WRITE="${ALLOW_CONCURRENT_MEMTABLE_WRITE:-false}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
RUN_ROOT="${RUN_ROOT:-${ARTIFACT_ROOT}/log_loads/paper_clean_vector_bgjob_${TARGET_GIB}gib_${RUN_ID}}"
DB_ROOT="${DB_ROOT:-${VCOMP_DB_ROOT%/}/exp/paper_clean_vector_bgjob_${TARGET_GIB}gib_${RUN_ID}}"
WAIT_FOR_IDLE="${WAIT_FOR_IDLE:-0}"
DRY_RUN="${DRY_RUN:-1}"

KEY_SIZE=24
VALUE_SIZE=1000
KV_SIZE=$((KEY_SIZE + VALUE_SIZE))

require_executable "${CLEAN_DB_BENCH}" "clean RocksDB db_bench"
require_dir "${CLEAN_ROOT}" "clean RocksDB source"
require_positive_uint TARGET_GIB
require_positive_uint PILOT_GIB
require_positive_uint WRITE_BUFFER_SIZE
require_positive_uint MAX_WRITE_BUFFER_NUMBER
require_positive_uint MIN_WRITE_BUFFER_NUMBER_TO_MERGE
[[ "${RUN_PILOT}" == "0" || "${RUN_PILOT}" == "1" ]] || die "RUN_PILOT must be 0 or 1"
[[ "${WAIT_FOR_IDLE}" == "0" || "${WAIT_FOR_IDLE}" == "1" ]] || die "WAIT_FOR_IDLE must be 0 or 1"
[[ "${DRY_RUN}" == "0" || "${DRY_RUN}" == "1" ]] || die "DRY_RUN must be 0 or 1"
[[ "${ALLOW_CONCURRENT_MEMTABLE_WRITE}" == "true" || \
   "${ALLOW_CONCURRENT_MEMTABLE_WRITE}" == "false" ]] || \
  die "ALLOW_CONCURRENT_MEMTABLE_WRITE must be true or false"

for jobs in ${BG_JOBS_LIST}; do
  require_positive_uint jobs
done

actual_commit="$(git -C "${CLEAN_ROOT}" rev-parse HEAD)"
[[ "${actual_commit}" == "${EXPECTED_COMMIT}" ]] || \
  die "clean RocksDB commit mismatch: ${actual_commit}"
actual_sha="$(sha256sum "${CLEAN_DB_BENCH}" | awk '{print $1}')"

help_text="$("${CLEAN_DB_BENCH}" --help 2>&1 || true)"
for flag in max_background_jobs max_write_buffer_number \
    min_write_buffer_number_to_merge write_buffer_size memtablerep \
    allow_concurrent_memtable_write use_direct_reads \
    use_direct_io_for_flush_and_compaction; do
  [[ "${help_text}" == *"-${flag}"* ]] || die "db_bench does not expose --${flag}"
done

print_command() {
  printf '%q ' "$@"
  printf '\n'
}

declare -a CMD
build_command() {
  local target_gib="$1"
  local jobs="$2"
  local db_dir="$3"
  local report_file="$4"
  local num_keys=$((target_gib * 1024 * 1024 * 1024 / KV_SIZE))

  CMD=(
    "${CLEAN_DB_BENCH}"
    --statistics=1
    --stats_interval_seconds=60
    --stats_per_interval=1
    --report_interval_seconds=1
    --report_file="${report_file}"
    --enable_index_compression=false
    --bloom_bits=10
    --disable_wal=true
    --max_background_jobs="${jobs}"
    --subcompactions=1
    --write_buffer_size="${WRITE_BUFFER_SIZE}"
    --max_write_buffer_number="${MAX_WRITE_BUFFER_NUMBER}"
    --min_write_buffer_number_to_merge="${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}"
    --num="${num_keys}"
    --key_size="${KEY_SIZE}"
    --value_size="${VALUE_SIZE}"
    --batch_size=1
    --threads=1
    --memtablerep=vector
    --allow_concurrent_memtable_write="${ALLOW_CONCURRENT_MEMTABLE_WRITE}"
    --seed=12345678
    --db="${db_dir}"
    --use_direct_reads=true
    --use_direct_io_for_flush_and_compaction=true
    --compression_type=none
    --benchmarks=fillrandom,flush,compact0,waitforcompaction,stats,levelstats
  )
}

printf 'Clean db_bench:       %s\n' "${CLEAN_DB_BENCH}"
printf 'Commit:               %s\n' "${actual_commit}"
printf 'SHA-256:              %s\n' "${actual_sha}"
printf 'Target:               %s GiB\n' "${TARGET_GIB}"
printf 'Background jobs:      %s\n' "${BG_JOBS_LIST}"
printf 'Write buffers:        %s x %s bytes\n' \
  "${MAX_WRITE_BUFFER_NUMBER}" "${WRITE_BUFFER_SIZE}"
printf 'Concurrent memtable:  %s\n' "${ALLOW_CONCURRENT_MEMTABLE_WRITE}"
printf 'Pilot:                enabled=%s, size=%s GiB\n' "${RUN_PILOT}" "${PILOT_GIB}"

if [[ "${DRY_RUN}" == "1" ]]; then
  if [[ "${RUN_PILOT}" == "1" ]]; then
    first_jobs="${BG_JOBS_LIST%% *}"
    build_command "${PILOT_GIB}" "${first_jobs}" \
      "${DB_ROOT}/pilot_${PILOT_GIB}gib_bg${first_jobs}" \
      "${RUN_ROOT}/pilot_${PILOT_GIB}gib_bg${first_jobs}/report.rep"
    printf '\n[pilot]\n'
    print_command "${CMD[@]}"
  fi
  for jobs in ${BG_JOBS_LIST}; do
    build_command "${TARGET_GIB}" "${jobs}" \
      "${DB_ROOT}/baseline_bg${jobs}" "${RUN_ROOT}/baseline_bg${jobs}/report.rep"
    printf '\n[bg%s]\n' "${jobs}"
    print_command "${CMD[@]}"
  done
  exit 0
fi

if [[ "${WAIT_FOR_IDLE}" == "1" ]]; then
  while pgrep -x db_bench >/dev/null 2>&1 || pgrep -x titandb_bench >/dev/null 2>&1; do
    printf '[%s] waiting for the active benchmark to finish\n' "$(date -u '+%FT%TZ')"
    sleep 30
  done
else
  require_no_db_bench "clean RocksDB background-job sweep"
fi

[[ ! -e "${RUN_ROOT}" ]] || die "log output already exists: ${RUN_ROOT}"
[[ ! -e "${DB_ROOT}" ]] || die "DB output already exists: ${DB_ROOT}"
mkdir -p "${RUN_ROOT}" "${DB_ROOT}"
mkdir -p "${RUN_ROOT}/runner_snapshot"
cp -- "$0" "${RUN_ROOT}/runner_snapshot/run_clean_vector_bgjob_sweep.sh"
chmod a-w "${RUN_ROOT}/runner_snapshot/run_clean_vector_bgjob_sweep.sh"
printf 'case\tstatus\ttarget_gib\tbg_jobs\twrite_buffer_size\tmax_write_buffer_number\telapsed_sec\tfillrandom_sec\tpeak_rss_kb\tfinal_db_bytes\tlog_dir\tdb_dir\n' \
  > "${RUN_ROOT}/summary.tsv"
git -C "${CLEAN_ROOT}" rev-parse HEAD > "${RUN_ROOT}/clean_commit.txt"
git -C "${CLEAN_ROOT}" status --short > "${RUN_ROOT}/clean_git_status.txt"
sha256sum "${CLEAN_DB_BENCH}" "$0" > "${RUN_ROOT}/SHA256SUMS"
{
  date -u
  uname -a
  lscpu
  free -h
  df -h "${DB_ROOT}"
} > "${RUN_ROOT}/environment.txt"

IOSTAT_PID=""
cleanup_iostat() {
  stop_process "${IOSTAT_PID:-}"
  IOSTAT_PID=""
}
trap cleanup_iostat EXIT
trap 'cleanup_iostat; exit 130' INT
trap 'cleanup_iostat; exit 143' TERM

run_one() {
  local case_id="$1"
  local target_gib="$2"
  local jobs="$3"
  local log_dir="${RUN_ROOT}/${case_id}"
  local db_dir="${DB_ROOT}/${case_id}"
  local raw_dir="${log_dir}/raw"
  local report_file="${log_dir}/report.rep"
  local bench_out="${log_dir}/bench.out"
  local start end elapsed rc fill_sec peak_rss final_bytes status

  require_no_db_bench "${case_id}"
  mkdir -p "${raw_dir}" "${db_dir}"
  build_command "${target_gib}" "${jobs}" "${db_dir}" "${report_file}"
  { printf '#!/usr/bin/env bash\n'; print_command "${CMD[@]}"; } > "${raw_dir}/load_cmd.sh"
  chmod a-w "${raw_dir}/load_cmd.sh"
  drop_page_cache
  cat /proc/diskstats > "${raw_dir}/diskstats.start"
  cat /proc/stat > "${raw_dir}/procstat.start"
  if command -v iostat >/dev/null 2>&1; then
    iostat -dx 1 > "${raw_dir}/iostat.log" &
    IOSTAT_PID=$!
  fi
  start="$(date +%s)"
  set +e
  /usr/bin/time -v -o "${raw_dir}/time.out" "${CMD[@]}" > "${bench_out}" 2>&1
  rc=$?
  set -e
  end="$(date +%s)"
  cleanup_iostat
  cat /proc/diskstats > "${raw_dir}/diskstats.end"
  cat /proc/stat > "${raw_dir}/procstat.end"
  printf '%s\n' "${rc}" > "${raw_dir}/exit_code.txt"

  elapsed=$((end - start))
  fill_sec="$(awk '/^fillrandom[[:space:]]*:/ {for (i=2; i<=NF; i++) if ($i=="seconds") v=$(i-1)} END {print v}' "${bench_out}")"
  peak_rss="$(awk -F: '/Maximum resident set size/ {gsub(/^[[:space:]]+/, "", $2); print $2}' "${raw_dir}/time.out")"
  final_bytes="$(du -sb "${db_dir}" | awk '{print $1}')"
  status=ok
  [[ "${rc}" -eq 0 ]] || status="failed_rc_${rc}"
  rg -q 'Memtablerep: VectorRepFactory' "${bench_out}" || status="failed_memtable"
  rg -q 'waitforcompaction\(.*\): finished with status \(OK\)' "${bench_out}" || status="failed_settle"
  if rg -q 'WARNING: (Optimization is disabled|Assertions are enabled)' "${bench_out}"; then
    status="failed_build"
  fi
  rg -q "Options.max_background_jobs: +${jobs}$" "${db_dir}"/LOG* || status="failed_bg_jobs"
  rg -q "write_buffer_size: +${WRITE_BUFFER_SIZE}$" "${db_dir}"/LOG* || status="failed_write_buffer"
  rg -q "max_write_buffer_number: +${MAX_WRITE_BUFFER_NUMBER}$" "${db_dir}"/LOG* || status="failed_buffer_count"
  expected_concurrent=0
  [[ "${ALLOW_CONCURRENT_MEMTABLE_WRITE}" == "false" ]] || expected_concurrent=1
  rg -q "Options.allow_concurrent_memtable_write: +${expected_concurrent}$" \
    "${db_dir}"/LOG* || status="failed_concurrent_memtable"

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${case_id}" "${status}" "${target_gib}" "${jobs}" \
    "${WRITE_BUFFER_SIZE}" "${MAX_WRITE_BUFFER_NUMBER}" "${elapsed}" \
    "${fill_sec:-NA}" "${peak_rss:-NA}" "${final_bytes}" "${log_dir}" "${db_dir}" \
    >> "${RUN_ROOT}/summary.tsv"
  [[ "${status}" == "ok" ]] || die "${case_id} validation failed: ${status}"
}

if [[ "${RUN_PILOT}" == "1" ]]; then
  first_jobs="${BG_JOBS_LIST%% *}"
  run_one "pilot_${PILOT_GIB}gib_bg${first_jobs}" "${PILOT_GIB}" "${first_jobs}"
fi
for jobs in ${BG_JOBS_LIST}; do
  run_one "baseline_bg${jobs}" "${TARGET_GIB}" "${jobs}"
done

touch "${RUN_ROOT}/COMPLETED"
printf 'Completed: %s\n' "${RUN_ROOT}"
