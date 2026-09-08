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
source "${SCRIPT_DIR}/../../lib/common.sh"
DB_BENCH="${DB_BENCH:-${VCOMP_DB_BENCH}}"

require_env MODE TARGET_DB_GB DB_ROOT
require_positive_uint TARGET_DB_GB
case "${MODE}" in
  baseline|vcomp|l0only|l0compact) ;;
  *) echo "[ERROR] MODE must be 'baseline', 'vcomp', 'l0only', or 'l0compact'" >&2; exit 1 ;;
esac
require_executable "${DB_BENCH}" "db_bench"
if [[ "${ALLOW_CONCURRENT_DB_BENCH:-0}" == "1" ]]; then
  echo "[WARN] ALLOW_CONCURRENT_DB_BENCH=1: result may contain CPU/I/O interference" >&2
else
  require_no_db_bench "${MODE} load"
fi

# ── Common parameters ──
KEY_SIZE="${KEY_SIZE:-24}"
VALUE_SIZE="${VALUE_SIZE:-1000}"
BATCH_SIZE="${BATCH_SIZE:-1}"
MEMTABLE_REP="${MEMTABLE_REP:-vector}"
require_positive_uint KEY_SIZE
require_positive_uint VALUE_SIZE
require_positive_uint BATCH_SIZE
[[ -n "${MEMTABLE_REP}" ]] || die "MEMTABLE_REP must not be empty"
if [[ "${MODE}" == "baseline" && "${MEMTABLE_REP}" != "vector" ]]; then
  die "Baseline loads require MEMTABLE_REP=vector"
fi
KV_SIZE=$((KEY_SIZE + VALUE_SIZE))

RUN_TS="$(date '+%y%m%d_%H%M%S')"
RUN_TAG="${RUN_TAG:+_${RUN_TAG}}"
RUN_DIR="${LOG_DIR:-${ARTIFACT_ROOT}/log_loads/${MODE}_${RUN_TS}_${TARGET_DB_GB}gb${RUN_TAG}}"
DB_DIR="${DB_DIR:-${DB_ROOT%/}/${MODE}_${TARGET_DB_GB}gb}"
RAW_DIR="${RUN_DIR}/raw"
REP_FILE="${RUN_DIR}/report.rep"
OUT_FILE="${RUN_DIR}/bench.out"
TIME_FILE="${RAW_DIR}/time.out"
IOSTAT_PID=""

cleanup_iostat() {
  stop_process "${IOSTAT_PID:-}"
  IOSTAT_PID=""
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
SUBCOMPACTIONS="${SUBCOMPACTIONS:-1}"
VCOMP_REGISTER_BATCH_MAX="${VCOMP_REGISTER_BATCH_MAX:-256}"
VCOMP_VISIBLE_L0_BATCH_MB="${VCOMP_VISIBLE_L0_BATCH_MB:-0}"
VCOMP_LOG_APPLY_TIMING="${VCOMP_LOG_APPLY_TIMING:-true}"
VCOMP_SORT_DETAIL_TIMING="${VCOMP_SORT_DETAIL_TIMING:-false}"
VCOMP_PHASE1_SHARDS="${VCOMP_PHASE1_SHARDS:-8}"
VCOMP_MATERIALIZE_WORKERS="${VCOMP_MATERIALIZE_WORKERS:-48}"
COMPRESSION_TYPE="${COMPRESSION_TYPE:-none}"
WRITE_BUFFER_SIZE="${WRITE_BUFFER_SIZE:-67108864}"
MAX_WRITE_BUFFER_NUMBER="${MAX_WRITE_BUFFER_NUMBER:-2}"
MIN_WRITE_BUFFER_NUMBER_TO_MERGE="${MIN_WRITE_BUFFER_NUMBER_TO_MERGE:-1}"
ALLOW_CONCURRENT_MEMTABLE_WRITE="${ALLOW_CONCURRENT_MEMTABLE_WRITE:-true}"
USE_DIRECT_READS="${USE_DIRECT_READS:-true}"
USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION="${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION:-true}"
ENABLE_BLOB_FILES="${ENABLE_BLOB_FILES:-false}"
MIN_BLOB_SIZE="${MIN_BLOB_SIZE:-0}"
BLOB_FILE_SIZE="${BLOB_FILE_SIZE:-268435456}"
BLOB_COMPRESSION_TYPE="${BLOB_COMPRESSION_TYPE:-none}"
ENABLE_BLOB_GARBAGE_COLLECTION="${ENABLE_BLOB_GARBAGE_COLLECTION:-false}"
BLOB_GARBAGE_COLLECTION_AGE_CUTOFF="${BLOB_GARBAGE_COLLECTION_AGE_CUTOFF:-0.25}"
BLOB_GARBAGE_COLLECTION_FORCE_THRESHOLD="${BLOB_GARBAGE_COLLECTION_FORCE_THRESHOLD:-1.0}"
BLOB_COMPACTION_READAHEAD_SIZE="${BLOB_COMPACTION_READAHEAD_SIZE:-0}"
BLOB_FILE_STARTING_LEVEL="${BLOB_FILE_STARTING_LEVEL:-0}"
require_positive_uint SUBCOMPACTIONS
require_positive_uint WRITE_BUFFER_SIZE
require_positive_uint MAX_WRITE_BUFFER_NUMBER
require_positive_uint MIN_WRITE_BUFFER_NUMBER_TO_MERGE
[[ "${ALLOW_CONCURRENT_MEMTABLE_WRITE}" == "true" || \
   "${ALLOW_CONCURRENT_MEMTABLE_WRITE}" == "false" ]] || \
  die "ALLOW_CONCURRENT_MEMTABLE_WRITE must be true or false"
for io_flag in USE_DIRECT_READS USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION; do
  [[ "${!io_flag}" == "true" || "${!io_flag}" == "false" ]] || \
    die "${io_flag} must be true or false: ${!io_flag}"
done
for blob_flag in ENABLE_BLOB_FILES ENABLE_BLOB_GARBAGE_COLLECTION; do
  [[ "${!blob_flag}" == "true" || "${!blob_flag}" == "false" ]] || \
    die "${blob_flag} must be true or false: ${!blob_flag}"
done
require_uint MIN_BLOB_SIZE
require_positive_uint BLOB_FILE_SIZE
require_uint BLOB_COMPACTION_READAHEAD_SIZE
require_uint BLOB_FILE_STARTING_LEVEL
if [[ "${ENABLE_BLOB_GARBAGE_COLLECTION}" == "true" && \
      "${ENABLE_BLOB_FILES}" != "true" ]]; then
  die "Blob garbage collection requires ENABLE_BLOB_FILES=true"
fi

[[ ! -e "${DB_DIR}" ]] || die "DB output already exists: ${DB_DIR}"
[[ ! -e "${RUN_DIR}" ]] || die "Log output already exists: ${RUN_DIR}"
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
  --subcompactions="${SUBCOMPACTIONS}"
  --write_buffer_size="${WRITE_BUFFER_SIZE}"
  --max_write_buffer_number="${MAX_WRITE_BUFFER_NUMBER}"
  --min_write_buffer_number_to_merge="${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}"
  --num="${NKEYS}"
  --key_size="${KEY_SIZE}"
  --value_size="${VALUE_SIZE}"
  --batch_size="${BATCH_SIZE}"
  --threads=1
  --memtablerep="${MEMTABLE_REP}"
  --allow_concurrent_memtable_write="${ALLOW_CONCURRENT_MEMTABLE_WRITE}"
  --seed=12345678
  --db="${DB_DIR}"
  --use_direct_reads="${USE_DIRECT_READS}"
  --use_direct_io_for_flush_and_compaction="${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}"
  --compression_type="${COMPRESSION_TYPE}"
)

if [[ "${ENABLE_BLOB_FILES}" == "true" ]]; then
  cmd+=(
    --enable_blob_files=true
    --min_blob_size="${MIN_BLOB_SIZE}"
    --blob_file_size="${BLOB_FILE_SIZE}"
    --blob_compression_type="${BLOB_COMPRESSION_TYPE}"
    --enable_blob_garbage_collection="${ENABLE_BLOB_GARBAGE_COLLECTION}"
    --blob_garbage_collection_age_cutoff="${BLOB_GARBAGE_COLLECTION_AGE_CUTOFF}"
    --blob_garbage_collection_force_threshold="${BLOB_GARBAGE_COLLECTION_FORCE_THRESHOLD}"
    --blob_compaction_readahead_size="${BLOB_COMPACTION_READAHEAD_SIZE}"
    --blob_file_starting_level="${BLOB_FILE_STARTING_LEVEL}"
  )
fi

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
drop_page_cache

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
echo "Memtable:  ${MEMTABLE_REP}"
echo "Buffers:   ${MAX_WRITE_BUFFER_NUMBER} x ${WRITE_BUFFER_SIZE} bytes; min_merge=${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}"
echo "Subcomp.:  ${SUBCOMPACTIONS}"
echo "Direct I/O: reads=${USE_DIRECT_READS}, flush/compaction=${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}"
echo "BlobDB:    enabled=${ENABLE_BLOB_FILES}, gc=${ENABLE_BLOB_GARBAGE_COLLECTION}, min_blob=${MIN_BLOB_SIZE}, blob_file=${BLOB_FILE_SIZE}"
echo "Elapsed:   ${elapsed} sec"
echo "Peak RSS:  ${peak_rss_gb:-NA} GiB (${peak_rss_kb:-NA} KB)"
echo "Log:       ${RUN_DIR}"
echo "DB:        ${DB_DIR}"
echo "Exit code: ${exit_code}"
exit "${exit_code}"
