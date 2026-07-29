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

# ── Required env ──
for name in BATCH MODE DB_SIZE_GB; do
  [[ -n "${!name:-}" ]] || { echo "[ERROR] Missing required env: ${name}" >&2; exit 1; }
done

RUNS="${RUNS:-30}"
WORKLOAD="${WORKLOAD:-readrandom}"
THREADS="${THREADS:-1}"
CACHE_PCT="${CACHE_PCT:-0}"
READS="${READS:-1000000}"
DURATION="${DURATION:-0}"
DB_ROOT="${DB_ROOT:-/work/vcomp}"
READ_ONLY="${READ_ONLY:-1}"

BATCH_DB_DIR="${DB_ROOT}/${BATCH}"
[[ -d "${BATCH_DB_DIR}" ]] || {
  echo "[ERROR] Batch DB dir not found: ${BATCH_DB_DIR}" >&2; exit 1
}

RUN_ROOT="${SCRIPT_DIR}/log_batch/${BATCH}/${WORKLOAD}_${THREADS}t_${CACHE_PCT}p"
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
echo "run_id,elapsed_s,peak_rss_kb,peak_rss_gb,throughput_ops_s,cpu_util,disk_read_MBs,exit_code" > "${CSV}"

for ((i = 1; i <= RUNS; i++)); do
  label="${MODE}_run${i}"
  db_dir="${BATCH_DB_DIR}/${label}"
  if [[ ! -d "${db_dir}" ]]; then
    echo "[SKIP] ${label}: ${db_dir} missing"
    continue
  fi

  result_dir="${RUN_ROOT}/${label}"
  if [[ -d "${result_dir}" && -f "${result_dir}/summary.txt" ]]; then
    echo "[SKIP] ${label}: result already exists"
    continue
  fi

  echo ""
  echo "────────────────────────────────────────"
  echo "[${label}] Starting ($(date))"
  echo "────────────────────────────────────────"

  RESULT_DIR="${result_dir}" \
  WORKLOAD="${WORKLOAD}" DB_DIR="${db_dir}" DB_SIZE_GB="${DB_SIZE_GB}" \
  CACHE_PCT="${CACHE_PCT}" THREADS="${THREADS}" \
  DURATION="${DURATION}" READS="${READS}" READ_ONLY="${READ_ONLY}" \
    bash "${SCRIPT_DIR}/run.sh" 2>&1 | tail -15 || true

  if [[ -f "${result_dir}/summary.txt" ]]; then
    elapsed=$(awk -F: '/^elapsed:/ {print $2}' "${result_dir}/summary.txt" | tr -d 's ' || echo 0)
    peak_rss_kb=$(awk -F: '/^peak_rss_kb:/ {print $2}' "${result_dir}/summary.txt" | tr -d ' ' || echo "")
    peak_rss_gb=$(awk -F: '/^peak_rss_gb:/ {print $2}' "${result_dir}/summary.txt" | tr -d ' ' || echo "")
    throughput=$(awk -F: '/^throughput:/ {print $2}' "${result_dir}/summary.txt" | awk '{print $1}' || echo 0)
    cpu_util=$(awk -F: '/^cpu_util:/ {print $2}' "${result_dir}/summary.txt" | tr -d '% ' || echo 0)
    disk_r=$(awk -F: '/^disk_read:/ {print $2}' "${result_dir}/summary.txt" | awk '{print $1}' || echo 0)
    exit_code=$(awk -F: '/^exit_code:/ {print $2}' "${result_dir}/summary.txt" | tr -d ' ' || echo 1)
    echo "${i},${elapsed},${peak_rss_kb},${peak_rss_gb},${throughput},${cpu_util},${disk_r},${exit_code}" >> "${CSV}"
  fi
done

echo ""
echo "=========================================="
echo "  Batch ${WORKLOAD} complete ($(date))"
echo "  CSV: ${CSV}"
echo "=========================================="
