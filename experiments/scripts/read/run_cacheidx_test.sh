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
READS="${READS:-1000000}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
OUT="${LOG_ROOT:-${ARTIFACT_ROOT}/log_runs/cacheidx_test_${RUN_ID}}"
DEV="${DISKSTAT_DEV:-md0}"
BASELINE_DB="${BASELINE_DB:-${VCOMP_DB_ROOT}/baseline_10tb}"
VCOMP_DB="${VCOMP_DB:-${VCOMP_DB_ROOT}/vcomp_10tb_fix}"

require_executable "${DBB}" "db_bench"
require_positive_uint NUM
require_positive_uint READS
require_dir "${BASELINE_DB}" "baseline DB"
require_dir "${VCOMP_DB}" "vcomp DB"
require_no_db_bench "cache-index comparison"
awk -v d="${DEV}" '$3 == d { found=1 } END { exit !found }' /proc/diskstats ||
  die "Diskstats device not found: ${DEV}"
[[ ! -e "${OUT}" ]] || die "Log output already exists: ${OUT}"
mkdir -p "${OUT}"

sectors_read() {
  awk -v d="${DEV}" '$3 == d { print $6; exit }' /proc/diskstats
}

run() {
  local nm="$1" db="$2"
  local s0 s1 rmb ops found run_rc
  echo "[$(date '+%T')] $nm readrandom (cache_size=1, cache_index_and_filter=true)"
  drop_page_cache
  s0="$(sectors_read)"
  set +e
  "${DBB}" --use_existing_db=true --readonly=true \
    --benchmarks=readrandom,stats --num="${NUM}" --reads="${READS}" --threads=1 \
    --key_size=24 --value_size=1000 --seed=87654321 --db="${db}" \
    --cache_size=1 --cache_index_and_filter_blocks=true --cache_type=lru_cache \
    --bloom_bits=10 --use_direct_reads=true --compression_type=none \
    --statistics=1 > "${OUT}/${nm}.out" 2>&1
  run_rc=$?
  set -e
  s1="$(sectors_read)"
  rmb="$(awk -v a="${s0}" -v b="${s1}" 'BEGIN { printf "%.1f", (b-a)*512/1048576 }')"
  ops="$(grep -m1 -aoE '[0-9]+ ops/sec' "${OUT}/${nm}.out" | grep -oE '^[0-9]+' || true)"
  found="$(grep -m1 -aoE 'reads [0-9]+ in [0-9]+ found' "${OUT}/${nm}.out" || true)"
  echo "    -> ops/sec=${ops:-NA}  disk_read=${rmb} MB  (${found:-NA})"
  echo "${nm} ops=${ops:-NA} disk_read_MB=${rmb} exit_code=${run_rc} ${found:-NA}" >> "${OUT}/summary.txt"
  [[ "${run_rc}" == "0" ]] || return "${run_rc}"
}
: > "${OUT}/summary.txt"
run baseline "${BASELINE_DB}"
run fix "${VCOMP_DB}"
echo "=== DONE ==="
cat "${OUT}/summary.txt"
