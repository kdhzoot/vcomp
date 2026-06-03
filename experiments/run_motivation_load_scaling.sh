#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOAD_SH="${SCRIPT_DIR}/load.sh"
DB_BENCH="${DB_BENCH:-${SCRIPT_DIR}/../rocksdb/db_bench}"

DB_ROOT="${DB_ROOT:-/work/vcomp}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
SIZES_GB_STR="${SIZES_GB:-500 1000 2000 4000 8000}"
BG_JOBS="${BG_JOBS:-48}"
DISKSTAT_DEV="${DISKSTAT_DEV:-md0}"

EXP_DIR="${SCRIPT_DIR}/log_loads/motivation_load_scaling_${RUN_ID}"
SUMMARY_FILE="${EXP_DIR}/summary.tsv"
RUN_LOG="${EXP_DIR}/run.log"

mkdir -p "${EXP_DIR}"

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "${RUN_LOG}"
}

if pgrep -x db_bench >/dev/null 2>&1; then
  log "ERROR: db_bench is already running. Stop it before starting this sequential experiment."
  exit 1
fi

if [[ ! -x "${LOAD_SH}" ]]; then
  log "ERROR: load.sh is not executable: ${LOAD_SH}"
  exit 1
fi
if [[ ! -x "${DB_BENCH}" ]]; then
  log "ERROR: clean RocksDB db_bench is not executable: ${DB_BENCH}"
  exit 1
fi

printf 'size_gb\tstatus\telapsed_sec\tbench_sec\tdb_size\tdiskstat_dev\ttotal_write_gb\tingest_gb\tcompaction_write_gb\tcompaction_wamp\tlog_dir\tdb_dir\n' > "${SUMMARY_FILE}"

extract_metric() {
  local bench_out="$1"
  local pattern="$2"
  grep -E "${pattern}" "${bench_out}" | tail -1 || true
}

disk_written_sectors() {
  local diskstats_file="$1"
  awk -v dev="${DISKSTAT_DEV}" '$3 == dev { print $10; found = 1 } END { if (!found) print "" }' "${diskstats_file}"
}

append_summary() {
  local size_gb="$1"
  local status="$2"
  local run_dir="$3"
  local db_dir="$4"
  local elapsed_sec=""
  local bench_sec=""
  local db_size=""
  local total_write_gb=""
  local compaction_write_gb=""
  local ingest_gb=""
  local compaction_wamp=""
  local bench_out="${run_dir}/bench.out"

  [[ -f "${run_dir}/raw/elapsed_sec.txt" ]] && elapsed_sec="$(<"${run_dir}/raw/elapsed_sec.txt")"
  [[ -d "${db_dir}" ]] && db_size="$(du -sh "${db_dir}" 2>/dev/null | cut -f1 || true)"
  if [[ -f "${run_dir}/raw/diskstats.start" && -f "${run_dir}/raw/diskstats.end" ]]; then
    local sectors_start=""
    local sectors_end=""
    sectors_start="$(disk_written_sectors "${run_dir}/raw/diskstats.start")"
    sectors_end="$(disk_written_sectors "${run_dir}/raw/diskstats.end")"
    if [[ -n "${sectors_start}" && -n "${sectors_end}" ]]; then
      total_write_gb="$(awk -v s="${sectors_start}" -v e="${sectors_end}" 'BEGIN { if (e >= s) printf "%.2f", (e - s) * 512 / 1024 / 1024 / 1024 }')"
    fi
  fi

  if [[ -f "${bench_out}" ]]; then
    bench_sec="$(awk '/^fillrandom[[:space:]]*:/ { for (i = 1; i <= NF; i++) if ($i == "seconds") sec = $(i - 1) } END {print sec}' "${bench_out}")"
    compaction_write_gb="$(extract_metric "${bench_out}" '^Cumulative compaction:' | awk '{print $3}')"
    ingest_gb="$(extract_metric "${bench_out}" '^Cumulative writes:' | sed -n 's/.*ingest: \([0-9.]*\) GB.*/\1/p')"
    compaction_wamp="$(grep -E '^ Sum[[:space:]]' "${bench_out}" | tail -1 | awk '{print $13}')"
  fi

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${size_gb}" "${status}" "${elapsed_sec}" "${bench_sec}" "${db_size}" \
    "${DISKSTAT_DEV}" "${total_write_gb}" "${ingest_gb}" \
    "${compaction_write_gb}" "${compaction_wamp}" "${run_dir}" "${db_dir}" >> "${SUMMARY_FILE}"
}

log "Starting motivation load-scaling experiment"
log "RUN_ID=${RUN_ID}"
log "DB_ROOT=${DB_ROOT}"
log "SIZES_GB=${SIZES_GB_STR}"
log "BG_JOBS=${BG_JOBS}"
log "DB_BENCH=${DB_BENCH}"
log "DISKSTAT_DEV=${DISKSTAT_DEV}"

for size_gb in ${SIZES_GB_STR}; do
  run_dir="${EXP_DIR}/baseline_${size_gb}gb"
  db_dir="${DB_ROOT%/}/motivation_baseline_${size_gb}gb_${RUN_ID}"

  if [[ -d "${run_dir}" ]]; then
    log "ERROR: run directory already exists: ${run_dir}"
    exit 1
  fi
  if [[ -d "${db_dir}" ]]; then
    log "ERROR: DB directory already exists: ${db_dir}"
    exit 1
  fi

  log "BEGIN baseline ${size_gb}GB"
  set +e
  MODE=baseline \
  DB_BENCH="${DB_BENCH}" \
  TARGET_DB_GB="${size_gb}" \
  DB_ROOT="${DB_ROOT}" \
  DB_DIR="${db_dir}" \
  LOG_DIR="${run_dir}" \
  RUN_TAG="motivation_${RUN_ID}_${size_gb}gb" \
  BG_JOBS="${BG_JOBS}" \
  bash "${LOAD_SH}" 2>&1 | tee -a "${RUN_LOG}"
  exit_code=${PIPESTATUS[0]}
  set -e

  if [[ "${exit_code}" -eq 0 ]]; then
    log "END baseline ${size_gb}GB status=ok"
    append_summary "${size_gb}" "ok" "${run_dir}" "${db_dir}"
  else
    log "END baseline ${size_gb}GB status=failed exit_code=${exit_code}"
    append_summary "${size_gb}" "failed:${exit_code}" "${run_dir}" "${db_dir}"
    exit "${exit_code}"
  fi
done

log "Finished motivation load-scaling experiment"
log "Summary: ${SUMMARY_FILE}"
