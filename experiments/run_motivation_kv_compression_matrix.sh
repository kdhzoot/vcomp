#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_BENCH="${DB_BENCH:-${SCRIPT_DIR}/../vcomp-prof/db_bench}"
DB_ROOT="${DB_ROOT:-/work/vcomp}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
TARGET_DB_GB="${TARGET_DB_GB:-500}"
KEY_SIZE="${KEY_SIZE:-24}"
BG_JOBS="${BG_JOBS:-48}"
DISKSTAT_DEV="${DISKSTAT_DEV:-md0}"
EXTRA_DB_BENCH_ARGS="${EXTRA_DB_BENCH_ARGS:-}"

EXP_DIR="${SCRIPT_DIR}/log_loads/motivation_kv_compression_${RUN_ID}"
SUMMARY_FILE="${EXP_DIR}/summary.tsv"
BREAKDOWN_ALL_FILE="${EXP_DIR}/compaction_breakdown_all.tsv"
BREAKDOWN_REP_FILE="${EXP_DIR}/compaction_breakdown_representative.tsv"
RUN_LOG="${EXP_DIR}/run.log"
IOSTAT_PID=""

cleanup_iostat() {
  if [[ -n "${IOSTAT_PID:-}" ]]; then
    kill "${IOSTAT_PID}" 2>/dev/null || true
    wait "${IOSTAT_PID}" 2>/dev/null || true
    IOSTAT_PID=""
  fi
}

trap cleanup_iostat EXIT
trap 'cleanup_iostat; exit 130' INT
trap 'cleanup_iostat; exit 143' TERM

if [[ -n "${CASES_SPEC:-}" ]]; then
  mapfile -t CASES <<< "${CASES_SPEC}"
else
  CASES=(
    "kv1000_nocompress 1000 none"
    "kv1000_snappy 1000 snappy"
    "kv91_nocompress 91 none"
    "kv91_snappy 91 snappy"
  )
fi

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "${RUN_LOG}"
}

disk_written_sectors() {
  local diskstats_file="$1"
  awk -v dev="${DISKSTAT_DEV}" '$3 == dev { print $10; found = 1 } END { if (!found) print "" }' "${diskstats_file}"
}

bench_seconds() {
  awk '/^fillrandom[[:space:]]*:/ { for (i = 1; i <= NF; i++) if ($i == "seconds") sec = $(i - 1) } END { print sec }' "$1"
}

bench_ops() {
  awk '/^fillrandom[[:space:]]*:/ { for (i = 1; i <= NF; i++) if ($i == "ops/sec") ops = $(i - 1) } END { print ops }' "$1"
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

extract_breakdown() {
  local case_name="$1"
  local run_dir="$2"
  local db_dir="$3"
  local bench_out="$4"
  local raw_dir="${run_dir}/raw"
  local raw_file="${raw_dir}/compaction_breakdown.raw"
  local tsv_file="${raw_dir}/compaction_breakdown.tsv"

  : > "${raw_file}"
  if compgen -G "${db_dir}/LOG*" >/dev/null; then
    rg --no-filename "VCOMP_PERF_COMPACTION_BREAKDOWN" "${db_dir}"/LOG* >> "${raw_file}" || true
  fi
  rg --no-filename "VCOMP_PERF_COMPACTION_BREAKDOWN" "${bench_out}" >> "${raw_file}" || true
  awk '!seen[$0]++' "${raw_file}" > "${raw_file}.dedup"
  mv "${raw_file}.dedup" "${raw_file}"

  python3 - "${case_name}" "${raw_file}" "${tsv_file}" \
    "${BREAKDOWN_ALL_FILE}" "${BREAKDOWN_REP_FILE}" <<'PY'
import os
import re
import sys

case_name, raw_file, case_tsv, all_tsv, rep_tsv = sys.argv[1:]
fields = [
    "case", "job", "cf", "start_level", "output_level", "reason",
    "input_files", "output_files", "input_bytes", "output_bytes", "status",
    "read_us", "write_us", "merge_us", "compress_us", "decompress_us",
    "sst_build_us", "other_us",
    "total_tracked_us", "prepare_us", "init_us", "run_subcompactions_us",
    "subcompaction_setup_us", "process_kv_us", "process_kv_excl_output_us",
    "open_output_us", "finish_output_us", "finalize_subcompaction_us",
    "collect_errors_us", "sync_dirs_us", "verify_output_us", "set_props_us",
    "aggregate_stats_us", "input_stats_us", "record_verify_us",
    "finalize_run_us", "install_stats_us", "install_edit_us",
    "log_and_apply_us", "install_total_us", "compaction_run_us",
    "compaction_cpu_us", "bytes_read_non_output", "bytes_read_output",
    "bytes_written", "compression",
]

def parse_int(row, key):
    try:
        return int(row.get(key, "0"))
    except ValueError:
        return 0

rows = []
if os.path.exists(raw_file):
    with open(raw_file, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if "VCOMP_PERF_COMPACTION_BREAKDOWN" not in line:
                continue
            row = dict(re.findall(r"([A-Za-z0-9_]+)=([^ \t\r\n]+)", line))
            if not row:
                continue
            row["case"] = case_name
            process = parse_int(row, "process_kv_us")
            open_us = parse_int(row, "open_output_us")
            finish_us = parse_int(row, "finish_output_us")
            row["process_kv_excl_output_us"] = str(max(0, process - open_us - finish_us))
            rows.append(row)

def write_rows(path, rows_to_write, append=False):
    need_header = (not append) or (not os.path.exists(path)) or os.path.getsize(path) == 0
    mode = "a" if append else "w"
    with open(path, mode, encoding="utf-8") as out:
        if need_header:
            out.write("\t".join(fields) + "\n")
        for row in rows_to_write:
            out.write("\t".join(row.get(field, "") for field in fields) + "\n")

write_rows(case_tsv, rows)
write_rows(all_tsv, rows, append=True)
if rows:
    representative = max(rows, key=lambda r: (parse_int(r, "input_bytes"), parse_int(r, "total_tracked_us")))
    write_rows(rep_tsv, [representative], append=True)
PY

  local jobs=0
  jobs="$(wc -l < "${raw_file}" | tr -d ' ')"
  printf '%s\t%s\t%s\n' "${jobs}" "${tsv_file}" "${raw_file}"
}

run_one() {
  local case_name="$1"
  local value_size="$2"
  local compression_type="$3"
  local nkeys=$((TARGET_DB_GB * 1024 * 1024 * 1024 / (KEY_SIZE + value_size)))
  local run_dir="${EXP_DIR}/${case_name}"
  local raw_dir="${run_dir}/raw"
  local db_dir="${DB_ROOT%/}/motivation_${case_name}_${TARGET_DB_GB}gb_${RUN_ID}"
  local rep_file="${run_dir}/report.rep"
  local out_file="${run_dir}/bench.out"
  local status rc start_ts end_ts elapsed db_size fill_sec fill_ops ingest comp_gb wamp
  local total_write_gb="" sectors_start="" sectors_end=""
  local extra_args=()
  local breakdown_jobs=0 breakdown_tsv="" breakdown_raw=""

  if [[ -n "${EXTRA_DB_BENCH_ARGS}" ]]; then
    read -r -a extra_args <<< "${EXTRA_DB_BENCH_ARGS}"
  fi
  if [[ -d "${run_dir}" ]]; then
    log "ERROR: run directory already exists: ${run_dir}"
    exit 1
  fi
  if [[ -d "${db_dir}" ]]; then
    log "ERROR: DB directory already exists: ${db_dir}"
    exit 1
  fi

  mkdir -p "${db_dir}" "${raw_dir}"
  local cmd=(
    "${DB_BENCH}"
    --statistics=1
    --stats_interval_seconds=60
    --stats_per_interval=1
    --report_interval_seconds=10
    --report_file="${rep_file}"
    --enable_index_compression=false
    --bloom_bits=10
    --disable_wal=true
    --max_background_jobs="${BG_JOBS}"
    --num="${nkeys}"
    --key_size="${KEY_SIZE}"
    --value_size="${value_size}"
    --seed=12345678
    --db="${db_dir}"
    --use_direct_reads=true
    --use_direct_io_for_flush_and_compaction=true
    --compression_type="${compression_type}"
    --benchmarks=fillrandom,flush,compact0,waitforcompaction,stats,levelstats
    --threads=1
    --memtablerep=vector
    "${extra_args[@]}"
  )

  { printf '#!/usr/bin/env bash\n'; printf '%q ' "${cmd[@]}"; echo; } > "${raw_dir}/load_cmd.sh"
  chmod +x "${raw_dir}/load_cmd.sh"

  log "BEGIN ${case_name} value_size=${value_size} compression=${compression_type}"
  {
    echo "=== ${case_name} | ${TARGET_DB_GB}GB | value_size=${value_size} | compression=${compression_type} | $(date) ==="
    echo "[RUN_CMD]"
    printf '%q ' "${cmd[@]}"
    echo
  } | tee "${out_file}" >/dev/null

  sync
  echo 3 | sudo -n tee /proc/sys/vm/drop_caches >/dev/null 2>&1 && \
    echo "[INFO] Page cache dropped" | tee -a "${out_file}" >/dev/null || \
    echo "[WARN] Cannot drop page cache" | tee -a "${out_file}" >/dev/null

  start_ts="$(date +%s)"
  echo "${start_ts}" > "${raw_dir}/start_epoch.txt"
  cat /proc/diskstats > "${raw_dir}/diskstats.start"
  cat /proc/stat > "${raw_dir}/procstat.start"
  if command -v iostat >/dev/null 2>&1; then
    iostat -dx 1 > "${raw_dir}/iostat.log" & IOSTAT_PID=$!
  fi

  set +e
  "${cmd[@]}" >> "${out_file}" 2>&1
  rc=$?
  set -e

  cleanup_iostat
  end_ts="$(date +%s)"
  elapsed=$((end_ts - start_ts))
  echo "${end_ts}" > "${raw_dir}/end_epoch.txt"
  echo "${elapsed}" > "${raw_dir}/elapsed_sec.txt"
  cat /proc/diskstats > "${raw_dir}/diskstats.end"
  cat /proc/stat > "${raw_dir}/procstat.end"

  sectors_start="$(disk_written_sectors "${raw_dir}/diskstats.start")"
  sectors_end="$(disk_written_sectors "${raw_dir}/diskstats.end")"
  if [[ -n "${sectors_start}" && -n "${sectors_end}" ]]; then
    total_write_gb="$(awk -v s="${sectors_start}" -v e="${sectors_end}" 'BEGIN { if (e >= s) printf "%.2f", (e - s) * 512 / 1024 / 1024 / 1024 }')"
  fi

  IFS=$'\t' read -r breakdown_jobs breakdown_tsv breakdown_raw < <(
    extract_breakdown "${case_name}" "${run_dir}" "${db_dir}" "${out_file}"
  )

  status=ok
  [[ "${rc}" -eq 0 ]] || status="failed:${rc}"
  db_size="$(du -sh "${db_dir}" 2>/dev/null | cut -f1 || true)"
  fill_sec="$(bench_seconds "${out_file}")"
  fill_ops="$(bench_ops "${out_file}")"
  ingest="$(ingest_gb "${out_file}")"
  comp_gb="$(compaction_gb "${out_file}")"
  wamp="$(write_amp "${out_file}")"

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${case_name}" "${TARGET_DB_GB}" "${KEY_SIZE}" "${value_size}" "${compression_type}" \
    "1" "vector" "${status}" "${elapsed}" "${fill_sec}" "${fill_ops}" \
    "${db_size}" "${DISKSTAT_DEV}" "${total_write_gb}" "${ingest}" "${comp_gb}" "${wamp}" \
    "${breakdown_jobs}" "${breakdown_tsv}" "${run_dir}" "${db_dir}" \
    >> "${SUMMARY_FILE}"

  {
    echo ""
    echo "=== Summary ==="
    echo "Case:           ${case_name}"
    echo "Status:         ${status}"
    echo "DB Size:        ${db_size}"
    echo "Keys:           ${nkeys}"
    echo "Elapsed:        ${elapsed} sec"
    echo "Breakdown jobs: ${breakdown_jobs}"
    echo "Breakdown TSV:  ${breakdown_tsv}"
    echo "Breakdown raw:  ${breakdown_raw}"
    echo "Log:            ${run_dir}"
    echo "DB:             ${db_dir}"
    echo "Exit code:      ${rc}"
  } | tee -a "${out_file}" >/dev/null

  log "END ${case_name} status=${status} elapsed=${elapsed}s fill_sec=${fill_sec} breakdown_jobs=${breakdown_jobs} db=${db_size}"
  [[ "${rc}" -eq 0 ]]
}

mkdir -p "${EXP_DIR}"

if pgrep -x db_bench >/dev/null 2>&1; then
  log "ERROR: db_bench is already running. Stop it before starting this sequential experiment."
  exit 1
fi
if [[ ! -x "${DB_BENCH}" ]]; then
  log "ERROR: vcomp-prof db_bench not executable: ${DB_BENCH}"
  exit 1
fi

printf 'case\ttarget_db_gb\tkey_size\tvalue_size\tcompression_type\tthreads\tmemtable\tstatus\telapsed_sec\tfillrandom_sec\tfillrandom_ops_sec\tdb_size\tdiskstat_dev\ttotal_write_gb\tingest_gb\tcompaction_write_gb\tcompaction_wamp\tbreakdown_jobs\tbreakdown_tsv\tlog_dir\tdb_dir\n' > "${SUMMARY_FILE}"
: > "${BREAKDOWN_ALL_FILE}"
: > "${BREAKDOWN_REP_FILE}"

log "Starting motivation KV/compression matrix with compaction breakdown extraction"
log "RUN_ID=${RUN_ID}"
log "TARGET_DB_GB=${TARGET_DB_GB}"
log "DB_BENCH=${DB_BENCH}"
log "DB_ROOT=${DB_ROOT}"
log "BG_JOBS=${BG_JOBS}"
log "THREADS=1"
log "MEMTABLE_REP=vector"
log "EXTRA_DB_BENCH_ARGS=${EXTRA_DB_BENCH_ARGS}"

for spec in "${CASES[@]}"; do
  read -r case_name value_size compression_type <<< "${spec}"
  run_one "${case_name}" "${value_size}" "${compression_type}"
done

log "Finished motivation KV/compression matrix"
log "Summary: ${SUMMARY_FILE}"
log "All compaction breakdowns: ${BREAKDOWN_ALL_FILE}"
log "Representative compaction breakdowns: ${BREAKDOWN_REP_FILE}"
