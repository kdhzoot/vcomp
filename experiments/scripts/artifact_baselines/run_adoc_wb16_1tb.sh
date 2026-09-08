#!/usr/bin/env bash
# Figure 4 ADOC-on sensitivity with max_write_buffer_number=16.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

ADOC_ROOT="${ADOC_ROOT:-${ARTIFACT_ROOT}/external_baselines/adoc}"
ADOC_DB_BENCH="${ADOC_DB_BENCH:-${ADOC_ROOT}/db_bench}"
EXPECTED_COMMIT="${EXPECTED_COMMIT:-5ed60f50d6cd8259b94e7f842ff06c6ab4df40a1}"
EXPECTED_SHA256="${EXPECTED_SHA256:-68eb52fb2c6db555f47fa877c015015a98ee7ecc879fe3574004c9ec47f47a05}"
TARGET_GIB="${TARGET_GIB:-1000}"
PILOT_GIB="${PILOT_GIB:-1}"
RUN_ID="${RUN_ID:-paper_adoc_wb16_${TARGET_GIB}gib_$(date -u '+%y%m%d_%H%M%S')}"
RUN_ROOT="${RUN_ROOT:-${ARTIFACT_ROOT}/log_loads/${RUN_ID}}"
DB_ROOT="${DB_ROOT:-${VCOMP_DB_ROOT%/}/exp/${RUN_ID}}"
ADOC_RUNNER="${SCRIPT_DIR}/run_adoc_1tb_comparison.sh"

require_executable "${ADOC_DB_BENCH}" "ADOC release db_bench"
require_file "${ADOC_RUNNER}" "ADOC comparison runner"
require_positive_uint TARGET_GIB
require_positive_uint PILOT_GIB
[[ "$(git -C "${ADOC_ROOT}" rev-parse HEAD)" == "${EXPECTED_COMMIT}" ]] || \
  die "ADOC commit mismatch"
[[ "$(sha256sum "${ADOC_DB_BENCH}" | awk '{print $1}')" == "${EXPECTED_SHA256}" ]] || \
  die "ADOC binary mismatch"
if pgrep -x db_bench >/dev/null 2>&1 || pgrep -x titandb_bench >/dev/null 2>&1; then
  die "another storage benchmark is active"
fi
[[ ! -e "${RUN_ROOT}" ]] || die "log output already exists: ${RUN_ROOT}"
[[ ! -e "${DB_ROOT}" ]] || die "DB output already exists: ${DB_ROOT}"
mkdir -p "${RUN_ROOT}" "${DB_ROOT}"

run_case() {
  local case_id="$1" target_gib="$2"
  local case_log="${RUN_ROOT}/${case_id}"
  local case_db="${DB_ROOT}/${case_id}"
  env \
    ADOC_ROOT="${ADOC_ROOT}" \
    ADOC_DB_BENCH="${ADOC_DB_BENCH}" \
    TARGET_GB="${target_gib}" \
    RUN_ID="${case_id}" \
    RUN_ROOT="${case_log}" \
    DB_ROOT="${case_db}" \
    BG_JOBS=48 \
    CORE_NUM=48 \
    SUBCOMPACTIONS=1 \
    WRITE_BUFFER_SIZE=67108864 \
    MAX_MEMTABLE_SIZE=536870912 \
    MAX_WRITE_BUFFER_NUMBER=16 \
    MIN_WRITE_BUFFER_NUMBER_TO_MERGE=1 \
    SOFT_PENDING_COMPACTION_BYTES_LIMIT=68719476736 \
    HARD_PENDING_COMPACTION_BYTES_LIMIT=137438953472 \
    REPORT_INTERVAL_SECONDS=1 \
    DOTA_TUNING_GAP=1 \
    SYSTEM_ORDER=adoc_on \
    ADOC_RELEASE_BUILD_CONFIRMED=1 \
    DRY_RUN=0 \
    bash "${ADOC_RUNNER}"

  rg -q -- '--max_write_buffer_number=16' "${case_log}/adoc_on/raw/load_cmd.sh" || \
    die "${case_id}: command did not request 16 buffers"
  rg -q 'Options.max_write_buffer_number: +16$' "${case_db}/adoc_on"/LOG* || \
    die "${case_id}: effective buffer count is not 16"
  read -r status read_found read_requested < <(
    awk -F '\t' '$1=="adoc_on" {print $2, $17, $18}' "${case_log}/summary.tsv"
  )
  [[ "${status}" == "ok" && "${read_found}" == "10000" && \
     "${read_requested}" == "10000" ]] || \
    die "${case_id}: validation failed: status=${status}, reads=${read_found}/${read_requested}"
}

run_case "pilot_${PILOT_GIB}gib" "${PILOT_GIB}"
run_case "adoc_${TARGET_GIB}gib" "${TARGET_GIB}"

cp -- "$0" "${RUN_ROOT}/run_adoc_wb16_1tb.sh"
chmod a-w "${RUN_ROOT}/run_adoc_wb16_1tb.sh"
cp -- "${RUN_ROOT}/adoc_${TARGET_GIB}gib/summary.tsv" \
  "${RUN_ROOT}/validated_summary.tsv"
touch "${RUN_ROOT}/COMPLETED"
printf 'Validated ADOC-on wb16 pilot and full run: %s\n' "${RUN_ROOT}"
