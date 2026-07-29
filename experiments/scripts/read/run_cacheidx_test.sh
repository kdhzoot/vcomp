#!/usr/bin/env bash
# Controlled read-amp test: cache_size=1 + cache_index_and_filter_blocks=true
# -> every filter/index/data access misses the block cache -> uniform IO path
#    (no table-open tail prefetch, so tail_size=0 no longer matters).
# readonly readrandom on baseline_10tb vs vcomp_10tb_fix. Source DBs untouched.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

DBB="${DB_BENCH:-${VCOMP_DB_BENCH}}"
NUM="${NUM:-10737418240}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
OUT="${LOG_ROOT:-${ARTIFACT_ROOT}/log_runs/cacheidx_test_${RUN_ID}}"
DEV="${DISKSTAT_DEV:-md0}"
BASELINE_DB="${BASELINE_DB:-${VCOMP_DB_ROOT}/baseline_10tb}"
VCOMP_DB="${VCOMP_DB:-${VCOMP_DB_ROOT}/vcomp_10tb_fix}"
mkdir -p "$OUT"
sectors_read(){ awk -v d="$DEV" '$3==d{print $6}' /proc/diskstats; }

run(){ local nm="$1" db="$2"
  echo "[$(date '+%T')] $nm readrandom (cache_size=1, cache_index_and_filter=true)"
  local s0=$(sectors_read)
  "$DBB" --use_existing_db=true --readonly=true \
    --benchmarks=readrandom,stats --num="$NUM" --reads=1000000 --threads=1 \
    --key_size=24 --value_size=1000 --seed=87654321 --db="$db" \
    --cache_size=1 --cache_index_and_filter_blocks=true --cache_type=lru_cache \
    --bloom_bits=10 --use_direct_reads=true --compression_type=none \
    --statistics=1 > "$OUT/$nm.out" 2>&1
  local s1=$(sectors_read)
  local rmb=$(awk -v a="$s0" -v b="$s1" 'BEGIN{printf "%.1f",(b-a)*512/1048576}')
  local ops=$(grep -aoE 'readrandom *: *[0-9.]+ micros/op [0-9]+ ops/sec' "$OUT/$nm.out"|grep -oE '[0-9]+ ops/sec'|grep -oE '^[0-9]+')
  local found=$(grep -aoE 'reads [0-9]+ in [0-9]+ found' "$OUT/$nm.out"|head -1)
  echo "    -> ops/sec=$ops  disk_read=${rmb} MB  ($found)"
  echo "$nm ops=$ops disk_read_MB=$rmb $found" >> "$OUT/summary.txt"
}
: > "$OUT/summary.txt"
run baseline "${BASELINE_DB}"
run fix      "${VCOMP_DB}"
echo "=== DONE ==="; cat "$OUT/summary.txt"
