#!/usr/bin/env bash
: <<'EXAMPLE'
# Baseline (real fillrandom + compaction)
MODE=baseline TARGET_DB_GB=10 DB_ROOT=/work/vcomp bash load.sh

# Virtual compaction (fillvirtual + PLR-based compaction + materialization)
MODE=vcomp TARGET_DB_GB=10 DB_ROOT=/work/vcomp bash load.sh

# Explicit size-gated visible L0 release
MODE=vcomp TARGET_DB_GB=10 DB_ROOT=/work/vcomp \
  VCOMP_RELEASE_BATCH_MAX=256 \
  VCOMP_VISIBLE_L0_BATCH_MB=4096 \
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
[[ "${MODE}" == "baseline" || "${MODE}" == "vcomp" ]] || {
  echo "[ERROR] MODE must be 'baseline' or 'vcomp'" >&2; exit 1
}
[[ -x "${DB_BENCH}" ]] || {
  echo "[ERROR] db_bench not found at ${DB_BENCH}. Run make.sh first." >&2; exit 1
}

# ── Common parameters ──
KEY_SIZE=24
VALUE_SIZE=1000
KV_SIZE=$((KEY_SIZE + VALUE_SIZE))

RUN_TS="$(date '+%y%m%d_%H%M')"
RUN_TAG="${RUN_TAG:+_${RUN_TAG}}"
RUN_DIR="${LOG_DIR:-${SCRIPT_DIR}/log_loads/${MODE}_${RUN_TS}_${TARGET_DB_GB}gb${RUN_TAG}}"
DB_DIR="${DB_DIR:-${DB_ROOT%/}/${MODE}_${TARGET_DB_GB}gb}"
RAW_DIR="${RUN_DIR}/raw"
REP_FILE="${RUN_DIR}/report.rep"
OUT_FILE="${RUN_DIR}/bench.out"

# CACHE_BYTES=$((1024 * 1024 * 1024 * CACHE_SIZE_GB))
NKEYS=$((TARGET_DB_GB * 1024 * 1024 * 1024 / KV_SIZE))

# ── PLR / vcomp parameters (only used in vcomp mode) ──
PLR_ERROR_BOUND="${PLR_ERROR_BOUND:-8}"
MEMTABLE_FLUSH_MB="${MEMTABLE_FLUSH_MB:-64}"
BG_JOBS="${BG_JOBS:-$(nproc)}"
VCOMP_RELEASE_BATCH_MAX="${VCOMP_RELEASE_BATCH_MAX:-256}"
VCOMP_VISIBLE_L0_BATCH_MB="${VCOMP_VISIBLE_L0_BATCH_MB:-4096}"
VCOMP_LOG_APPLY_TIMING="${VCOMP_LOG_APPLY_TIMING:-true}"

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
  --seed=12345678
  --db="${DB_DIR}"
  --use_direct_reads=true
  --use_direct_io_for_flush_and_compaction=true
  --compression_type=none
)

if [[ "${MODE}" == "baseline" ]]; then
  # compact0 before waitforcompaction so follow-on L1→L2 triggers are waited
  # for; leaves the tree in a fully settled state.
  cmd+=(
    --benchmarks=fillrandom,flush,compact0,waitforcompaction,stats,levelstats
  )
else
  cmd+=(
    --benchmarks=fillvirtual,flush,compact0,waitforcompaction,stats,levelstats
    --use_virtual_compaction=true
    --plr_error_bound="${PLR_ERROR_BOUND}"
    --memtable_flush_size="${MEMTABLE_FLUSH_MB}"
  )
  [[ -z "${VCOMP_RELEASE_BATCH_MAX}" ]] || \
    cmd+=(--vcomp_release_batch_max="${VCOMP_RELEASE_BATCH_MAX}")
  [[ -z "${VCOMP_VISIBLE_L0_BATCH_MB}" ]] || \
    cmd+=(--vcomp_visible_l0_batch_mb="${VCOMP_VISIBLE_L0_BATCH_MB}")
  cmd+=(--vcomp_log_apply_timing="${VCOMP_LOG_APPLY_TIMING}")
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
  iostat -dx 1 > "${RAW_DIR}/iostat.log" & iostat_pid=$!
else
  iostat_pid=""
fi

"${cmd[@]}" >> "${OUT_FILE}" 2>&1
exit_code=$?

[[ -z "${iostat_pid:-}" ]] || { kill "${iostat_pid}" 2>/dev/null || true; wait "${iostat_pid}" 2>/dev/null || true; }

# ── Collect after-stats ──
end_ts="$(date +%s)"
echo "${end_ts}" > "${RAW_DIR}/end_epoch.txt"
elapsed=$((end_ts - start_ts))
echo "${elapsed}" > "${RAW_DIR}/elapsed_sec.txt"
cat /proc/diskstats > "${RAW_DIR}/diskstats.end"
cat /proc/stat > "${RAW_DIR}/procstat.end"

# ── Summary ──
db_size="$(du -sh "${DB_DIR}" 2>/dev/null | cut -f1)"
echo ""
echo "=== Summary ==="
echo "Mode:      ${MODE}"
echo "DB Size:   ${db_size}"
echo "Keys:      ${NKEYS}"
echo "Elapsed:   ${elapsed} sec"
echo "Log:       ${RUN_DIR}"
echo "DB:        ${DB_DIR}"
echo "Exit code: ${exit_code}"
