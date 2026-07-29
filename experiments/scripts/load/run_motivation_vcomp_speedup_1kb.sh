#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"
LOAD_SH="${SCRIPT_DIR}/load.sh"

RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
SIZES_GB_STR="${SIZES_GB:-500 1000 2000 4000 8000}"
RESUME="${RESUME:-0}"

VCOMP_BENCH="${VCOMP_BENCH:-${VCOMP_DB_BENCH}}"
BASELINE_SUMMARY="${BASELINE_SUMMARY:-${ARTIFACT_ROOT}/log_loads/motivation_load_scaling_260602_cleanrocksdb_motivation/summary.tsv}"

EXP_DB_ROOT="${EXP_DB_ROOT:-${VCOMP_DB_ROOT}/exp/motivation_speedup_1kb}"
LOG_ROOT="${LOG_ROOT:-${ARTIFACT_ROOT}/log_loads/motivation_vcomp_speedup_1kb_${RUN_ID}}"
SUMMARY_FILE="${LOG_ROOT}/motivation_vcomp_speedup_summary.tsv"
RUN_LOG="${LOG_ROOT}/run.log"

KEY_SIZE="${KEY_SIZE:-24}"
VALUE_SIZE="${VALUE_SIZE:-1000}"
COMPRESSION_TYPE="${COMPRESSION_TYPE:-none}"
BG_JOBS="${BG_JOBS:-48}"
DISKSTAT_DEV="${DISKSTAT_DEV:-md0}"

VCOMP_REGISTER_BATCH_MAX="${VCOMP_REGISTER_BATCH_MAX:-256}"
VCOMP_VISIBLE_L0_BATCH_MB="${VCOMP_VISIBLE_L0_BATCH_MB:-0}"
VCOMP_LOG_APPLY_TIMING="${VCOMP_LOG_APPLY_TIMING:-false}"
VCOMP_SORT_DETAIL_TIMING="${VCOMP_SORT_DETAIL_TIMING:-false}"
VCOMP_PHASE1_SHARDS="${VCOMP_PHASE1_SHARDS:-8}"
VCOMP_MATERIALIZE_WORKERS="${VCOMP_MATERIALIZE_WORKERS:-48}"

mkdir -p "${LOG_ROOT}" "${EXP_DB_ROOT}"

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "${RUN_LOG}"
}

metric_from_tsv() {
  local file="$1"
  local match_col="$2"
  local match_value="$3"
  local metric_col="$4"
  awk -F '\t' \
    -v match_col="${match_col}" \
    -v match_value="${match_value}" \
    -v metric_col="${metric_col}" '
    NR == 1 {
      for (i = 1; i <= NF; i++) {
        header[$i] = i
      }
      next
    }
    header[match_col] && header[metric_col] &&
      $(header[match_col]) == match_value {
      print $(header[metric_col])
      exit
    }
  ' "${file}" 2>/dev/null || true
}

disk_written_sectors() {
  local diskstats_file="$1"
  awk -v dev="${DISKSTAT_DEV}" '$3 == dev { print $10; found = 1 } END { if (!found) print "" }' "${diskstats_file}"
}

read_peak_rss_kb() {
  local raw_dir="$1"
  if [[ -f "${raw_dir}/peak_rss_kb.txt" ]]; then
    cat "${raw_dir}/peak_rss_kb.txt"
  elif [[ -f "${raw_dir}/time.out" ]]; then
    awk -F: '/Maximum resident set size/ {gsub(/^[ \t]+/, "", $2); print $2}' "${raw_dir}/time.out" 2>/dev/null || true
  fi
}

rss_kb_to_gb() {
  local rss_kb="$1"
  awk -v kb="${rss_kb}" 'BEGIN { if (kb != "") printf "%.3f", kb / 1024 / 1024 }'
}

bench_seconds() {
  local bench_out="$1"
  awk '/^fillvirtual[[:space:]]*:/ { for (i = 1; i <= NF; i++) if ($i == "seconds") sec = $(i - 1) } END { print sec }' "${bench_out}"
}

bench_ops() {
  local bench_out="$1"
  awk '/^fillvirtual[[:space:]]*:/ { for (i = 1; i <= NF; i++) if ($i == "ops/sec") ops = $(i - 1) } END { print ops }' "${bench_out}"
}

extract_ingest_gb() {
  grep -E '^Cumulative writes:' "$1" | tail -1 | sed -n 's/.*ingest: \([0-9.]*\) GB.*/\1/p' || true
}

extract_compaction_gb() {
  grep -E '^Cumulative compaction:' "$1" | tail -1 | awk '{ print $3 }' || true
}

extract_write_amp() {
  grep -E '^ Sum[[:space:]]' "$1" | tail -1 | awk '{ print $13 }' || true
}

calc_speedup() {
  local baseline_sec="$1"
  local vcomp_sec="$2"
  awk -v b="${baseline_sec}" -v v="${vcomp_sec}" 'BEGIN { if (b > 0 && v > 0) printf "%.2f", b / v }'
}

summary_status() {
  local size_gb="$1"
  [[ -f "${SUMMARY_FILE}" ]] || return 0
  awk -F '\t' -v size_gb="${size_gb}" '
    NR > 1 && $1 == size_gb { status = $2 }
    END { if (status != "") print status }
  ' "${SUMMARY_FILE}"
}

append_summary() {
  local size_gb="$1"
  local status="$2"
  local run_dir="$3"
  local db_dir="$4"
  local bench_out="${run_dir}/bench.out"

  local baseline_status=""
  local baseline_elapsed=""
  local baseline_db_size=""
  local baseline_total_write=""
  local baseline_ingest=""
  local baseline_compaction=""
  local baseline_wamp=""

  baseline_status="$(metric_from_tsv "${BASELINE_SUMMARY}" size_gb "${size_gb}" status)"
  baseline_elapsed="$(metric_from_tsv "${BASELINE_SUMMARY}" size_gb "${size_gb}" elapsed_sec)"
  baseline_db_size="$(metric_from_tsv "${BASELINE_SUMMARY}" size_gb "${size_gb}" db_size)"
  baseline_total_write="$(metric_from_tsv "${BASELINE_SUMMARY}" size_gb "${size_gb}" total_write_gb)"
  baseline_ingest="$(metric_from_tsv "${BASELINE_SUMMARY}" size_gb "${size_gb}" ingest_gb)"
  baseline_compaction="$(metric_from_tsv "${BASELINE_SUMMARY}" size_gb "${size_gb}" compaction_write_gb)"
  baseline_wamp="$(metric_from_tsv "${BASELINE_SUMMARY}" size_gb "${size_gb}" compaction_wamp)"

  local vcomp_elapsed=""
  local vcomp_peak_rss_kb=""
  local vcomp_peak_rss_gb=""
  local vcomp_bench_sec=""
  local vcomp_ops=""
  local vcomp_db_size=""
  local vcomp_total_write=""
  local vcomp_ingest=""
  local vcomp_compaction=""
  local vcomp_wamp=""
  local speedup=""

  [[ -f "${run_dir}/raw/elapsed_sec.txt" ]] && vcomp_elapsed="$(<"${run_dir}/raw/elapsed_sec.txt")"
  vcomp_peak_rss_kb="$(read_peak_rss_kb "${run_dir}/raw")"
  vcomp_peak_rss_gb="$(rss_kb_to_gb "${vcomp_peak_rss_kb}")"
  [[ -d "${db_dir}" ]] && vcomp_db_size="$(du -sh "${db_dir}" 2>/dev/null | cut -f1 || true)"

  if [[ -f "${run_dir}/raw/diskstats.start" && -f "${run_dir}/raw/diskstats.end" ]]; then
    local sectors_start=""
    local sectors_end=""
    sectors_start="$(disk_written_sectors "${run_dir}/raw/diskstats.start")"
    sectors_end="$(disk_written_sectors "${run_dir}/raw/diskstats.end")"
    if [[ -n "${sectors_start}" && -n "${sectors_end}" ]]; then
      vcomp_total_write="$(awk -v s="${sectors_start}" -v e="${sectors_end}" 'BEGIN { if (e >= s) printf "%.2f", (e - s) * 512 / 1024 / 1024 / 1024 }')"
    fi
  fi

  if [[ -f "${bench_out}" ]]; then
    vcomp_bench_sec="$(bench_seconds "${bench_out}")"
    vcomp_ops="$(bench_ops "${bench_out}")"
    vcomp_ingest="$(extract_ingest_gb "${bench_out}")"
    vcomp_compaction="$(extract_compaction_gb "${bench_out}")"
    vcomp_wamp="$(extract_write_amp "${bench_out}")"
  fi

  speedup="$(calc_speedup "${baseline_elapsed}" "${vcomp_elapsed}")"

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${size_gb}" "${status}" "${baseline_status}" "${baseline_elapsed}" \
    "${vcomp_elapsed}" "${speedup}" "${baseline_db_size}" "${vcomp_db_size}" \
    "${baseline_total_write}" "${vcomp_total_write}" "${baseline_ingest}" \
    "${vcomp_ingest}" "${baseline_compaction}" "${vcomp_compaction}" \
    "${baseline_wamp}" "${vcomp_wamp}" "${vcomp_peak_rss_kb}" \
    "${vcomp_peak_rss_gb}" "${vcomp_bench_sec}" "${vcomp_ops}" \
    "${KEY_SIZE}" "${VALUE_SIZE}" "${COMPRESSION_TYPE}" \
    "${run_dir}" "${db_dir}" "${BASELINE_SUMMARY}" "${RUN_ID}" >> "${SUMMARY_FILE}"
}

if pgrep -x db_bench >/dev/null 2>&1; then
  log "ERROR: db_bench is already running. Stop it before starting this sequential experiment."
  exit 1
fi
if [[ ! -x "${LOAD_SH}" ]]; then
  log "ERROR: load.sh is not executable: ${LOAD_SH}"
  exit 1
fi
if [[ ! -x "${VCOMP_BENCH}" ]]; then
  log "ERROR: vcomp db_bench is not executable: ${VCOMP_BENCH}"
  exit 1
fi
if [[ ! -f "${BASELINE_SUMMARY}" ]]; then
  log "ERROR: baseline summary not found: ${BASELINE_SUMMARY}"
  exit 1
fi

SUMMARY_HEADER='size_gb	status	baseline_status	baseline_elapsed_sec	vcomp_elapsed_sec	speedup_x	baseline_db_size	vcomp_db_size	baseline_total_write_gb	vcomp_total_write_gb	baseline_ingest_gb	vcomp_ingest_gb	baseline_compaction_write_gb	vcomp_compaction_write_gb	baseline_compaction_wamp	vcomp_compaction_wamp	vcomp_peak_rss_kb	vcomp_peak_rss_gb	vcomp_bench_sec	vcomp_ops_sec	key_size	value_size	compression_type	log_dir	db_dir	baseline_summary	run_id'
if [[ "${RESUME}" == "1" ]]; then
  if [[ ! -s "${SUMMARY_FILE}" ]]; then
    printf '%s\n' "${SUMMARY_HEADER}" > "${SUMMARY_FILE}"
  fi
else
  printf '%s\n' "${SUMMARY_HEADER}" > "${SUMMARY_FILE}"
fi

log "Starting 1KB vcomp speedup experiment"
log "RUN_ID=${RUN_ID}"
log "SIZES_GB=${SIZES_GB_STR}"
log "EXP_DB_ROOT=${EXP_DB_ROOT}"
log "LOG_ROOT=${LOG_ROOT}"
log "BASELINE_SUMMARY=${BASELINE_SUMMARY}"
log "VCOMP_BENCH=${VCOMP_BENCH}"
log "KEY_SIZE=${KEY_SIZE}"
log "VALUE_SIZE=${VALUE_SIZE}"
log "COMPRESSION_TYPE=${COMPRESSION_TYPE}"
log "THREADS=1"
log "MEMTABLE_REP=vector"
log "BG_JOBS=${BG_JOBS}"
log "VCOMP_LOG_APPLY_TIMING=${VCOMP_LOG_APPLY_TIMING}"
log "VCOMP_SORT_DETAIL_TIMING=${VCOMP_SORT_DETAIL_TIMING}"
log "VCOMP_PHASE1_SHARDS=${VCOMP_PHASE1_SHARDS}"
log "VCOMP_MATERIALIZE_WORKERS=${VCOMP_MATERIALIZE_WORKERS}"

for size_gb in ${SIZES_GB_STR}; do
  if [[ "${RESUME}" == "1" ]]; then
    existing_status="$(summary_status "${size_gb}")"
    if [[ "${existing_status}" == "ok" ]]; then
      log "SKIP vcomp ${size_gb}GB status=ok"
      continue
    fi
  fi

  run_dir="${LOG_ROOT}/vcomp_${size_gb}gb_1kb_none"
  db_dir="${EXP_DB_ROOT}/vcomp_${size_gb}gb_1kb_none_${RUN_ID}"

  if [[ -d "${run_dir}" ]]; then
    log "ERROR: run directory already exists: ${run_dir}"
    exit 1
  fi
  if [[ -d "${db_dir}" ]]; then
    log "ERROR: DB directory already exists: ${db_dir}"
    exit 1
  fi

  log "BEGIN vcomp ${size_gb}GB 1KB none"
  set +e
  MODE=vcomp \
  DB_BENCH="${VCOMP_BENCH}" \
  TARGET_DB_GB="${size_gb}" \
  DB_ROOT="${EXP_DB_ROOT}" \
  DB_DIR="${db_dir}" \
  LOG_DIR="${run_dir}" \
  RUN_TAG="motivation_vcomp_speedup_1kb_${RUN_ID}_${size_gb}gb" \
  KEY_SIZE="${KEY_SIZE}" \
  VALUE_SIZE="${VALUE_SIZE}" \
  COMPRESSION_TYPE="${COMPRESSION_TYPE}" \
  BG_JOBS="${BG_JOBS}" \
  VCOMP_REGISTER_BATCH_MAX="${VCOMP_REGISTER_BATCH_MAX}" \
  VCOMP_VISIBLE_L0_BATCH_MB="${VCOMP_VISIBLE_L0_BATCH_MB}" \
  VCOMP_LOG_APPLY_TIMING="${VCOMP_LOG_APPLY_TIMING}" \
  VCOMP_SORT_DETAIL_TIMING="${VCOMP_SORT_DETAIL_TIMING}" \
  VCOMP_PHASE1_SHARDS="${VCOMP_PHASE1_SHARDS}" \
  VCOMP_MATERIALIZE_WORKERS="${VCOMP_MATERIALIZE_WORKERS}" \
  bash "${LOAD_SH}" 2>&1 | tee -a "${RUN_LOG}"
  exit_code=${PIPESTATUS[0]}
  set -e

  if [[ "${exit_code}" -eq 0 ]]; then
    log "END vcomp ${size_gb}GB status=ok"
    append_summary "${size_gb}" "ok" "${run_dir}" "${db_dir}"
  else
    log "END vcomp ${size_gb}GB status=failed exit_code=${exit_code}"
    append_summary "${size_gb}" "failed:${exit_code}" "${run_dir}" "${db_dir}"
    exit "${exit_code}"
  fi
done

log "Finished 1KB vcomp speedup experiment"
log "Summary: ${SUMMARY_FILE}"
