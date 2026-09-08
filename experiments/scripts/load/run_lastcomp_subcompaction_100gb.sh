#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

LOAD_SH="${SCRIPT_DIR}/load.sh"
DB_BENCH="${DB_BENCH:-${VCOMP_DB_BENCH}}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
POLL_SECONDS="${POLL_SECONDS:-60}"
SETTLE_SECONDS="${SETTLE_SECONDS:-60}"
TARGET_DB_GB="${TARGET_DB_GB:-100}"
BG_JOBS="${BG_JOBS:-48}"
LASTCOMP_SUBCOMPACTIONS="${LASTCOMP_SUBCOMPACTIONS:-48}"
ALLOW_CONCURRENT_DB_BENCH="${ALLOW_CONCURRENT_DB_BENCH:-0}"

EXP_DIR="${ARTIFACT_ROOT}/log_loads/paper_lastcomp_subcomp_100gb_${RUN_ID}"
DB_BASE="${VCOMP_DB_ROOT%/}/exp/paper_lastcomp_subcomp_100gb/${RUN_ID}"
SUMMARY_FILE="${EXP_DIR}/summary.tsv"
RUN_LOG="${EXP_DIR}/run.log"

require_executable "${LOAD_SH}" "load runner"
require_executable "${DB_BENCH}" "RocksDB 10.10.1 db_bench"
require_positive_uint POLL_SECONDS
require_positive_uint SETTLE_SECONDS
require_positive_uint TARGET_DB_GB
require_positive_uint BG_JOBS
require_positive_uint LASTCOMP_SUBCOMPACTIONS
[[ ! -e "${EXP_DIR}" ]] || die "Experiment output exists: ${EXP_DIR}"
[[ ! -e "${DB_BASE}" ]] || die "Database output exists: ${DB_BASE}"
mkdir -p "${EXP_DIR}" "${DB_BASE}"

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "${RUN_LOG}"
}

wait_for_idle_db_bench() {
  if [[ "${ALLOW_CONCURRENT_DB_BENCH}" == "1" ]]; then
    log "WARNING: concurrent db_bench explicitly allowed; skipping idle wait"
    return 0
  fi
  while pgrep -x db_bench >/dev/null 2>&1; do
    log "Waiting: another db_bench is active"
    sleep "${POLL_SECONDS}"
  done
  log "No db_bench found; waiting ${SETTLE_SECONDS}s for storage to settle"
  sleep "${SETTLE_SECONDS}"
  if pgrep -x db_bench >/dev/null 2>&1; then
    log "A db_bench appeared during settling; returning to wait"
    wait_for_idle_db_bench
  fi
}

extract_seconds() {
  local label="$1"
  local file="$2"
  awk -v label="${label}" '
    $1 == label && $2 == ":" {
      for (i = 1; i <= NF; i++) {
        if ($i == "seconds") value = $(i - 1)
      }
    }
    END { print value }
  ' "${file}"
}

extract_subcompactions() {
  local file="$1"
  awk '
    /rocksdb.num.subcompactions.scheduled/ {
      for (i = 1; i <= NF; i++) {
        if ($i == "SUM" && $(i + 1) == ":") value = $(i + 2)
      }
    }
    END { if (value == "") value = 0; printf "%.0f", value }
  ' "${file}"
}

append_summary() {
  local system="$1"
  local subcompactions="$2"
  local run_dir="$3"
  local db_dir="$4"
  local bench_out="${run_dir}/bench.out"
  local elapsed=""
  local fillrandom=""
  local compact=""
  local actual_subcompactions=""
  local pending_bytes=""
  local final_db_size=""

  elapsed="$(<"${run_dir}/raw/elapsed_sec.txt")"
  fillrandom="$(extract_seconds fillrandom "${bench_out}")"
  compact="$(extract_seconds compact "${bench_out}")"
  actual_subcompactions="$(extract_subcompactions "${bench_out}")"
  pending_bytes="$(awk -F': ' '/^Estimated pending compaction bytes:/ {v=$2} END {print v}' "${bench_out}")"
  final_db_size="$(du -sb "${db_dir}" | awk '{print $1}')"

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${system}" "${TARGET_DB_GB}" "${subcompactions}" \
    "${actual_subcompactions}" "${elapsed}" "${fillrandom}" "${compact}" \
    "${pending_bytes}" "${final_db_size}" "${run_dir}" "${db_dir}" \
    >> "${SUMMARY_FILE}"
}

run_one() {
  local system="$1"
  local mode="$2"
  local subcompactions="$3"
  local run_dir="${EXP_DIR}/${system}"
  local db_dir="${DB_BASE}/${system}"

  log "BEGIN ${system}: mode=${mode} subcompactions=${subcompactions}"
  MODE="${mode}" \
  DB_BENCH="${DB_BENCH}" \
  TARGET_DB_GB="${TARGET_DB_GB}" \
  DB_ROOT="${DB_BASE}" \
  DB_DIR="${db_dir}" \
  LOG_DIR="${run_dir}" \
  BG_JOBS="${BG_JOBS}" \
  SUBCOMPACTIONS="${subcompactions}" \
  ALLOW_CONCURRENT_DB_BENCH="${ALLOW_CONCURRENT_DB_BENCH}" \
  VCOMP_DROP_PAGE_CACHE="$([[ "${ALLOW_CONCURRENT_DB_BENCH}" == "1" ]] && printf 0 || printf 1)" \
  KEY_SIZE=24 \
  VALUE_SIZE=1000 \
  BATCH_SIZE=1 \
  MEMTABLE_REP=vector \
  COMPRESSION_TYPE=none \
  bash "${LOAD_SH}" 2>&1 | tee -a "${RUN_LOG}"
  append_summary "${system}" "${subcompactions}" "${run_dir}" "${db_dir}"
  log "END ${system}"
}

log "Queued 100 GB baseline/last-comp subcompaction pilot"
log "RUN_ID=${RUN_ID}"
log "DB_BENCH=${DB_BENCH}"
log "ALLOW_CONCURRENT_DB_BENCH=${ALLOW_CONCURRENT_DB_BENCH}"
log "DB_BENCH_SHA256=$(sha256sum "${DB_BENCH}" | awk '{print $1}')"
log "VCOMP_COMMIT=$(git -C "${REPO_ROOT}" rev-parse HEAD)"
git -C "${REPO_ROOT}" status --short > "${EXP_DIR}/git_status.txt"
printf 'system\ttarget_gib\tconfigured_subcompactions\tactual_scheduled_subcompactions\telapsed_sec\tfillrandom_sec\tcompact_sec\tpending_compaction_bytes\tfinal_db_bytes\tlog_dir\tdb_dir\n' > "${SUMMARY_FILE}"

wait_for_idle_db_bench
run_one baseline baseline 1
run_one lastcomp_sub48 l0compact "${LASTCOMP_SUBCOMPACTIONS}"

log "Completed both runs"
log "Summary: ${SUMMARY_FILE}"
