#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TRACE_ROOT="${TRACE_ROOT:-/work/vcomp/load_traces}"
MANIFEST="${MANIFEST:-${TRACE_ROOT}/trace_matrix_manifest.tsv}"
DB_ROOT="${DB_ROOT:-/work/vcomp/exp/trace_baseline}"
LOG_ROOT="${LOG_ROOT:-${SCRIPT_DIR}/log_loads/trace_baseline_$(date '+%y%m%d_%H%M%S')}"
DB_BENCH="${DB_BENCH:-${SCRIPT_DIR}/../vcomp/db_bench}"
BG_JOBS="${BG_JOBS:-48}"
RESUME="${RESUME:-0}"

SUMMARY="${LOG_ROOT}/summary.tsv"
RUN_LOG="${LOG_ROOT}/run.log"

mkdir -p "${LOG_ROOT}" "${DB_ROOT}"

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "${RUN_LOG}"
}

if [[ ! -x "${DB_BENCH}" ]]; then
  log "ERROR: db_bench not executable: ${DB_BENCH}"
  exit 1
fi
if [[ ! -f "${MANIFEST}" ]]; then
  log "ERROR: manifest not found: ${MANIFEST}"
  exit 1
fi
if pgrep -x db_bench >/dev/null 2>&1; then
  log "ERROR: another db_bench is running. Stop it before baseline loading."
  exit 1
fi

printf 'case_id\tsize_gb\tkv_label\tkey_size\tvalue_size\tdistribution\tunique_ratio\tzipf_alpha\tstatus\telapsed_sec\tbench_sec\tbench_ops_sec\tdb_size\ttrace_path\tlog_dir\tdb_dir\n' > "${SUMMARY}"

tail -n +2 "${MANIFEST}" | while IFS=$'\t' read -r case_id size_gb kv_label key_size value_size distribution unique_ratio zipf_alpha num_records key_domain unique_count trace_path expected_bytes status; do
  if [[ "${status}" != "ok" ]]; then
    log "SKIP ${case_id}: trace status=${status}"
    continue
  fi

  run_dir="${LOG_ROOT}/${case_id}"
  db_dir="${DB_ROOT}/${case_id}"
  if [[ "${RESUME}" == "1" && -f "${run_dir}/raw/elapsed_sec.txt" ]]; then
    log "SKIP ${case_id}: already has elapsed_sec"
    continue
  fi
  if [[ -d "${run_dir}" || -d "${db_dir}" ]]; then
    log "ERROR: existing output for ${case_id}: ${run_dir} ${db_dir}"
    exit 1
  fi

  mkdir -p "${run_dir}/raw" "${db_dir}"
  cmd=(
    "${DB_BENCH}"
    --statistics=1
    --stats_interval_seconds=60
    --stats_per_interval=1
    --report_interval_seconds=10
    --report_file="${run_dir}/report.rep"
    --enable_index_compression=false
    --bloom_bits=10
    --disable_wal=true
    --max_background_jobs="${BG_JOBS}"
    --num="${num_records}"
    --key_size="${key_size}"
    --value_size="${value_size}"
    --threads=1
    --memtablerep=vector
    --seed=12345678
    --db="${db_dir}"
    --use_direct_reads=true
    --use_direct_io_for_flush_and_compaction=true
    --compression_type=none
    --benchmarks=baseload,flush,compact0,waitforcompaction,stats,levelstats
    --load_trace_file="${trace_path}"
  )

  { printf '#!/usr/bin/env bash\n'; printf '%q ' "${cmd[@]}"; echo; } > "${run_dir}/raw/load_cmd.sh"
  chmod +x "${run_dir}/raw/load_cmd.sh"

  log "BEGIN ${case_id}"
  sync
  echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null 2>&1 || true
  date +%s > "${run_dir}/raw/start_epoch.txt"
  set +e
  "${cmd[@]}" > "${run_dir}/bench.out" 2>&1
  exit_code=$?
  set -e
  date +%s > "${run_dir}/raw/end_epoch.txt"
  elapsed=$(( $(<"${run_dir}/raw/end_epoch.txt") - $(<"${run_dir}/raw/start_epoch.txt") ))
  echo "${elapsed}" > "${run_dir}/raw/elapsed_sec.txt"

  status_out="ok"
  if [[ "${exit_code}" -ne 0 ]]; then
    status_out="failed:${exit_code}"
  fi
  bench_sec="$(awk '/^baseload[[:space:]]*:/ { for (i = 1; i <= NF; i++) if ($i == "seconds") sec = $(i - 1) } END { print sec }' "${run_dir}/bench.out")"
  bench_ops="$(awk '/^baseload[[:space:]]*:/ { for (i = 1; i <= NF; i++) if ($i == "ops/sec") ops = $(i - 1) } END { print ops }' "${run_dir}/bench.out")"
  db_size="$(du -sh "${db_dir}" 2>/dev/null | cut -f1 || true)"

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${case_id}" "${size_gb}" "${kv_label}" "${key_size}" "${value_size}" \
    "${distribution}" "${unique_ratio}" "${zipf_alpha}" "${status_out}" \
    "${elapsed}" "${bench_sec}" "${bench_ops}" "${db_size}" "${trace_path}" \
    "${run_dir}" "${db_dir}" >> "${SUMMARY}"
  log "END ${case_id} status=${status_out} elapsed=${elapsed}s db=${db_size}"

  if [[ "${exit_code}" -ne 0 ]]; then
    exit "${exit_code}"
  fi
done

log "Summary: ${SUMMARY}"
