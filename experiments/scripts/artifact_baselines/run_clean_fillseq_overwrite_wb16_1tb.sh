#!/usr/bin/env bash
# Exact Figure 4 Fillseq+10% overwrite sensitivity with 16 write buffers.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

TARGET_GIB="${TARGET_GIB:-1000}"
PILOT_GIB="${PILOT_GIB:-1}"
RUN_ID="${RUN_ID:-paper_clean_fillseq_overwrite_wb16_${TARGET_GIB}gib_$(date -u '+%y%m%d_%H%M%S')}"
RUN_ROOT="${RUN_ROOT:-${ARTIFACT_ROOT}/log_loads/${RUN_ID}}"
DB_ROOT="${DB_ROOT:-${VCOMP_DB_ROOT%/}/exp/${RUN_ID}}"
PHASE_RUNNER="${SCRIPT_DIR}/run_clean_rocksdb_fillseq_overwrite.sh"

require_positive_uint TARGET_GIB
require_positive_uint PILOT_GIB
require_file "${PHASE_RUNNER}" "Fillseq+overwrite runner"
if pgrep -x db_bench >/dev/null 2>&1 || pgrep -x titandb_bench >/dev/null 2>&1; then
  die "another storage benchmark is active"
fi
[[ ! -e "${RUN_ROOT}" ]] || die "log output already exists: ${RUN_ROOT}"
[[ ! -e "${DB_ROOT}" ]] || die "DB output already exists: ${DB_ROOT}"
mkdir -p "${RUN_ROOT}" "${DB_ROOT}"

run_case() {
  local case_id="$1" target_gib="$2"
  env \
    TARGET_GIB="${target_gib}" \
    RUN_ID="${case_id}" \
    RUN_ROOT="${RUN_ROOT}/${case_id}" \
    DB_DIR="${DB_ROOT}/${case_id}" \
    BG_JOBS=48 \
    SUBCOMPACTIONS=1 \
    WRITE_BUFFER_SIZE=67108864 \
    MAX_WRITE_BUFFER_NUMBER=16 \
    MIN_WRITE_BUFFER_NUMBER_TO_MERGE=1 \
    OVERWRITE_PERCENT=10 \
    CLEAN_RELEASE_BUILD_CONFIRMED=1 \
    DRY_RUN=0 \
    bash "${PHASE_RUNNER}"
}

run_case "pilot_${PILOT_GIB}gib" "${PILOT_GIB}"
run_case "fillseq_overwrite_${TARGET_GIB}gib" "${TARGET_GIB}"

cp -- "$0" "${RUN_ROOT}/run_clean_fillseq_overwrite_wb16_1tb.sh"
chmod a-w "${RUN_ROOT}/run_clean_fillseq_overwrite_wb16_1tb.sh"
touch "${RUN_ROOT}/COMPLETED"
printf 'Validated Fillseq+overwrite wb16 pilot and full run: %s\n' "${RUN_ROOT}"
