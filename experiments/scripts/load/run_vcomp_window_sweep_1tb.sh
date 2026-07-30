#!/usr/bin/env bash
# 1TB vcomp: no-fix (visible window=0 -> 1.6GB) vs fix (256MB = ~4 L0 files, score~1)
# Frequent stats (stats_dump_period_sec=5) to capture per-level score timeline.
# Read/keep both DBs for coverage min/max comparison. New paths only (no destroy of existing).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

DBB="${DB_BENCH:-${VCOMP_DB_BENCH}}"
NUM="${NUM:-1048576000}" # matches motivation_baseline_1000gb (key24/val1000)
BG_JOBS="${BG_JOBS:-48}"
MATERIALIZE_WORKERS="${VCOMP_MATERIALIZE_WORKERS:-48}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
LOGROOT="${LOG_ROOT:-${ARTIFACT_ROOT}/log_loads/vcomp_window_1tb_${RUN_ID}}"
DB_ROOT="${DB_ROOT:-${VCOMP_DB_ROOT}/exp/vcomp_window_1tb_${RUN_ID}}"

require_executable "${DBB}" "db_bench"
require_positive_uint NUM
require_positive_uint BG_JOBS
require_positive_uint MATERIALIZE_WORKERS
require_no_db_bench "1TB visible-window sweep"
[[ ! -e "${LOGROOT}" ]] || die "Log output already exists: ${LOGROOT}"
[[ ! -e "${DB_ROOT}" ]] || die "DB output already exists: ${DB_ROOT}"
mkdir -p "${LOGROOT}" "${DB_ROOT}"

run_one() {
  local tag="$1" window="$2" db="$3"
  local ld="${LOGROOT}/${tag}"
  local -a cmd
  mkdir -p "${ld}"
  cmd=(
    "${DBB}"
    --statistics=1 --stats_dump_period_sec=5 --stats_interval_seconds=5 --stats_per_interval=1
    --report_interval_seconds=10 --report_file="${ld}/report.rep"
    --enable_index_compression=false --bloom_bits=10 --disable_wal=true
    --max_background_jobs="${BG_JOBS}" --num="${NUM}" --key_size=24 --value_size=1000
    --threads=1 --memtablerep=vector --seed=12345678 --db="${db}"
    --use_direct_reads=true --use_direct_io_for_flush_and_compaction=true --compression_type=none
    --benchmarks=fillvirtual,flush,compact0,waitforcompaction,stats,levelstats
    --use_virtual_compaction=true --plr_error_bound=8 --memtable_flush_size=64
    --vcomp_register_batch_max=256 --vcomp_visible_l0_batch_mb="${window}"
    --vcomp_log_apply_timing=false --vcomp_sort_detail_timing=false
    --vcomp_phase1_shards=8 --vcomp_materialize_workers="${MATERIALIZE_WORKERS}"
  )
  { printf '#!/usr/bin/env bash\n'; printf '%q ' "${cmd[@]}"; echo; } > "${ld}/load_cmd.sh"
  chmod +x "${ld}/load_cmd.sh"
  echo "[$(date '+%T')] START $tag (visible_l0_batch_mb=$window) -> $db"
  drop_page_cache
  /usr/bin/time -v -o "${ld}/time.out" "${cmd[@]}" > "${ld}/bench.out" 2>&1
  echo "[$(date '+%T')] DONE $tag  size=$(du -sh "$db" 2>/dev/null | cut -f1)  elapsed=$(awk '/Elapsed/{print $NF}' "${ld}/time.out" 2>/dev/null)"
}

run_one nofix 0 "${DB_ROOT}/nofix"
run_one fix 256 "${DB_ROOT}/fix"
echo "[$(date '+%T')] ALL DONE"
