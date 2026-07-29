#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"
TRACE_ROOT="${TRACE_ROOT:-${VCOMP_DB_ROOT}/load_traces}"
GEN_BIN="${GEN_BIN:-/tmp/generate_load_trace_fast}"
GEN_SRC="${GEN_SRC:-${REPO_ROOT}/tools/generate_load_trace_fast.cc}"
MANIFEST="${TRACE_ROOT}/trace_matrix_manifest.tsv"
SEED="${SEED:-12345678}"
ZIPF_ALPHA="${ZIPF_ALPHA:-0.99}"
PROGRESS_EVERY="${PROGRESS_EVERY:-0}"

mkdir -p "${TRACE_ROOT}"

if [[ ! -x "${GEN_BIN}" || "${GEN_SRC}" -nt "${GEN_BIN}" ]]; then
  g++ -O3 -std=c++17 "${GEN_SRC}" -o "${GEN_BIN}"
fi

printf 'case_id\tsize_gb\tkv_label\tkey_size\tvalue_size\tdistribution\tunique_ratio\tzipf_alpha\tnum_records\tkey_domain\tunique_count\ttrace_path\texpected_bytes\tstatus\n' > "${MANIFEST}"

add_case() {
  local size_gb="$1"
  local kv_label="$2"
  local key_size="$3"
  local value_size="$4"
  local distribution="$5"
  local unique_ratio="$6"
  local zipf_alpha="$7"
  local case_id="s${size_gb}gb_${kv_label}_${distribution}"
  local trace_path="${TRACE_ROOT}/${case_id}_seed${SEED}.vload"
  local num_records expected_bytes key_domain unique_count actual_bytes status

  num_records="$(python3 - <<PY
size_gb=${size_gb}
key_size=${key_size}
value_size=${value_size}
print(size_gb * 1024**3 // (key_size + value_size))
PY
)"
  key_domain="${num_records}"
  unique_count="$(python3 - <<PY
num_records=${num_records}
ratio=${unique_ratio}
u=round(num_records * ratio)
if num_records > 0:
    u=max(1, min(u, num_records))
print(u)
PY
)"
  expected_bytes=$((88 + num_records * 8))

  if [[ ! -f "${trace_path}" || "$(stat -c%s "${trace_path}" 2>/dev/null || echo 0)" -ne "${expected_bytes}" ]]; then
    "${GEN_BIN}" "${trace_path}" \
      --num "${num_records}" \
      --key-size "${key_size}" \
      --value-size "${value_size}" \
      --key-domain "${key_domain}" \
      --unique-ratio "${unique_ratio}" \
      --zipf-alpha "${zipf_alpha}" \
      --seed "${SEED}" \
      --progress-every "${PROGRESS_EVERY}" \
      --force > "${trace_path}.generate.log"
  fi

  actual_bytes="$(stat -c%s "${trace_path}")"
  status="ok"
  if [[ "${actual_bytes}" -ne "${expected_bytes}" ]]; then
    status="bad_size:${actual_bytes}"
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${case_id}" "${size_gb}" "${kv_label}" "${key_size}" "${value_size}" \
    "${distribution}" "${unique_ratio}" "${zipf_alpha}" "${num_records}" \
    "${key_domain}" "${unique_count}" "${trace_path}" "${expected_bytes}" \
    "${status}" >> "${MANIFEST}"
}

for size_gb in 500 1000; do
  for kv in "91B 48 43" "1024B 24 1000"; do
    read -r kv_label key_size value_size <<< "${kv}"
    add_case "${size_gb}" "${kv_label}" "${key_size}" "${value_size}" unique100 1.0 0.0
    add_case "${size_gb}" "${kv_label}" "${key_size}" "${value_size}" uniform50 0.5 0.0
    add_case "${size_gb}" "${kv_label}" "${key_size}" "${value_size}" zipf99_50 0.5 "${ZIPF_ALPHA}"
  done
done

python3 - <<'PY' "${MANIFEST}"
import struct
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
rows = manifest.read_text().strip().splitlines()
header = rows[0].split("\t")
idx = {name: i for i, name in enumerate(header)}
fmt = struct.Struct("<8sIIQQQIIddQQQ")
bad = []
for line in rows[1:]:
    cols = line.split("\t")
    path = Path(cols[idx["trace_path"]])
    expected = int(cols[idx["expected_bytes"]])
    if path.stat().st_size != expected:
        bad.append((str(path), "size"))
        continue
    h = fmt.unpack(path.open("rb").read(fmt.size))
    checks = [
        h[0] == b"VLOADTR1",
        h[1] == 1,
        h[2] == 88,
        h[3] == int(cols[idx["num_records"]]),
        h[4] == int(cols[idx["key_domain"]]),
        h[5] == int(cols[idx["unique_count"]]),
        h[6] == int(cols[idx["key_size"]]),
        h[7] == int(cols[idx["value_size"]]),
    ]
    if not all(checks):
        bad.append((str(path), "header"))
if bad:
    for item in bad:
        print("BAD", item)
    raise SystemExit(1)
print(f"validated_traces\t{len(rows)-1}")
PY

du -sh "${TRACE_ROOT}" || true
echo "manifest ${MANIFEST}"
