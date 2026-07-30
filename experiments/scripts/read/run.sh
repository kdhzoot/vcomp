#!/usr/bin/env bash
: <<'EXAMPLE'
# Read-only workloads (run on original DB)
WORKLOAD=readrandom DB_DIR=/work/vcomp/vcomp_1000gb DB_SIZE_GB=1000 CACHE_PCT=2 THREADS=16 bash run.sh
WORKLOAD=seekrandom DB_DIR=/work/vcomp/baseline_250gb DB_SIZE_GB=250 CACHE_PCT=5 THREADS=32 bash run.sh

# Read-write workloads (copies DB to temp dir first)
WORKLOAD=readwhilewriting DB_DIR=/work/vcomp/vcomp_1000gb DB_SIZE_GB=1000 CACHE_PCT=2 THREADS=16 READ_ONLY=0 bash run.sh

# Supported workloads: readrandom, seekrandom, readwhilewriting, mixgraph
EXAMPLE

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"
DB_BENCH="${DB_BENCH:-${VCOMP_PROF_DB_BENCH}}"

# ── Required env ──
require_env WORKLOAD DB_DIR DB_SIZE_GB CACHE_PCT THREADS
require_positive_uint DB_SIZE_GB
require_positive_uint THREADS
require_uint CACHE_PCT
(( CACHE_PCT <= 100 )) || die "CACHE_PCT must be between 0 and 100"
require_executable "${DB_BENCH}" "db_bench"
require_dir "${DB_DIR}" "DB directory"

# ── Defaults ──
READ_ONLY="${READ_ONLY:-1}"
DURATION="${DURATION:-180}"
READS="${READS:-0}"
TMP_ROOT="${TMP_ROOT:-/work/tmp}"
require_uint READ_ONLY
require_uint DURATION
require_uint READS
[[ "${READ_ONLY}" == "0" || "${READ_ONLY}" == "1" ]] ||
  die "READ_ONLY must be 0 or 1"
case "${WORKLOAD}" in
  readrandom|seekrandom|readwhilewriting|mixgraph) ;;
  *) die "Unknown workload: ${WORKLOAD} (supported: readrandom, seekrandom, readwhilewriting, mixgraph)" ;;
esac

# ── KV parameters (match load.sh) ──
KEY_SIZE=24
VALUE_SIZE=1000
KV_SIZE=$((KEY_SIZE + VALUE_SIZE))

# ── Derived values ──
DB_SIZE_BYTES=$((DB_SIZE_GB * 1024 * 1024 * 1024))
NKEYS=$((DB_SIZE_BYTES / KV_SIZE))
CACHE_SIZE=$((DB_SIZE_BYTES * CACHE_PCT / 100))
# cache=0 → use 1 byte so block cache stats (filter/index/data miss) are tracked
(( CACHE_SIZE == 0 )) && CACHE_SIZE=1

DB_NAME="$(basename "${DB_DIR}")"
RUN_TS="$(date '+%y%m%d_%H%M%S')"
RESULT_DIR="${RESULT_DIR:-${ARTIFACT_ROOT}/log_runs/${DB_NAME}/${WORKLOAD}_${THREADS}t_${CACHE_PCT}p_${RUN_TS}}"
TIME_FILE="${RESULT_DIR}/time.out"

[[ ! -e "${RESULT_DIR}" ]] || die "Result output already exists: ${RESULT_DIR}"
mkdir -p "${RESULT_DIR}"
ulimit -n 1048576

# ── Non-read-only: stage DB via hard-links ──
RUN_DIR="${DB_DIR}"
if [[ "${READ_ONLY}" == "0" ]]; then
  mkdir -p "${TMP_ROOT}"
  RUN_DIR="$(mktemp -d "${TMP_ROOT}/${DB_NAME}.XXXXXX")"
  trap 'rm -rf "${RUN_DIR}"' EXIT
  echo "[INFO] Staging DB: ${DB_DIR} -> ${RUN_DIR}"
  find "${DB_DIR}" -maxdepth 1 -type f -name '*.sst' -exec ln -t "${RUN_DIR}" {} +
  find "${DB_DIR}" -maxdepth 1 -type f ! -name '*.sst' -exec cp -a -t "${RUN_DIR}" {} +
  echo "[INFO] Staging complete"
fi

# ── Workload-specific options ──
case "${WORKLOAD}" in
  readrandom)
    bench_opts=(--benchmarks=readrandom,stats,levelstats)
    ;;
  seekrandom)
    bench_opts=(--benchmarks=seekrandom,stats,levelstats --seek_nexts=100)
    ;;
  readwhilewriting)
    bench_opts=(--benchmarks=readwhilewriting,stats,levelstats)
    ;;
  mixgraph)
    MIX_GET=0.83; MIX_PUT=0.14; MIX_SEEK=0.03
    [[ "${READ_ONLY}" == "1" ]] && { MIX_GET=1; MIX_PUT=0; MIX_SEEK=0; }
    bench_opts=(
      --benchmarks=mixgraph,stats,levelstats
      --key_dist_a=0.002312
      --key_dist_b=0.3467
      --keyrange_dist_a=14.18
      --keyrange_dist_b=-2.917
      --keyrange_dist_c=0.0164
      --keyrange_dist_d=-0.08082
      --keyrange_num=30
      --value_k=0.2615
      --value_sigma=25.45
      --iter_k=2.517
      --iter_sigma=14.236
      --sine_mix_rate_interval_milliseconds=5000
      --sine_a=1000
      --sine_b=0.000073
      --sine_d=450000000
      --mix_get_ratio="${MIX_GET}"
      --mix_put_ratio="${MIX_PUT}"
      --mix_seek_ratio="${MIX_SEEK}"
    )
    ;;
esac

# ── Print config ──
echo "=========================================="
echo "  Workload:    ${WORKLOAD}"
echo "  DB:          ${RUN_DIR}"
echo "  DB Size:     ${DB_SIZE_GB} GB"
echo "  Num Keys:    ${NKEYS}"
echo "  Threads:     ${THREADS}"
echo "  Cache:       ${CACHE_PCT}% = $((CACHE_SIZE / 1024 / 1024)) MB"
echo "  Duration:    ${DURATION} sec"
echo "  Read-only:   ${READ_ONLY}"
echo "  Result:      ${RESULT_DIR}"
echo "=========================================="

# ── Drop page cache ──
drop_page_cache

# ── Capture before-stats ──
start_ts="$(date +%s.%N)"
cat /proc/stat > "${RESULT_DIR}/procstat.start"
cat /proc/diskstats > "${RESULT_DIR}/diskstats.start"

# ── Run ──
cmd=(
  "${DB_BENCH}"
  --threads="${THREADS}"
  --statistics=1
  --stats_interval_seconds=10
  --stats_per_interval=1
  --report_interval_seconds=10
  --report_file="${RESULT_DIR}/report.rep"
  --cache_size="${CACHE_SIZE}"
  --cache_index_and_filter_blocks=true
  --cache_type=hyper_clock_cache
  --bloom_bits=10
  --max_background_jobs="$(nproc)"
  --num="${NKEYS}"
  --reads=$(( READS > 0 ? READS : NKEYS * 10 ))
  --key_size="${KEY_SIZE}"
  --value_size="${VALUE_SIZE}"
  --seed=87654321
  --db="${RUN_DIR}"
  --use_existing_db=1
  --use_direct_reads=true
  --use_direct_io_for_flush_and_compaction=true
  --compression_type=none
  --duration="${DURATION}"
)
[[ "${READ_ONLY}" == "1" ]] && cmd+=(--readonly=true)
cmd+=("${bench_opts[@]}")

set +e
/usr/bin/time -v -o "${TIME_FILE}" "${cmd[@]}" \
  > "${RESULT_DIR}/stdout.txt" 2> "${RESULT_DIR}/stderr.txt"
exit_code=$?
set -e

# ── Capture after-stats ──
end_ts="$(date +%s.%N)"
elapsed="$(awk -v start="${start_ts}" -v end="${end_ts}" 'BEGIN { printf "%.3f", end - start }')"
cat /proc/stat > "${RESULT_DIR}/procstat.end"
cat /proc/diskstats > "${RESULT_DIR}/diskstats.end"
PEAK_RSS_KB="$(extract_peak_rss_kb "${TIME_FILE}")"
PEAK_RSS_GB="$(rss_kb_to_gb "${PEAK_RSS_KB}")"
echo "${PEAK_RSS_KB}" > "${RESULT_DIR}/peak_rss_kb.txt"
echo "${PEAK_RSS_GB}" > "${RESULT_DIR}/peak_rss_gb.txt"

# ── CPU utilization ──
get_cpu() {
  awk -v limit="$2" '
    /^cpu[0-9]+/ {
      n = substr($1, 4) + 0
      if (n >= limit) next
      t = 0; for (i=2; i<=NF; i++) t += $i
      idle = $5 + $6
      sum_t += t; sum_i += idle
    }
    END { print sum_t+0, sum_i+0 }
  ' "$1"
}
read T0 I0 < <(get_cpu "${RESULT_DIR}/procstat.start" "${THREADS}")
read T1 I1 < <(get_cpu "${RESULT_DIR}/procstat.end" "${THREADS}")
DT=$((T1 - T0)); DI=$((I1 - I0))
CPU_UTIL="$(awk -v total="${DT}" -v idle="${DI}" \
  'BEGIN { if (total > 0) printf "%.1f", 100 * (1 - idle / total); else print "0" }')"

# ── Disk bandwidth ──
get_disk() {
  awk '$3 ~ /^(sd|nvme|md)/ && NF>=14 { r += $6; w += $10 } END { print r+0, w+0 }' "$1"
}
read R0 W0 < <(get_disk "${RESULT_DIR}/diskstats.start")
read R1 W1 < <(get_disk "${RESULT_DIR}/diskstats.end")
RS=$(( R1 - R0 )); WS=$(( W1 - W0 ))
READ_MB="$(awk -v sectors="${RS}" -v seconds="${elapsed}" \
  'BEGIN { if (seconds > 0) printf "%.1f", sectors * 512 / 1048576 / seconds; else print "0" }')"
WRITE_MB="$(awk -v sectors="${WS}" -v seconds="${elapsed}" \
  'BEGIN { if (seconds > 0) printf "%.1f", sectors * 512 / 1048576 / seconds; else print "0" }')"

# ── Extract throughput ──
OPS="$(awk -v workload="${WORKLOAD}" '
  $1 == workload && $2 == ":" {
    for (i = 1; i <= NF; i++) {
      if ($i == "ops/sec") {
        print $(i - 1) " ops/sec"
        exit
      }
    }
  }
' "${RESULT_DIR}/stdout.txt")"
OPS="${OPS:-? ops/sec}"

# ── Summary ──
{
  echo "workload: ${WORKLOAD}"
  echo "db: ${DB_NAME}"
  echo "threads: ${THREADS}"
  echo "cache_pct: ${CACHE_PCT}%"
  echo "duration: ${DURATION}s"
  echo "elapsed: ${elapsed}s"
  echo "peak_rss_kb: ${PEAK_RSS_KB}"
  echo "peak_rss_gb: ${PEAK_RSS_GB}"
  echo "throughput: ${OPS}"
  echo "cpu_util: ${CPU_UTIL}%"
  echo "disk_read: ${READ_MB} MB/s"
  echo "disk_write: ${WRITE_MB} MB/s"
  echo "exit_code: ${exit_code}"
} | tee "${RESULT_DIR}/summary.txt"

echo ""
echo "Results: ${RESULT_DIR}"
exit "${exit_code}"
