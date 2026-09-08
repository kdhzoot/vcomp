#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
MATRIX="${MATRIX:-${ARTIFACT_ROOT}/log_loads/paper_figure4_read_db_matrix.tsv}"
ROOT="${LOG_ROOT:-${ARTIFACT_ROOT}/log_runs/paper_figure4_uniform_read_cache_${RUN_ID}}"
TMP_ROOT="${TMP_ROOT:-/work/vcomp/exp/paper_figure4_uniform_read_tmp}"
DURATION="${DURATION:-300}"
THREADS="${THREADS:-48}"
SYSTEMS="${SYSTEMS:-flush_only last_comp fillseq fillseq_ow f2load}"
CONFIGS="${CONFIGS:-A_cache_zero B_cache_5pct C_pinned_zero D_pinned_5pct}"

require_file "${MATRIX}" "Figure 4 read matrix"
require_executable "${VCOMP_PROF_DB_BENCH}" "vcomp-prof db_bench"
require_positive_uint DURATION
require_positive_uint THREADS
require_no_db_bench "Figure 4 uniform read-cache matrix"
[[ ! -e "${ROOT}" ]] || die "Log output already exists: ${ROOT}"
mkdir -p "${ROOT}" "${TMP_ROOT}"

printf 'run_id=%s\nroot=%s\nmatrix=%s\nduration=%s\nthreads=%s\nsystems=%s\nconfigs=%s\n' \
  "${RUN_ID}" "${ROOT}" "${MATRIX}" "${DURATION}" "${THREADS}" \
  "${SYSTEMS}" "${CONFIGS}" > "${ROOT}/manifest.txt"
sha256sum "${VCOMP_PROF_DB_BENCH}" > "${ROOT}/db_bench.sha256"
git -C "${REPO_ROOT}/../vcomp-prof" rev-parse HEAD > "${ROOT}/vcomp-prof.commit"
git -C "${REPO_ROOT}/../vcomp-prof" status --short > "${ROOT}/vcomp-prof.status"

for config in ${CONFIGS}; do
  case "${config}" in
    A_cache_zero)
      cache_pct=0
      force_cache_bytes=1
      cache_index_filter=true
      ;;
    B_cache_5pct)
      cache_pct=5
      force_cache_bytes=""
      cache_index_filter=true
      ;;
    C_pinned_zero)
      cache_pct=0
      force_cache_bytes=1
      cache_index_filter=false
      ;;
    D_pinned_5pct)
      cache_pct=5
      force_cache_bytes=""
      cache_index_filter=false
      ;;
    *) die "Unknown cache configuration: ${config}" ;;
  esac

  config_root="${ROOT}/${config}"
  env_args=(
    RUN_ID="${RUN_ID}_${config}"
    MATRIX="${MATRIX}"
    LOG_ROOT="${config_root}"
    TMP_ROOT="${TMP_ROOT}"
    DB_BENCH="${VCOMP_PROF_DB_BENCH}"
    SYSTEMS="${SYSTEMS}"
    SIZES_GB=1000
    KV_LABELS=1024B
    DISTRIBUTIONS=uniform
    WORKLOADS=workloadc
    THREADS="${THREADS}"
    DURATION="${DURATION}"
    READS=10000000000
    CACHE_PCT="${cache_pct}"
    CACHE_INDEX_FILTER="${cache_index_filter}"
    CACHE_TYPE=lru_cache
    YCSB_REQUEST_DISTRIBUTION=uniform
    OPEN_FILES=-1
    KEEP_RUN_DB=0
  )
  if [[ -n "${force_cache_bytes}" ]]; then
    env_args+=(FORCE_CACHE_BYTES="${force_cache_bytes}")
  fi

  env "${env_args[@]}" bash "${SCRIPT_DIR}/run_q3_read_workloads.sh"
done

printf 'completed_at=%s\n' "$(date --iso-8601=seconds)" >> "${ROOT}/manifest.txt"
printf '[DONE] Figure 4 uniform read-cache matrix: %s\n' "${ROOT}"
