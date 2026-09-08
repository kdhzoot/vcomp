#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_ROOT="${WORKSPACE_ROOT:-/home/smrc/virtual_compaction}"
VCOMP_ROOT="${VCOMP_ROOT:-${WORKSPACE_ROOT}/vcomp}"
DIFFKV_ROOT="${DIFFKV_ROOT:-${VCOMP_ROOT}/experiments/artifacts/external_baselines/diffkv}"
YCSB_BIN="${YCSB_BIN:-${VCOMP_ROOT}/experiments/artifacts/external_baselines/diffkv_ycsb_smoke_build/ycsbc_smoke}"
VERIFY_BIN="${VERIFY_BIN:-${VCOMP_ROOT}/experiments/artifacts/external_baselines/diffkv_ycsb_smoke_build/verify_diffkv_smoke}"
ASSET_ROOT="${ASSET_ROOT:-${VCOMP_ROOT}/experiments/scripts/artifact_baselines/diffkv_ycsb_smoke}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
RUN_ROOT="${RUN_ROOT:-${VCOMP_ROOT}/experiments/artifacts/log_loads/paper_artifact_baselines_${RUN_ID}/diffkv_smoke}"
DB_ROOT="${DB_ROOT:-/work/vcomp/exp/paper_artifact_baselines/diffkv/${RUN_ID}}"
THREADS="${THREADS:-1}"
TARGET_GIB="${TARGET_GIB:-1}"
SUBCOMPACTIONS="${SUBCOMPACTIONS:-1}"
BG_JOBS="${BG_JOBS:-8}"
SYSTEM_ORDER="${SYSTEM_ORDER:-titan diffkv}"
RECORD_COUNT=$((TARGET_GIB * 1048576))
LOGICAL_BYTES=$((TARGET_GIB * 1024 * 1024 * 1024))

[[ -x "${YCSB_BIN}" ]] || { echo "YCSB smoke binary not executable: ${YCSB_BIN}" >&2; exit 1; }
[[ -x "${VERIFY_BIN}" ]] || { echo "Smoke verifier not executable: ${VERIFY_BIN}" >&2; exit 1; }
[[ "${THREADS}" =~ ^[1-9][0-9]*$ ]] || { echo "Invalid THREADS=${THREADS}" >&2; exit 1; }
[[ "${TARGET_GIB}" =~ ^[1-9][0-9]*$ ]] || { echo "Invalid TARGET_GIB=${TARGET_GIB}" >&2; exit 1; }
[[ "${SUBCOMPACTIONS}" =~ ^[1-9][0-9]*$ ]] || { echo "Invalid SUBCOMPACTIONS=${SUBCOMPACTIONS}" >&2; exit 1; }
[[ "${BG_JOBS}" =~ ^[1-9][0-9]*$ ]] || { echo "Invalid BG_JOBS=${BG_JOBS}" >&2; exit 1; }
[[ "${SYSTEM_ORDER}" == "titan diffkv" || "${SYSTEM_ORDER}" == "diffkv titan" ]] || {
  echo "Invalid SYSTEM_ORDER=${SYSTEM_ORDER}" >&2
  exit 1
}
[[ ! -e "${RUN_ROOT}" ]] || { echo "Run root exists: ${RUN_ROOT}" >&2; exit 1; }
[[ ! -e "${DB_ROOT}" ]] || { echo "DB root exists: ${DB_ROOT}" >&2; exit 1; }
if pgrep -x ycsbc_smoke >/dev/null 2>&1 || pgrep -x db_bench >/dev/null 2>&1; then
  echo "Another storage benchmark is active" >&2
  exit 1
fi

mkdir -p "${RUN_ROOT}/runner_snapshot" "${DB_ROOT}"
cp -- "$0" "${RUN_ROOT}/runner_snapshot/run_diffkv_smoke.sh"
awk -v records="${RECORD_COUNT}" '
  /^recordcount=/ {print "recordcount=" records; next}
  /^operationcount=/ {print "operationcount=" records; next}
  {print}
' "${ASSET_ROOT}/workload_1g_load.spec" > "${RUN_ROOT}/runner_snapshot/workload_load.spec"
awk -v records="${RECORD_COUNT}" '
  /^recordcount=/ {print "recordcount=" records; next}
  {print}
' "${ASSET_ROOT}/workload_reopen_read.spec" > "${RUN_ROOT}/runner_snapshot/workload_read.spec"
cp -- "${ASSET_ROOT}/CMakeLists.txt" "${ASSET_ROOT}/db_factory_smoke.cc" \
  "${ASSET_ROOT}/titandb_db_smoke.cc" "${ASSET_ROOT}/titandb_db_smoke.h" \
  "${RUN_ROOT}/runner_snapshot/"
cp -- "${ASSET_ROOT}/verify_diffkv_smoke.cc" "${RUN_ROOT}/runner_snapshot/"
chmod -R a-w "${RUN_ROOT}/runner_snapshot"
sha256sum "${YCSB_BIN}" "${VERIFY_BIN}" "${RUN_ROOT}/runner_snapshot/"* > "${RUN_ROOT}/SHA256SUMS"
git -C "${DIFFKV_ROOT}" rev-parse HEAD > "${RUN_ROOT}/diffkv_commit.txt"
git -C "${DIFFKV_ROOT}" submodule status --recursive > "${RUN_ROOT}/diffkv_submodules.txt"
git -C "${DIFFKV_ROOT}" status --short > "${RUN_ROOT}/diffkv_git_status.txt"
git -C "${DIFFKV_ROOT}" diff > "${RUN_ROOT}/diffkv.patch"
cp -- "${DIFFKV_ROOT}/README.md" "${RUN_ROOT}/README.diffkv.md"
{
  date -u
  uname -a
  g++ --version | head -1
  free -h
  swapon --show
  df -h /work
} > "${RUN_ROOT}/environment.txt"

printf 'system\tstatus\ttarget_gib\tthreads\tconfigured_subcompactions\tconfigured_bg_jobs\toptions_proven\telapsed_sec\tpeak_rss_kb\tsystem_swap_in_delta\tsystem_swap_out_delta\tbenchmark_peak_swap_kb\tfinal_db_bytes\tload_ops\trun_ops\tlog_dir\tdb_dir\tmd0_write_bytes\tlogical_bytes\tdevice_write_amplification\n' > "${RUN_ROOT}/summary.tsv"

vmstat_value() {
  local name="$1" file="$2"
  awk -v name="${name}" '$1 == name {print $2}' "${file}"
}

md0_written_sectors() {
  awk '$3 == "md0" {print $10}' "$1"
}

cleanup_iostat() {
  local pid="${1:-}"
  [[ -n "${pid}" ]] || return 0
  kill "${pid}" 2>/dev/null || true
  wait "${pid}" 2>/dev/null || true
}

run_one() {
  local system="$1"
  local backend="$2"
  local config="$3"
  local log_dir="${RUN_ROOT}/${system}"
  local db_dir="${DB_ROOT}/${system}"
  local load_spec="${RUN_ROOT}/runner_snapshot/workload_load.spec"
  local read_spec="${RUN_ROOT}/runner_snapshot/workload_read.spec"
  local iostat_pid=""
  local start end elapsed peak_rss db_bytes load_ops run_ops swap_in swap_out options_proven
  local time_pid benchmark_pid benchmark_swap_kb benchmark_peak_swap_kb=0 monitor_seen=false
  local sectors_start sectors_end md0_write_bytes device_write_amplification
  mkdir -p "${log_dir}/raw" "${db_dir}"
  cp -- "${config}" "${log_dir}/config.ini"

  local load_cmd=(
    "${YCSB_BIN}"
    -db "${backend}"
    -dbfilename "${db_dir}"
    -threads "${THREADS}"
    -P "${load_spec}"
    -phase load
    -configpath "${log_dir}/config.ini"
  )
  local reopen_cmd=(
    "${YCSB_BIN}"
    -db "${backend}"
    -dbfilename "${db_dir}"
    -threads 1
    -P "${read_spec}"
    -phase run
    -configpath "${log_dir}/config.ini"
  )
  { printf '#!/usr/bin/env bash\n'; printf '%q ' "${load_cmd[@]}"; printf '\n'; } > "${log_dir}/raw/load_cmd.sh"
  { printf '#!/usr/bin/env bash\n'; printf '%q ' "${reopen_cmd[@]}"; printf '\n'; } > "${log_dir}/raw/reopen_cmd.sh"
  chmod a-w "${log_dir}/raw/load_cmd.sh" "${log_dir}/raw/reopen_cmd.sh"

  sync
  cat /proc/diskstats > "${log_dir}/raw/diskstats.start"
  cat /proc/stat > "${log_dir}/raw/procstat.start"
  cat /proc/vmstat > "${log_dir}/raw/vmstat.start"
  iostat -dx 1 > "${log_dir}/raw/iostat.log" & iostat_pid=$!
  start="$(date +%s)"
  set +e
  /usr/bin/time -v -o "${log_dir}/raw/time.out" env \
    DIFFKV_SUBCOMPACTIONS="${SUBCOMPACTIONS}" DIFFKV_BG_JOBS="${BG_JOBS}" \
    "${load_cmd[@]}" > "${log_dir}/load.out" 2>&1 &
  time_pid=$!
  while kill -0 "${time_pid}" 2>/dev/null; do
    benchmark_pid="$(pgrep -P "${time_pid}" -x ycsbc_smoke | head -1 || true)"
    if [[ -n "${benchmark_pid}" && -r "/proc/${benchmark_pid}/status" ]]; then
      monitor_seen=true
      benchmark_swap_kb="$(awk '/^VmSwap:/ {print $2}' "/proc/${benchmark_pid}/status")"
      benchmark_swap_kb="${benchmark_swap_kb:-0}"
      if (( benchmark_swap_kb > benchmark_peak_swap_kb )); then
        benchmark_peak_swap_kb="${benchmark_swap_kb}"
      fi
    fi
    sleep 0.25
  done
  wait "${time_pid}"
  local rc=$?
  set -e
  end="$(date +%s)"
  cleanup_iostat "${iostat_pid}"
  cat /proc/diskstats > "${log_dir}/raw/diskstats.end"
  cat /proc/stat > "${log_dir}/raw/procstat.end"
  cat /proc/vmstat > "${log_dir}/raw/vmstat.end"
  elapsed=$((end - start))
  sectors_start="$(md0_written_sectors "${log_dir}/raw/diskstats.start")"
  sectors_end="$(md0_written_sectors "${log_dir}/raw/diskstats.end")"
  md0_write_bytes=$(( (sectors_end - sectors_start) * 512 ))
  device_write_amplification="$(awk -v written="${md0_write_bytes}" -v logical="${LOGICAL_BYTES}" \
    'BEGIN {printf "%.6f", written / logical}')"
  if [[ "${rc}" -ne 0 ]]; then
    printf '%s\tfailed_load\t%s\t%s\t%s\t%s\t\t%s\t\t\t\t\t\t\t%s\t%s\n' \
      "${system}" "${TARGET_GIB}" "${THREADS}" "${SUBCOMPACTIONS}" "${BG_JOBS}" \
      "${elapsed}" "${log_dir}" "${db_dir}" >> "${RUN_ROOT}/summary.tsv"
    return "${rc}"
  fi

  env DIFFKV_SUBCOMPACTIONS="${SUBCOMPACTIONS}" DIFFKV_BG_JOBS="${BG_JOBS}" \
    "${reopen_cmd[@]}" > "${log_dir}/reopen.out" 2>&1
  "${VERIFY_BIN}" "${db_dir}" "${log_dir}/config.ini" "${RECORD_COUNT}" \
    > "${log_dir}/verify.out" 2>&1
  if rg -n -i "can't open|insert error|corruption|segmentation|assertion.*failed|unknown database" \
      "${log_dir}/load.out" "${log_dir}/reopen.out" > "${log_dir}/validation_errors.txt"; then
    printf '%s\tfailed_validation\t%s\t\t\t\t\t%s\t%s\n' "${system}" "${elapsed}" "${log_dir}" "${db_dir}" >> "${RUN_ROOT}/summary.tsv"
    return 1
  fi

  peak_rss="$(awk -F: '/Maximum resident set size/ {gsub(/^[ \t]+/,"",$2); print $2}' "${log_dir}/raw/time.out")"
  db_bytes="$(du -sb "${db_dir}" | awk '{print $1}')"
  load_ops="$(awk '/^# Loading records:/ {v=$NF} END{print v}' "${log_dir}/load.out")"
  run_ops="$(awk '/^Read ops/ {v=$NF} END{print v}' "${log_dir}/reopen.out")"
  [[ "${load_ops}" == "${RECORD_COUNT}" ]] || { echo "Unexpected load ops for ${system}: ${load_ops}" >&2; return 1; }
  [[ "${run_ops}" == "10000" ]] || { echo "Unexpected reopen ops for ${system}: ${run_ops}" >&2; return 1; }
  [[ "${db_bytes}" -ge $((TARGET_GIB * 536870912)) ]] || { echo "DB too small for ${TARGET_GIB} GiB smoke (${system}): ${db_bytes}" >&2; return 1; }
  rg -q '^verified_records=[0-9]+ value_bytes=1000$' "${log_dir}/verify.out" || {
    echo "Direct value verification failed for ${system}" >&2
    return 1
  }
  options_proven=false
  if rg -q "Options.max_subcompactions: +${SUBCOMPACTIONS}$" "${db_dir}"/LOG* && \
      rg -q "Options.max_background_jobs: +${BG_JOBS}$" "${db_dir}"/LOG*; then
    options_proven=true
  fi
  [[ "${options_proven}" == "true" ]] || { echo "Configured RocksDB options not proven for ${system}" >&2; return 1; }
  swap_in=$(( $(vmstat_value pswpin "${log_dir}/raw/vmstat.end") - $(vmstat_value pswpin "${log_dir}/raw/vmstat.start") ))
  swap_out=$(( $(vmstat_value pswpout "${log_dir}/raw/vmstat.end") - $(vmstat_value pswpout "${log_dir}/raw/vmstat.start") ))
  [[ "${monitor_seen}" == "true" ]] || { echo "Benchmark swap monitor did not observe ${system}" >&2; return 1; }
  [[ "${benchmark_peak_swap_kb}" -eq 0 ]] || {
    echo "Benchmark process swapped for ${system}: ${benchmark_peak_swap_kb} KB" >&2
    return 1
  }
  {
    printf '%s\tok' "${system}"
    printf '\t%s' "${TARGET_GIB}" "${THREADS}" "${SUBCOMPACTIONS}" "${BG_JOBS}" \
      "${options_proven}" "${elapsed}" "${peak_rss}" "${swap_in}" "${swap_out}" \
      "${benchmark_peak_swap_kb}" "${db_bytes}" "${load_ops}" "${run_ops}" \
      "${log_dir}" "${db_dir}" "${md0_write_bytes}" "${LOGICAL_BYTES}" \
      "${device_write_amplification}"
    printf '\n'
  } >> "${RUN_ROOT}/summary.tsv"
}

for system in ${SYSTEM_ORDER}; do
  case "${system}" in
    titan) run_one titan titandb "${DIFFKV_ROOT}/bench_tools/YCSB-C/configDir/titandb_config.ini" ;;
    diffkv) run_one diffkv diffkv "${DIFFKV_ROOT}/bench_tools/YCSB-C/configDir/diffkv_config.ini" ;;
  esac
done
printf 'completed_utc=%s\n' "$(date -u '+%FT%TZ')" > "${RUN_ROOT}/COMPLETED"
