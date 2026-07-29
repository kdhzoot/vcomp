#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"
DB_BENCH="${DB_BENCH:-${VCOMP_DB_BENCH}}"

TARGET_DB_GB="${TARGET_DB_GB:-1000}"
DB_ROOT="${DB_ROOT:-${VCOMP_DB_ROOT}}"
RUN_ROOT="${RUN_ROOT:-${DB_ROOT%/}/profile_runs}"
RUN_TAG="${RUN_TAG:-cpuwait}"
PROFILE_SECONDS="${PROFILE_SECONDS:-20}"
BG_JOBS="${BG_JOBS:-$(nproc)}"
PLR_ERROR_BOUND="${PLR_ERROR_BOUND:-8}"
MEMTABLE_FLUSH_MB="${MEMTABLE_FLUSH_MB:-64}"
VCOMP_REGISTER_BATCH_MAX="${VCOMP_REGISTER_BATCH_MAX:-256}"
VCOMP_VISIBLE_L0_BATCH_MB="${VCOMP_VISIBLE_L0_BATCH_MB:-0}"

KEY_SIZE=24
VALUE_SIZE=1000
KV_SIZE=$((KEY_SIZE + VALUE_SIZE))
NKEYS=$((TARGET_DB_GB * 1024 * 1024 * 1024 / KV_SIZE))

RUN_TS="$(date '+%y%m%d_%H%M%S')"
RUN_DIR="${RUN_ROOT}/vcomp_profile_${TARGET_DB_GB}gb_${RUN_TS}_${RUN_TAG}"
DB_DIR="${RUN_DIR}/db"
LOG_DIR="${RUN_DIR}/logs"
RAW_DIR="${RUN_DIR}/raw"
PERF_DIR="${RUN_DIR}/perf"
REPORT_DIR="${RUN_DIR}/reports"
IOSTAT_PID=""
BENCH_PID=""
SAMPLER_PID=""
RSS_SAMPLER_PID=""

cleanup_background() {
  if [[ -n "${RSS_SAMPLER_PID:-}" ]]; then
    kill "${RSS_SAMPLER_PID}" 2>/dev/null || true
    wait "${RSS_SAMPLER_PID}" 2>/dev/null || true
    RSS_SAMPLER_PID=""
  fi
  if [[ -n "${SAMPLER_PID:-}" ]]; then
    kill "${SAMPLER_PID}" 2>/dev/null || true
    wait "${SAMPLER_PID}" 2>/dev/null || true
    SAMPLER_PID=""
  fi
  if [[ -n "${IOSTAT_PID:-}" ]]; then
    kill "${IOSTAT_PID}" 2>/dev/null || true
    wait "${IOSTAT_PID}" 2>/dev/null || true
    IOSTAT_PID=""
  fi
}

cleanup_on_signal() {
  cleanup_background
  if [[ -n "${BENCH_PID:-}" ]]; then
    kill "${BENCH_PID}" 2>/dev/null || true
    wait "${BENCH_PID}" 2>/dev/null || true
    BENCH_PID=""
  fi
}

trap cleanup_background EXIT
trap 'cleanup_on_signal; exit 130' INT
trap 'cleanup_on_signal; exit 143' TERM

mkdir -p "${DB_DIR}" "${LOG_DIR}" "${RAW_DIR}" "${PERF_DIR}" "${REPORT_DIR}"
ulimit -n 1048576

cmd=(
  "${DB_BENCH}"
  --statistics=1
  --stats_interval_seconds=60
  --stats_per_interval=1
  --report_interval_seconds=10
  --report_file="${LOG_DIR}/report.rep"
  --enable_index_compression=false
  --bloom_bits=10
  --disable_wal=true
  --max_background_jobs="${BG_JOBS}"
  --num="${NKEYS}"
  --key_size="${KEY_SIZE}"
  --value_size="${VALUE_SIZE}"
  --threads=1
  --memtablerep=vector
  --seed=12345678
  --db="${DB_DIR}"
  --use_direct_reads=true
  --use_direct_io_for_flush_and_compaction=true
  --compression_type=none
  --benchmarks=fillvirtual,flush,compact0,waitforcompaction,stats,levelstats
  --use_virtual_compaction=true
  --plr_error_bound="${PLR_ERROR_BOUND}"
  --memtable_flush_size="${MEMTABLE_FLUSH_MB}"
  --vcomp_register_batch_max="${VCOMP_REGISTER_BATCH_MAX}"
  --vcomp_visible_l0_batch_mb="${VCOMP_VISIBLE_L0_BATCH_MB}"
)

printf '%q ' "${cmd[@]}" > "${RAW_DIR}/load_cmd.sh"
printf '\n' >> "${RAW_DIR}/load_cmd.sh"
chmod +x "${RAW_DIR}/load_cmd.sh"

cat > "${RUN_DIR}/README.md" <<EOF
# vcomp profiling run

- start: $(date -Is)
- target_db_gb: ${TARGET_DB_GB}
- threads: 1
- memtable_rep: vector
- bg_jobs: ${BG_JOBS}
- db_dir: ${DB_DIR}
- bench_out: ${LOG_DIR}/bench.out
- profile_seconds: ${PROFILE_SECONDS}
- profiles: oncpu task-clock, perf sched latency, sched switch/wakeup, futex syscalls
EOF

echo "[RUN_DIR] ${RUN_DIR}"
echo "[DB_DIR] ${DB_DIR}"
echo "[CMD] $(cat "${RAW_DIR}/load_cmd.sh")"

if [[ -w /proc/sys/vm/drop_caches ]]; then
  sync
  echo 3 > /proc/sys/vm/drop_caches
  echo "[INFO] Page cache dropped"
else
  sync
  sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches' && \
    echo "[INFO] Page cache dropped (sudo)"
fi

cat /proc/diskstats > "${RAW_DIR}/diskstats.start"
cat /proc/stat > "${RAW_DIR}/procstat.start"
date +%s > "${RAW_DIR}/start_epoch.txt"

if command -v iostat >/dev/null 2>&1; then
  iostat -dx 1 > "${RAW_DIR}/iostat.log" &
  IOSTAT_PID=$!
fi

"${cmd[@]}" > "${LOG_DIR}/bench.out" 2>&1 &
BENCH_PID=$!
echo "${BENCH_PID}" > "${RAW_DIR}/db_bench.pid"
echo "[PID] ${BENCH_PID}"

sample_threads() {
  while kill -0 "${BENCH_PID}" 2>/dev/null; do
    date -Is
    ps -L -p "${BENCH_PID}" -o pid,tid,psr,pcpu,stat,wchan:36,comm
    echo
    sleep 2
  done
}
sample_threads > "${REPORT_DIR}/thread_samples.txt" &
SAMPLER_PID=$!

sample_peak_rss() {
  local peak_kb=0
  local rss_kb=0
  while kill -0 "${BENCH_PID}" 2>/dev/null; do
    rss_kb="$(awk '/VmRSS:/ {print $2}' "/proc/${BENCH_PID}/status" 2>/dev/null || echo 0)"
    if [[ -n "${rss_kb}" && "${rss_kb}" =~ ^[0-9]+$ && "${rss_kb}" -gt "${peak_kb}" ]]; then
      peak_kb="${rss_kb}"
    fi
    sleep 1
  done
  echo "${peak_kb}" > "${RAW_DIR}/peak_rss_kb.txt"
  awk -v kb="${peak_kb}" 'BEGIN { printf "%.3f", kb / 1024 / 1024 }' > "${RAW_DIR}/peak_rss_gb.txt"
}
sample_peak_rss &
RSS_SAMPLER_PID=$!

run_if_alive() {
  local name="$1"
  shift
  if kill -0 "${BENCH_PID}" 2>/dev/null; then
    echo "[PROFILE] ${name}" | tee -a "${REPORT_DIR}/profile_steps.txt"
    "$@" > "${PERF_DIR}/${name}.stdout" 2> "${PERF_DIR}/${name}.stderr" || true
  else
    echo "[SKIP] ${name}: db_bench already exited" | tee -a "${REPORT_DIR}/profile_steps.txt"
  fi
}

sleep 2
ps -L -p "${BENCH_PID}" -o pid,tid,psr,pcpu,stat,wchan:36,comm > "${REPORT_DIR}/threads.initial.txt" || true

run_if_alive oncpu \
  sudo perf record -o "${PERF_DIR}/oncpu.data" -g -e task-clock -p "${BENCH_PID}" -- sleep "${PROFILE_SECONDS}"

run_if_alive sched \
  sudo perf sched record -o "${PERF_DIR}/sched.data" -p "${BENCH_PID}" -- sleep "${PROFILE_SECONDS}"

run_if_alive switch_wakeup \
  sudo perf record -o "${PERF_DIR}/switch_wakeup.data" -g \
    -e sched:sched_switch,sched:sched_wakeup -p "${BENCH_PID}" -- sleep "${PROFILE_SECONDS}"

run_if_alive futex \
  sudo perf record -o "${PERF_DIR}/futex.data" -g \
    -e syscalls:sys_enter_futex,syscalls:sys_exit_futex -p "${BENCH_PID}" -- sleep "${PROFILE_SECONDS}"

set +e
wait "${BENCH_PID}"
bench_rc=$?
set -e
wait "${RSS_SAMPLER_PID}" 2>/dev/null || true
RSS_SAMPLER_PID=""
BENCH_PID=""

cleanup_background

date +%s > "${RAW_DIR}/end_epoch.txt"
cat /proc/diskstats > "${RAW_DIR}/diskstats.end"
cat /proc/stat > "${RAW_DIR}/procstat.end"

make_report() {
  local data="$1"
  local out="$2"
  [[ -f "${data}" ]] || return 0
  sudo perf report --stdio -i "${data}" --no-children > "${out}" 2>&1 || true
}

make_report "${PERF_DIR}/oncpu.data" "${REPORT_DIR}/oncpu.report.txt"
make_report "${PERF_DIR}/switch_wakeup.data" "${REPORT_DIR}/switch_wakeup.report.txt"
make_report "${PERF_DIR}/futex.data" "${REPORT_DIR}/futex.report.txt"
if [[ -f "${PERF_DIR}/sched.data" ]]; then
  sudo perf sched latency -i "${PERF_DIR}/sched.data" > "${REPORT_DIR}/sched_latency.txt" 2>&1 || true
fi

{
  echo "# Summary"
  echo
  echo "- end: $(date -Is)"
  echo "- exit_code: ${bench_rc}"
  echo "- elapsed_sec: $(( $(cat "${RAW_DIR}/end_epoch.txt") - $(cat "${RAW_DIR}/start_epoch.txt") ))"
  echo "- peak_rss_kb: $(cat "${RAW_DIR}/peak_rss_kb.txt" 2>/dev/null || echo NA)"
  echo "- peak_rss_gb: $(cat "${RAW_DIR}/peak_rss_gb.txt" 2>/dev/null || echo NA)"
  echo "- db_size: $(du -sh "${DB_DIR}" 2>/dev/null | cut -f1)"
  echo
  echo "## Key bench lines"
  rg -n "fillvirtual|Virtual compaction|LogAndApply|prepare_detail|compaction_pri_by_level|file_index_detail|BG virtual|L0 release|Virtual SSTs|Level [0-9]|Cumulative virtual" "${LOG_DIR}/bench.out" || true
} > "${RUN_DIR}/SUMMARY.md"

sudo chown -R "$(id -u):$(id -g)" "${RUN_DIR}" 2>/dev/null || true

echo "[DONE] exit=${bench_rc}"
echo "[RUN_DIR] ${RUN_DIR}"
echo "[SUMMARY] ${RUN_DIR}/SUMMARY.md"
exit "${bench_rc}"
