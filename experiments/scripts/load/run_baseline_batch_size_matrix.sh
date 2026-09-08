#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

LOAD_SH="${SCRIPT_DIR}/load.sh"
DB_BENCH="${DB_BENCH:-${BASELINE_DB_BENCH}}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
TARGET_DB_GB="${TARGET_DB_GB:-500}"
BATCH_SIZES_STR="${BATCH_SIZES:-1 10 100 1000}"
KEY_SIZE="${KEY_SIZE:-24}"
VALUE_SIZE="${VALUE_SIZE:-1000}"
MEMTABLE_REP="vector"
BG_JOBS="${BG_JOBS:-48}"
DISKSTAT_DEV="${DISKSTAT_DEV:-md0}"
KEEP_DBS="${KEEP_DBS:-1}"

EXP_DB_ROOT="${EXP_DB_ROOT:-${VCOMP_DB_ROOT}/exp/baseline_batch_size_${TARGET_DB_GB}gb_${RUN_ID}}"
LOG_ROOT="${LOG_ROOT:-${ARTIFACT_ROOT}/log_loads/baseline_batch_size_${TARGET_DB_GB}gb_${RUN_ID}}"
SUMMARY_FILE="${LOG_ROOT}/summary.tsv"
RUN_LOG="${LOG_ROOT}/run.log"

require_executable "${LOAD_SH}" "load runner"
require_executable "${DB_BENCH}" "clean RocksDB db_bench"
require_positive_uint TARGET_DB_GB
require_positive_uint KEY_SIZE
require_positive_uint VALUE_SIZE
require_positive_uint BG_JOBS
[[ -n "${MEMTABLE_REP}" ]] || die "MEMTABLE_REP must not be empty"
[[ "${KEEP_DBS}" == "0" || "${KEEP_DBS}" == "1" ]] || die "KEEP_DBS must be 0 or 1"
require_no_db_bench "baseline batch-size matrix"
[[ ! -e "${LOG_ROOT}" ]] || die "Log output already exists: ${LOG_ROOT}"
[[ ! -e "${EXP_DB_ROOT}" ]] || die "DB output root already exists: ${EXP_DB_ROOT}"

mkdir -p "${LOG_ROOT}" "${EXP_DB_ROOT}"
printf '%s\n' \
  $'batch_size\tmemtable_rep\tstatus\telapsed_sec\tfillrandom_sec\tfillrandom_ops_sec\twrite_calls\tdb_write_sec\twrite_control_sec\tactive_db_write_sec\tbenchmark_side_sec\twrite_control_pct\tdelay_events\tstop_events\tdelay_calls\tavg_delay_ms\tdb_size\ttotal_write_gb\tingest_gb\tcompaction_read_gb\tcompaction_write_gb\tcompaction_wamp\tlog_dir\tdb_dir' \
  > "${SUMMARY_FILE}"

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "${RUN_LOG}"
}

disk_written_sectors() {
  local diskstats_file="$1"
  awk -v dev="${DISKSTAT_DEV}" \
    '$3 == dev { print $10; found = 1 } END { if (!found) print "" }' \
    "${diskstats_file}"
}

histogram_metric() {
  local bench_out="$1"
  local histogram="$2"
  local metric="$3"
  awk -v histogram="${histogram}" -v metric="${metric}" '
    $1 == histogram {
      for (i = 1; i <= NF; i++) {
        if ($i == metric) {
          value = $(i + 2)
        }
      }
    }
    END { print value }
  ' "${bench_out}"
}

for batch_size in ${BATCH_SIZES_STR}; do
  [[ "${batch_size}" =~ ^[1-9][0-9]*$ ]] || die "Invalid batch size: ${batch_size}"

  run_dir="${LOG_ROOT}/batch_${batch_size}"
  db_dir="${EXP_DB_ROOT}/batch_${batch_size}"
  log "BEGIN batch_size=${batch_size}"

  set +e
  MODE=baseline \
  DB_BENCH="${DB_BENCH}" \
  TARGET_DB_GB="${TARGET_DB_GB}" \
  DB_ROOT="${EXP_DB_ROOT}" \
  DB_DIR="${db_dir}" \
  LOG_DIR="${run_dir}" \
  RUN_TAG="baseline_batch_size_${batch_size}_${RUN_ID}" \
  KEY_SIZE="${KEY_SIZE}" \
  VALUE_SIZE="${VALUE_SIZE}" \
  BATCH_SIZE="${batch_size}" \
  MEMTABLE_REP="${MEMTABLE_REP}" \
  COMPRESSION_TYPE=none \
  BG_JOBS="${BG_JOBS}" \
  bash "${LOAD_SH}" 2>&1 | tee -a "${RUN_LOG}"
  rc=${PIPESTATUS[0]}
  set -e

  status="ok"
  if [[ "${rc}" -ne 0 ]]; then
    status="failed:${rc}"
  fi

  bench_out="${run_dir}/bench.out"
  elapsed_sec="$(<"${run_dir}/raw/elapsed_sec.txt")"
  fillrandom_sec="$(awk '
    /^fillrandom[[:space:]]*:/ {
      for (i = 1; i <= NF; i++) {
        if ($i == "seconds") sec = $(i - 1)
      }
    }
    END { print sec }
  ' "${bench_out}")"
  fillrandom_ops_sec="$(awk '
    /^fillrandom[[:space:]]*:/ {
      for (i = 1; i <= NF; i++) {
        if ($i == "ops/sec") ops = $(i - 1)
      }
    }
    END { print ops }
  ' "${bench_out}")"

  db_write_us="$(histogram_metric "${bench_out}" rocksdb.db.write.micros SUM)"
  write_calls="$(histogram_metric "${bench_out}" rocksdb.db.write.micros COUNT)"
  write_control_us="$(histogram_metric "${bench_out}" rocksdb.db.write.stall SUM)"
  delay_calls="$(histogram_metric "${bench_out}" rocksdb.db.write.stall COUNT)"
  read -r db_write_sec write_control_sec active_db_write_sec benchmark_side_sec \
    write_control_pct avg_delay_ms < <(
      awk -v fill="${fillrandom_sec}" -v db_us="${db_write_us:-0}" \
        -v wait_us="${write_control_us:-0}" -v calls="${delay_calls:-0}" '
        BEGIN {
          db = db_us / 1000000
          wait = wait_us / 1000000
          active = db - wait
          outside = fill - db
          wait_pct = fill > 0 ? 100 * wait / fill : 0
          avg_ms = calls > 0 ? wait_us / calls / 1000 : 0
          printf "%.6f %.6f %.6f %.6f %.3f %.6f\n",
                 db, wait, active, outside, wait_pct, avg_ms
        }
      '
    )

  stall_line="$(grep -E '^Write Stall \(count\): cf-' "${bench_out}" | tail -1 || true)"
  delay_events="$(sed -n 's/.*total-delays: \([0-9]*\).*/\1/p' <<< "${stall_line}")"
  stop_events="$(sed -n 's/.*total-stops: \([0-9]*\).*/\1/p' <<< "${stall_line}")"

  compaction_line="$(grep -E '^Cumulative compaction:' "${bench_out}" | tail -1 || true)"
  compaction_write_gb="$(awk '{ print $3 }' <<< "${compaction_line}")"
  compaction_read_gb="$(awk '{ print $9 }' <<< "${compaction_line}")"
  ingest_gb="$(grep -E '^Cumulative writes:' "${bench_out}" | tail -1 |
    sed -n 's/.*ingest: \([0-9.]*\) GB.*/\1/p')"
  compaction_wamp="$(grep -E '^ Sum[[:space:]]' "${bench_out}" | tail -1 |
    awk '{ print $13 }')"
  db_size="$(du -sh "${db_dir}" 2>/dev/null | cut -f1)"

  sectors_start="$(disk_written_sectors "${run_dir}/raw/diskstats.start")"
  sectors_end="$(disk_written_sectors "${run_dir}/raw/diskstats.end")"
  total_write_gb="$(awk -v start="${sectors_start}" -v end="${sectors_end}" '
    BEGIN {
      if (end >= start) {
        printf "%.2f", (end - start) * 512 / 1024 / 1024 / 1024
      }
    }
  ')"

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${batch_size}" "${MEMTABLE_REP}" "${status}" "${elapsed_sec}" "${fillrandom_sec}" \
    "${fillrandom_ops_sec}" "${write_calls}" "${db_write_sec}" \
    "${write_control_sec}" "${active_db_write_sec}" "${benchmark_side_sec}" \
    "${write_control_pct}" "${delay_events}" "${stop_events}" "${delay_calls}" \
    "${avg_delay_ms}" "${db_size}" "${total_write_gb}" "${ingest_gb}" \
    "${compaction_read_gb}" "${compaction_write_gb}" "${compaction_wamp}" \
    "${run_dir}" "${db_dir}" >> "${SUMMARY_FILE}"

  log "END batch_size=${batch_size} status=${status} elapsed=${elapsed_sec}s"
  if [[ "${status}" != "ok" ]]; then
    exit "${rc}"
  fi

  if [[ "${KEEP_DBS}" == "0" ]]; then
    rm -rf -- "${db_dir}"
    log "REMOVED DB batch_size=${batch_size}: ${db_dir}"
  fi
done

log "Finished baseline batch-size matrix"
log "Summary: ${SUMMARY_FILE}"
