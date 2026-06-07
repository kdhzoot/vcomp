#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOAD_SH="${SCRIPT_DIR}/load.sh"

RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
SIZES_GB_STR="${SIZES_GB:-500 1000 2000 4000 8000}"
SYSTEMS_STR="${SYSTEMS:-baseline}"
RESUME="${RESUME:-0}"

EXP_DB_ROOT="${EXP_DB_ROOT:-/work/vcomp/exp/scaling}"
LOG_ROOT="${LOG_ROOT:-${SCRIPT_DIR}/log_loads/exp_${RUN_ID}/scaling_91b_none}"
SUMMARY_FILE="${LOG_ROOT}/scaling_91b_summary.tsv"
RUN_LOG="${LOG_ROOT}/run.log"

ROCKSDB_BENCH="${ROCKSDB_BENCH:-${SCRIPT_DIR}/../rocksdb/db_bench}"
VCOMP_BENCH="${VCOMP_BENCH:-${SCRIPT_DIR}/../vcomp/db_bench}"

KEY_SIZE="${KEY_SIZE:-48}"
VALUE_SIZE="${VALUE_SIZE:-43}"
COMPRESSION_TYPE="${COMPRESSION_TYPE:-none}"
BG_JOBS="${BG_JOBS:-48}"
DISKSTAT_DEV="${DISKSTAT_DEV:-md0}"

VCOMP_REGISTER_BATCH_MAX="${VCOMP_REGISTER_BATCH_MAX:-256}"
VCOMP_VISIBLE_L0_BATCH_MB="${VCOMP_VISIBLE_L0_BATCH_MB:-0}"
VCOMP_BG_COMMIT_BATCH_MAX="${VCOMP_BG_COMMIT_BATCH_MAX:-16}"
VCOMP_BG_COMMIT_DELAY_US="${VCOMP_BG_COMMIT_DELAY_US:-100}"

mkdir -p "${LOG_ROOT}" "${EXP_DB_ROOT}"

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "${RUN_LOG}"
}

disk_written_sectors() {
  local diskstats_file="$1"
  awk -v dev="${DISKSTAT_DEV}" '$3 == dev { print $10; found = 1 } END { if (!found) print "" }' "${diskstats_file}"
}

bench_seconds() {
  local bench_out="$1"
  awk '/^fill(random|virtual)[[:space:]]*:/ { for (i = 1; i <= NF; i++) if ($i == "seconds") sec = $(i - 1) } END { print sec }' "${bench_out}"
}

bench_ops() {
  local bench_out="$1"
  awk '/^fill(random|virtual)[[:space:]]*:/ { for (i = 1; i <= NF; i++) if ($i == "ops/sec") ops = $(i - 1) } END { print ops }' "${bench_out}"
}

bench_name() {
  local bench_out="$1"
  awk '/^fill(random|virtual)[[:space:]]*:/ { sub(":", "", $1); print $1; exit }' "${bench_out}"
}

ingest_gb() {
  grep -E '^Cumulative writes:' "$1" | tail -1 | sed -n 's/.*ingest: \([0-9.]*\) GB.*/\1/p' || true
}

compaction_gb() {
  grep -E '^Cumulative compaction:' "$1" | tail -1 | awk '{ print $3 }' || true
}

write_amp() {
  grep -E '^ Sum[[:space:]]' "$1" | tail -1 | awk '{ print $13 }' || true
}

append_summary() {
  local system="$1"
  local size_gb="$2"
  local status="$3"
  local run_dir="$4"
  local db_dir="$5"
  local bench_out="${run_dir}/bench.out"
  local elapsed_sec=""
  local fill_sec=""
  local fill_ops=""
  local fill_bench=""
  local db_size=""
  local total_write_gb=""
  local ingest=""
  local comp_gb=""
  local wamp=""

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
    fill_bench="$(bench_name "${bench_out}")"
    fill_sec="$(bench_seconds "${bench_out}")"
    fill_ops="$(bench_ops "${bench_out}")"
    ingest="$(ingest_gb "${bench_out}")"
    comp_gb="$(compaction_gb "${bench_out}")"
    wamp="$(write_amp "${bench_out}")"
  fi

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${system}" "${size_gb}" "91B" "${KEY_SIZE}" "${VALUE_SIZE}" "${COMPRESSION_TYPE}" \
    "1" "vector" "${status}" "${elapsed_sec}" "${fill_bench}" "${fill_sec}" "${fill_ops}" \
    "${db_size}" "${DISKSTAT_DEV}" "${total_write_gb}" "${ingest}" "${comp_gb}" "${wamp}" \
    "${run_dir}" "${db_dir}" "${RUN_ID}" >> "${SUMMARY_FILE}"
}

summary_status() {
  local system_name="$1"
  local size_gb="$2"
  [[ -f "${SUMMARY_FILE}" ]] || return 0
  awk -F '\t' -v system_name="${system_name}" -v size_gb="${size_gb}" '
    NR > 1 && $1 == system_name && $2 == size_gb { status = $9 }
    END { if (status != "") print status }
  ' "${SUMMARY_FILE}"
}

db_bench_for_system() {
  case "$1" in
    baseline) printf '%s\n' "${ROCKSDB_BENCH}" ;;
    vcomp) printf '%s\n' "${VCOMP_BENCH}" ;;
    *) log "ERROR: unknown system '$1'"; exit 1 ;;
  esac
}

if pgrep -x db_bench >/dev/null 2>&1; then
  log "ERROR: db_bench is already running. Stop it before starting this sequential experiment."
  exit 1
fi
if [[ ! -x "${LOAD_SH}" ]]; then
  log "ERROR: load.sh is not executable: ${LOAD_SH}"
  exit 1
fi
for system in ${SYSTEMS_STR}; do
  bench="$(db_bench_for_system "${system}")"
  if [[ ! -x "${bench}" ]]; then
    log "ERROR: ${system} db_bench is not executable: ${bench}"
    exit 1
  fi
done

SUMMARY_HEADER='system	size_gb	kv	key_size	value_size	compression_type	threads	memtable	status	elapsed_sec	bench_name	bench_sec	bench_ops_sec	db_size	diskstat_dev	total_write_gb	ingest_gb	compaction_write_gb	compaction_wamp	log_dir	db_dir	run_id'
if [[ "${RESUME}" == "1" ]]; then
  if [[ ! -s "${SUMMARY_FILE}" ]]; then
    printf '%s\n' "${SUMMARY_HEADER}" > "${SUMMARY_FILE}"
  fi
else
  printf '%s\n' "${SUMMARY_HEADER}" > "${SUMMARY_FILE}"
fi

log "Starting 91B no-compression DB-size scaling experiment"
log "RUN_ID=${RUN_ID}"
log "SIZES_GB=${SIZES_GB_STR}"
log "SYSTEMS=${SYSTEMS_STR}"
log "RESUME=${RESUME}"
log "EXP_DB_ROOT=${EXP_DB_ROOT}"
log "LOG_ROOT=${LOG_ROOT}"
log "KEY_SIZE=${KEY_SIZE}"
log "VALUE_SIZE=${VALUE_SIZE}"
log "COMPRESSION_TYPE=${COMPRESSION_TYPE}"
log "BG_JOBS=${BG_JOBS}"
log "THREADS=1"
log "MEMTABLE_REP=vector"
log "ROCKSDB_BENCH=${ROCKSDB_BENCH}"
log "VCOMP_BENCH=${VCOMP_BENCH}"

for size_gb in ${SIZES_GB_STR}; do
  for system in ${SYSTEMS_STR}; do
    bench="$(db_bench_for_system "${system}")"
    run_dir="${LOG_ROOT}/${system}_${size_gb}gb_91b_none"
    db_dir="${EXP_DB_ROOT}/${system}_${size_gb}gb_91b_none_${RUN_ID}"

    if [[ "${RESUME}" == "1" ]]; then
      existing_status="$(summary_status "${system}" "${size_gb}")"
      if [[ "${existing_status}" == "ok" || "${existing_status}" == "ok_recovered" ]]; then
        log "SKIP ${system} ${size_gb}GB status=${existing_status}"
        continue
      fi
    fi

    if [[ -d "${run_dir}" ]]; then
      log "ERROR: run directory already exists: ${run_dir}"
      exit 1
    fi
    if [[ -d "${db_dir}" ]]; then
      log "ERROR: DB directory already exists: ${db_dir}"
      exit 1
    fi

    log "BEGIN ${system} ${size_gb}GB 91B none"
    set +e
    MODE="${system}" \
    DB_BENCH="${bench}" \
    TARGET_DB_GB="${size_gb}" \
    DB_ROOT="${EXP_DB_ROOT}" \
    DB_DIR="${db_dir}" \
    LOG_DIR="${run_dir}" \
    RUN_TAG="exp91b_${RUN_ID}_${system}_${size_gb}gb" \
    KEY_SIZE="${KEY_SIZE}" \
    VALUE_SIZE="${VALUE_SIZE}" \
    COMPRESSION_TYPE="${COMPRESSION_TYPE}" \
    BG_JOBS="${BG_JOBS}" \
    VCOMP_REGISTER_BATCH_MAX="${VCOMP_REGISTER_BATCH_MAX}" \
    VCOMP_VISIBLE_L0_BATCH_MB="${VCOMP_VISIBLE_L0_BATCH_MB}" \
    VCOMP_BG_COMMIT_BATCH_MAX="${VCOMP_BG_COMMIT_BATCH_MAX}" \
    VCOMP_BG_COMMIT_DELAY_US="${VCOMP_BG_COMMIT_DELAY_US}" \
    bash "${LOAD_SH}" 2>&1 | tee -a "${RUN_LOG}"
    exit_code=${PIPESTATUS[0]}
    set -e

    if [[ "${exit_code}" -eq 0 ]]; then
      log "END ${system} ${size_gb}GB status=ok"
      append_summary "${system}" "${size_gb}" "ok" "${run_dir}" "${db_dir}"
    else
      log "END ${system} ${size_gb}GB status=failed exit_code=${exit_code}"
      append_summary "${system}" "${size_gb}" "failed:${exit_code}" "${run_dir}" "${db_dir}"
      exit "${exit_code}"
    fi
  done
done

log "Finished 91B no-compression DB-size scaling experiment"
log "Summary: ${SUMMARY_FILE}"
