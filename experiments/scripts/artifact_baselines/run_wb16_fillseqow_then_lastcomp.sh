#!/usr/bin/env bash
# Sequential queue: Fillseq+OW wb16, then one-shot compact preserved No-comp wb16.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

QUEUE_ID="${QUEUE_ID:-paper_wb16_followups_260903_run1}"
FILL_RUN_ID="${FILL_RUN_ID:-paper_clean_fillseq_overwrite_wb16_1000gib_260903_run1}"
LAST_RUN_ID="${LAST_RUN_ID:-paper_lastcomp_from_nocomp_wb16_1000gib_260903_run1}"
QUEUE_LOG="${ARTIFACT_ROOT}/log_loads/${QUEUE_ID}.state.log"

if pgrep -x db_bench >/dev/null 2>&1 || pgrep -x titandb_bench >/dev/null 2>&1; then
  die "another storage benchmark is active"
fi
printf '[%s] starting Fillseq+OW wb16\n' "$(date -u '+%FT%TZ')" | tee -a "${QUEUE_LOG}"
RUN_ID="${FILL_RUN_ID}" bash "${SCRIPT_DIR}/run_clean_fillseq_overwrite_wb16_1tb.sh"

last_root="${ARTIFACT_ROOT}/log_loads/${LAST_RUN_ID}"
last_db_root="${VCOMP_DB_ROOT%/}/exp/${LAST_RUN_ID}"
mkdir -p "${last_root}" "${last_db_root}"
printf '[%s] starting Last-comp 1 GiB checkpoint pilot\n' "$(date -u '+%FT%TZ')" | tee -a "${QUEUE_LOG}"
SOURCE_DB="/work/vcomp/exp/paper_lastcomp_flushonly_wb16_1tib_260902_run1/pilot_1gib" \
SOURCE_LOG="${ARTIFACT_ROOT}/log_loads/paper_lastcomp_flushonly_wb16_1tib_260902_run1/pilot_1gib" \
SOURCE_ELAPSED_SEC=2 TARGET_GIB=1 READS=1000 \
RUN_ROOT="${last_root}/pilot_1gib" DB_DIR="${last_db_root}/pilot_1gib" DRY_RUN=0 \
bash "${SCRIPT_DIR}/run_lastcomp_from_nocomp_wb16.sh"

printf '[%s] starting Last-comp 1 TiB one-shot compaction\n' "$(date -u '+%FT%TZ')" | tee -a "${QUEUE_LOG}"
SOURCE_DB="/work/vcomp/exp/paper_lastcomp_flushonly_wb16_1tib_260902_run1/flushonly_1000gib" \
SOURCE_LOG="${ARTIFACT_ROOT}/log_loads/paper_lastcomp_flushonly_wb16_1tib_260902_run1/flushonly_1000gib" \
SOURCE_ELAPSED_SEC=973 TARGET_GIB=1000 READS=10000 \
RUN_ROOT="${last_root}/lastcomp_1000gib" DB_DIR="${last_db_root}/lastcomp_1000gib" DRY_RUN=0 \
bash "${SCRIPT_DIR}/run_lastcomp_from_nocomp_wb16.sh"

touch "${last_root}/COMPLETED"
printf '[%s] all queued wb16 experiments completed\n' "$(date -u '+%FT%TZ')" | tee -a "${QUEUE_LOG}"
