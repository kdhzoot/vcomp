#!/usr/bin/env bash
# Detached buffered-I/O sequence requested for the paper figure:
#   1. Qualify DiffKV at 100 GiB without direct I/O.
#   2. Run DiffKV at 1,000 GiB with the same buffered-I/O policy.
#   3. Run clean RocksDB at 1,000 GiB with VectorRepFactory and the identical
#      buffered-I/O policy. Each stage starts only after the prior validation
#      and performance gates succeed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

RUN_ID="${RUN_ID:-260901_buffered_run1}"
SEQUENCE_ROOT="${SEQUENCE_ROOT:-${ARTIFACT_ROOT}/log_loads/paper_buffered_diffkv_then_clean_vector_1tb_${RUN_ID}}"

PILOT_RUN_ID="${PILOT_RUN_ID:-260901_buffered_gate1}"
PILOT_RUN_ROOT="${PILOT_RUN_ROOT:-${ARTIFACT_ROOT}/log_loads/paper_diffkv_100gib_${PILOT_RUN_ID}}"
PILOT_DB_DIR="${PILOT_DB_DIR:-${VCOMP_DB_ROOT%/}/exp/paper_diffkv_100gib_${PILOT_RUN_ID}}"
PILOT_MAX_ELAPSED_SEC="${PILOT_MAX_ELAPSED_SEC:-3600}"

DIFFKV_RUN_ROOT="${DIFFKV_RUN_ROOT:-${ARTIFACT_ROOT}/log_loads/paper_diffkv_1000gib_${RUN_ID}}"
DIFFKV_DB_DIR="${DIFFKV_DB_DIR:-${VCOMP_DB_ROOT%/}/exp/paper_diffkv_1000gib_${RUN_ID}}"
DIFFKV_ROOT="${DIFFKV_ROOT:-${ARTIFACT_ROOT}/external_baselines/diffkv}"
DIFFKV_DB_BENCH="${DIFFKV_DB_BENCH:-${DIFFKV_ROOT}/build-paper-release/titandb_bench}"

CLEAN_RUN_ROOT="${CLEAN_RUN_ROOT:-${ARTIFACT_ROOT}/log_loads/paper_clean_vector_1000gib_${RUN_ID}}"
CLEAN_DB_DIR="${CLEAN_DB_DIR:-${VCOMP_DB_ROOT%/}/exp/paper_clean_vector_1000gib_${RUN_ID}}"
CLEAN_ROOT="${CLEAN_ROOT:-${REPO_ROOT}/../rocksdb-f455-release}"
CLEAN_DB_BENCH="${CLEAN_DB_BENCH:-${CLEAN_ROOT}/db_bench}"
CLEAN_COMMIT="f455ab7bd6a8c67f00d48075bb310f131d9fae5f"
CLEAN_BINARY_SHA256="8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b"

STATUS_FILE="${SEQUENCE_ROOT}/status.txt"
USE_DIRECT_READS=false
USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION=false

set_status() {
  printf '%s\t%s\n' "$(date -u '+%FT%TZ')" "$1" > "${STATUS_FILE}"
}

on_exit() {
  local rc=$?
  if [[ "${rc}" -ne 0 ]]; then
    set_status "failed exit_code=${rc}"
  fi
}
trap on_exit EXIT

require_executable "${DIFFKV_DB_BENCH}" "DiffKV titandb_bench"
require_executable "${CLEAN_DB_BENCH}" "clean RocksDB db_bench"
require_positive_uint PILOT_MAX_ELAPSED_SEC
[[ "$(git -C "${CLEAN_ROOT}" rev-parse HEAD)" == "${CLEAN_COMMIT}" ]] || \
  die "clean RocksDB commit changed before sequence start"
[[ "$(sha256sum "${CLEAN_DB_BENCH}" | awk '{print $1}')" == \
   "${CLEAN_BINARY_SHA256}" ]] || \
  die "clean RocksDB binary changed before sequence start"
if pgrep -x db_bench >/dev/null 2>&1 || \
    pgrep -x titandb_bench >/dev/null 2>&1; then
  die "Another db_bench/titandb_bench is running"
fi
for output in "${PILOT_RUN_ROOT}" "${PILOT_DB_DIR}" \
    "${DIFFKV_RUN_ROOT}" "${DIFFKV_DB_DIR}" \
    "${CLEAN_RUN_ROOT}" "${CLEAN_DB_DIR}"; do
  [[ ! -e "${output}" ]] || die "Sequence output already exists: ${output}"
done

mkdir -p "${SEQUENCE_ROOT}/runner_snapshot"
cp -- "$0" "${SEQUENCE_ROOT}/runner_snapshot/"
cp -- "${SCRIPT_DIR}/run_diffkv_db_bench_load.sh" \
  "${SEQUENCE_ROOT}/runner_snapshot/"
cp -- "${SCRIPT_DIR}/run_clean_vector_baseline_load.sh" \
  "${SEQUENCE_ROOT}/runner_snapshot/"
chmod a-w "${SEQUENCE_ROOT}/runner_snapshot/"*.sh
sha256sum "${DIFFKV_DB_BENCH}" "${CLEAN_DB_BENCH}" \
  "${SEQUENCE_ROOT}/runner_snapshot/"*.sh > "${SEQUENCE_ROOT}/SHA256SUMS"
{
  date -u
  uname -a
  free -h
  swapon --show
  df -h /work
  cat /proc/mdstat
} > "${SEQUENCE_ROOT}/preflight.txt"

set_status "diffkv_100gib_buffered_pilot_running"
DRY_RUN=0 \
TARGET_GIB=100 \
RUN_ID="${PILOT_RUN_ID}" \
RUN_ROOT="${PILOT_RUN_ROOT}" \
DB_DIR="${PILOT_DB_DIR}" \
DIFFKV_ROOT="${DIFFKV_ROOT}" \
DIFFKV_DB_BENCH="${DIFFKV_DB_BENCH}" \
USE_DIRECT_READS="${USE_DIRECT_READS}" \
USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION="${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}" \
bash "${SCRIPT_DIR}/run_diffkv_db_bench_load.sh"

pilot_elapsed="$(awk -F '\t' 'NR == 2 {print $5}' "${PILOT_RUN_ROOT}/summary.tsv")"
[[ "${pilot_elapsed}" =~ ^[0-9]+$ ]] || die "missing 100 GiB pilot elapsed time"
(( pilot_elapsed <= PILOT_MAX_ELAPSED_SEC )) || \
  die "buffered-I/O pilot took ${pilot_elapsed}s, exceeding ${PILOT_MAX_ELAPSED_SEC}s gate"

set_status "diffkv_100gib_buffered_pilot_passed_elapsed_${pilot_elapsed}s; diffkv_1000gib_running"
DRY_RUN=0 \
TARGET_GIB=1000 \
RUN_ID="${RUN_ID}" \
RUN_ROOT="${DIFFKV_RUN_ROOT}" \
DB_DIR="${DIFFKV_DB_DIR}" \
DIFFKV_ROOT="${DIFFKV_ROOT}" \
DIFFKV_DB_BENCH="${DIFFKV_DB_BENCH}" \
USE_DIRECT_READS="${USE_DIRECT_READS}" \
USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION="${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}" \
bash "${SCRIPT_DIR}/run_diffkv_db_bench_load.sh"

set_status "diffkv_1000gib_complete_clean_vector_1000gib_starting"
sync

DRY_RUN=0 \
TARGET_GIB=1000 \
RUN_ID="${RUN_ID}" \
RUN_ROOT="${CLEAN_RUN_ROOT}" \
DB_DIR="${CLEAN_DB_DIR}" \
CLEAN_ROOT="${CLEAN_ROOT}" \
CLEAN_DB_BENCH="${CLEAN_DB_BENCH}" \
EXPECTED_COMMIT="${CLEAN_COMMIT}" \
EXPECTED_BINARY_SHA256="${CLEAN_BINARY_SHA256}" \
USE_DIRECT_READS="${USE_DIRECT_READS}" \
USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION="${USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION}" \
bash "${SCRIPT_DIR}/run_clean_vector_baseline_load.sh"

set_status "complete"
trap - EXIT
