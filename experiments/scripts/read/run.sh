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
require_env() {
  local name="$1"
  [[ -n "${!name:-}" ]] || { echo "[ERROR] Missing required env: ${name}" >&2; exit 1; }
}
for name in WORKLOAD DB_DIR DB_SIZE_GB CACHE_PCT THREADS; do
  require_env "$name"
done

[[ -x "${DB_BENCH}" ]] || {
  echo "[ERROR] db_bench not found at ${DB_BENCH}" >&2; exit 1
}
[[ -d "${DB_DIR}" ]] || {
  echo "[ERROR] DB directory not found: ${DB_DIR}" >&2; exit 1
}

# ── Defaults ──
READ_ONLY="${READ_ONLY:-1}"
DURATION="${DURATION:-180}"
READS="${READS:-0}"
TMP_ROOT="${TMP_ROOT:-/work/tmp}"

# ── KV parameters (match load.sh) ──
KEY_SIZE=24
VALUE_SIZE=1000
KV_SIZE=$((KEY_SIZE + VALUE_SIZE))

# ── Derived values ──
DB_SIZE_BYTES=$((DB_SIZE_GB * 1024 * 1024 * 1024))
NKEYS=$((DB_SIZE_BYTES / KV_SIZE))
CACHE_SIZE=$(echo "scale=0; $DB_SIZE_BYTES * $CACHE_PCT / 100" | bc)
# cache=0 → use 1 byte so block cache stats (filter/index/data miss) are tracked
(( CACHE_SIZE == 0 )) && CACHE_SIZE=1

DB_NAME="$(basename "${DB_DIR}")"
RUN_TS="$(date '+%y%m%d_%H%M')"
RESULT_DIR="${RESULT_DIR:-${ARTIFACT_ROOT}/log_runs/${DB_NAME}/${WORKLOAD}_${THREADS}t_${CACHE_PCT}p_${RUN_TS}}"
TIME_FILE="${RESULT_DIR}/time.out"

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
    BENCH_OPTS="--benchmarks=readrandom,stats,levelstats"
    ;;
  seekrandom)
    BENCH_OPTS="--benchmarks=seekrandom,stats,levelstats --seek_nexts=100"
    ;;
  readwhilewriting)
    BENCH_OPTS="--benchmarks=readwhilewriting,stats,levelstats"
    ;;
  mixgraph)
    MIX_GET=0.83; MIX_PUT=0.14; MIX_SEEK=0.03
    [[ "${READ_ONLY}" == "1" ]] && { MIX_GET=1; MIX_PUT=0; MIX_SEEK=0; }
    BENCH_OPTS="--benchmarks=mixgraph,stats,levelstats \
      --key_dist_a=0.002312 --key_dist_b=0.3467 \
      --keyrange_dist_a=14.18 --keyrange_dist_b=-2.917 \
      --keyrange_dist_c=0.0164 --keyrange_dist_d=-0.08082 \
      --keyrange_num=30 \
      --value_k=0.2615 --value_sigma=25.45 \
      --iter_k=2.517 --iter_sigma=14.236 \
      --sine_mix_rate_interval_milliseconds=5000 \
      --sine_a=1000 --sine_b=0.000073 --sine_d=450000000 \
      --mix_get_ratio=${MIX_GET} --mix_put_ratio=${MIX_PUT} --mix_seek_ratio=${MIX_SEEK}"
    ;;
  *)
    echo "[ERROR] Unknown workload: ${WORKLOAD}" >&2
    echo "  Supported: readrandom, seekrandom, readwhilewriting, mixgraph" >&2
    exit 1
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
sync
echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null 2>&1 && echo "[INFO] Page cache dropped" \
  || echo "[WARN] Cannot drop page cache"

# ── Capture before-stats ──
start_ts="$(date +%s.%N)"
cat /proc/stat > "${RESULT_DIR}/procstat.start"
cat /proc/diskstats > "${RESULT_DIR}/diskstats.start"

# ── Run ──
/usr/bin/time -v -o "${TIME_FILE}" "${DB_BENCH}" \
  --threads="${THREADS}" \
  --statistics=1 \
  --stats_interval_seconds=10 \
  --stats_per_interval=1 \
  --report_interval_seconds=10 \
  --report_file="${RESULT_DIR}/report.rep" \
  --cache_size="${CACHE_SIZE}" \
  --cache_index_and_filter_blocks=$( (( CACHE_SIZE > 0 )) && echo true || echo false ) \
  --bloom_bits=10 \
  --max_background_jobs=$(nproc) \
  --num="${NKEYS}" \
  --reads=$(( READS > 0 ? READS : NKEYS * 10 )) \
  --key_size="${KEY_SIZE}" \
  --value_size="${VALUE_SIZE}" \
  --seed=87654321 \
  --db="${RUN_DIR}" \
  --use_existing_db=1 \
  $( [[ "${READ_ONLY}" == "1" ]] && echo "--readonly=true" ) \
  --use_direct_reads=true \
  --use_direct_io_for_flush_and_compaction=true \
  --compression_type=none \
  --duration="${DURATION}" \
  $( (( CACHE_SIZE > 0 )) && echo "--cache_type=hyper_clock_cache" ) \
  ${BENCH_OPTS} \
  > "${RESULT_DIR}/stdout.txt" 2> "${RESULT_DIR}/stderr.txt"

exit_code=$?

# ── Capture after-stats ──
end_ts="$(date +%s.%N)"
elapsed=$(echo "$end_ts - $start_ts" | bc)
cat /proc/stat > "${RESULT_DIR}/procstat.end"
cat /proc/diskstats > "${RESULT_DIR}/diskstats.end"
PEAK_RSS_KB="$(awk -F: '/Maximum resident set size/ {gsub(/^[ \t]+/, "", $2); print $2}' "${TIME_FILE}" 2>/dev/null || true)"
PEAK_RSS_GB="$(awk -v kb="${PEAK_RSS_KB}" 'BEGIN { if (kb != "") printf "%.3f", kb / 1024 / 1024 }')"
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
CPU_UTIL=$( (( DT > 0 )) && echo "scale=1; 100*(1-$DI/$DT)" | bc || echo "0" )

# ── Disk bandwidth ──
get_disk() {
  awk '$3 ~ /^(sd|nvme|md)/ && NF>=14 { r += $6; w += $10 } END { print r+0, w+0 }' "$1"
}
read R0 W0 < <(get_disk "${RESULT_DIR}/diskstats.start")
read R1 W1 < <(get_disk "${RESULT_DIR}/diskstats.end")
RS=$(( R1 - R0 )); WS=$(( W1 - W0 ))
READ_MB=$(echo "scale=1; $RS*512/1048576/$elapsed" | bc 2>/dev/null || echo 0)
WRITE_MB=$(echo "scale=1; $WS*512/1048576/$elapsed" | bc 2>/dev/null || echo 0)

# ── Extract throughput ──
OPS=$(grep -oP '\d+ ops/sec' "${RESULT_DIR}/stdout.txt" | head -1 || echo "? ops/sec")

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
