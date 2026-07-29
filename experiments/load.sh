#!/usr/bin/env bash
: <<'EXAMPLE'
# Baseline (real fillrandom + compaction)
MODE=baseline TARGET_DB_GB=10 DB_ROOT=/work/vcomp bash load.sh

# Virtual compaction (fillvirtual + PLR-based compaction + materialization)
MODE=vcomp TARGET_DB_GB=10 DB_ROOT=/work/vcomp bash load.sh

# Explicit bounded visible-L0 refill. The default value, 0, uses
# RocksDB's max_compaction_bytes as the visible-L0 target.
MODE=vcomp TARGET_DB_GB=10 DB_ROOT=/work/vcomp \
  VCOMP_REGISTER_BATCH_MAX=256 \
  VCOMP_VISIBLE_L0_BATCH_MB=0 \
  bash load.sh
EXAMPLE

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_BENCH="${DB_BENCH:-${SCRIPT_DIR}/../vcomp/db_bench}"

require_env() {
  local name="$1"
  [[ -n "${!name:-}" ]] || { echo "[ERROR] Missing required env: ${name}" >&2; exit 1; }
}
for name in MODE TARGET_DB_GB DB_ROOT; do
  require_env "$name"
done
case "${MODE}" in
  baseline|vcomp|l0only|l0compact) ;;
  *) echo "[ERROR] MODE must be 'baseline', 'vcomp', 'l0only', or 'l0compact'" >&2; exit 1 ;;
esac
[[ -x "${DB_BENCH}" ]] || {
  echo "[ERROR] db_bench not found at ${DB_BENCH}. Run make.sh first." >&2; exit 1
}

# ── Common parameters ──
KEY_SIZE="${KEY_SIZE:-24}"
VALUE_SIZE="${VALUE_SIZE:-1000}"
KV_SIZE=$((KEY_SIZE + VALUE_SIZE))

RUN_TS="$(date '+%y%m%d_%H%M')"
RUN_TAG="${RUN_TAG:+_${RUN_TAG}}"
RUN_DIR="${LOG_DIR:-${SCRIPT_DIR}/log_loads/${MODE}_${RUN_TS}_${TARGET_DB_GB}gb${RUN_TAG}}"
DB_DIR="${DB_DIR:-${DB_ROOT%/}/${MODE}_${TARGET_DB_GB}gb}"
RAW_DIR="${RUN_DIR}/raw"
REP_FILE="${RUN_DIR}/report.rep"
OUT_FILE="${RUN_DIR}/bench.out"
TIME_FILE="${RAW_DIR}/time.out"
IOSTAT_PID=""

extract_peak_rss_kb() {
  awk -F: '/Maximum resident set size/ {gsub(/^[ \t]+/, "", $2); print $2}' "$1" 2>/dev/null || true
}

rss_kb_to_gb() {
  local rss_kb="$1"
  awk -v kb="${rss_kb}" 'BEGIN { if (kb != "") printf "%.3f", kb / 1024 / 1024 }'
}

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

# CACHE_BYTES=$((1024 * 1024 * 1024 * CACHE_SIZE_GB))
NKEYS=$((TARGET_DB_GB * 1024 * 1024 * 1024 / KV_SIZE))

# ── PLR / vcomp parameters (only used in vcomp mode) ──
PLR_ERROR_BOUND="${PLR_ERROR_BOUND:-8}"
MEMTABLE_FLUSH_MB="${MEMTABLE_FLUSH_MB:-64}"
BG_JOBS="${BG_JOBS:-$(nproc)}"
VCOMP_REGISTER_BATCH_MAX="${VCOMP_REGISTER_BATCH_MAX:-256}"
VCOMP_VISIBLE_L0_BATCH_MB="${VCOMP_VISIBLE_L0_BATCH_MB:-0}"
VCOMP_LOG_APPLY_TIMING="${VCOMP_LOG_APPLY_TIMING:-true}"
VCOMP_SORT_DETAIL_TIMING="${VCOMP_SORT_DETAIL_TIMING:-false}"
VCOMP_PHASE1_SHARDS="${VCOMP_PHASE1_SHARDS:-8}"
VCOMP_MATERIALIZE_WORKERS="${VCOMP_MATERIALIZE_WORKERS:-48}"
COMPRESSION_TYPE="${COMPRESSION_TYPE:-none}"

[[ ! -d "${DB_DIR}" ]] || { echo "[ERROR] DB already exists: ${DB_DIR}" >&2; exit 1; }
mkdir -p "${DB_DIR}" "${RAW_DIR}"
ulimit -n 1048576

# ── Build command ──
cmd=(
  "${DB_BENCH}"
  --statistics=1
  --stats_interval_seconds=60
  --stats_per_interval=1
  --report_interval_seconds=10
  --report_file="${REP_FILE}"
  # --cache_size="${CACHE_BYTES}"
  --enable_index_compression=false
  --bloom_bits=10
  --disable_wal=true
  --max_background_jobs="${BG_JOBS}"
  --num="${NKEYS}"
  --key_size="${KEY_SIZE}"
  --value_size="${VALUE_SIZE}"
  --threads=1
  --memtablerep=vector
  --seed=12345678
  --db="${DB_DIR}"
  --use_direct_reads=true
  --use_direct_io_for_flush_and_compaction=true
  --compression_type="${COMPRESSION_TYPE}"
)

if [[ "${MODE}" == "baseline" ]]; then
  # compact0 before waitforcompaction so follow-on L1→L2 triggers are waited
  # for; leaves the tree in a fully settled state.
  cmd+=(
    --benchmarks=fillrandom,flush,compact0,waitforcompaction,stats,levelstats
  )
elif [[ "${MODE}" == "l0only" || "${MODE}" == "l0compact" ]]; then
  # Same write path as baseline (identical cmd[] above); the ONLY variable is
  # the compaction strategy. Auto compaction is disabled so every flushed SST
  # piles up in L0. With auto off, L0 never shrinks, so all write-stall limits
  # must be lifted or the load deadlocks (L0 stop / pending-bytes stall).
  STALL_TRIGGER="${STALL_TRIGGER:-1073741824}"
  cmd+=(
    --disable_auto_compactions=true
    --level0_file_num_compaction_trigger="${STALL_TRIGGER}"
    --level0_slowdown_writes_trigger="${STALL_TRIGGER}"
    --level0_stop_writes_trigger="${STALL_TRIGGER}"
    --soft_pending_compaction_bytes_limit=0
    --hard_pending_compaction_bytes_limit=0
  )
  if [[ "${MODE}" == "l0only" ]]; then
    # (ii-fail) leave everything overlapping in L0 — no compaction at all.
    cmd+=(--benchmarks=fillrandom,flush,stats,levelstats)
  else
    # l0compact: one final full CompactRange (kForceOptimized) collapses all of
    # L0 into a single non-overlapping sorted run at the bottom level.
    cmd+=(--benchmarks=fillrandom,flush,compact,stats,levelstats)
  fi
else
  cmd+=(
    --benchmarks=fillvirtual,flush,compact0,waitforcompaction,stats,levelstats
    --use_virtual_compaction=true
    --plr_error_bound="${PLR_ERROR_BOUND}"
    --memtable_flush_size="${MEMTABLE_FLUSH_MB}"
  )
  [[ -z "${VCOMP_REGISTER_BATCH_MAX}" ]] || \
    cmd+=(--vcomp_register_batch_max="${VCOMP_REGISTER_BATCH_MAX}")
  [[ -z "${VCOMP_VISIBLE_L0_BATCH_MB}" ]] || \
    cmd+=(--vcomp_visible_l0_batch_mb="${VCOMP_VISIBLE_L0_BATCH_MB}")
  cmd+=(--vcomp_log_apply_timing="${VCOMP_LOG_APPLY_TIMING}")
  cmd+=(--vcomp_sort_detail_timing="${VCOMP_SORT_DETAIL_TIMING}")
  cmd+=(--vcomp_phase1_shards="${VCOMP_PHASE1_SHARDS}")
  cmd+=(--vcomp_materialize_workers="${VCOMP_MATERIALIZE_WORKERS}")
fi

# ── Save run info ──
{ printf '#!/usr/bin/env bash\n'; printf '%q ' "${cmd[@]}"; echo; } > "${RAW_DIR}/load_cmd.sh"
chmod +x "${RAW_DIR}/load_cmd.sh"

echo "=== ${MODE} | ${TARGET_DB_GB}GB | $(date) ==="
{ echo "[MODE] ${MODE}"; echo "[RUN_CMD]"; printf '%q ' "${cmd[@]}"; echo; } | tee "${OUT_FILE}"

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
/usr/bin/time -v -o "${TIME_FILE}" "${cmd[@]}" >> "${OUT_FILE}" 2>&1
exit_code=$?
set -e

cleanup_iostat

# ── Collect after-stats ──
end_ts="$(date +%s)"
echo "${end_ts}" > "${RAW_DIR}/end_epoch.txt"
elapsed=$((end_ts - start_ts))
echo "${elapsed}" > "${RAW_DIR}/elapsed_sec.txt"
peak_rss_kb="$(extract_peak_rss_kb "${TIME_FILE}")"
peak_rss_gb="$(rss_kb_to_gb "${peak_rss_kb}")"
echo "${peak_rss_kb}" > "${RAW_DIR}/peak_rss_kb.txt"
echo "${peak_rss_gb}" > "${RAW_DIR}/peak_rss_gb.txt"
cat /proc/diskstats > "${RAW_DIR}/diskstats.end"
cat /proc/stat > "${RAW_DIR}/procstat.end"

# ── Summary ──
db_size="$(du -sh "${DB_DIR}" 2>/dev/null | cut -f1)"
echo ""
echo "=== Summary ==="
echo "Mode:      ${MODE}"
echo "DB Size:   ${db_size}"
echo "Keys:      ${NKEYS}"
echo "Threads:   1"
echo "Memtable:  vector"
echo "Elapsed:   ${elapsed} sec"
echo "Peak RSS:  ${peak_rss_gb:-NA} GiB (${peak_rss_kb:-NA} KB)"
echo "Log:       ${RUN_DIR}"
echo "DB:        ${DB_DIR}"
echo "Exit code: ${exit_code}"
exit "${exit_code}"
