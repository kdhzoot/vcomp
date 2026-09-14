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

die() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

require_env() {
  local name
  for name in "$@"; do
    [[ -n "${!name:-}" ]] || die "Missing required environment variable: ${name}"
  done
}

require_executable() {
  local path="$1"
  local label="${2:-executable}"
  [[ -x "${path}" ]] || die "${label} is not executable: ${path}"
}

require_file() {
  local path="$1"
  local label="${2:-file}"
  [[ -f "${path}" ]] || die "${label} not found: ${path}"
}

require_dir() {
  local path="$1"
  local label="${2:-directory}"
  [[ -d "${path}" ]] || die "${label} not found: ${path}"
}

require_uint() {
  local name="$1"
  local value="${!name:-}"
  [[ "${value}" =~ ^[0-9]+$ ]] || die "${name} must be a non-negative integer: ${value:-<empty>}"
}

require_positive_uint() {
  local name="$1"
  local value="${!name:-}"
  [[ "${value}" =~ ^[1-9][0-9]*$ ]] || die "${name} must be a positive integer: ${value:-<empty>}"
}

require_no_db_bench() {
  local context="${1:-experiment}"
  if pgrep -x db_bench >/dev/null 2>&1; then
    die "Another db_bench is running; stop it before starting ${context}"
  fi
}

drop_page_cache() {
  if [[ "${VCOMP_DROP_PAGE_CACHE:-1}" == "0" ]]; then
    echo "[INFO] Page cache drop disabled"
    return 0
  fi
  sync
  if [[ -w /proc/sys/vm/drop_caches ]]; then
    printf '3\n' > /proc/sys/vm/drop_caches
    echo "[INFO] Page cache dropped"
  elif command -v sudo >/dev/null 2>&1 &&
      printf '3\n' | sudo -n tee /proc/sys/vm/drop_caches >/dev/null 2>&1; then
    echo "[INFO] Page cache dropped (sudo)"
  else
    echo "[WARN] Cannot drop page cache without non-interactive privilege" >&2
  fi
}

extract_peak_rss_kb() {
  awk -F: '/Maximum resident set size/ {gsub(/^[ \t]+/, "", $2); print $2}' \
    "$1" 2>/dev/null || true
}

read_peak_rss_kb() {
  local raw_dir="$1"
  if [[ -f "${raw_dir}/peak_rss_kb.txt" ]]; then
    cat "${raw_dir}/peak_rss_kb.txt"
  elif [[ -f "${raw_dir}/time.out" ]]; then
    extract_peak_rss_kb "${raw_dir}/time.out"
  fi
}

rss_kb_to_gb() {
  local rss_kb="$1"
  awk -v kb="${rss_kb}" 'BEGIN { if (kb != "") printf "%.3f", kb / 1024 / 1024 }'
}

stop_process() {
  local pid="${1:-}"
  [[ -n "${pid}" ]] || return 0
  kill "${pid}" 2>/dev/null || true
  wait "${pid}" 2>/dev/null || true
}

ensure_artifact_dirs() {
  mkdir -p \
    "${ARTIFACT_ROOT}/log_loads" \
    "${ARTIFACT_ROOT}/log_runs" \
    "${ARTIFACT_ROOT}/log_batch" \
    "${ARTIFACT_ROOT}/coverage_dumps" \
    "${ARTIFACT_ROOT}/compaction_logs"
}
