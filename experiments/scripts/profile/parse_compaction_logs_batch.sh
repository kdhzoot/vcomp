#!/usr/bin/env bash
# Parse all RocksDB LOGs under a batch directory into per-run CSVs.
#
# Usage:
#   parse_compaction_logs_batch.sh <BATCH_DIR> <MODE> <OUT_DIR>
# Example:
#   parse_compaction_logs_batch.sh /work/vcomp/260415_0635_250gb_x30 baseline compaction_logs/baseline_260415

set -euo pipefail
BATCH_DIR="${1:?batch dir}"
MODE="${2:?mode (baseline|vcomp)}"
OUT_DIR="${3:?out dir}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

require_dir "${BATCH_DIR}" "batch directory"
[[ "${MODE}" == "baseline" || "${MODE}" == "vcomp" ]] ||
  die "MODE must be baseline or vcomp"
JOBS="${JOBS:-8}"
require_positive_uint JOBS
mkdir -p "${OUT_DIR}"

# Each parse is independent, so process them in bounded parallel batches.
N=0
matched=0
for db in "${BATCH_DIR}"/${MODE}_run*; do
  [[ -d "${db}" ]] || continue
  [[ -f "${db}/LOG" ]] || continue
  matched=$((matched + 1))
  name="$(basename "${db}")"
  out="${OUT_DIR}/${name}.csv"
  if [[ -f "${out}" ]]; then
    echo "[skip] ${name} (already parsed)" >&2
    continue
  fi
  python3 "${EXPERIMENT_ROOT}/analysis/parse_compaction_log.py" "${db}/LOG" \
      --mode "${MODE}" --run "${name}" --out "${out}" &
  N=$((N + 1))
  if (( N % JOBS == 0 )); then wait; fi
done
wait
(( matched > 0 )) || die "No ${MODE} LOG files found under ${BATCH_DIR}"
echo "[done] parsed ${N} logs into ${OUT_DIR}" >&2
