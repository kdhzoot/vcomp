#!/usr/bin/env bash
# Preserve the pre-final-compaction state for a last-comp experiment.

set -euo pipefail

VCOMP_ROOT="${VCOMP_ROOT:-/home/smrc/virtual_compaction/vcomp}"
DB_BENCH="${DB_BENCH:-${VCOMP_ROOT}/db_bench}"
EXPECTED_DB_BENCH_SHA256="${EXPECTED_DB_BENCH_SHA256:-c5964e54fb0afc4779fd17b82f7366bdd8d19be9bd2acae33dd26c77e55019cd}"
TARGET_GIB="${TARGET_GIB:-1000}"
PILOT_GIB="${PILOT_GIB:-1}"
RUN_PILOT="${RUN_PILOT:-1}"
RUN_ID="${RUN_ID:-paper_lastcomp_flushonly_wb16_1tib_$(date -u '+%y%m%d_%H%M%S')}"
RUN_ROOT="${RUN_ROOT:-${VCOMP_ROOT}/experiments/artifacts/log_loads/${RUN_ID}}"
DB_ROOT="${DB_ROOT:-/work/vcomp/exp/${RUN_ID}}"
BG_JOBS="${BG_JOBS:-48}"
WRITE_BUFFER_SIZE="${WRITE_BUFFER_SIZE:-67108864}"
MAX_WRITE_BUFFER_NUMBER="${MAX_WRITE_BUFFER_NUMBER:-16}"
MIN_WRITE_BUFFER_NUMBER_TO_MERGE="${MIN_WRITE_BUFFER_NUMBER_TO_MERGE:-1}"
STALL_TRIGGER="${STALL_TRIGGER:-1073741824}"

die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
log() { printf '[%s] %s\n' "$(date -u '+%FT%TZ')" "$*" | tee -a "${RUN_ROOT}/run.log"; }
is_positive_uint() { [[ "$1" =~ ^[1-9][0-9]*$ ]]; }
stop_process() {
  local pid="${1:-}"
  [[ -n "${pid}" ]] || return 0
  kill "${pid}" 2>/dev/null || true
  wait "${pid}" 2>/dev/null || true
}
drop_page_cache() {
  sync
  if [[ -w /proc/sys/vm/drop_caches ]]; then
    printf '3\n' > /proc/sys/vm/drop_caches
  elif command -v sudo >/dev/null 2>&1 &&
      printf '3\n' | sudo -n tee /proc/sys/vm/drop_caches >/dev/null 2>&1; then
    :
  else
    printf '[WARN] page cache could not be dropped\n' >&2
  fi
}
print_command() { printf '%q ' "$@"; printf '\n'; }

for value in "${TARGET_GIB}" "${PILOT_GIB}" "${BG_JOBS}" \
    "${WRITE_BUFFER_SIZE}" "${MAX_WRITE_BUFFER_NUMBER}" \
    "${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}" "${STALL_TRIGGER}"; do
  is_positive_uint "${value}" || die "expected a positive integer, got: ${value}"
done
[[ "${RUN_PILOT}" == "0" || "${RUN_PILOT}" == "1" ]] || die "RUN_PILOT must be 0 or 1"
[[ -x "${DB_BENCH}" ]] || die "db_bench is not executable: ${DB_BENCH}"
[[ "$(sha256sum "${DB_BENCH}" | awk '{print $1}')" == "${EXPECTED_DB_BENCH_SHA256}" ]] ||
  die "db_bench hash differs from the original Figure 4 last-comp binary"
pgrep -x db_bench >/dev/null 2>&1 && die "another db_bench is active"
pgrep -x titandb_bench >/dev/null 2>&1 && die "another titandb_bench is active"
[[ ! -e "${RUN_ROOT}" ]] || die "log output already exists: ${RUN_ROOT}"
[[ ! -e "${DB_ROOT}" ]] || die "DB output already exists: ${DB_ROOT}"

mkdir -p "${RUN_ROOT}/runner_snapshot" "${DB_ROOT}"
cp -- "$0" "${RUN_ROOT}/runner_snapshot/run_lastcomp_flushonly_wb16_1tb.sh"
chmod a-w "${RUN_ROOT}/runner_snapshot/run_lastcomp_flushonly_wb16_1tb.sh"
sha256sum "${DB_BENCH}" "$0" > "${RUN_ROOT}/SHA256SUMS"
git -C "${VCOMP_ROOT}" rev-parse HEAD > "${RUN_ROOT}/vcomp_commit.txt"
git -C "${VCOMP_ROOT}" status --short > "${RUN_ROOT}/vcomp_git_status.txt"
"${DB_BENCH}" --version > "${RUN_ROOT}/db_bench_version.txt" 2>&1
{
  date -u
  uname -a
  lscpu
  free -h
  swapon --show
  df -h "${DB_ROOT}"
} > "${RUN_ROOT}/environment.txt"
printf 'case\tstatus\ttarget_gib\telapsed_sec\tfillrandom_sec\tpeak_rss_kb\tl0_files\tnon_l0_files\tcompaction_read_bytes\tfinal_db_bytes\tlog_dir\tdb_dir\n' \
  > "${RUN_ROOT}/summary.tsv"

declare -a CMD
build_command() {
  local target_gib="$1" db_dir="$2" report_file="$3"
  local num_keys=$((target_gib * 1024 * 1024 * 1024 / 1024))
  CMD=(
    "${DB_BENCH}"
    --statistics=1
    --stats_interval_seconds=60
    --stats_per_interval=1
    --report_interval_seconds=10
    --report_file="${report_file}"
    --enable_index_compression=false
    --bloom_bits=10
    --disable_wal=true
    --max_background_jobs="${BG_JOBS}"
    --subcompactions=1
    --write_buffer_size="${WRITE_BUFFER_SIZE}"
    --max_write_buffer_number="${MAX_WRITE_BUFFER_NUMBER}"
    --min_write_buffer_number_to_merge="${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}"
    --num="${num_keys}"
    --key_size=24
    --value_size=1000
    --batch_size=1
    --threads=1
    --memtablerep=vector
    --seed=12345678
    --db="${db_dir}"
    --use_direct_reads=true
    --use_direct_io_for_flush_and_compaction=true
    --compression_type=none
    --disable_auto_compactions=true
    --level0_file_num_compaction_trigger="${STALL_TRIGGER}"
    --level0_slowdown_writes_trigger="${STALL_TRIGGER}"
    --level0_stop_writes_trigger="${STALL_TRIGGER}"
    --soft_pending_compaction_bytes_limit=0
    --hard_pending_compaction_bytes_limit=0
    --benchmarks=fillrandom,flush,stats,levelstats
  )
}

IOSTAT_PID=""
cleanup() {
  stop_process "${IOSTAT_PID:-}"
  IOSTAT_PID=""
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

run_one() {
  local case_id="$1" target_gib="$2"
  local log_dir="${RUN_ROOT}/${case_id}" db_dir="${DB_ROOT}/${case_id}"
  local raw_dir="${log_dir}/raw" report_file="${log_dir}/report.rep"
  local bench_out="${log_dir}/bench.out"
  local start end elapsed rc fill_sec peak_rss l0_files non_l0_files
  local compact_read_bytes final_bytes status options_log

  pgrep -x db_bench >/dev/null 2>&1 && die "unexpected db_bench before ${case_id}"
  mkdir -p "${raw_dir}" "${db_dir}"
  build_command "${target_gib}" "${db_dir}" "${report_file}"
  { printf '#!/usr/bin/env bash\n'; print_command "${CMD[@]}"; } > "${raw_dir}/load_cmd.sh"
  chmod a-w "${raw_dir}/load_cmd.sh"
  log "BEGIN ${case_id}: target=${target_gib}GiB, flush-only, max_write_buffers=${MAX_WRITE_BUFFER_NUMBER}"
  drop_page_cache
  cat /proc/diskstats > "${raw_dir}/diskstats.start"
  cat /proc/stat > "${raw_dir}/procstat.start"
  if command -v iostat >/dev/null 2>&1; then
    iostat -dx 1 > "${raw_dir}/iostat.log" &
    IOSTAT_PID=$!
  fi
  start="$(date +%s)"
  printf '%s\n' "${start}" > "${raw_dir}/start_epoch.txt"
  set +e
  /usr/bin/time -v -o "${raw_dir}/time.out" "${CMD[@]}" > "${bench_out}" 2>&1
  rc=$?
  set -e
  end="$(date +%s)"
  printf '%s\n' "${end}" > "${raw_dir}/end_epoch.txt"
  cleanup
  cat /proc/diskstats > "${raw_dir}/diskstats.end"
  cat /proc/stat > "${raw_dir}/procstat.end"
  printf '%s\n' "${rc}" > "${raw_dir}/exit_code.txt"

  elapsed=$((end - start))
  printf '%s\n' "${elapsed}" > "${raw_dir}/elapsed_sec.txt"
  fill_sec="$(awk '/^fillrandom[[:space:]]*:/ {for(i=2;i<=NF;i++) if($i=="seconds") v=$(i-1)} END{print v}' "${bench_out}")"
  peak_rss="$(awk -F: '/Maximum resident set size/ {gsub(/^[[:space:]]+/, "", $2); print $2}' "${raw_dir}/time.out")"
  l0_files="$(awk '$1=="0" && NF==3 {v=$2} END{print v+0}' "${bench_out}")"
  non_l0_files="$(awk '$1~/^[1-6]$/ && NF==3 {s+=$2} END{print s+0}' "${bench_out}")"
  compact_read_bytes="$(awk '$1=="rocksdb.compact.read.bytes" && $2=="COUNT" {v=$4} END{print v+0}' "${bench_out}")"
  final_bytes="$(du -sb "${db_dir}" | awk '{print $1}')"
  options_log="$(find "${db_dir}" -maxdepth 1 -type f -name 'LOG*' -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"

  status=ok
  [[ "${rc}" -eq 0 ]] || status="failed_rc_${rc}"
  [[ -n "${fill_sec}" ]] || status=failed_missing_fill_time
  [[ "${l0_files}" -gt 0 && "${non_l0_files}" -eq 0 ]] || status=failed_final_layout
  [[ "${compact_read_bytes}" -eq 0 ]] || status=failed_unexpected_compaction_read
  rg -q 'rocksdb.compaction.times.micros .* COUNT : 0 ' "${bench_out}" || status=failed_unexpected_compaction
  rg -q 'Memtablerep: VectorRepFactory' "${bench_out}" || status=failed_memtable
  rg -q 'Options.max_background_jobs: +48$' "${options_log}" || status=failed_bg_jobs
  rg -q 'Options.write_buffer_size: +67108864$' "${options_log}" || status=failed_write_buffer
  rg -q 'Options.max_write_buffer_number: +16$' "${options_log}" || status=failed_buffer_count
  rg -q 'Options.disable_auto_compactions: +1$' "${options_log}" || status=failed_auto_compaction_option
  if rg -q 'Corruption:|Segmentation fault|Assertion .*failed|FATAL|Out of memory|WARNING: (Optimization is disabled|Assertions are enabled)' "${bench_out}"; then
    status=failed_log_validation
  fi

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${case_id}" "${status}" "${target_gib}" "${elapsed}" "${fill_sec:-NA}" \
    "${peak_rss:-NA}" "${l0_files}" "${non_l0_files}" "${compact_read_bytes}" \
    "${final_bytes}" "${log_dir}" "${db_dir}" >> "${RUN_ROOT}/summary.tsv"
  log "END ${case_id}: status=${status}, elapsed=${elapsed}s, L0=${l0_files}, non-L0=${non_l0_files}"
  [[ "${status}" == "ok" ]] || die "${case_id} validation failed: ${status}"
}

if [[ "${RUN_PILOT}" == "1" ]]; then
  run_one "pilot_${PILOT_GIB}gib" "${PILOT_GIB}"
fi
run_one "flushonly_${TARGET_GIB}gib" "${TARGET_GIB}"
touch "${RUN_ROOT}/COMPLETED"
log "all runs completed; database intentionally preserved without final compaction"
