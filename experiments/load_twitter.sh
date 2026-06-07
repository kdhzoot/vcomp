#!/usr/bin/env bash
: <<'EXAMPLE'
# Baseline (twitterload + real compaction)
MODE=baseline TRACE_FILE=/path/to/cluster012.vcomptrace \
  DB_ROOT=/work/vcomp bash load_twitter.sh

# Virtual compaction (twitterload + PLR-based compaction + materialization)
MODE=vcomp TRACE_FILE=/path/to/cluster012.vcomptrace \
  DB_ROOT=/work/vcomp bash load_twitter.sh

# Optional env:
#   MAX_OPS=10000000   -- stop after N records (0 = whole file, default 0)
#   RG_VALUE_SIZE=...  -- RandomGenerator bound (must be >= trace max value_size)
#   RUN_NAME=name      -- short base name for both DB_DIR and LOG_DIR
#   RUN_TAG=stage1     -- appended to default RUN_NAME
#   EXTRA_DB_BENCH_ARGS="--flag=value ..." -- appended to db_bench command
EXAMPLE

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_BENCH="${SCRIPT_DIR}/../vcomp/db_bench"

require_env() {
  local name="$1"
  [[ -n "${!name:-}" ]] || { echo "[ERROR] Missing required env: ${name}" >&2; exit 1; }
}
for name in MODE TRACE_FILE DB_ROOT; do
  require_env "$name"
done
[[ "${MODE}" == "baseline" || "${MODE}" == "vcomp" ]] || {
  echo "[ERROR] MODE must be 'baseline' or 'vcomp'" >&2; exit 1
}
[[ -x "${DB_BENCH}" ]] || {
  echo "[ERROR] db_bench not found at ${DB_BENCH}. Run make.sh first." >&2; exit 1
}
[[ -f "${TRACE_FILE}" ]] || {
  echo "[ERROR] TRACE_FILE not found: ${TRACE_FILE}" >&2; exit 1
}

# Optional caps
MAX_OPS="${MAX_OPS:-0}"
# RandomGenerator bound. 2 MiB covers the cluster012 sample max (312480 B).
# If full trace has larger values, bump this (twitterload will error out with
# a clear message telling you the required minimum).
RG_VALUE_SIZE="${RG_VALUE_SIZE:-2097152}"

# Cosmetic: reported key_size in db_bench header. cluster012 has 44 B keys;
# twitterload reads actual key bytes from the trace regardless of this flag.
KEY_SIZE_HINT="${KEY_SIZE_HINT:-44}"

TRACE_BASENAME="$(basename "${TRACE_FILE}" .vcomptrace)"
format_ops() {
  local n="$1"
  if [[ "${n}" == "0" ]]; then
    echo "all"
  elif (( n % 1000000000 == 0 )); then
    echo "$((n / 1000000000))B"
  elif (( n % 1000000 == 0 )); then
    echo "$((n / 1000000))M"
  elif (( n % 1000 == 0 )); then
    echo "$((n / 1000))K"
  else
    echo "${n}"
  fi
}
RUN_TS="$(date '+%y%m%d_%H%M')"
RUN_TAG_SUFFIX="${RUN_TAG:+_${RUN_TAG}}"
OPS_LABEL="$(format_ops "${MAX_OPS}")"
RUN_NAME="${RUN_NAME:-${MODE}_tl_${TRACE_BASENAME}_${OPS_LABEL}${RUN_TAG_SUFFIX}}"
RUN_DIR="${LOG_DIR:-${SCRIPT_DIR}/log_loads/${RUN_NAME}_${RUN_TS}}"
DB_DIR="${DB_DIR:-${DB_ROOT%/}/${RUN_NAME}}"
RAW_DIR="${RUN_DIR}/raw"
REP_FILE="${RUN_DIR}/report.rep"
OUT_FILE="${RUN_DIR}/bench.out"
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

# ── PLR / vcomp parameters (only used in vcomp mode) ──
PLR_ERROR_BOUND="${PLR_ERROR_BOUND:-8}"
MEMTABLE_FLUSH_MB="${MEMTABLE_FLUSH_MB:-64}"

[[ ! -d "${DB_DIR}" ]] || { echo "[ERROR] DB already exists: ${DB_DIR}" >&2; exit 1; }
mkdir -p "${DB_DIR}" "${RAW_DIR}"
ulimit -n 1048576

# ── Build command ──
# Mirrors eval-vcomp/load.sh exactly, except:
#   - benchmarks:     fillrandom      -> twitterload
#   - --num:          NKEYS           -> MAX_OPS (0 = whole trace)
#   - --value_size:   1000            -> RG_VALUE_SIZE (RandomGenerator bound)
#   - --key_size:     24              -> KEY_SIZE_HINT (cosmetic for header)
#   - additions:      --twitter_trace_file, --twitter_trace_max_ops
cmd=(
  "${DB_BENCH}"
  --statistics=1
  --stats_interval_seconds=60
  --stats_per_interval=1
  --report_interval_seconds=10
  --report_file="${REP_FILE}"
  --enable_index_compression=false
  --bloom_bits=10
  --disable_wal=true
  --max_background_jobs=$(nproc)
  --num="${MAX_OPS}"
  --key_size="${KEY_SIZE_HINT}"
  --value_size="${RG_VALUE_SIZE}"
  --threads=1
  --memtablerep=vector
  --seed=12345678
  --db="${DB_DIR}"
  --use_direct_reads=true
  --use_direct_io_for_flush_and_compaction=true
  --compression_type=none
  --twitter_trace_file="${TRACE_FILE}"
  --twitter_trace_max_ops="${MAX_OPS}"
)

if [[ "${MODE}" == "baseline" ]]; then
  cmd+=(
    --benchmarks=twitterload,flush,compact0,waitforcompaction,stats,levelstats
  )
else
  # vcomp: fillvirtual reads the trace via --twitter_trace_file (added above)
  # instead of generating uint64 keys via Random64. Spec: vcomp/README.md §7.7.
  cmd+=(
    --benchmarks=fillvirtual,flush,compact0,waitforcompaction,stats,levelstats
    --use_virtual_compaction=true
    --plr_error_bound="${PLR_ERROR_BOUND}"
    --memtable_flush_size="${MEMTABLE_FLUSH_MB}"
  )
fi

if [[ -n "${EXTRA_DB_BENCH_ARGS:-}" ]]; then
  # shellcheck disable=SC2206
  extra_args=( ${EXTRA_DB_BENCH_ARGS} )
  cmd+=( "${extra_args[@]}" )
fi

# ── Save run info ──
{ printf '#!/usr/bin/env bash\n'; printf '%q ' "${cmd[@]}"; echo; } > "${RAW_DIR}/load_cmd.sh"
chmod +x "${RAW_DIR}/load_cmd.sh"

echo "=== ${MODE} twitterload | trace=${TRACE_BASENAME} | MAX_OPS=${MAX_OPS} | $(date) ==="
{ echo "[MODE] ${MODE}"; echo "[TRACE_FILE] ${TRACE_FILE}"; echo "[MAX_OPS] ${MAX_OPS}"; \
  echo "[RUN_CMD]"; printf '%q ' "${cmd[@]}"; echo; } | tee "${OUT_FILE}"

# ── Drop page cache for clean measurement ──
if [[ -w /proc/sys/vm/drop_caches ]]; then
  sync
  echo 3 > /proc/sys/vm/drop_caches
  echo "[INFO] Page cache dropped"
else
  sync
  echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null 2>&1 && echo "[INFO] Page cache dropped (sudo)" \
    || echo "[WARN] Cannot drop page cache (no permission)"
fi

# ── Collect before-stats and run ──
start_ts="$(date +%s)"
echo "${start_ts}" > "${RAW_DIR}/start_epoch.txt"
cat /proc/diskstats > "${RAW_DIR}/diskstats.start"
cat /proc/stat > "${RAW_DIR}/procstat.start"
if command -v iostat >/dev/null 2>&1; then
  iostat -dx 1 > "${RAW_DIR}/iostat.log" & IOSTAT_PID=$!
fi

set +e
"${cmd[@]}" >> "${OUT_FILE}" 2>&1
exit_code=$?
set -e

cleanup_iostat

# ── Collect after-stats ──
end_ts="$(date +%s)"
echo "${end_ts}" > "${RAW_DIR}/end_epoch.txt"
elapsed=$((end_ts - start_ts))
echo "${elapsed}" > "${RAW_DIR}/elapsed_sec.txt"
cat /proc/diskstats > "${RAW_DIR}/diskstats.end"
cat /proc/stat > "${RAW_DIR}/procstat.end"

# ── Split db_bench time into primary benchmark vs the rest ──
# The db_bench-reported primary line captures only twitterload/fillvirtual.
# elapsed_sec captures the full db_bench command, including
# flush/compact0/waitforcompaction/stats/levelstats and DB teardown.
primary_line="$(grep -E '^(twitterload|fillvirtual)[[:space:]]*:' "${OUT_FILE}" | tail -1 || true)"
primary_bench="$(printf '%s\n' "${primary_line}" | sed -nE 's/^([[:alnum:]_]+)[[:space:]]*:.*$/\1/p')"
primary_sec="$(printf '%s\n' "${primary_line}" | sed -nE 's/.*[[:space:]]([0-9]+(\.[0-9]+)?) seconds[[:space:]].*/\1/p')"
non_primary_sec="NA"
if [[ -n "${primary_sec}" ]]; then
  non_primary_sec="$(awk -v total="${elapsed}" -v primary="${primary_sec}" 'BEGIN { printf "%.3f", total - primary }')"
fi
echo "${primary_bench:-NA}" > "${RAW_DIR}/primary_benchmark.txt"
echo "${primary_sec:-NA}" > "${RAW_DIR}/primary_benchmark_sec.txt"
echo "${non_primary_sec}" > "${RAW_DIR}/non_primary_db_bench_sec.txt"

# ── Summary ──
db_size="$(du -sh "${DB_DIR}" 2>/dev/null | cut -f1)"
echo ""
echo "=== Summary ==="
echo "Mode:      ${MODE}"
echo "Trace:     ${TRACE_FILE}"
echo "MAX_OPS:   ${MAX_OPS}"
echo "DB Size:   ${db_size}"
echo "Threads:   1"
echo "Memtable:  vector"
echo "Elapsed:   ${elapsed} sec (full db_bench command)"
echo "Primary:   ${primary_bench:-NA} ${primary_sec:-NA} sec"
echo "Remainder: ${non_primary_sec} sec (post-primary benchmarks + teardown)"
echo "Log:       ${RUN_DIR}"
echo "DB:        ${DB_DIR}"
echo "Exit code: ${exit_code}"
exit "${exit_code}"
