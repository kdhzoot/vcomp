#!/usr/bin/env bash
# Run and validate the clean-RocksDB vector-memtable baseline using the common
# load harness. The caller supplies a release db_bench built from the pinned
# clean source worktree; the historical SkipList database is never touched.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

CLEAN_ROOT="${CLEAN_ROOT:-${REPO_ROOT}/../rocksdb}"
CLEAN_DB_BENCH="${CLEAN_DB_BENCH:-${CLEAN_ROOT}/db_bench}"
EXPECTED_COMMIT="${EXPECTED_COMMIT:-f455ab7bd6a8c67f00d48075bb310f131d9fae5f}"
EXPECTED_BINARY_SHA256="${EXPECTED_BINARY_SHA256:-}"
TARGET_GIB="${TARGET_GIB:-1000}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
RUN_ROOT="${RUN_ROOT:-${ARTIFACT_ROOT}/log_loads/paper_clean_vector_${TARGET_GIB}gib_${RUN_ID}}"
DB_DIR="${DB_DIR:-${VCOMP_DB_ROOT%/}/paper_clean_vector_${TARGET_GIB}gib_${RUN_ID}}"
READS="${READS:-10000}"
USE_DIRECT_READS="${USE_DIRECT_READS:-true}"
USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION="${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION:-true}"
DRY_RUN="${DRY_RUN:-1}"

require_executable "${CLEAN_DB_BENCH}" "clean RocksDB db_bench"
require_dir "${CLEAN_ROOT}" "clean RocksDB source"
require_positive_uint TARGET_GIB
require_positive_uint READS
[[ "${DRY_RUN}" == "0" || "${DRY_RUN}" == "1" ]] || \
  die "DRY_RUN must be 0 or 1"
for io_flag in USE_DIRECT_READS USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION; do
  [[ "${!io_flag}" == "true" || "${!io_flag}" == "false" ]] || \
    die "${io_flag} must be true or false: ${!io_flag}"
done

actual_commit="$(git -C "${CLEAN_ROOT}" rev-parse HEAD)"
[[ "${actual_commit}" == "${EXPECTED_COMMIT}" ]] || \
  die "clean RocksDB commit mismatch: ${actual_commit}"
actual_sha="$(sha256sum "${CLEAN_DB_BENCH}" | awk '{print $1}')"
if [[ -n "${EXPECTED_BINARY_SHA256}" && \
      "${actual_sha}" != "${EXPECTED_BINARY_SHA256}" ]]; then
  die "clean db_bench SHA-256 mismatch: ${actual_sha}"
fi

NUM_KEYS=$((TARGET_GIB * 1024 * 1024 * 1024 / 1024))
declare -a REOPEN_CMD
REOPEN_CMD=(
  "${CLEAN_DB_BENCH}"
  --use_existing_db=true
  --num="${NUM_KEYS}"
  --reads="${READS}"
  --key_size=24
  --value_size=1000
  --threads=1
  --memtablerep=vector
  --allow_concurrent_memtable_write=false
  --seed=12345678
  --db="${DB_DIR}"
  --use_direct_reads="${USE_DIRECT_READS}"
  --use_direct_io_for_flush_and_compaction="${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}"
  --compression_type=none
  --benchmarks=waitforcompaction,readrandom,stats,levelstats
)

print_command() {
  printf '%q ' "$@"
  printf '\n'
}

printf 'Clean db_bench: %s\n' "${CLEAN_DB_BENCH}"
printf 'Commit:         %s\n' "${actual_commit}"
printf 'SHA-256:        %s\n' "${actual_sha}"
printf 'Target:         %s GiB, %s records, vector memtable\n' \
  "${TARGET_GIB}" "${NUM_KEYS}"
printf 'I/O policy:     direct_reads=%s, direct_flush_compaction=%s\n' \
  "${USE_DIRECT_READS}" "${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}"

if [[ "${DRY_RUN}" == "1" ]]; then
  printf '[load harness]\n'
  printf 'MODE=baseline TARGET_DB_GB=%q DB_BENCH=%q DB_DIR=%q LOG_DIR=%q BG_JOBS=48 SUBCOMPACTIONS=1 MEMTABLE_REP=vector USE_DIRECT_READS=%q USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION=%q bash %q\n' \
    "${TARGET_GIB}" "${CLEAN_DB_BENCH}" "${DB_DIR}" "${RUN_ROOT}" \
    "${USE_DIRECT_READS}" "${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}" \
    "${REPO_ROOT}/experiments/scripts/load/load.sh"
  printf '[reopen validation]\n'
  print_command "${REOPEN_CMD[@]}"
  exit 0
fi

if pgrep -x db_bench >/dev/null 2>&1 || \
    pgrep -x titandb_bench >/dev/null 2>&1; then
  die "Another db_bench/titandb_bench is running"
fi
[[ ! -e "${RUN_ROOT}" ]] || die "Log output already exists: ${RUN_ROOT}"
[[ ! -e "${DB_DIR}" ]] || die "DB output already exists: ${DB_DIR}"

MODE=baseline \
TARGET_DB_GB="${TARGET_GIB}" \
DB_ROOT="$(dirname "${DB_DIR}")" \
DB_DIR="${DB_DIR}" \
LOG_DIR="${RUN_ROOT}" \
DB_BENCH="${CLEAN_DB_BENCH}" \
BG_JOBS=48 \
SUBCOMPACTIONS=1 \
MEMTABLE_REP=vector \
COMPRESSION_TYPE=none \
USE_DIRECT_READS="${USE_DIRECT_READS}" \
USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION="${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}" \
bash "${REPO_ROOT}/experiments/scripts/load/load.sh"

mkdir -p "${RUN_ROOT}/runner_snapshot"
cp -- "$0" "${RUN_ROOT}/runner_snapshot/run_clean_vector_baseline_load.sh"
chmod a-w "${RUN_ROOT}/runner_snapshot/run_clean_vector_baseline_load.sh"
sha256sum "${CLEAN_DB_BENCH}" \
  "${RUN_ROOT}/runner_snapshot/run_clean_vector_baseline_load.sh" \
  > "${RUN_ROOT}/SHA256SUMS"
git -C "${CLEAN_ROOT}" rev-parse HEAD > "${RUN_ROOT}/clean_commit.txt"
git -C "${CLEAN_ROOT}" status --short > "${RUN_ROOT}/clean_git_status.txt"
git -C "${CLEAN_ROOT}" diff > "${RUN_ROOT}/clean.patch"
{
  printf '#!/usr/bin/env bash\n'
  print_command "${REOPEN_CMD[@]}"
} > "${RUN_ROOT}/raw/reopen_cmd.sh"
chmod a-w "${RUN_ROOT}/raw/reopen_cmd.sh"

rg -q 'Memtablerep: VectorRepFactory' "${RUN_ROOT}/bench.out" || \
  die "clean baseline did not activate VectorRepFactory"
if rg -q 'WARNING: (Optimization is disabled|Assertions are enabled)' \
    "${RUN_ROOT}/bench.out"; then
  die "clean baseline binary is not an assertion-free optimized build"
fi
rg -q 'waitforcompaction\(.*\): finished with status \(OK\)' \
  "${RUN_ROOT}/bench.out" || \
  die "clean baseline did not reach the settled compaction boundary"

drop_page_cache
reopen_start="$(date +%s)"
set +e
/usr/bin/time -v -o "${RUN_ROOT}/raw/reopen_time.out" \
  "${REOPEN_CMD[@]}" > "${RUN_ROOT}/reopen.out" 2>&1
reopen_rc=$?
set -e
reopen_end="$(date +%s)"
printf '%s\n' "${reopen_rc}" > "${RUN_ROOT}/raw/reopen_exit_code.txt"
[[ "${reopen_rc}" -eq 0 ]] || die "clean baseline reopen failed: ${reopen_rc}"

read_counts="$(sed -n 's/.*(\([0-9][0-9]*\) of \([0-9][0-9]*\) found).*/\1 \2/p' \
  "${RUN_ROOT}/reopen.out" | tail -1)"
read_found="${read_counts%% *}"
read_requested="${read_counts##* }"
[[ "${read_found}" == "${READS}" && "${read_requested}" == "${READS}" ]] || \
  die "clean baseline reopen validation mismatch: ${read_counts:-missing}"

elapsed_sec="$(<"${RUN_ROOT}/raw/elapsed_sec.txt")"
peak_rss_kb="$(<"${RUN_ROOT}/raw/peak_rss_kb.txt")"
final_db_bytes="$(du -sb "${DB_DIR}" | awk '{print $1}')"
reopen_elapsed_sec=$((reopen_end - reopen_start))
printf 'system\tstatus\ttarget_gib\tnum_keys\telapsed_sec\tpeak_rss_kb\tfinal_db_bytes\treopen_elapsed_sec\tread_found\tread_requested\tuse_direct_reads\tuse_direct_io_for_flush_and_compaction\tlog_dir\tdb_dir\n' \
  > "${RUN_ROOT}/validated_summary.tsv"
printf 'clean_vector\tok\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "${TARGET_GIB}" "${NUM_KEYS}" "${elapsed_sec}" "${peak_rss_kb}" \
  "${final_db_bytes}" "${reopen_elapsed_sec}" "${read_found}" \
  "${read_requested}" "${USE_DIRECT_READS}" \
  "${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}" "${RUN_ROOT}" "${DB_DIR}" \
  >> "${RUN_ROOT}/validated_summary.tsv"

printf 'Clean vector baseline completed: elapsed=%ss, reads=%s/%s, DB=%s bytes\n' \
  "${elapsed_sec}" "${read_found}" "${read_requested}" "${final_db_bytes}"
