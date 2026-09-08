#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

TARGET_DB_GB="${TARGET_DB_GB:-1000}"
START_ORDER="${START_ORDER:-1}"
DB_BENCH="${DB_BENCH:-${VCOMP_PROF_DB_BENCH}}"
EXPECTED_BINARY_SHA256="4a807ef1113420b3029812b497f1bb5c7fb1f11b3f79a9838a0045da84709321"
SERIES_ID="${SERIES_ID:-figure2_1tb_fourcell_$(date '+%y%m%d_%H%M%S')}"
SERIES_ROOT="${SERIES_ROOT:-${ARTIFACT_ROOT}/log_loads/${SERIES_ID}}"
DB_ROOT="${DB_ROOT:-${VCOMP_DB_ROOT}/exp/${SERIES_ID}}"
DISKSTAT_DEV="${DISKSTAT_DEV:-md0}"
IDLE_CHECK_SECONDS="${IDLE_CHECK_SECONDS:-60}"
MAX_IDLE_WRITE_MIB="${MAX_IDLE_WRITE_MIB:-64}"
LOAD_RUNNER="${SCRIPT_DIR}/load.sh"
VALIDATOR="${SCRIPT_DIR}/../../analysis/validate_figure2_profile.py"
PROF_REPO="${PROF_REPO:-${REPO_ROOT}/../vcomp-prof}"
SERIES_LOG="${SERIES_ROOT}/series.log"
STATUS_FILE="${SERIES_ROOT}/status.tsv"

[[ "${TARGET_DB_GB}" == "1000" ]] || die "Figure 2 run requires TARGET_DB_GB=1000"
require_positive_uint START_ORDER
(( START_ORDER <= 4 )) || die "START_ORDER must be between 1 and 4"
require_executable "${DB_BENCH}" "vcomp-prof db_bench"
require_file "${LOAD_RUNNER}" "load runner"
require_file "${VALIDATOR}" "Figure 2 profile validator"
require_positive_uint IDLE_CHECK_SECONDS
require_positive_uint MAX_IDLE_WRITE_MIB
require_no_db_bench "Figure 2 four-cell series"
[[ ! -e "${SERIES_ROOT}" ]] || die "Series output already exists: ${SERIES_ROOT}"
[[ ! -e "${DB_ROOT}" ]] || die "Series DB root already exists: ${DB_ROOT}"

actual_sha256="$(sha256sum "${DB_BENCH}" | awk '{print $1}')"
[[ "${actual_sha256}" == "${EXPECTED_BINARY_SHA256}" ]] || \
  die "Profiler binary hash changed: ${actual_sha256}"

available_bytes="$(df --output=avail -B1 /work | awk 'NR==2 {print $1}')"
minimum_bytes=$((6 * 1024 * 1024 * 1024 * 1024))
(( available_bytes >= minimum_bytes )) || die "Less than 6 TiB free on /work"

mkdir -p "${SERIES_ROOT}/provenance" "${SERIES_ROOT}/control" "${DB_ROOT}"
ulimit -n 1048576

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "${SERIES_LOG}"
}

written_sectors() {
  awk -v dev="${DISKSTAT_DEV}" '$3 == dev {print $10; found=1} END {if (!found) print ""}' /proc/diskstats
}

check_idle_device() {
  local control_dir="$1"
  local start end delta_mib
  require_no_db_bench "Figure 2 idle check"
  sync
  cat /proc/diskstats > "${control_dir}/diskstats.idle_start"
  start="$(written_sectors)"
  [[ -n "${start}" ]] || die "Cannot find ${DISKSTAT_DEV} in /proc/diskstats"
  sleep "${IDLE_CHECK_SECONDS}"
  cat /proc/diskstats > "${control_dir}/diskstats.idle_end"
  end="$(written_sectors)"
  delta_mib="$(awk -v s="${start}" -v e="${end}" 'BEGIN {printf "%.3f", (e-s)*512/1024/1024}')"
  echo "${delta_mib}" > "${control_dir}/idle_write_mib.txt"
  log "Idle check: device=${DISKSTAT_DEV} seconds=${IDLE_CHECK_SECONDS} delta_MiB=${delta_mib}"
  awk -v d="${delta_mib}" -v m="${MAX_IDLE_WRITE_MIB}" 'BEGIN {exit !(d <= m)}' || \
    die "Idle-write delta ${delta_mib} MiB exceeds ${MAX_IDLE_WRITE_MIB} MiB"
}

record_provenance() {
  sha256sum "${DB_BENCH}" > "${SERIES_ROOT}/provenance/db_bench.sha256"
  git -C "${PROF_REPO}" rev-parse HEAD > "${SERIES_ROOT}/provenance/vcomp_prof_commit.txt"
  git -C "${PROF_REPO}" status --short > "${SERIES_ROOT}/provenance/vcomp_prof_status.txt"
  git -C "${PROF_REPO}" diff --binary > "${SERIES_ROOT}/provenance/vcomp_prof.diff"
  cp "${PROF_REPO}/monitoring/vcomp_compaction_profiler.h" \
    "${SERIES_ROOT}/provenance/vcomp_compaction_profiler.h"
  git -C "${REPO_ROOT}" rev-parse HEAD > "${SERIES_ROOT}/provenance/vcomp_commit.txt"
  git -C "${REPO_ROOT}" status --short > "${SERIES_ROOT}/provenance/vcomp_status.txt"
  cp "${BASH_SOURCE[0]}" "${LOAD_RUNNER}" "${VALIDATOR}" "${SERIES_ROOT}/provenance/"
  uname -a > "${SERIES_ROOT}/provenance/uname.txt"
  lscpu > "${SERIES_ROOT}/provenance/lscpu.txt"
  free -h > "${SERIES_ROOT}/provenance/free_before.txt"
  swapon --show > "${SERIES_ROOT}/provenance/swapon_before.txt"
  df -h /work > "${SERIES_ROOT}/provenance/df_work_before.txt"
  cat /proc/mdstat > "${SERIES_ROOT}/provenance/mdstat.txt"
}

run_case() {
  local order="$1" label="$2" kv_label="$3" mode="$4" key_size="$5" value_size="$6"
  local run_dir="${SERIES_ROOT}/${label}"
  local db_dir="${DB_ROOT}/${label}"
  local control_dir="${SERIES_ROOT}/control/${label}"
  local start_epoch end_epoch elapsed

  mkdir -p "${control_dir}"
  check_idle_device "${control_dir}"
  start_epoch="$(date +%s)"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\trunning\n' \
    "${order}" "${label}" "${kv_label}" "${mode}" "${key_size}" "${value_size}" "${start_epoch}" \
    >> "${STATUS_FILE}"
  log "BEGIN order=${order} label=${label} kv=${kv_label} mode=${mode}"
  cat /proc/diskstats > "${control_dir}/diskstats.pre_run"

  MODE="${mode}" TARGET_DB_GB="${TARGET_DB_GB}" DB_BENCH="${DB_BENCH}" \
  KEY_SIZE="${key_size}" VALUE_SIZE="${value_size}" BG_JOBS=48 SUBCOMPACTIONS=1 \
  COMPRESSION_TYPE=none MEMTABLE_REP=vector WRITE_BUFFER_SIZE=67108864 \
  MAX_WRITE_BUFFER_NUMBER=2 MIN_WRITE_BUFFER_NUMBER_TO_MERGE=1 \
  ALLOW_CONCURRENT_MEMTABLE_WRITE=true USE_DIRECT_READS=true \
  USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION=true DB_ROOT="${DB_ROOT}" \
  DB_DIR="${db_dir}" LOG_DIR="${run_dir}" \
    bash "${LOAD_RUNNER}" 2>&1 | tee -a "${SERIES_LOG}"

  sync
  date +%s > "${run_dir}/raw/post_sync_epoch.txt"
  cat /proc/diskstats > "${run_dir}/raw/diskstats.post_sync"
  python3 "${VALIDATOR}" --case "${label}" --mode "${mode}" \
    --db-dir "${db_dir}" --bench-out "${run_dir}/bench.out" \
    --output-dir "${run_dir}/raw"
  python3 "${SCRIPT_DIR}/../../analysis/summarize_flush_breakdown.py" \
    "${run_dir}/raw/flush_breakdown.tsv" \
    --output "${run_dir}/flush_breakdown_summary.tsv"

  if [[ "${mode}" == "baseline" ]]; then
    rg 'Estimated pending compaction bytes: 0' "${run_dir}/bench.out" >/dev/null || \
      die "Conventional case did not report zero pending compaction bytes: ${label}"
  fi
  end_epoch="$(date +%s)"
  elapsed=$((end_epoch - start_epoch))
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\tok:%s\n' \
    "${order}" "${label}" "${kv_label}" "${mode}" "${key_size}" "${value_size}" "${end_epoch}" "${elapsed}" \
    >> "${STATUS_FILE}"
  log "END order=${order} label=${label} elapsed_with_sync_and_validation=${elapsed}s"
}

record_provenance
printf 'order\tlabel\tkv\tmode\tkey_size_B\tvalue_size_B\tepoch\tstatus\n' > "${STATUS_FILE}"
log "Starting Figure 2 four-cell series: ${SERIES_ID}"
log "target=1000GiB start_order=${START_ORDER} profiler_sha256=${actual_sha256} db_root=${DB_ROOT}"

(( START_ORDER > 1 )) || run_case 1 order1_kv1024_conventional '1 KB' baseline 24 1000
(( START_ORDER > 2 )) || run_case 2 order2_kv91_nocomp '91 B' l0only 48 43
(( START_ORDER > 3 )) || run_case 3 order3_kv91_conventional '91 B' baseline 48 43
(( START_ORDER > 4 )) || run_case 4 order4_kv1024_nocomp '1 KB' l0only 24 1000

df -h /work > "${SERIES_ROOT}/provenance/df_work_after.txt"
free -h > "${SERIES_ROOT}/provenance/free_after.txt"
log "Finished Figure 2 four-cell series"
