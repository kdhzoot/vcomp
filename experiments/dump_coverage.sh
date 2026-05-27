#!/usr/bin/env bash
# Dump per-level coverage for every DB matching a glob.
# Output: one .cov file per DB, raw stdout of `coverage` benchmark.
#
# Usage:
#   dump_coverage.sh <DB_GLOB> <OUT_DIR>
# Example:
#   dump_coverage.sh '/work/vcomp/260410_0339_250gb_x30/baseline_run*' coverage_dumps/baseline_260410

set -euo pipefail

DB_GLOB="${1:?usage: dump_coverage.sh <DB_GLOB> <OUT_DIR>}"
OUT_DIR="${2:?usage: dump_coverage.sh <DB_GLOB> <OUT_DIR>}"
DB_BENCH="${DB_BENCH:-$(dirname "$0")/../vcomp/db_bench}"

mkdir -p "$OUT_DIR"

shopt -s nullglob
for db in $DB_GLOB; do
  [ -d "$db" ] || continue
  name=$(basename "$db")
  out="$OUT_DIR/${name}.cov"
  echo "[dump] $name -> $out" >&2
  "$DB_BENCH" \
    --use_existing_db=true \
    --readonly \
    --num=0 \
    --benchmarks=coverage \
    --db="$db" \
    --key_size=24 --value_size=1000 \
    > "$out" 2>&1
done
