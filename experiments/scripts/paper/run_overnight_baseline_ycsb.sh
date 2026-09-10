#!/usr/bin/env bash
# Four more baseline loads; after each load, YCSB A-F on that fresh DB.
#
# Each iteration is a separate loader run (pilot + one 1000 GiB full repeat +
# L1 coverage dump on a read-only clone) followed by a YCSB A-F campaign at the
# 50 GiB cached configuration against that new DB. All four loaded DBs are
# retained. Iterations are sequential and the script stops at the first
# failure so an unattended run never piles work onto a broken state.
set -o errexit
set -o nounset
set -o pipefail

EXPERIMENTS="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="${TAG:-260908_night}"
ITERATIONS="${ITERATIONS:-4}"
LOG_ROOT="${EXPERIMENTS}/artifacts/log_loads/overnight_${TAG}"
mkdir -p "${LOG_ROOT}"
SUMMARY="${LOG_ROOT}/progress.tsv"
[ -f "${SUMMARY}" ] || printf 'utc\titeration\tstage\tstatus\trun_id\n' > "${SUMMARY}"

note() {
    printf '%s\t%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$3" "$4" \
        | tee -a "${SUMMARY}"
}

for index in $(seq 1 "${ITERATIONS}"); do
    suffix="$(printf 'n%02d' "${index}")"
    load_run="baseline_coverage_${TAG}_${suffix}"
    ycsb_run="baseline_repeat_ycsb_all_${TAG}_${suffix}"

    if [ -f "${EXPERIMENTS}/artifacts/log_loads/${load_run}/COMPLETED" ]; then
        note "${index}" load skip "${load_run}"
    else
        note "${index}" load start "${load_run}"
        python3 "${EXPERIMENTS}/scripts/load/run_baseline_coverage_repeats.py" \
            --run-id "${load_run}" --repeats 1 \
            > "${LOG_ROOT}/${suffix}.load.log" 2>&1
        note "${index}" load done "${load_run}"
    fi

    if [ -f "${EXPERIMENTS}/artifacts/log_runs/${ycsb_run}/COMPLETED.json" ]; then
        note "${index}" ycsb skip "${ycsb_run}"
    else
        note "${index}" ycsb start "${ycsb_run}"
        python3 "${EXPERIMENTS}/scripts/read/run_baseline_repeat_ycsb_all.py" \
            --run-id "${ycsb_run}" --load-run "${load_run}" \
            > "${LOG_ROOT}/${suffix}.ycsb.log" 2>&1
        note "${index}" ycsb done "${ycsb_run}"
    fi
done

note all campaign complete "${TAG}"
