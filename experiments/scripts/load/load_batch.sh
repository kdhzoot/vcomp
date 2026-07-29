#!/usr/bin/env bash
: <<'EXAMPLE'
# Load 30 baseline + 30 vcomp 250GB DBs
RUNS=30 TARGET_DB_GB=250 DB_ROOT=/work/vcomp bash load_batch.sh

# Load only vcomp
RUNS=10 TARGET_DB_GB=250 DB_ROOT=/work/vcomp MODES=vcomp bash load_batch.sh

# Load only baseline
RUNS=5 TARGET_DB_GB=1000 DB_ROOT=/work/vcomp MODES=baseline bash load_batch.sh
EXAMPLE

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

# ── Required env ──
for name in RUNS TARGET_DB_GB DB_ROOT; do
  [[ -n "${!name:-}" ]] || { echo "[ERROR] Missing: ${name}" >&2; exit 1; }
done

MODES="${MODES:-baseline,vcomp}"
RUN_TS="$(date '+%y%m%d_%H%M')"
BATCH_NAME="${RUN_TS}_${TARGET_DB_GB}gb_x${RUNS}"
SUMMARY_DIR="${LOG_ROOT:-${ARTIFACT_ROOT}/log_batch/${BATCH_NAME}}"
BATCH_DB_DIR="${DB_ROOT}/${BATCH_NAME}"
mkdir -p "${SUMMARY_DIR}" "${BATCH_DB_DIR}"

echo "=========================================="
echo "  Batch Loading Benchmark"
echo "  Runs:      ${RUNS} per mode"
echo "  Modes:     ${MODES}"
echo "  DB Size:   ${TARGET_DB_GB} GB"
echo "  Threads:   1"
echo "  Memtable:  vector"
echo "  DB Dir:    ${BATCH_DB_DIR}"
echo "  Log Dir:   ${SUMMARY_DIR}"
echo "=========================================="

# ── Run function ──
run_one() {
  local mode="$1" run_id="$2"
  local db_dir="${BATCH_DB_DIR}/${mode}_run${run_id}"
  local label="${mode}_run${run_id}"

  # Skip if DB already exists
  if [[ -d "${db_dir}" ]]; then
    echo "[SKIP] ${label}: ${db_dir} already exists"
    return 0
  fi

  echo ""
  echo "────────────────────────────────────────"
  echo "[${label}] Starting ($(date))"
  echo "────────────────────────────────────────"

  local start_ts
  start_ts="$(date +%s)"

  LOG_DIR="${SUMMARY_DIR}/${label}" \
    DB_DIR="${db_dir}" MODE="${mode}" TARGET_DB_GB="${TARGET_DB_GB}" \
    DB_ROOT="${DB_ROOT}" \
    bash "${SCRIPT_DIR}/load.sh" 2>&1 | tail -20

  local end_ts elapsed
  end_ts="$(date +%s)"
  elapsed=$((end_ts - start_ts))

  local db_size
  db_size="$(du -sh "${db_dir}" 2>/dev/null | cut -f1)"
  local peak_rss_kb=""
  local peak_rss_gb=""
  [[ -f "${SUMMARY_DIR}/${label}/raw/peak_rss_kb.txt" ]] && peak_rss_kb="$(<"${SUMMARY_DIR}/${label}/raw/peak_rss_kb.txt")"
  [[ -f "${SUMMARY_DIR}/${label}/raw/peak_rss_gb.txt" ]] && peak_rss_gb="$(<"${SUMMARY_DIR}/${label}/raw/peak_rss_gb.txt")"

  echo "[${label}] Done: ${elapsed}s, ${db_size}, peak_rss=${peak_rss_gb:-NA}GiB"

  # Append to CSV
  echo "${mode},${run_id},1,vector,${elapsed},${peak_rss_kb},${peak_rss_gb},${db_size}" >> "${SUMMARY_DIR}/results.csv"
}

# ── CSV header ──
echo "mode,run_id,threads,memtable,elapsed_sec,peak_rss_kb,peak_rss_gb,db_size" > "${SUMMARY_DIR}/results.csv"

# ── Main loop ──
IFS=',' read -ra mode_arr <<< "${MODES}"
for mode in "${mode_arr[@]}"; do
  echo ""
  echo "============ ${mode} x ${RUNS} ============"
  for ((i = 1; i <= RUNS; i++)); do
    run_one "${mode}" "${i}"
  done
done

# ── Summary report ──
echo ""
echo "=========================================="
echo "  Batch Complete ($(date))"
echo "=========================================="

for mode in "${mode_arr[@]}"; do
  echo ""
  echo "--- ${mode} ---"
  times=$(grep "^${mode}," "${SUMMARY_DIR}/results.csv" | awk -F',' '{print $5}')
  count=$(echo "$times" | wc -l)
  if [[ "$count" -gt 0 && -n "$times" ]]; then
    echo "$times" | awk '{
      sum += $1; sumsq += $1*$1; n++
      if (n==1 || $1<min) min=$1
      if (n==1 || $1>max) max=$1
    }
    END {
      avg = sum/n
      stddev = (n>1) ? sqrt((sumsq - sum*sum/n)/(n-1)) : 0
      printf "  Runs:   %d\n", n
      printf "  Mean:   %.1f sec\n", avg
      printf "  Stddev: %.1f sec\n", stddev
      printf "  Min:    %d sec\n", min
      printf "  Max:    %d sec\n", max
    }'
  fi
done

echo ""
echo "CSV: ${SUMMARY_DIR}/results.csv"
echo "Done."
