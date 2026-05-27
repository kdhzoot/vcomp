#!/usr/bin/env bash
: <<'EXAMPLE'
# Default: 250GB synthetic vcomp load on vcomp-proto
bash load_vcomp_proto.sh

# Smoke test
TARGET_DB_GB=10 bash load_vcomp_proto.sh

# Reuse an existing vcomp/db_bench without rebuilding
SKIP_BUILD=1 TARGET_DB_GB=250 bash load_vcomp_proto.sh
EXAMPLE

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VCOMP_DIR="${SCRIPT_DIR}/../vcomp"

branch="$(git -C "${VCOMP_DIR}" rev-parse --abbrev-ref HEAD)"
if [[ "${branch}" != "vcomp-proto" ]]; then
  echo "[ERROR] vcomp must be on branch vcomp-proto, current=${branch}" >&2
  exit 1
fi

TARGET_DB_GB="${TARGET_DB_GB:-250}"
DB_ROOT="${DB_ROOT:-/work/vcomp}"
RUN_TS="$(date '+%y%m%d_%H%M')"
RUN_NAME="${RUN_NAME:-vcomp_proto_${TARGET_DB_GB}gb_${RUN_TS}}"

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  (
    cd "${VCOMP_DIR}"
    DEBUG_LEVEL=0 LIB_MODE=static CXX="${CXX:-g++-11}" CC="${CC:-gcc-11}" \
      make -j"${BUILD_JOBS:-$(nproc)}" db_bench
  )
fi

MODE=vcomp \
TARGET_DB_GB="${TARGET_DB_GB}" \
DB_ROOT="${DB_ROOT}" \
DB_DIR="${DB_ROOT%/}/${RUN_NAME}" \
LOG_DIR="${SCRIPT_DIR}/log_loads/${RUN_NAME}" \
bash "${SCRIPT_DIR}/load.sh"
