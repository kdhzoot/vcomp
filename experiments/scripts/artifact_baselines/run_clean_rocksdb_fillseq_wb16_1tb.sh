#!/usr/bin/env bash
# Wait for an existing suite, then run the exact Figure 4 Fillseq with 16 buffers.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

TARGET_GIB="${TARGET_GIB:-1000}"
PILOT_GIB="${PILOT_GIB:-1}"
WAIT_FOR_PID="${WAIT_FOR_PID:-}"
RUN_ID="${RUN_ID:-paper_clean_fillseq_wb16_${TARGET_GIB}gib_$(date -u '+%y%m%d_%H%M%S')}"
RUN_ROOT="${RUN_ROOT:-${ARTIFACT_ROOT}/log_loads/${RUN_ID}}"
DB_ROOT="${DB_ROOT:-${VCOMP_DB_ROOT%/}/exp/${RUN_ID}}"
FILLSEQ_RUNNER="${SCRIPT_DIR}/run_clean_rocksdb_fillseq_only.sh"

require_positive_uint TARGET_GIB
require_positive_uint PILOT_GIB
require_file "${FILLSEQ_RUNNER}" "Fillseq runner"
if [[ -n "${WAIT_FOR_PID}" ]]; then
  require_positive_uint WAIT_FOR_PID
  while kill -0 "${WAIT_FOR_PID}" 2>/dev/null; do
    printf '[%s] waiting for experiment-suite PID %s\n' \
      "$(date -u '+%FT%TZ')" "${WAIT_FOR_PID}"
    sleep 30
  done
fi
while pgrep -x db_bench >/dev/null 2>&1 || \
      pgrep -x titandb_bench >/dev/null 2>&1 || \
      pgrep -x ycsbc >/dev/null 2>&1; do
  printf '[%s] waiting for storage benchmark processes to drain\n' \
    "$(date -u '+%FT%TZ')"
  sleep 30
done

[[ ! -e "${RUN_ROOT}" ]] || die "log output already exists: ${RUN_ROOT}"
[[ ! -e "${DB_ROOT}" ]] || die "DB output already exists: ${DB_ROOT}"
mkdir -p "${RUN_ROOT}" "${DB_ROOT}"

run_case() {
  local case_id="$1"
  local target_gib="$2"
  local reads="$3"
  env \
    TARGET_GIB="${target_gib}" \
    RUN_ID="${case_id}" \
    RUN_ROOT="${RUN_ROOT}/${case_id}" \
    DB_DIR="${DB_ROOT}/${case_id}" \
    READS="${reads}" \
    BG_JOBS=48 \
    SUBCOMPACTIONS=1 \
    WRITE_BUFFER_SIZE=67108864 \
    MAX_WRITE_BUFFER_NUMBER=16 \
    MIN_WRITE_BUFFER_NUMBER_TO_MERGE=1 \
    CLEAN_RELEASE_BUILD_CONFIRMED=1 \
    DRY_RUN=0 \
    bash "${FILLSEQ_RUNNER}"
}

run_case "pilot_${PILOT_GIB}gib" "${PILOT_GIB}" 1000
run_case "fillseq_${TARGET_GIB}gib" "${TARGET_GIB}" 10000

cp -- "$0" "${RUN_ROOT}/run_clean_rocksdb_fillseq_wb16_1tb.sh"
chmod a-w "${RUN_ROOT}/run_clean_rocksdb_fillseq_wb16_1tb.sh"
touch "${RUN_ROOT}/COMPLETED"
printf 'Validated Fillseq wb16 pilot and full load: %s\n' "${RUN_ROOT}"
