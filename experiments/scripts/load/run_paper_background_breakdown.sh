#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

TARGET_DB_GB="${TARGET_DB_GB:-500}"
BG_JOBS="${BG_JOBS:-48}"
DISKSTAT_DEV="${DISKSTAT_DEV:-md0}"
DB_BENCH="${DB_BENCH:-${VCOMP_PROF_DB_BENCH}}"
SERIES_ID="${SERIES_ID:-paper_bg_breakdown_$(date '+%y%m%d_%H%M%S')}"
SERIES_ROOT="${SERIES_ROOT:-${ARTIFACT_ROOT}/log_loads/${SERIES_ID}}"
DB_ROOT="${DB_ROOT:-/work/vcomp/exp/${SERIES_ID}}"
IDLE_CHECK_SECONDS="${IDLE_CHECK_SECONDS:-60}"
MAX_IDLE_WRITE_MIB="${MAX_IDLE_WRITE_MIB:-64}"
MATRIX_RUNNER="${SCRIPT_DIR}/run_motivation_kv_compression_matrix.sh"
FLUSH_SUMMARIZER="${SCRIPT_DIR}/../../analysis/summarize_flush_breakdown.py"
PROF_REPO="$(cd "${SCRIPT_DIR}/../../../.." && pwd)/vcomp-prof"
SERIES_LOG="${SERIES_ROOT}/series.log"
MANIFEST="${SERIES_ROOT}/manifest.tsv"

require_executable "${DB_BENCH}" "vcomp-prof db_bench"
require_file "${MATRIX_RUNNER}" "breakdown matrix runner"
require_file "${FLUSH_SUMMARIZER}" "flush breakdown summarizer"
require_positive_uint TARGET_DB_GB
require_positive_uint BG_JOBS
require_positive_uint IDLE_CHECK_SECONDS
require_positive_uint MAX_IDLE_WRITE_MIB
require_no_db_bench "paper background breakdown series"
[[ ! -e "${SERIES_ROOT}" ]] || die "Series output already exists: ${SERIES_ROOT}"
[[ ! -e "${DB_ROOT}" ]] || die "Series DB root already exists: ${DB_ROOT}"

mkdir -p "${SERIES_ROOT}/provenance" "${DB_ROOT}"
ulimit -n 1048576

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "${SERIES_LOG}"
}

written_sectors() {
  awk -v dev="${DISKSTAT_DEV}" '$3 == dev {print $10; found=1} END {if (!found) print ""}' /proc/diskstats
}

check_idle_device() {
  local start end delta_mib
  require_no_db_bench "paper background breakdown idle check"
  start="$(written_sectors)"
  [[ -n "${start}" ]] || die "Cannot find ${DISKSTAT_DEV} in /proc/diskstats"
  sleep "${IDLE_CHECK_SECONDS}"
  end="$(written_sectors)"
  delta_mib="$(awk -v s="${start}" -v e="${end}" 'BEGIN {printf "%.3f", (e-s)*512/1024/1024}')"
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
  cp "${BASH_SOURCE[0]}" "${SERIES_ROOT}/provenance/"
  cp "${MATRIX_RUNNER}" "${SERIES_ROOT}/provenance/"
  cp "${FLUSH_SUMMARIZER}" "${SERIES_ROOT}/provenance/"
  uname -a > "${SERIES_ROOT}/provenance/uname.txt"
  lscpu > "${SERIES_ROOT}/provenance/lscpu.txt"
  if ! lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS \
      > "${SERIES_ROOT}/provenance/lsblk.txt" 2>/dev/null; then
    lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT \
      > "${SERIES_ROOT}/provenance/lsblk.txt"
  fi
  df -h /work > "${SERIES_ROOT}/provenance/df_work.txt"
  cat /proc/mdstat > "${SERIES_ROOT}/provenance/mdstat.txt"
  mount | rg ' on /work ' > "${SERIES_ROOT}/provenance/work_mount.txt" || true
}

run_case() {
  local order="$1"
  local replicate="$2"
  local label="$3"
  local case_name="$4"
  local key_size="$5"
  local value_size="$6"
  local run_id="${SERIES_ID}_o${order}_r${replicate}_${label}"
  local run_root="${SERIES_ROOT}/o${order}_r${replicate}_${label}"
  local summary="${run_root}/summary.tsv"

  check_idle_device
  log "BEGIN order=${order} replicate=${replicate} label=${label} key=${key_size} value=${value_size}"
  RUN_ID="${run_id}" \
  LOG_ROOT="${run_root}" \
  CASES_SPEC="${case_name} ${value_size} none" \
  KEY_SIZE="${key_size}" \
  TARGET_DB_GB="${TARGET_DB_GB}" \
  BG_JOBS="${BG_JOBS}" \
  DISKSTAT_DEV="${DISKSTAT_DEV}" \
  DB_ROOT="${DB_ROOT}" \
  DB_BENCH="${DB_BENCH}" \
    bash "${MATRIX_RUNNER}"

  awk -F '\t' 'NR==2 && $8=="ok" && $20+0>0 && $24+0>0 {ok=1} END {exit !ok}' "${summary}" || \
    die "Run did not pass status/breakdown gate: ${summary}"
  python3 "${FLUSH_SUMMARIZER}" "${run_root}/flush_breakdown_all.tsv" \
    --output "${run_root}/flush_breakdown_summary.tsv" || \
    die "Flush breakdown validation failed: ${run_root}/flush_breakdown_all.tsv"
  log "END order=${order} replicate=${replicate} label=${label}"
}

record_provenance
printf 'order\treplicate\tlabel\tkey_size_B\tvalue_size_B\ttotal_kv_size_B\tcompression\n' > "${MANIFEST}"
printf '1\t1\t1KB\t24\t1000\t1024\tnone\n' >> "${MANIFEST}"
printf '2\t1\t91B\t48\t43\t91\tnone\n' >> "${MANIFEST}"
printf '3\t2\t91B\t48\t43\t91\tnone\n' >> "${MANIFEST}"
printf '4\t2\t1KB\t24\t1000\t1024\tnone\n' >> "${MANIFEST}"
printf '5\t3\t1KB\t24\t1000\t1024\tnone\n' >> "${MANIFEST}"
printf '6\t3\t91B\t48\t43\t91\tnone\n' >> "${MANIFEST}"

log "Starting paper background breakdown series"
log "SERIES_ID=${SERIES_ID} TARGET_DB_GB=${TARGET_DB_GB} BG_JOBS=${BG_JOBS}"
log "DB_BENCH=${DB_BENCH} DB_ROOT=${DB_ROOT}"

run_case 1 1 1kb kv1024_nocompress 24 1000
run_case 2 1 91b kv91_nocompress 48 43
run_case 3 2 91b kv91_nocompress 48 43
run_case 4 2 1kb kv1024_nocompress 24 1000
run_case 5 3 1kb kv1024_nocompress 24 1000
run_case 6 3 91b kv91_nocompress 48 43

log "Finished paper background breakdown series"
