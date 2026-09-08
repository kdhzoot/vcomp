#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WORKSPACE_ROOT="$(cd "${EXPERIMENT_ROOT}/../.." && pwd)"

HISTORICAL_SUMMARY="${HISTORICAL_SUMMARY:-${EXPERIMENT_ROOT}/artifacts/log_loads/exp_260604_exp91b_baseline/scaling_91b_none/scaling_91b_summary.tsv}"
NEW_SUMMARY="${NEW_SUMMARY:-${EXPERIMENT_ROOT}/artifacts/log_loads/exp_260822_paper_bg_91b_8tb_direct/scaling_91b_none/scaling_91b_summary.tsv}"
RESULT_DIR="${RESULT_DIR:-${EXPERIMENT_ROOT}/results/paper_91b_scaling}"
PAPER_FIG_DIR="${PAPER_FIG_DIR:-${WORKSPACE_ROOT}/paper/figs}"
POLL_SECONDS="${POLL_SECONDS:-60}"

mkdir -p "${RESULT_DIR}" "${PAPER_FIG_DIR}"

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*"
}

summary_status() {
  [[ -s "${NEW_SUMMARY}" ]] || return 0
  awk -F '\t' 'NR > 1 && $1 == "baseline" && $2 == "8000" {print $9}' \
    "${NEW_SUMMARY}" | tail -1
}

log "Waiting for a complete 8 TB true-91 B summary row"
while true; do
  status="$(summary_status)"
  case "${status}" in
    ok)
      break
      ;;
    failed:*)
      log "8 TB run failed with status=${status}; refusing to generate results"
      exit 1
      ;;
  esac
  sleep "${POLL_SECONDS}"
done

log "Validated status=ok; merging historical and new measurements"
python3 "${EXPERIMENT_ROOT}/analysis/make_paper_91b_scaling.py" \
  --historical-summary "${HISTORICAL_SUMMARY}" \
  --new-summary "${NEW_SUMMARY}" \
  --output-tsv "${RESULT_DIR}/paper_91b_loading_scale.tsv" \
  --output-pdf "${RESULT_DIR}/paper_91b_loading_scale.pdf" \
  --output-png "${RESULT_DIR}/paper_91b_loading_scale.png"

cp "${RESULT_DIR}/paper_91b_loading_scale.pdf" \
  "${PAPER_FIG_DIR}/bg_loading_91b_scale.pdf"
cp "${RESULT_DIR}/paper_91b_loading_scale.png" \
  "${PAPER_FIG_DIR}/bg_loading_91b_scale.png"

log "Finalized ${RESULT_DIR}/paper_91b_loading_scale.tsv"
