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
require_env RUNS TARGET_DB_GB DB_ROOT
require_positive_uint RUNS
require_positive_uint TARGET_DB_GB

MODES="${MODES:-baseline,vcomp}"
IFS=',' read -r -a mode_arr <<< "${MODES}"
for mode in "${mode_arr[@]}"; do
  [[ "${mode}" == "baseline" || "${mode}" == "vcomp" ]] ||
    die "MODES accepts only baseline and vcomp: ${mode}"
done

RUN_TS="$(date '+%y%m%d_%H%M%S')"
BATCH_NAME="${BATCH_NAME:-${RUN_TS}_${TARGET_DB_GB}gb_x${RUNS}}"
SUMMARY_DIR="${LOG_ROOT:-${ARTIFACT_ROOT}/log_batch/${BATCH_NAME}}"
BATCH_DB_DIR="${DB_ROOT}/${BATCH_NAME}"
CSV="${SUMMARY_DIR}/results.csv"
mkdir -p "${SUMMARY_DIR}" "${BATCH_DB_DIR}"
if [[ ! -s "${CSV}" ]]; then
  echo "mode,run_id,threads,memtable,elapsed_sec,peak_rss_kb,peak_rss_gb,db_size" > "${CSV}"
fi

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

append_result() {
  local mode="$1" run_id="$2" db_dir="$3" raw_dir="$4"
  if awk -F, -v mode="${mode}" -v run_id="${run_id}" \
      'NR > 1 && $1 == mode && $2 == run_id { found=1 } END { exit !found }' "${CSV}"; then
    return 0
  fi

  local elapsed peak_rss_kb peak_rss_gb db_size
  [[ -f "${raw_dir}/elapsed_sec.txt" ]] ||
    die "Cannot resume ${mode}_run${run_id}: missing ${raw_dir}/elapsed_sec.txt"
  elapsed="$(<"${raw_dir}/elapsed_sec.txt")"
  peak_rss_kb="$(read_peak_rss_kb "${raw_dir}")"
  peak_rss_gb="$(rss_kb_to_gb "${peak_rss_kb}")"
  db_size="$(du -sh "${db_dir}" 2>/dev/null | cut -f1 || true)"
  echo "${mode},${run_id},1,vector,${elapsed},${peak_rss_kb},${peak_rss_gb},${db_size}" >> "${CSV}"
}

# ── Run function ──
run_one() {
  local mode="$1" run_id="$2"
  local db_dir="${BATCH_DB_DIR}/${mode}_run${run_id}"
  local label="${mode}_run${run_id}"
  local raw_dir="${SUMMARY_DIR}/${label}/raw"

  # Skip if DB already exists
  if [[ -d "${db_dir}" ]]; then
    append_result "${mode}" "${run_id}" "${db_dir}" "${raw_dir}"
    echo "[SKIP] ${label}: ${db_dir} already exists"
    return 0
  fi

  echo ""
  echo "────────────────────────────────────────"
  echo "[${label}] Starting ($(date))"
  echo "────────────────────────────────────────"

  LOG_DIR="${SUMMARY_DIR}/${label}" \
    DB_DIR="${db_dir}" MODE="${mode}" TARGET_DB_GB="${TARGET_DB_GB}" \
    DB_ROOT="${DB_ROOT}" \
    bash "${SCRIPT_DIR}/load.sh" 2>&1 | tail -20

  append_result "${mode}" "${run_id}" "${db_dir}" "${raw_dir}"
  echo "[${label}] Done"
}

# ── Main loop ──
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
  times="$(awk -F, -v mode="${mode}" '$1 == mode { print $5 }' "${CSV}")"
  count="$(awk -F, -v mode="${mode}" '$1 == mode { n++ } END { print n+0 }' "${CSV}")"
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
echo "CSV: ${CSV}"
echo "Done."
