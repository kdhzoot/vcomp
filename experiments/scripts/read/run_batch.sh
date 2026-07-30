#!/usr/bin/env bash
: <<'EXAMPLE'
# Run readrandom 1M on all 30 runs in a batch, writing logs under
#   log_batch/${BATCH}/${WORKLOAD}_${THREADS}t_${CACHE_PCT}p/${MODE}_run$i/
BATCH=260414_1710_250gb_x30 MODE=vcomp RUNS=30 DB_SIZE_GB=250 \
  WORKLOAD=readrandom CACHE_PCT=0 THREADS=1 READS=1000000 \
  bash run_batch.sh

# Baseline 30 runs with same setup
BATCH=260410_0339_250gb_x30 MODE=baseline RUNS=30 DB_SIZE_GB=250 \
  WORKLOAD=readrandom CACHE_PCT=0 THREADS=1 READS=1000000 \
  bash run_batch.sh
EXAMPLE

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

# ── Required env ──
require_env BATCH MODE DB_SIZE_GB
[[ "${MODE}" == "baseline" || "${MODE}" == "vcomp" ]] ||
  die "MODE must be baseline or vcomp"

RUNS="${RUNS:-30}"
WORKLOAD="${WORKLOAD:-readrandom}"
THREADS="${THREADS:-1}"
CACHE_PCT="${CACHE_PCT:-0}"
READS="${READS:-1000000}"
DURATION="${DURATION:-0}"
DB_ROOT="${DB_ROOT:-${VCOMP_DB_ROOT}}"
READ_ONLY="${READ_ONLY:-1}"
require_positive_uint RUNS
require_positive_uint DB_SIZE_GB
require_positive_uint THREADS
require_uint CACHE_PCT
(( CACHE_PCT <= 100 )) || die "CACHE_PCT must be between 0 and 100"
require_uint READS
require_uint DURATION
[[ "${READ_ONLY}" == "0" || "${READ_ONLY}" == "1" ]] ||
  die "READ_ONLY must be 0 or 1"

BATCH_DB_DIR="${DB_ROOT}/${BATCH}"
[[ -d "${BATCH_DB_DIR}" ]] || {
  echo "[ERROR] Batch DB dir not found: ${BATCH_DB_DIR}" >&2; exit 1
}

RUN_ROOT="${LOG_ROOT:-${ARTIFACT_ROOT}/log_batch/${BATCH}/${WORKLOAD}_${THREADS}t_${CACHE_PCT}p}"
mkdir -p "${RUN_ROOT}"

echo "=========================================="
echo "  Batch ${WORKLOAD}"
echo "  Batch:     ${BATCH}"
echo "  Mode:      ${MODE}"
echo "  Runs:      ${RUNS}"
echo "  DB Size:   ${DB_SIZE_GB} GB"
echo "  Threads:   ${THREADS}"
echo "  Cache:     ${CACHE_PCT}%"
echo "  Reads:     ${READS}"
echo "  DB Root:   ${BATCH_DB_DIR}"
echo "  Log Root:  ${RUN_ROOT}"
echo "=========================================="

CSV="${RUN_ROOT}/results.csv"
if [[ ! -s "${CSV}" ]]; then
  echo "run_id,elapsed_s,peak_rss_kb,peak_rss_gb,throughput_ops_s,cpu_util,disk_read_MBs,exit_code" > "${CSV}"
fi

summary_value() {
  local key="$1" summary="$2"
  awk -F: -v key="${key}" '$1 == key { sub(/^[[:space:]]+/, "", $2); print $2; exit }' "${summary}"
}

append_result() {
  local run_id="$1" summary="$2"
  if awk -F, -v run_id="${run_id}" \
      'NR > 1 && $1 == run_id { found=1 } END { exit !found }' "${CSV}"; then
    return 0
  fi

  local elapsed peak_rss_kb peak_rss_gb throughput cpu_util disk_r exit_code
  elapsed="$(summary_value elapsed "${summary}")"; elapsed="${elapsed%s}"
  peak_rss_kb="$(summary_value peak_rss_kb "${summary}")"
  peak_rss_gb="$(summary_value peak_rss_gb "${summary}")"
  throughput="$(summary_value throughput "${summary}")"; throughput="${throughput%% *}"
  cpu_util="$(summary_value cpu_util "${summary}")"; cpu_util="${cpu_util%\%}"
  disk_r="$(summary_value disk_read "${summary}")"; disk_r="${disk_r%% *}"
  exit_code="$(summary_value exit_code "${summary}")"
  echo "${run_id},${elapsed},${peak_rss_kb},${peak_rss_gb},${throughput},${cpu_util},${disk_r},${exit_code}" >> "${CSV}"
}

failures=0

for ((i = 1; i <= RUNS; i++)); do
  label="${MODE}_run${i}"
  db_dir="${BATCH_DB_DIR}/${label}"
  if [[ ! -d "${db_dir}" ]]; then
    echo "[SKIP] ${label}: ${db_dir} missing"
    failures=$((failures + 1))
    continue
  fi

  result_dir="${RUN_ROOT}/${label}"
  summary="${result_dir}/summary.txt"
  if [[ -f "${summary}" ]]; then
    append_result "${i}" "${summary}"
    result_rc="$(summary_value exit_code "${summary}")"
    [[ "${result_rc}" == "0" ]] || failures=$((failures + 1))
    echo "[SKIP] ${label}: result already exists"
    continue
  fi
  [[ ! -e "${result_dir}" ]] ||
    die "Incomplete result directory exists: ${result_dir}"

  echo ""
  echo "────────────────────────────────────────"
  echo "[${label}] Starting ($(date))"
  echo "────────────────────────────────────────"

  set +e
  RESULT_DIR="${result_dir}" \
    WORKLOAD="${WORKLOAD}" DB_DIR="${db_dir}" DB_SIZE_GB="${DB_SIZE_GB}" \
    CACHE_PCT="${CACHE_PCT}" THREADS="${THREADS}" \
    DURATION="${DURATION}" READS="${READS}" READ_ONLY="${READ_ONLY}" \
      bash "${SCRIPT_DIR}/run.sh" 2>&1 | tail -15
  run_rc="${PIPESTATUS[0]}"
  set -e

  if [[ -f "${summary}" ]]; then
    append_result "${i}" "${summary}"
  else
    failures=$((failures + 1))
  fi
  if [[ "${run_rc}" != "0" && -f "${summary}" ]]; then
    failures=$((failures + 1))
  fi
done

echo ""
echo "=========================================="
echo "  Batch ${WORKLOAD} complete ($(date))"
echo "  CSV: ${CSV}"
echo "=========================================="

if (( failures > 0 )); then
  die "Batch completed with ${failures} missing or failed run(s)"
fi
