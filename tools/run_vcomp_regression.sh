#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DB_BENCH="${DB_BENCH:-${REPO_DIR}/db_bench}"
TEST_ROOT="${TEST_ROOT:-/tmp/vcomp-regression-${USER:-unknown}-$$}"
KEEP_TEST_DB="${KEEP_TEST_DB:-0}"

if [[ ! -x "${DB_BENCH}" ]]; then
  echo "db_bench is not executable: ${DB_BENCH}" >&2
  exit 1
fi

cleanup() {
  if [[ "${KEEP_TEST_DB}" != "1" && -d "${TEST_ROOT}" ]]; then
    find "${TEST_ROOT}" -depth -delete
  fi
}
trap cleanup EXIT

mkdir -p "${TEST_ROOT}"

run_case() {
  local name="$1"
  local kmv_enabled="$2"
  local db_dir="${TEST_ROOT}/${name}/db"
  local load_log="${TEST_ROOT}/${name}/fillvirtual.log"
  local read_log="${TEST_ROOT}/${name}/reopen.log"
  mkdir -p "${TEST_ROOT}/${name}"

  VCOMP_KMV_ENABLED="${kmv_enabled}" "${DB_BENCH}" \
    --benchmarks=fillvirtual \
    --db="${db_dir}" \
    --num=200000 \
    --key_size=24 \
    --value_size=100 \
    --threads=1 \
    --memtablerep=vector \
    --disable_wal=true \
    --compression_type=none \
    --max_background_jobs=4 \
    --use_virtual_compaction=true \
    --plr_error_bound=8 \
    --memtable_flush_size=1 \
    --vcomp_phase1_shards=2 \
    --vcomp_materialize_workers=4 \
    >"${load_log}" 2>&1

  rg -q "Phase 2 \\(materialization\\):" "${load_log}"
  [[ -f "${db_dir}/CURRENT" ]]
  rg -q "^MANIFEST-" "${db_dir}/CURRENT"

  if find "${db_dir}" -type f -name '*.sst' -size 0 -print -quit |
      rg -q .; then
    echo "${name}: zero-byte SST found" >&2
    exit 1
  fi

  "${DB_BENCH}" \
    --benchmarks=readrandom \
    --db="${db_dir}" \
    --use_existing_db=true \
    --num=200000 \
    --reads=10000 \
    --key_size=24 \
    --value_size=100 \
    --threads=1 \
    >"${read_log}" 2>&1

  rg -q "^readrandom[[:space:]]*:" "${read_log}"
  if rg -q "Corruption:|Error:" "${load_log}" "${read_log}"; then
    echo "${name}: error found in benchmark output" >&2
    exit 1
  fi

  echo "${name}: PASS"
}

run_case kmv-on 1
run_case kmv-off 0
