#!/usr/bin/env bash
# Run the Figure 4 clean baseline with only max_write_buffer_number changed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

CLEAN_ROOT="${CLEAN_ROOT:-${REPO_ROOT}/../rocksdb-f455-release}"
CLEAN_DB_BENCH="${CLEAN_DB_BENCH:-${CLEAN_ROOT}/db_bench}"
EXPECTED_COMMIT="${EXPECTED_COMMIT:-f455ab7bd6a8c67f00d48075bb310f131d9fae5f}"
EXPECTED_BINARY_SHA256="${EXPECTED_BINARY_SHA256:-8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b}"
TARGET_GIB="${TARGET_GIB:-1000}"
PILOT_GIB="${PILOT_GIB:-1}"
READS="${READS:-10000}"
RUN_ID="${RUN_ID:-paper_clean_vector_wb16_${TARGET_GIB}gib_$(date -u '+%y%m%d_%H%M%S')}"
RUN_ROOT="${RUN_ROOT:-${ARTIFACT_ROOT}/log_loads/${RUN_ID}}"
DB_ROOT="${DB_ROOT:-${VCOMP_DB_ROOT%/}/exp/${RUN_ID}}"
SWEEP_RUNNER="${SCRIPT_DIR}/run_clean_vector_bgjob_sweep.sh"

require_executable "${CLEAN_DB_BENCH}" "clean RocksDB db_bench"
require_file "${SWEEP_RUNNER}" "baseline sweep runner"
require_positive_uint TARGET_GIB
require_positive_uint PILOT_GIB
require_positive_uint READS
[[ "$(git -C "${CLEAN_ROOT}" rev-parse HEAD)" == "${EXPECTED_COMMIT}" ]] || \
  die "clean RocksDB commit mismatch"
[[ "$(sha256sum "${CLEAN_DB_BENCH}" | awk '{print $1}')" == \
   "${EXPECTED_BINARY_SHA256}" ]] || die "clean db_bench binary mismatch"
if pgrep -x db_bench >/dev/null 2>&1 || pgrep -x titandb_bench >/dev/null 2>&1; then
  die "another storage benchmark is active"
fi
[[ ! -e "${RUN_ROOT}" ]] || die "log output already exists: ${RUN_ROOT}"
[[ ! -e "${DB_ROOT}" ]] || die "DB output already exists: ${DB_ROOT}"

env \
  CLEAN_ROOT="${CLEAN_ROOT}" \
  CLEAN_DB_BENCH="${CLEAN_DB_BENCH}" \
  EXPECTED_COMMIT="${EXPECTED_COMMIT}" \
  TARGET_GIB="${TARGET_GIB}" \
  PILOT_GIB="${PILOT_GIB}" \
  RUN_PILOT=1 \
  BG_JOBS_LIST=48 \
  WRITE_BUFFER_SIZE=67108864 \
  MAX_WRITE_BUFFER_NUMBER=16 \
  MIN_WRITE_BUFFER_NUMBER_TO_MERGE=1 \
  ALLOW_CONCURRENT_MEMTABLE_WRITE=true \
  RUN_ID="${RUN_ID}" \
  RUN_ROOT="${RUN_ROOT}" \
  DB_ROOT="${DB_ROOT}" \
  WAIT_FOR_IDLE=0 \
  DRY_RUN=0 \
  bash "${SWEEP_RUNNER}"

full_log_dir="${RUN_ROOT}/baseline_bg48"
full_db_dir="${DB_ROOT}/baseline_bg48"
num_keys=$((TARGET_GIB * 1024 * 1024 * 1024 / 1024))
declare -a reopen_cmd=(
  "${CLEAN_DB_BENCH}"
  --use_existing_db=true
  --statistics=1
  --max_background_jobs=48
  --write_buffer_size=67108864
  --max_write_buffer_number=16
  --min_write_buffer_number_to_merge=1
  --num="${num_keys}"
  --reads="${READS}"
  --key_size=24
  --value_size=1000
  --threads=1
  --memtablerep=vector
  --allow_concurrent_memtable_write=true
  --seed=12345678
  --db="${full_db_dir}"
  --use_direct_reads=true
  --use_direct_io_for_flush_and_compaction=true
  --compression_type=none
  --benchmarks=waitforcompaction,readrandom,stats,levelstats
)

mkdir -p "${RUN_ROOT}/runner_snapshot"
cp -- "$0" "${RUN_ROOT}/runner_snapshot/run_clean_vector_baseline_wb16_1tb.sh"
chmod a-w "${RUN_ROOT}/runner_snapshot/run_clean_vector_baseline_wb16_1tb.sh"
{
  printf '#!/usr/bin/env bash\n'
  printf '%q ' "${reopen_cmd[@]}"
  printf '\n'
} > "${full_log_dir}/raw/reopen_cmd.sh"
chmod a-w "${full_log_dir}/raw/reopen_cmd.sh"

drop_page_cache
reopen_start="$(date +%s)"
set +e
/usr/bin/time -v -o "${full_log_dir}/raw/reopen_time.out" \
  "${reopen_cmd[@]}" > "${full_log_dir}/reopen.out" 2>&1
reopen_rc=$?
set -e
reopen_end="$(date +%s)"
printf '%s\n' "${reopen_rc}" > "${full_log_dir}/raw/reopen_exit_code.txt"
[[ "${reopen_rc}" -eq 0 ]] || die "reopen validation failed: ${reopen_rc}"

read_counts="$(sed -n 's/.*(\([0-9][0-9]*\) of \([0-9][0-9]*\) found).*/\1 \2/p' \
  "${full_log_dir}/reopen.out" | tail -1)"
read_found="${read_counts%% *}"
read_requested="${read_counts##* }"
[[ "${read_found}" == "${READS}" && "${read_requested}" == "${READS}" ]] || \
  die "reopen read validation mismatch: ${read_counts:-missing}"

IFS=$'\t' read -r case_id status target_gib bg_jobs write_buffer_size \
  max_write_buffers elapsed_sec fillrandom_sec peak_rss_kb final_db_bytes \
  log_dir db_dir < <(awk -F '\t' '$1=="baseline_bg48" {print}' "${RUN_ROOT}/summary.tsv")
[[ "${status}" == "ok" && "${max_write_buffers}" == "16" ]] || \
  die "load summary validation failed"
memtable_stops="$(awk '
  /Write Stall \(count\): cf-/ {line=$0}
  END {if (match(line, /memtable-limit-stops: [0-9]+/)) {
    value=substr(line, RSTART, RLENGTH); sub(/.*: /, "", value); print value
  }}' "${full_log_dir}/bench.out")"
total_stall_us="$(awk '/^rocksdb.db.write.stall / {for(i=1;i<=NF;i++) if($i=="SUM") v=$(i+2)} END{print v+0}' \
  "${full_log_dir}/bench.out")"

printf 'system\tstatus\ttarget_gib\tnum_keys\telapsed_sec\tfillrandom_sec\tpeak_rss_kb\tfinal_db_bytes\tmax_background_jobs\twrite_buffer_size\tmax_write_buffer_number\tmemtable_limit_stops\ttotal_write_stall_us\treopen_elapsed_sec\tread_found\tread_requested\tlog_dir\tdb_dir\n' \
  > "${RUN_ROOT}/validated_summary.tsv"
printf 'clean_vector_wb16\tok\t%s\t%s\t%s\t%s\t%s\t%s\t48\t67108864\t16\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "${target_gib}" "${num_keys}" "${elapsed_sec}" "${fillrandom_sec}" \
  "${peak_rss_kb}" "${final_db_bytes}" "${memtable_stops:-NA}" \
  "${total_stall_us}" "$((reopen_end - reopen_start))" "${read_found}" \
  "${read_requested}" "${log_dir}" "${db_dir}" >> "${RUN_ROOT}/validated_summary.tsv"
sha256sum "${CLEAN_DB_BENCH}" "${SWEEP_RUNNER}" "$0" > "${RUN_ROOT}/VALIDATED_SHA256SUMS"
touch "${RUN_ROOT}/VALIDATED"
printf 'Validated baseline wb16: elapsed=%ss, reads=%s/%s, DB=%s\n' \
  "${elapsed_sec}" "${read_found}" "${read_requested}" "${full_db_dir}"
