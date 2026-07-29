#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

TRACE_ROOT="${TRACE_ROOT:-${VCOMP_DB_ROOT}/load_traces}"
MANIFEST="${MANIFEST:-${TRACE_ROOT}/trace_matrix_manifest.tsv}"
DB_ROOT="${DB_ROOT:-${VCOMP_DB_ROOT}/exp/trace_vcomp}"
LOG_ROOT="${LOG_ROOT:-${ARTIFACT_ROOT}/log_loads/trace_vcomp_$(date '+%y%m%d_%H%M%S')}"
DB_BENCH="${DB_BENCH:-${VCOMP_DB_BENCH}}"
BG_JOBS="${BG_JOBS:-48}"
RESUME="${RESUME:-0}"

PLR_ERROR_BOUND="${PLR_ERROR_BOUND:-8}"
MEMTABLE_FLUSH_MB="${MEMTABLE_FLUSH_MB:-64}"
VCOMP_REGISTER_BATCH_MAX="${VCOMP_REGISTER_BATCH_MAX:-256}"
VCOMP_VISIBLE_L0_BATCH_MB="${VCOMP_VISIBLE_L0_BATCH_MB:-0}"
VCOMP_LOG_APPLY_TIMING="${VCOMP_LOG_APPLY_TIMING:-false}"
VCOMP_PHASE1_SHARDS="${VCOMP_PHASE1_SHARDS:-8}"
VCOMP_MATERIALIZE_WORKERS="${VCOMP_MATERIALIZE_WORKERS:-48}"
COMPRESSION_TYPE="${COMPRESSION_TYPE:-none}"

SUMMARY="${LOG_ROOT}/summary.tsv"
RUN_LOG="${LOG_ROOT}/run.log"

mkdir -p "${LOG_ROOT}" "${DB_ROOT}"

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "${RUN_LOG}"
}

extract_peak_rss_kb() {
  local time_out="$1"
  awk -F: '/Maximum resident set size/ {gsub(/^[ \t]+/, "", $2); print $2}' "${time_out}" 2>/dev/null || true
}

rss_kb_to_gb() {
  local rss_kb="$1"
  awk -v kb="${rss_kb}" 'BEGIN { if (kb != "") printf "%.3f", kb / 1024 / 1024 }'
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
  log "ERROR: another db_bench is running. Stop it before vcomp trace loading."
  exit 1
fi

printf 'case_id\tsize_gb\tkv_label\tkey_size\tvalue_size\tdistribution\tunique_ratio\tzipf_alpha\tstatus\telapsed_sec\tpeak_rss_kb\tpeak_rss_gb\tbench_sec\tbench_ops_sec\tdb_size\ttrace_path\tlog_dir\tdb_dir\n' > "${SUMMARY}"

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
  time_out="${run_dir}/raw/time.out"
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
    --compression_type="${COMPRESSION_TYPE}"
    --benchmarks=fillvirtual,flush,compact0,waitforcompaction,stats,levelstats
    --use_virtual_compaction=true
    --plr_error_bound="${PLR_ERROR_BOUND}"
    --memtable_flush_size="${MEMTABLE_FLUSH_MB}"
    --vcomp_register_batch_max="${VCOMP_REGISTER_BATCH_MAX}"
    --vcomp_visible_l0_batch_mb="${VCOMP_VISIBLE_L0_BATCH_MB}"
    --vcomp_log_apply_timing="${VCOMP_LOG_APPLY_TIMING}"
    --vcomp_phase1_shards="${VCOMP_PHASE1_SHARDS}"
    --vcomp_materialize_workers="${VCOMP_MATERIALIZE_WORKERS}"
    --load_trace_file="${trace_path}"
  )

  { printf '#!/usr/bin/env bash\n'; printf '%q ' "${cmd[@]}"; echo; } > "${run_dir}/raw/load_cmd.sh"
  chmod +x "${run_dir}/raw/load_cmd.sh"

  log "BEGIN ${case_id}"
  sync
  echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null 2>&1 || true
  date +%s > "${run_dir}/raw/start_epoch.txt"
  set +e
  /usr/bin/time -v -o "${time_out}" "${cmd[@]}" > "${run_dir}/bench.out" 2>&1
  exit_code=$?
  set -e
  date +%s > "${run_dir}/raw/end_epoch.txt"
  elapsed=$(( $(<"${run_dir}/raw/end_epoch.txt") - $(<"${run_dir}/raw/start_epoch.txt") ))
  echo "${elapsed}" > "${run_dir}/raw/elapsed_sec.txt"
  peak_rss_kb="$(extract_peak_rss_kb "${time_out}")"
  peak_rss_gb="$(rss_kb_to_gb "${peak_rss_kb}")"
  echo "${peak_rss_kb}" > "${run_dir}/raw/peak_rss_kb.txt"
  echo "${peak_rss_gb}" > "${run_dir}/raw/peak_rss_gb.txt"

  status_out="ok"
  if [[ "${exit_code}" -ne 0 ]]; then
    status_out="failed:${exit_code}"
  fi
  bench_sec="$(awk '/^fillvirtual[[:space:]]*:/ { for (i = 1; i <= NF; i++) if ($i == "seconds") sec = $(i - 1) } END { print sec }' "${run_dir}/bench.out")"
  bench_ops="$(awk '/^fillvirtual[[:space:]]*:/ { for (i = 1; i <= NF; i++) if ($i == "ops/sec") ops = $(i - 1) } END { print ops }' "${run_dir}/bench.out")"
  db_size="$(du -sh "${db_dir}" 2>/dev/null | cut -f1 || true)"
  if [[ "${exit_code}" -eq 0 && -f "${db_dir}/LOG" ]]; then
    python3 "${EXPERIMENT_ROOT}/analysis/extract_write_stats.py" \
      --system vcomp \
      --case-id "${case_id}" \
      --log "${db_dir}/LOG" \
      --out-dir "${run_dir}/write_stats" \
      >> "${run_dir}/raw/write_stats_extract.log" 2>&1 || \
      log "WARN ${case_id}: write stats extraction failed"
  fi

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${case_id}" "${size_gb}" "${kv_label}" "${key_size}" "${value_size}" \
    "${distribution}" "${unique_ratio}" "${zipf_alpha}" "${status_out}" \
    "${elapsed}" "${peak_rss_kb}" "${peak_rss_gb}" "${bench_sec}" "${bench_ops}" "${db_size}" "${trace_path}" \
    "${run_dir}" "${db_dir}" >> "${SUMMARY}"
  log "END ${case_id} status=${status_out} elapsed=${elapsed}s db=${db_size}"

  if [[ "${exit_code}" -ne 0 ]]; then
    exit "${exit_code}"
  fi
done

log "Summary: ${SUMMARY}"
