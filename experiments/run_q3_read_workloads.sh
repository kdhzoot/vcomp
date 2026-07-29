#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
MATRIX="${MATRIX:-${SCRIPT_DIR}/log_loads/q3_read_db_matrix.tsv}"
LOG_ROOT="${LOG_ROOT:-${SCRIPT_DIR}/log_runs/q3_flex_${RUN_ID}}"
SUMMARY="${LOG_ROOT}/q3_read_summary.tsv"
RUN_LOG="${LOG_ROOT}/run.log"

DB_BENCH="${DB_BENCH:-${SCRIPT_DIR}/../vcomp-prof/db_bench}"
TMP_ROOT="${TMP_ROOT:-/work/vcomp/exp/q3_read_tmp}"
DISKSTAT_DEV="${DISKSTAT_DEV:-md0}"

SYSTEMS="${SYSTEMS:-baseline vcomp}"
SIZES_GB="${SIZES_GB:-500 1000}"
KV_LABELS="${KV_LABELS:-91B 1024B}"
DISTRIBUTIONS="${DISTRIBUTIONS:-unique100 uniform50 zipf99_50}"
WORKLOADS="${WORKLOADS:-workloada workloadb workloadc workloadd workloade workloadf mixgraph}"

THREADS="${THREADS:-1}"
DURATION="${DURATION:-60}"
READS="${READS:-0}"
CACHE_PCT="${CACHE_PCT:-0}"
KEEP_RUN_DB="${KEEP_RUN_DB:-0}"

mkdir -p "${LOG_ROOT}" "${TMP_ROOT}"

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "${RUN_LOG}"
}

contains_word() {
  local needle="$1"
  local haystack="$2"
  [[ " ${haystack} " == *" ${needle} "* ]]
}

disk_line() {
  local file="$1"
  awk -v dev="${DISKSTAT_DEV}" '$3 == dev { print; found = 1 } END { if (!found) print "" }' "${file}"
}

disk_delta_summary() {
  local start_file="$1"
  local end_file="$2"
  local elapsed="$3"
  awk -v dev="${DISKSTAT_DEV}" -v elapsed="${elapsed}" '
    FNR == NR && $3 == dev {
      r0=$4; rs0=$6; rt0=$7; w0=$8; ws0=$10; wt0=$11
      next
    }
    FNR != NR && $3 == dev {
      r=$4-r0; rs=$6-rs0; rt=$7-rt0; w=$8-w0; ws=$10-ws0; wt=$11-wt0
      read_mb = rs * 512 / 1024 / 1024
      write_mb = ws * 512 / 1024 / 1024
      read_lat = r > 0 ? rt / r : 0
      write_lat = w > 0 ? wt / w : 0
      read_bw = elapsed > 0 ? read_mb / elapsed : 0
      write_bw = elapsed > 0 ? write_mb / elapsed : 0
      printf "%.0f\t%.0f\t%.2f\t%.2f\t%.3f\t%.3f\t%.2f\t%.2f",
             r, w, read_mb, write_mb, read_lat, write_lat, read_bw, write_bw
    }
  ' "${start_file}" "${end_file}"
}

stat_count() {
  local name="$1"
  local file="$2"
  awk -v name="${name}" '$0 ~ "^" name " " { print $(NF); found=1 } END { if (!found) print "" }' "${file}"
}

hist_value() {
  local name="$1"
  local pct="$2"
  local file="$3"
  awk -v name="${name}" -v pct="${pct}" '
    $0 ~ "^" name " " {
      for (i = 1; i <= NF; ++i) {
        if ($i == pct) {
          print $(i + 2)
          found = 1
          exit
        }
      }
    }
    END { if (!found) print "" }
  ' "${file}"
}

bench_line_metric() {
  local workload="$1"
  local file="$2"
  python3 - "$workload" "$file" <<'PY'
import re
import sys

workload, path = sys.argv[1], sys.argv[2]
text = open(path, errors="ignore").read().splitlines()
names = {
    "workloada": ["workloada"],
    "workloadb": ["workloadb"],
    "workloadc": ["workloadc"],
    "workloadd": ["workloadd"],
    "workloade": ["workloade"],
    "workloadf": ["workloadf"],
    "mixgraph": ["mixgraph"],
}[workload]
for line in text:
    stripped = line.strip()
    if any(stripped.startswith(name) for name in names):
        lat = re.search(r"([0-9.]+) micros/op", stripped)
        ops = re.search(r"([0-9.]+) ops/sec", stripped)
        print((lat.group(1) if lat else "") + "\t" + (ops.group(1) if ops else ""))
        break
else:
    print("\t")
PY
}

stage_db() {
  local src="$1"
  local dst="$2"
  mkdir -p "${dst}"
  # SST files are immutable, so hard-link them to avoid copying TB-scale data.
  # Mutable DB metadata is copied, not linked, so read/mixed runs never modify
  # the original loaded DB directory.
  find "${src}" -maxdepth 1 -type f -name '*.sst' -exec ln -t "${dst}" {} +
  # RocksDB regenerates info LOG files and LOCK for the staged DB. Copying old
  # LOG files can dominate staging time on large experiments.
  find "${src}" -maxdepth 1 -type f \
    ! -name '*.sst' \
    ! -name 'LOG' \
    ! -name 'LOG.old.*' \
    ! -name 'LOCK' \
    -exec cp -a -t "${dst}" {} +
}

workload_opts() {
  local workload="$1"
  case "${workload}" in
    workloada|workloadb|workloadc|workloadd|workloade|workloadf)
      printf '%s\n' "--benchmarks=${workload},stats,levelstats"
      ;;
    mixgraph)
      printf '%s\n' "--benchmarks=mixgraph,stats,levelstats --mix_get_ratio=0.83 --mix_put_ratio=0.14 --mix_seek_ratio=0.03 --key_dist_a=0.002312 --key_dist_b=0.3467 --keyrange_dist_a=14.18 --keyrange_dist_b=-2.917 --keyrange_dist_c=0.0164 --keyrange_dist_d=-0.08082 --keyrange_num=30 --value_k=0.2615 --value_sigma=25.45 --iter_k=2.517 --iter_sigma=14.236"
      ;;
    *)
      log "ERROR: unknown workload ${workload}"
      exit 1
      ;;
  esac
}

is_readonly_workload() {
  [[ "$1" == "workloadc" ]]
}

if [[ ! -x "${DB_BENCH}" ]]; then
  log "ERROR: db_bench not executable: ${DB_BENCH}"
  exit 1
fi

if [[ ! -f "${MATRIX}" ]]; then
  log "Matrix missing, generating ${MATRIX}"
  python3 "${SCRIPT_DIR}/make_q3_read_matrix.py" --out "${MATRIX}" | tee -a "${RUN_LOG}"
fi

if pgrep -x db_bench >/dev/null 2>&1; then
  log "ERROR: another db_bench is running. Stop it before Q3 read workloads."
  exit 1
fi

printf 'system\tcase_id\tsize_gb\tkv_label\tdistribution\tunique_ratio\tworkload\tstatus\telapsed_sec\tthreads\tduration_sec\tcache_pct\tavg_latency_us\tthroughput_ops_sec\tp50_us\tp95_us\tp99_us\tfilter_hit\tfilter_miss\tfilter_bytes_insert\tindex_hit\tindex_miss\tindex_bytes_insert\tdata_hit\tdata_miss\tdata_bytes_insert\trocksdb_bytes_read\trocksdb_bytes_written\tdisk_read_ios\tdisk_write_ios\tdisk_read_mb\tdisk_write_mb\tdisk_read_lat_ms\tdisk_write_lat_ms\tdisk_read_mb_s\tdisk_write_mb_s\tread_amp_bytes_per_op\twrite_amp_bytes_per_op\tresult_dir\trun_db_dir\tsource_db_dir\n' > "${SUMMARY}"

log "Starting Q3 read workloads"
log "RUN_ID=${RUN_ID}"
log "MATRIX=${MATRIX}"
log "WORKLOADS=${WORKLOADS}"
log "SYSTEMS=${SYSTEMS}"
log "SIZES_GB=${SIZES_GB}"
log "KV_LABELS=${KV_LABELS}"
log "DISTRIBUTIONS=${DISTRIBUTIONS}"
log "THREADS=${THREADS}"
log "DURATION=${DURATION}"
log "READS=${READS}"
log "CACHE_PCT=${CACHE_PCT}"

tail -n +2 "${MATRIX}" | while IFS=$'\t' read -r system case_id size_gb kv_label key_size value_size distribution unique_ratio zipf_alpha db_dir load_log_dir source_summary; do
  contains_word "${system}" "${SYSTEMS}" || continue
  contains_word "${size_gb}" "${SIZES_GB}" || continue
  contains_word "${kv_label}" "${KV_LABELS}" || continue
  contains_word "${distribution}" "${DISTRIBUTIONS}" || continue

  if [[ ! -d "${db_dir}" ]]; then
    log "SKIP missing DB ${system} ${case_id}: ${db_dir}"
    continue
  fi

  kv_size=$((key_size + value_size))
  num_keys=$((size_gb * 1024 * 1024 * 1024 / kv_size))
  if [[ "${READS}" -gt 0 ]]; then
    reads="${READS}"
  else
    reads=$((num_keys * 10))
  fi
  if [[ -n "${FORCE_CACHE_BYTES:-}" ]]; then
    # Clean-IO mode: fixed (tiny) block cache + cache index/filter blocks so
    # every index/filter/data block goes through the cache-miss IO path and the
    # table-open tail prefetch is not used. Removes tail_size confound.
    cache_size="${FORCE_CACHE_BYTES}"
    cache_index_and_filter_blocks=true
  elif [[ "${CACHE_PCT}" -le 0 ]]; then
    cache_size=0
    cache_index_and_filter_blocks=false
  else
    cache_size=$((size_gb * 1024 * 1024 * 1024 * CACHE_PCT / 100))
    [[ "${cache_size}" -gt 0 ]] || cache_size=1
    cache_index_and_filter_blocks=true
  fi
  # Override: when set, pin index/filter in the table readers (cache=false)
  # instead of routing them through the block cache. With open_files=-1 the
  # readers stay open so index/filter are prefetched once and pinned in RAM.
  if [[ -n "${CACHE_INDEX_FILTER:-}" ]]; then
    cache_index_and_filter_blocks="${CACHE_INDEX_FILTER}"
  fi

  for workload in ${WORKLOADS}; do
    workload_reads="${reads}"
    workload_reads_var="READS_${workload^^}"
    if [[ -n "${!workload_reads_var:-}" ]]; then
      workload_reads="${!workload_reads_var}"
    fi
    result_dir="${LOG_ROOT}/${system}/${case_id}/${workload}"
    raw_dir="${result_dir}/raw"
    mkdir -p "${raw_dir}"
    stdout_file="${result_dir}/stdout.txt"
    stderr_file="${result_dir}/stderr.txt"
    time_file="${raw_dir}/time.out"
    run_db_dir="${TMP_ROOT}/${RUN_ID}_${system}_${case_id}_${workload}"
    if [[ -d "${run_db_dir}" ]]; then
      log "ERROR: temp DB already exists: ${run_db_dir}"
      exit 1
    fi
    log "Stage workload DB ${db_dir} -> ${run_db_dir}"
    stage_db "${db_dir}" "${run_db_dir}"

    if is_readonly_workload "${workload}"; then
      readonly_arg="--readonly=true"
    else
      readonly_arg=""
    fi

    read -r -a bench_opts <<< "$(workload_opts "${workload}")"
    cmd=(
      "${DB_BENCH}"
      --use_existing_db=true
      --statistics=1
      --stats_level=3
      --histogram=true
      --perf_level=3
      --stats_interval_seconds=30
      --stats_per_interval=1
      --report_interval_seconds=10
      --report_file="${result_dir}/report.rep"
      --threads="${THREADS}"
      --duration="${DURATION}"
      --reads="${workload_reads}"
      --num="${num_keys}"
      --key_size="${key_size}"
      --value_size="${value_size}"
      --seed=87654321
      --db="${run_db_dir}"
      --cache_size="${cache_size}"
      --cache_index_and_filter_blocks="${cache_index_and_filter_blocks}"
      --cache_type="${CACHE_TYPE:-hyper_clock_cache}"
      --open_files="${OPEN_FILES:--1}"
      --bloom_bits=10
      --max_background_jobs=48
      --use_direct_reads=true
      --use_direct_io_for_flush_and_compaction=true
      --compression_type=none
    )
    [[ -n "${readonly_arg}" ]] && cmd+=("${readonly_arg}")
    cmd+=("${bench_opts[@]}")

    { printf '#!/usr/bin/env bash\n'; printf '%q ' "${cmd[@]}"; echo; } > "${raw_dir}/run_cmd.sh"
    chmod +x "${raw_dir}/run_cmd.sh"

    log "BEGIN ${system} ${case_id} ${workload} reads=${workload_reads}"
    sync
    echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null 2>&1 || true
    cat /proc/diskstats > "${raw_dir}/diskstats.start"
    start_ts="$(date +%s)"
    set +e
    /usr/bin/time -v -o "${time_file}" "${cmd[@]}" > "${stdout_file}" 2> "${stderr_file}"
    exit_code=$?
    set -e
    end_ts="$(date +%s)"
    cat /proc/diskstats > "${raw_dir}/diskstats.end"
    elapsed=$((end_ts - start_ts))
    status="ok"
    [[ "${exit_code}" -eq 0 ]] || status="failed:${exit_code}"
    disk_stats="$(disk_delta_summary "${raw_dir}/diskstats.start" "${raw_dir}/diskstats.end" "${elapsed}")"
    if [[ -n "${disk_stats}" ]]; then
      read -r disk_read_ios disk_write_ios disk_read_mb disk_write_mb \
        disk_read_lat_ms disk_write_lat_ms disk_read_mb_s disk_write_mb_s \
        <<< "${disk_stats}"
    else
      disk_read_ios=""
      disk_write_ios=""
      disk_read_mb=""
      disk_write_mb=""
      disk_read_lat_ms=""
      disk_write_lat_ms=""
      disk_read_mb_s=""
      disk_write_mb_s=""
    fi

    read -r avg_latency_us throughput_ops_sec < <(bench_line_metric "${workload}" "${stdout_file}")
    p50_us="$(hist_value rocksdb.db.get.micros P50 "${stdout_file}")"
    p95_us="$(hist_value rocksdb.db.get.micros P95 "${stdout_file}")"
    p99_us="$(hist_value rocksdb.db.get.micros P99 "${stdout_file}")"
    if [[ -z "${p50_us}" && "${workload}" != "workloadc" ]]; then
      p50_us="$(hist_value rocksdb.db.write.micros P50 "${stdout_file}")"
      p95_us="$(hist_value rocksdb.db.write.micros P95 "${stdout_file}")"
      p99_us="$(hist_value rocksdb.db.write.micros P99 "${stdout_file}")"
    fi

    filter_hit="$(stat_count rocksdb.block.cache.filter.hit "${stdout_file}")"
    filter_miss="$(stat_count rocksdb.block.cache.filter.miss "${stdout_file}")"
    filter_bytes="$(stat_count rocksdb.block.cache.filter.bytes.insert "${stdout_file}")"
    index_hit="$(stat_count rocksdb.block.cache.index.hit "${stdout_file}")"
    index_miss="$(stat_count rocksdb.block.cache.index.miss "${stdout_file}")"
    index_bytes="$(stat_count rocksdb.block.cache.index.bytes.insert "${stdout_file}")"
    data_hit="$(stat_count rocksdb.block.cache.data.hit "${stdout_file}")"
    data_miss="$(stat_count rocksdb.block.cache.data.miss "${stdout_file}")"
    data_bytes="$(stat_count rocksdb.block.cache.data.bytes.insert "${stdout_file}")"
    rocksdb_bytes_read="$(stat_count rocksdb.bytes.read "${stdout_file}")"
    rocksdb_bytes_written="$(stat_count rocksdb.bytes.written "${stdout_file}")"

    read_amp=""
    write_amp=""
    if [[ -n "${throughput_ops_sec}" && "${throughput_ops_sec}" != "0" && -n "${rocksdb_bytes_read}" ]]; then
      read_amp="$(awk -v b="${rocksdb_bytes_read}" -v ops="${throughput_ops_sec}" -v d="${DURATION}" 'BEGIN { if (ops*d > 0) printf "%.3f", b / (ops*d) }')"
    fi
    if [[ -n "${throughput_ops_sec}" && "${throughput_ops_sec}" != "0" && -n "${rocksdb_bytes_written}" ]]; then
      write_amp="$(awk -v b="${rocksdb_bytes_written}" -v ops="${throughput_ops_sec}" -v d="${DURATION}" 'BEGIN { if (ops*d > 0) printf "%.3f", b / (ops*d) }')"
    fi

    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "${system}" "${case_id}" "${size_gb}" "${kv_label}" "${distribution}" \
      "${unique_ratio}" "${workload}" "${status}" "${elapsed}" "${THREADS}" \
      "${DURATION}" "${CACHE_PCT}" "${avg_latency_us}" "${throughput_ops_sec}" \
      "${p50_us}" "${p95_us}" "${p99_us}" "${filter_hit}" "${filter_miss}" \
      "${filter_bytes}" "${index_hit}" "${index_miss}" "${index_bytes}" \
      "${data_hit}" "${data_miss}" "${data_bytes}" "${rocksdb_bytes_read}" \
      "${rocksdb_bytes_written}" "${disk_read_ios}" "${disk_write_ios}" \
      "${disk_read_mb}" "${disk_write_mb}" "${disk_read_lat_ms}" \
      "${disk_write_lat_ms}" "${disk_read_mb_s}" "${disk_write_mb_s}" \
      "${read_amp}" "${write_amp}" "${result_dir}" "${run_db_dir}" "${db_dir}" \
      >> "${SUMMARY}"

    log "END ${system} ${case_id} ${workload} status=${status} elapsed=${elapsed}s"
    if [[ "${KEEP_RUN_DB}" != "1" && "${run_db_dir}" != "${db_dir}" ]]; then
      rm -rf "${run_db_dir}"
    fi
    if [[ "${exit_code}" -ne 0 ]]; then
      exit "${exit_code}"
    fi
  done
done

log "Summary: ${SUMMARY}"
