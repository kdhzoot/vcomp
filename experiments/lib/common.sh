#!/usr/bin/env bash

# Shared locations for all virtual-compaction experiment runners.
EXPERIMENT_ROOT="${VCOMP_EXPERIMENT_ROOT:-$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
)}"
REPO_ROOT="${VCOMP_REPO_ROOT:-$(cd "${EXPERIMENT_ROOT}/.." && pwd)}"

ARTIFACT_ROOT="${VCOMP_ARTIFACT_ROOT:-${EXPERIMENT_ROOT}/artifacts}"
RESULTS_ROOT="${VCOMP_RESULTS_ROOT:-${EXPERIMENT_ROOT}/results}"

VCOMP_DB_BENCH="${VCOMP_DB_BENCH:-${REPO_ROOT}/db_bench}"
BASELINE_DB_BENCH="${BASELINE_DB_BENCH:-${REPO_ROOT}/../rocksdb/db_bench}"
VCOMP_PROF_DB_BENCH="${VCOMP_PROF_DB_BENCH:-${REPO_ROOT}/../vcomp-prof/db_bench}"
VCOMP_DB_ROOT="${VCOMP_DB_ROOT:-/work/vcomp}"

export EXPERIMENT_ROOT REPO_ROOT ARTIFACT_ROOT RESULTS_ROOT
export VCOMP_DB_BENCH BASELINE_DB_BENCH VCOMP_PROF_DB_BENCH VCOMP_DB_ROOT

ensure_artifact_dirs() {
  mkdir -p \
    "${ARTIFACT_ROOT}/log_loads" \
    "${ARTIFACT_ROOT}/log_runs" \
    "${ARTIFACT_ROOT}/log_batch" \
    "${ARTIFACT_ROOT}/coverage_dumps" \
    "${ARTIFACT_ROOT}/compaction_logs"
}
