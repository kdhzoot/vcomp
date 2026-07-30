#!/usr/bin/env bash
# Dump per-level coverage for every DB matching a glob.
# Output: one .cov file per DB, raw stdout of `coverage` benchmark.
#
# Usage:
#   dump_coverage.sh <DB_GLOB> <OUT_DIR>
# Example:
#   dump_coverage.sh '/work/vcomp/260410_0339_250gb_x30/baseline_run*' coverage_dumps/baseline_260410

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

DB_GLOB="${1:?usage: dump_coverage.sh <DB_GLOB> <OUT_DIR>}"
OUT_DIR="${2:?usage: dump_coverage.sh <DB_GLOB> <OUT_DIR>}"
DB_BENCH="${DB_BENCH:-${VCOMP_DB_BENCH}}"

require_executable "${DB_BENCH}" "db_bench"
require_no_db_bench "coverage dump"
mapfile -t db_dirs < <(compgen -G "${DB_GLOB}" || true)
(( ${#db_dirs[@]} > 0 )) || die "DB glob matched no paths: ${DB_GLOB}"
mkdir -p "${OUT_DIR}"

dump_count=0
for db in "${db_dirs[@]}"; do
  [[ -d "${db}" ]] || continue
  name="$(basename "${db}")"
  out="${OUT_DIR}/${name}.cov"
  time_out="${OUT_DIR}/${name}.time.out"
  [[ ! -e "${out}" && ! -e "${time_out}" ]] ||
    die "Coverage output already exists for ${name}: ${OUT_DIR}"
  echo "[dump] ${name} -> ${out}" >&2
  /usr/bin/time -v -o "${time_out}" "${DB_BENCH}" \
    --use_existing_db=true \
    --readonly \
    --num=0 \
    --benchmarks=coverage \
    --db="${db}" \
    --key_size=24 --value_size=1000 \
    > "${out}" 2>&1
  peak_rss_kb="$(extract_peak_rss_kb "${time_out}")"
  echo "${peak_rss_kb}" > "${OUT_DIR}/${name}.peak_rss_kb"
  rss_kb_to_gb "${peak_rss_kb}" > "${OUT_DIR}/${name}.peak_rss_gb"
  dump_count=$((dump_count + 1))
done
(( dump_count > 0 )) || die "DB glob matched no directories: ${DB_GLOB}"
