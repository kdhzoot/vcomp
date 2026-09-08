#!/usr/bin/env bash
# Detached fallback for the buffered DiffKV -> clean Vector sequence. If the
# primary sequence exits before producing a validated clean-Vector result,
# wait for all benchmark processes to leave and run that final baseline once.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

MASTER_PID="${MASTER_PID:?MASTER_PID is required}"
SEQUENCE_ROOT="${SEQUENCE_ROOT:?SEQUENCE_ROOT is required}"
RUN_ID="${RUN_ID:-260901_buffered_run1}"
CLEAN_RUN_ROOT="${CLEAN_RUN_ROOT:-${ARTIFACT_ROOT}/log_loads/paper_clean_vector_1000gib_${RUN_ID}}"
CLEAN_DB_DIR="${CLEAN_DB_DIR:-${VCOMP_DB_ROOT%/}/exp/paper_clean_vector_1000gib_${RUN_ID}}"
CLEAN_ROOT="${CLEAN_ROOT:-${REPO_ROOT}/../rocksdb-f455-release}"
CLEAN_DB_BENCH="${CLEAN_DB_BENCH:-${CLEAN_ROOT}/db_bench}"
CLEAN_COMMIT="f455ab7bd6a8c67f00d48075bb310f131d9fae5f"
CLEAN_BINARY_SHA256="8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b"
STATUS_FILE="${SEQUENCE_ROOT}/status.txt"

require_positive_uint MASTER_PID

while kill -0 "${MASTER_PID}" 2>/dev/null; do
  sleep 30
done

if [[ -f "${CLEAN_RUN_ROOT}/validated_summary.tsv" ]]; then
  exit 0
fi

# Do not overlap a late child or an unrelated user benchmark. The fallback is
# deliberately patient because its only job is to guarantee the final stage.
while pgrep -x db_bench >/dev/null 2>&1 || \
    pgrep -x titandb_bench >/dev/null 2>&1; do
  sleep 30
done

if [[ -e "${CLEAN_RUN_ROOT}" || -e "${CLEAN_DB_DIR}" ]]; then
  printf '%s\t%s\n' "$(date -u '+%FT%TZ')" \
    'fallback_blocked_clean_vector_partial_output_exists' > "${STATUS_FILE}"
  exit 1
fi

printf '%s\t%s\n' "$(date -u '+%FT%TZ')" \
  'primary_sequence_failed; fallback_clean_vector_1000gib_running' \
  > "${STATUS_FILE}"

DRY_RUN=0 \
TARGET_GIB=1000 \
RUN_ID="${RUN_ID}" \
RUN_ROOT="${CLEAN_RUN_ROOT}" \
DB_DIR="${CLEAN_DB_DIR}" \
CLEAN_ROOT="${CLEAN_ROOT}" \
CLEAN_DB_BENCH="${CLEAN_DB_BENCH}" \
EXPECTED_COMMIT="${CLEAN_COMMIT}" \
EXPECTED_BINARY_SHA256="${CLEAN_BINARY_SHA256}" \
USE_DIRECT_READS=false \
USE_DIRECT_IO_FOR_FLUSH_AND_COMPACTION=false \
bash "${SCRIPT_DIR}/run_clean_vector_baseline_load.sh"

printf '%s\t%s\n' "$(date -u '+%FT%TZ')" \
  'fallback_complete_clean_vector_validated; primary_diffkv_sequence_failed' \
  > "${STATUS_FILE}"
