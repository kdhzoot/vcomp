#!/usr/bin/env bash
# Figure 4 BlobDB GC-off sensitivity: only raise max write buffers to 16.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

TARGET_GIB="${TARGET_GIB:-1000}"
PILOT_GIB="${PILOT_GIB:-1}"
RUN_ID="${RUN_ID:-paper_blobdb_wb16_${TARGET_GIB}gib_$(date -u '+%y%m%d_%H%M%S')}"
RUN_ROOT="${RUN_ROOT:-${ARTIFACT_ROOT}/log_loads/${RUN_ID}}"
DB_ROOT="${DB_ROOT:-${VCOMP_DB_ROOT%/}/exp/${RUN_ID}}"
BLOB_RUNNER="${SCRIPT_DIR}/run_clean_blobdb_gc_load.sh"

require_positive_uint TARGET_GIB
require_positive_uint PILOT_GIB
require_file "${BLOB_RUNNER}" "BlobDB runner"
if pgrep -x db_bench >/dev/null 2>&1 || pgrep -x titandb_bench >/dev/null 2>&1; then
  die "another storage benchmark is active"
fi
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
    WRITE_BUFFER_SIZE=67108864 \
    MAX_WRITE_BUFFER_NUMBER=16 \
    MIN_WRITE_BUFFER_NUMBER_TO_MERGE=1 \
    ALLOW_CONCURRENT_MEMTABLE_WRITE=true \
    USE_DIRECT_READS=true \
    USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION=true \
    MIN_BLOB_SIZE=128 \
    BLOB_FILE_SIZE=1073741824 \
    BLOB_COMPRESSION_TYPE=none \
    ENABLE_BLOB_GARBAGE_COLLECTION=false \
    BLOB_GARBAGE_COLLECTION_AGE_CUTOFF=0.25 \
    BLOB_GARBAGE_COLLECTION_FORCE_THRESHOLD=1.0 \
    BLOB_COMPACTION_READAHEAD_SIZE=0 \
    BLOB_FILE_STARTING_LEVEL=0 \
    DRY_RUN=0 \
    bash "${BLOB_RUNNER}"
}

run_case "pilot_${PILOT_GIB}gib" "${PILOT_GIB}" 1000
run_case "blobdb_${TARGET_GIB}gib" "${TARGET_GIB}" 10000

cp -- "$0" "${RUN_ROOT}/run_clean_blobdb_wb16_1tb.sh"
chmod a-w "${RUN_ROOT}/run_clean_blobdb_wb16_1tb.sh"
touch "${RUN_ROOT}/COMPLETED"
printf 'Validated BlobDB wb16 pilot and full load: %s\n' "${RUN_ROOT}"
