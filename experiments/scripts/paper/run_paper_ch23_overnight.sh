#!/usr/bin/env bash
# Durable approved queue: full measurements, validated data/prose promotion, build.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENTS="$(cd "${SCRIPT_DIR}/../.." && pwd)"
RUN_ID="${1:?run ID required}"
PILOT_ROOT="${2:?completed pilot root required}"
python3 "${SCRIPT_DIR}/run_paper_ch23_common.py" \
  --run-id "${RUN_ID}" --phase full --pilot-root "${PILOT_ROOT}"
python3 "${EXPERIMENTS}/analysis/promote_paper_ch23_common.py" \
  --run-root "${EXPERIMENTS}/artifacts/log_loads/${RUN_ID}/full"
