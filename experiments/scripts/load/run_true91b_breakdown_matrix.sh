#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"
RUN_ID="${RUN_ID:-true91b_breakdown_$(date '+%y%m%d_%H%M%S')}"

# Rerun only the corrected 91B-total cases for compaction breakdown.
# The original motivation matrix used key_size=24,value_size=91 by mistake.
CASES_SPEC=$'kv91_nocompress 43 none\nkv91_snappy 43 snappy' \
KEY_SIZE=48 \
TARGET_DB_GB="${TARGET_DB_GB:-500}" \
RUN_ID="${RUN_ID}" \
DB_BENCH="${DB_BENCH:-${VCOMP_PROF_DB_BENCH}}" \
"${SCRIPT_DIR}/run_motivation_kv_compression_matrix.sh"
