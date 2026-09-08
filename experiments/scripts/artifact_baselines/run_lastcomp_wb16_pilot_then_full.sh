#!/usr/bin/env bash
# Resume the Last-comp portion after the Fillseq+OW queue has completed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

RUN_ID="${RUN_ID:-paper_lastcomp_from_nocomp_wb16_1000gib_260903_run1}"
RUN_ROOT="${ARTIFACT_ROOT}/log_loads/${RUN_ID}"
DB_ROOT="${VCOMP_DB_ROOT%/}/exp/${RUN_ID}"

if pgrep -x db_bench >/dev/null 2>&1 || pgrep -x titandb_bench >/dev/null 2>&1; then
  die "another storage benchmark is active"
fi
mkdir -p "${RUN_ROOT}" "${DB_ROOT}"

SOURCE_DB="/work/vcomp/exp/paper_lastcomp_flushonly_wb16_1tib_260902_run1/pilot_1gib" \
SOURCE_LOG="${ARTIFACT_ROOT}/log_loads/paper_lastcomp_flushonly_wb16_1tib_260902_run1/pilot_1gib" \
SOURCE_ELAPSED_SEC=2 TARGET_GIB=1 READS=1000 \
RUN_ROOT="${RUN_ROOT}/pilot_1gib" DB_DIR="${DB_ROOT}/pilot_1gib" DRY_RUN=0 \
bash "${SCRIPT_DIR}/run_lastcomp_from_nocomp_wb16.sh"

SOURCE_DB="/work/vcomp/exp/paper_lastcomp_flushonly_wb16_1tib_260902_run1/flushonly_1000gib" \
SOURCE_LOG="${ARTIFACT_ROOT}/log_loads/paper_lastcomp_flushonly_wb16_1tib_260902_run1/flushonly_1000gib" \
SOURCE_ELAPSED_SEC=973 TARGET_GIB=1000 READS=10000 \
RUN_ROOT="${RUN_ROOT}/lastcomp_1000gib" DB_DIR="${DB_ROOT}/lastcomp_1000gib" DRY_RUN=0 \
bash "${SCRIPT_DIR}/run_lastcomp_from_nocomp_wb16.sh"

touch "${RUN_ROOT}/COMPLETED"
printf 'Last-comp wb16 pilot and full run completed: %s\n' "${RUN_ROOT}"
