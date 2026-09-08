#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_ROOT="${WORKSPACE_ROOT:-/home/smrc/virtual_compaction}"
VCOMP_ROOT="${VCOMP_ROOT:-${WORKSPACE_ROOT}/vcomp}"
ADOC_ROOT="${ADOC_ROOT:-${VCOMP_ROOT}/experiments/artifacts/external_baselines/adoc}"
ADOC_DB_BENCH="${ADOC_DB_BENCH:-${ADOC_ROOT}/db_bench}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
RUN_ROOT="${RUN_ROOT:-${VCOMP_ROOT}/experiments/artifacts/log_loads/paper_artifact_baselines_${RUN_ID}/adoc_smoke}"
DB_ROOT="${DB_ROOT:-/work/vcomp/exp/paper_artifact_baselines/adoc/${RUN_ID}}"
TARGET_GIB="${TARGET_GIB:-1}"
SUBCOMPACTIONS="${SUBCOMPACTIONS:-1}"
BG_JOBS="${BG_JOBS:-8}"
SYSTEM_ORDER="${SYSTEM_ORDER:-adoc_off adoc_on}"
KV_BYTES=1024
NUM_KEYS=$((TARGET_GIB * 1024 * 1024 * 1024 / KV_BYTES))
LOGICAL_BYTES=$((TARGET_GIB * 1024 * 1024 * 1024))

[[ -x "${ADOC_DB_BENCH}" ]] || { echo "ADOC db_bench not executable: ${ADOC_DB_BENCH}" >&2; exit 1; }
[[ "${TARGET_GIB}" =~ ^[1-9][0-9]*$ ]] || { echo "Invalid TARGET_GIB=${TARGET_GIB}" >&2; exit 1; }
[[ "${SUBCOMPACTIONS}" =~ ^[1-9][0-9]*$ ]] || { echo "Invalid SUBCOMPACTIONS=${SUBCOMPACTIONS}" >&2; exit 1; }
[[ "${SYSTEM_ORDER}" == "adoc_off adoc_on" || "${SYSTEM_ORDER}" == "adoc_on adoc_off" ]] || {
  echo "Invalid SYSTEM_ORDER=${SYSTEM_ORDER}" >&2
  exit 1
}
[[ ! -e "${RUN_ROOT}" ]] || { echo "Run root exists: ${RUN_ROOT}" >&2; exit 1; }
[[ ! -e "${DB_ROOT}" ]] || { echo "DB root exists: ${DB_ROOT}" >&2; exit 1; }
if pgrep -x db_bench >/dev/null 2>&1; then
  echo "Another db_bench is active" >&2
  exit 1
fi

mkdir -p "${RUN_ROOT}/runner_snapshot" "${DB_ROOT}"
cp -- "$0" "${RUN_ROOT}/runner_snapshot/run_adoc_smoke.sh"
chmod a-w "${RUN_ROOT}/runner_snapshot/run_adoc_smoke.sh"
sha256sum "${RUN_ROOT}/runner_snapshot/run_adoc_smoke.sh" "${ADOC_DB_BENCH}" > "${RUN_ROOT}/SHA256SUMS"
BINARY_HASH="$(sha256sum "${ADOC_DB_BENCH}" | awk '{print $1}')"
git -C "${ADOC_ROOT}" rev-parse HEAD > "${RUN_ROOT}/adoc_commit.txt"
git -C "${ADOC_ROOT}" status --short > "${RUN_ROOT}/adoc_git_status.txt"
git -C "${ADOC_ROOT}" diff > "${RUN_ROOT}/adoc.patch"
cp -- "${ADOC_ROOT}/README.md" "${RUN_ROOT}/README.adoc.md"
{
  date -u
  uname -a
  g++ --version | head -1
  free -h
  swapon --show
  df -h /work
} > "${RUN_ROOT}/environment.txt"

printf 'system\tstatus\ttarget_gib\tconfigured_subcompactions\tactual_subcompactions\telapsed_sec\tfillrandom_sec\tpeak_rss_kb\tswap_in_delta\tswap_out_delta\tpending_bytes\tfinal_db_bytes\tread_found\tread_requested\tlog_dir\tdb_dir\tmd0_write_bytes\tlogical_bytes\tdevice_write_amplification\n' > "${RUN_ROOT}/summary.tsv"

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
  local fea="$2"
  local tea="$3"
  local log_dir="${RUN_ROOT}/${system}"
  local db_dir="${DB_ROOT}/${system}"
  local iostat_pid=""
  local start end elapsed fill_sec peak_rss db_bytes found requested actual pending swap_in swap_out
  local sectors_start sectors_end md0_write_bytes device_write_amplification
  mkdir -p "${log_dir}/raw" "${db_dir}"
  [[ "$(sha256sum "${ADOC_DB_BENCH}" | awk '{print $1}')" == "${BINARY_HASH}" ]] || {
    echo "ADOC db_bench changed during paired run" >&2
    return 1
  }

  local cmd=(
    "${ADOC_DB_BENCH}"
    --statistics=1
    --stats_interval_seconds=1
    --stats_per_interval=1
    --report_interval_seconds=1
    --report_file="${log_dir}/report.rep"
    --enable_index_compression=false
    --bloom_bits=10
    --disable_wal=true
    --max_background_jobs="${BG_JOBS}"
    --subcompactions="${SUBCOMPACTIONS}"
    --num="${NUM_KEYS}"
    --key_size=24
    --value_size=1000
    --batch_size=1
    --threads=1
    --memtablerep=vector
    --allow_concurrent_memtable_write=false
    --seed=12345678
    --db="${db_dir}"
    --use_direct_reads=true
    --use_direct_io_for_flush_and_compaction=true
    --compression_type=none
    --DOTA_enabled=false
    --FEA_enable="${fea}"
    --TEA_enable="${tea}"
    --DOTA_tuning_gap=1
    --benchmarks=fillrandom,flush,compact0,waitforcompaction,stats,levelstats
  )
  { printf '#!/usr/bin/env bash\n'; printf '%q ' "${cmd[@]}"; printf '\n'; } > "${log_dir}/raw/load_cmd.sh"
  chmod a-w "${log_dir}/raw/load_cmd.sh"

  sync
  cat /proc/diskstats > "${log_dir}/raw/diskstats.start"
  cat /proc/stat > "${log_dir}/raw/procstat.start"
  cat /proc/vmstat > "${log_dir}/raw/vmstat.start"
  iostat -dx 1 > "${log_dir}/raw/iostat.log" & iostat_pid=$!
  start="$(date +%s)"
  set +e
  /usr/bin/time -v -o "${log_dir}/raw/time.out" "${cmd[@]}" > "${log_dir}/bench.out" 2>&1
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
  printf '%s\n' "${elapsed}" > "${log_dir}/raw/elapsed_sec.txt"
  if [[ "${rc}" -ne 0 ]]; then
    printf '%s\tfailed_load\t%s\t%s\t\t%s\t\t\t\t\t\t\t\t\t%s\t%s\n' \
      "${system}" "${TARGET_GIB}" "${SUBCOMPACTIONS}" "${elapsed}" "${log_dir}" "${db_dir}" >> "${RUN_ROOT}/summary.tsv"
    return "${rc}"
  fi

  local reopen=(
    "${ADOC_DB_BENCH}"
    --use_existing_db=true
    --num="${NUM_KEYS}"
    --reads=10000
    --key_size=24
    --value_size=1000
    --threads=1
    --seed=12345678
    --db="${db_dir}"
    --use_direct_reads=true
    --compression_type=none
    --DOTA_enabled=false
    --FEA_enable=false
    --TEA_enable=false
    --benchmarks=readrandom,stats,levelstats
  )
  { printf '#!/usr/bin/env bash\n'; printf '%q ' "${reopen[@]}"; printf '\n'; } > "${log_dir}/raw/reopen_cmd.sh"
  chmod a-w "${log_dir}/raw/reopen_cmd.sh"
  "${reopen[@]}" > "${log_dir}/reopen.out" 2>&1
  if rg -n 'Corruption:|Segmentation fault|Assertion .*failed|FATAL|Out of memory' \
      "${log_dir}/bench.out" "${log_dir}/reopen.out" > "${log_dir}/validation_errors.txt"; then
    printf '%s\tfailed_validation\t%s\t%s\t\t%s\t\t\t\t\t\t\t\t\t%s\t%s\n' \
      "${system}" "${TARGET_GIB}" "${SUBCOMPACTIONS}" "${elapsed}" "${log_dir}" "${db_dir}" >> "${RUN_ROOT}/summary.tsv"
    return 1
  fi

  fill_sec="$(awk '$1 == "fillrandom" && $2 == ":" {for(i=1;i<=NF;i++) if($i=="seconds") v=$(i-1)} END{print v}' "${log_dir}/bench.out")"
  peak_rss="$(awk -F: '/Maximum resident set size/ {gsub(/^[ \t]+/,"",$2); print $2}' "${log_dir}/raw/time.out")"
  db_bytes="$(du -sb "${db_dir}" | awk '{print $1}')"
  found="$(awk '/readrandom.*found/ {for(i=1;i<=NF;i++) if($i=="of") {gsub(/\(/,"",$(i-1)); v=$(i-1)}} END{print v}' "${log_dir}/reopen.out")"
  requested=10000
  actual="$(awk '/rocksdb.num.subcompactions.scheduled/ {for(i=1;i<=NF;i++) if($i=="SUM" && $(i+1)==":") v=$(i+2)} END{if(v=="")v=0; printf "%.0f",v}' "${log_dir}/bench.out")"
  pending="$(awk -F': ' '/^Estimated pending compaction bytes:/ {v=$2} END{if(v=="")v="NA"; print v}' "${log_dir}/bench.out")"
  swap_in=$(( $(vmstat_value pswpin "${log_dir}/raw/vmstat.end") - $(vmstat_value pswpin "${log_dir}/raw/vmstat.start") ))
  swap_out=$(( $(vmstat_value pswpout "${log_dir}/raw/vmstat.end") - $(vmstat_value pswpout "${log_dir}/raw/vmstat.start") ))
  [[ "${found}" == "${requested}" ]] || { echo "Read validation failed for ${system}" >&2; return 1; }
  [[ "${pending}" == "0" || "${pending}" == "NA" ]] || {
    echo "Pending compaction remains for ${system}: ${pending}" >&2
    return 1
  }
  [[ "${swap_in}" -eq 0 && "${swap_out}" -eq 0 ]] || { echo "Swap activity detected for ${system}" >&2; return 1; }
  printf '%s\tok\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${system}" "${TARGET_GIB}" "${SUBCOMPACTIONS}" "${actual}" "${elapsed}" \
    "${fill_sec}" "${peak_rss}" "${swap_in}" "${swap_out}" "${pending}" \
    "${db_bytes}" "${found}" "${requested}" "${log_dir}" "${db_dir}" \
    "${md0_write_bytes}" "${LOGICAL_BYTES}" "${device_write_amplification}" >> "${RUN_ROOT}/summary.tsv"
}

for system in ${SYSTEM_ORDER}; do
  case "${system}" in
    adoc_off) run_one adoc_off false false ;;
    adoc_on) run_one adoc_on true true ;;
  esac
done
printf 'completed_utc=%s\n' "$(date -u '+%FT%TZ')" > "${RUN_ROOT}/COMPLETED"
