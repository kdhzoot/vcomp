#!/usr/bin/env bash
set -euo pipefail

VCOMP_ROOT="${VCOMP_ROOT:-/home/smrc/virtual_compaction/vcomp}"
DB_BENCH="${DB_BENCH:-${VCOMP_ROOT}/db_bench}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
TARGET_DB_GB="${TARGET_DB_GB:-100}"
CAPS="${CAPS:-1 4 8 16 32}"
BG_JOBS="${BG_JOBS:-48}"
SETTLE_SECONDS="${SETTLE_SECONDS:-15}"
READS="${READS:-10000}"
MAX_RSS_KB="${MAX_RSS_KB:-268435456}"
ABORT_RSS_KB="${ABORT_RSS_KB:-0}"
RUN_NO_COMP="${RUN_NO_COMP:-1}"
RUN_BASELINE="${RUN_BASELINE:-1}"
RUN_LASTCOMP="${RUN_LASTCOMP:-1}"
EXP_DIR="${EXP_DIR:-${VCOMP_ROOT}/experiments/artifacts/log_loads/paper_subcomp_sweep_${TARGET_DB_GB}gb_${RUN_ID}}"
DB_BASE="${DB_BASE:-/work/vcomp/exp/paper_subcomp_sweep_${TARGET_DB_GB}gb/${RUN_ID}}"
SNAPSHOT_ROOT="${EXP_DIR}/runner_snapshot"

die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
log() { printf '[%s] %s\n' "$(date -u '+%FT%TZ')" "$*" | tee -a "${EXP_DIR}/run.log"; }
is_positive_uint() { [[ "$1" =~ ^[1-9][0-9]*$ ]]; }

is_positive_uint "${TARGET_DB_GB}" || die "invalid TARGET_DB_GB=${TARGET_DB_GB}"
is_positive_uint "${BG_JOBS}" || die "invalid BG_JOBS=${BG_JOBS}"
is_positive_uint "${SETTLE_SECONDS}" || die "invalid SETTLE_SECONDS=${SETTLE_SECONDS}"
is_positive_uint "${READS}" || die "invalid READS=${READS}"
is_positive_uint "${MAX_RSS_KB}" || die "invalid MAX_RSS_KB=${MAX_RSS_KB}"
[[ "${ABORT_RSS_KB}" =~ ^[0-9]+$ ]] || die "invalid ABORT_RSS_KB=${ABORT_RSS_KB}"
for toggle in RUN_NO_COMP RUN_BASELINE RUN_LASTCOMP; do
  [[ "${!toggle}" == "0" || "${!toggle}" == "1" ]] || die "invalid ${toggle}=${!toggle}"
done
for cap in ${CAPS}; do is_positive_uint "${cap}" || die "invalid cap=${cap}"; done

if [[ "${RUNNER_SNAPSHOT_ACTIVE:-0}" != "1" ]]; then
  [[ -x "${DB_BENCH}" ]] || die "db_bench not executable: ${DB_BENCH}"
  [[ -x "${VCOMP_ROOT}/experiments/scripts/load/load.sh" ]] || die "load.sh not executable"
  [[ ! -e "${EXP_DIR}" ]] || die "experiment output exists: ${EXP_DIR}"
  [[ ! -e "${DB_BASE}" ]] || die "database output exists: ${DB_BASE}"
  pgrep -x db_bench >/dev/null 2>&1 && die "another db_bench is active"
  pgrep -x ycsbc_smoke >/dev/null 2>&1 && die "another storage benchmark is active"

  mkdir -p "${SNAPSHOT_ROOT}/scripts/load" "${SNAPSHOT_ROOT}/lib"
  cp -- "$0" "${SNAPSHOT_ROOT}/scripts/load/run_paper_subcompaction_sweep.sh"
  cp -- "${VCOMP_ROOT}/experiments/scripts/load/load.sh" \
    "${SNAPSHOT_ROOT}/scripts/load/load.sh"
  cp -- "${VCOMP_ROOT}/experiments/lib/common.sh" "${SNAPSHOT_ROOT}/lib/common.sh"
  chmod -R a-w "${SNAPSHOT_ROOT}"
  sha256sum "${DB_BENCH}" "${SNAPSHOT_ROOT}/scripts/load/"*.sh \
    "${SNAPSHOT_ROOT}/lib/common.sh" > "${EXP_DIR}/SHA256SUMS"
  printf '%s\n' "${DB_BENCH}" > "${EXP_DIR}/db_bench_path.txt"
  "${DB_BENCH}" --version > "${EXP_DIR}/db_bench_version.txt" 2>&1
  bench_repo="$(dirname "${DB_BENCH}")"
  if git -C "${bench_repo}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "${bench_repo}" rev-parse HEAD > "${EXP_DIR}/db_bench_commit.txt"
    git -C "${bench_repo}" status --short > "${EXP_DIR}/db_bench_git_status.txt"
  fi
  git -C "${VCOMP_ROOT}" rev-parse HEAD > "${EXP_DIR}/vcomp_commit.txt"
  git -C "${VCOMP_ROOT}" status --short > "${EXP_DIR}/vcomp_git_status.txt"
  git -C "${VCOMP_ROOT}" diff > "${EXP_DIR}/vcomp.patch"
  {
    date -u
    uname -a
    free -h
    swapon --show
    df -h /work
    lscpu
  } > "${EXP_DIR}/environment.txt"
  exec env \
    RUNNER_SNAPSHOT_ACTIVE=1 \
    VCOMP_ROOT="${VCOMP_ROOT}" \
    VCOMP_EXPERIMENT_ROOT="${SNAPSHOT_ROOT}" \
    VCOMP_REPO_ROOT="${VCOMP_ROOT}" \
    DB_BENCH="${DB_BENCH}" \
    RUN_ID="${RUN_ID}" TARGET_DB_GB="${TARGET_DB_GB}" CAPS="${CAPS}" \
    BG_JOBS="${BG_JOBS}" SETTLE_SECONDS="${SETTLE_SECONDS}" READS="${READS}" \
    MAX_RSS_KB="${MAX_RSS_KB}" ABORT_RSS_KB="${ABORT_RSS_KB}" \
    RUN_NO_COMP="${RUN_NO_COMP}" RUN_BASELINE="${RUN_BASELINE}" \
    RUN_LASTCOMP="${RUN_LASTCOMP}" EXP_DIR="${EXP_DIR}" DB_BASE="${DB_BASE}" \
    bash "${SNAPSHOT_ROOT}/scripts/load/run_paper_subcompaction_sweep.sh"
fi

LOAD_SH="${SNAPSHOT_ROOT}/scripts/load/load.sh"
SUMMARY="${EXP_DIR}/summary.tsv"
EXPECTED_BINARY_HASH="$(awk -v path="${DB_BENCH}" '$2 == path {print $1}' "${EXP_DIR}/SHA256SUMS")"
NKEYS=$((TARGET_DB_GB * 1024 * 1024 * 1024 / 1024))

[[ -n "${EXPECTED_BINARY_HASH}" ]] || die "missing frozen db_bench hash"
[[ ! -e "${DB_BASE}" ]] || die "database output exists: ${DB_BASE}"
mkdir -p "${DB_BASE}"
printf 'system\ttarget_gib\tconfigured_cap\tactual_subcompactions\tstatus\telapsed_sec\tfill_sec\tcompact_sec\tpeak_rss_kb\tbenchmark_peak_swap_kb\tsystem_swap_in_delta\tsystem_swap_out_delta\tpending_bytes\tfinal_db_bytes\tread_found\tread_requested\tlog_dir\tdb_dir\n' > "${SUMMARY}"

check_idle() {
  pgrep -x db_bench >/dev/null 2>&1 && die "unexpected db_bench before next run"
  pgrep -x ycsbc_smoke >/dev/null 2>&1 && die "unexpected ycsbc_smoke before next run"
  log "storage idle; settling ${SETTLE_SECONDS}s"
  sleep "${SETTLE_SECONDS}"
  pgrep -x db_bench >/dev/null 2>&1 && die "db_bench appeared during settle"
  return 0
}

vmstat_value() {
  local name="$1" file="$2"
  awk -v name="${name}" '$1 == name {print $2}' "${file}"
}

extract_seconds() {
  local label="$1" file="$2"
  awk -v label="${label}" '$1 == label && $2 == ":" {for(i=1;i<=NF;i++) if($i=="seconds") v=$(i-1)} END{print v}' "${file}"
}

extract_subcompactions() {
  awk '/rocksdb.num.subcompactions.scheduled/ {for(i=1;i<=NF;i++) if($i=="SUM" && $(i+1)==":") v=$(i+2)} END{if(v=="")v=0; printf "%.0f",v}' "$1"
}

extract_pending() {
  awk -F': ' '/^Estimated pending compaction bytes:/ {v=$2} END{if(v=="")v="NA"; print v}' "$1"
}

run_one() {
  local system="$1" mode="$2" cap="$3"
  local run_dir="${EXP_DIR}/${system}" db_dir="${DB_BASE}/${system}"
  local vm_pre="${EXP_DIR}/${system}.vmstat.start.tmp"
  local runner_capture="${EXP_DIR}/${system}.runner.out.tmp"
  local vm_start="${run_dir}/vmstat.start" vm_end="${run_dir}/vmstat.end"
  local rc status elapsed fill_sec compact_sec rss swap_in swap_out pending db_bytes actual found
  local benchmark_swap_kb=0 benchmark_peak_swap_kb=0 monitor_seen=0

  check_idle
  [[ "$(sha256sum "${DB_BENCH}" | awk '{print $1}')" == "${EXPECTED_BINARY_HASH}" ]] || \
    die "db_bench changed after runner snapshot"
  cp /proc/vmstat "${vm_pre}"
  log "BEGIN ${system}: mode=${mode} cap=${cap}"
  set +e
  (
    MODE="${mode}" DB_BENCH="${DB_BENCH}" TARGET_DB_GB="${TARGET_DB_GB}" \
      DB_ROOT="${DB_BASE}" DB_DIR="${db_dir}" LOG_DIR="${run_dir}" \
      BG_JOBS="${BG_JOBS}" SUBCOMPACTIONS="${cap}" \
      VCOMP_DROP_PAGE_CACHE=1 KEY_SIZE=24 VALUE_SIZE=1000 BATCH_SIZE=1 \
      MEMTABLE_REP=vector COMPRESSION_TYPE=none bash "${LOAD_SH}"
  ) > "${runner_capture}" 2>&1 &
  local load_pid=$! db_pid="" current_rss=0 memory_abort=0
  while kill -0 "${load_pid}" 2>/dev/null; do
    db_pid="$(pgrep -x db_bench | head -1)"
    if [[ -n "${db_pid}" && -r "/proc/${db_pid}/status" ]]; then
      monitor_seen=1
      current_rss="$(awk '/^VmRSS:/ {print $2}' "/proc/${db_pid}/status")"
      benchmark_swap_kb="$(awk '/^VmSwap:/ {print $2}' "/proc/${db_pid}/status")"
      benchmark_swap_kb="${benchmark_swap_kb:-0}"
      if (( benchmark_swap_kb > benchmark_peak_swap_kb )); then
        benchmark_peak_swap_kb="${benchmark_swap_kb}"
      fi
      printf '%s\t%s\n' "$(date -u '+%FT%TZ')" "${current_rss:-0}" \
        >> "${EXP_DIR}/${system}.rss_monitor.tmp"
      if [[ "${ABORT_RSS_KB}" -gt 0 && "${current_rss:-0}" -ge "${ABORT_RSS_KB}" ]]; then
        memory_abort=1
        printf 'abort_utc=%s\nthreshold_kb=%s\nobserved_rss_kb=%s\n' \
          "$(date -u '+%FT%TZ')" "${ABORT_RSS_KB}" "${current_rss}" \
          > "${EXP_DIR}/${system}.memory_abort.tmp"
        kill -TERM "${db_pid}" 2>/dev/null || true
        break
      fi
    fi
    sleep 0.25
  done
  wait "${load_pid}"
  rc=$?
  set -e
  mkdir -p "${run_dir}"
  mv "${vm_pre}" "${vm_start}"
  mv "${runner_capture}" "${run_dir}/runner.out"
  [[ ! -e "${EXP_DIR}/${system}.rss_monitor.tmp" ]] || \
    mv "${EXP_DIR}/${system}.rss_monitor.tmp" "${run_dir}/rss_monitor.tsv"
  [[ ! -e "${EXP_DIR}/${system}.memory_abort.tmp" ]] || \
    mv "${EXP_DIR}/${system}.memory_abort.tmp" "${run_dir}/MEMORY_ABORT"
  printf '%s\n' "${benchmark_peak_swap_kb}" > "${run_dir}/raw/benchmark_peak_swap_kb.txt"
  cp /proc/vmstat "${vm_end}"
  if [[ "${rc}" -ne 0 ]]; then
    local failed_status="failed_load_${rc}"
    [[ "${memory_abort}" -eq 0 ]] || failed_status=aborted_memory_gate
    {
      printf '%s\t%s\t%s\t\t%s' "${system}" "${TARGET_DB_GB}" "${cap}" "${failed_status}"
      printf '\t\t\t\t%s\t%s' "${current_rss:-}" "${benchmark_peak_swap_kb}"
      printf '\t\t\t\t\t\t\t%s\t%s\n' "${run_dir}" "${db_dir}"
    } >> "${SUMMARY}"
    log "FAILED ${system}: status=${failed_status} rc=${rc} observed_rss=${current_rss:-NA}KB"
    return 1
  fi

  local reopen=(
    "${DB_BENCH}" --use_existing_db=true --db="${db_dir}"
    --num="${NKEYS}" --reads="${READS}" --key_size=24 --value_size=1000
    --threads=1 --seed=12345678 --use_direct_reads=true
    --compression_type=none --benchmarks=readrandom,stats,levelstats
  )
  { printf '#!/usr/bin/env bash\n'; printf '%q ' "${reopen[@]}"; printf '\n'; } \
    > "${run_dir}/raw/reopen_cmd.sh"
  chmod a-w "${run_dir}/raw/reopen_cmd.sh"
  set +e
  "${reopen[@]}" > "${run_dir}/reopen.out" 2>&1
  local reopen_rc=$?
  set -e

  elapsed="$(<"${run_dir}/raw/elapsed_sec.txt")"
  fill_sec="$(extract_seconds fillrandom "${run_dir}/bench.out")"
  compact_sec="$(extract_seconds compact "${run_dir}/bench.out")"
  rss="$(<"${run_dir}/raw/peak_rss_kb.txt")"
  actual="$(extract_subcompactions "${run_dir}/bench.out")"
  pending="$(extract_pending "${run_dir}/bench.out")"
  db_bytes="$(du -sb "${db_dir}" | awk '{print $1}')"
  found="$(awk '/readrandom.*found/ {for(i=1;i<=NF;i++) if($i=="of") {gsub(/\(/,"",$(i-1)); v=$(i-1)}} END{print v}' "${run_dir}/reopen.out")"
  swap_in=$(( $(vmstat_value pswpin "${vm_end}") - $(vmstat_value pswpin "${vm_start}") ))
  swap_out=$(( $(vmstat_value pswpout "${vm_end}") - $(vmstat_value pswpout "${vm_start}") ))
  status=ok
  [[ "${reopen_rc}" -eq 0 ]] || status=failed_reopen
  [[ "${found}" == "${READS}" ]] || status=failed_reads
  [[ -n "${rss}" && "${rss}" -lt "${MAX_RSS_KB}" ]] || status=failed_memory_gate
  [[ "${monitor_seen}" -eq 1 ]] || status=failed_swap_monitor
  [[ "${benchmark_peak_swap_kb}" -eq 0 ]] || status=failed_benchmark_swap
  if [[ "${mode}" == "l0only" ]]; then
    [[ "${actual}" -eq 0 ]] || status=failed_unexpected_subcompaction
  else
    [[ "${pending}" == "0" ]] || status=failed_pending_compaction
  fi
  if rg -n 'Corruption:|Segmentation fault|Assertion .*failed|FATAL|Out of memory' \
      "${run_dir}/bench.out" "${run_dir}/reopen.out" > "${run_dir}/validation_errors.txt"; then
    status=failed_log_validation
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${system}" "${TARGET_DB_GB}" "${cap}" "${actual}" "${status}" \
    "${elapsed}" "${fill_sec}" "${compact_sec}" "${rss}" "${benchmark_peak_swap_kb}" \
    "${swap_in}" "${swap_out}" "${pending}" "${db_bytes}" "${found}" "${READS}" \
    "${run_dir}" "${db_dir}" >> "${SUMMARY}"
  log "END ${system}: status=${status} elapsed=${elapsed}s rss=${rss}KB actual_subcomp=${actual}"
  [[ "${status}" == "ok" ]]
}

log "immutable runner active: run_id=${RUN_ID} target=${TARGET_DB_GB}GiB caps=${CAPS}"
if [[ "${RUN_NO_COMP}" == "1" ]]; then
  run_one no_comp l0only 1
fi
index=0
for cap in ${CAPS}; do
  if [[ "${RUN_BASELINE}" == "1" && "${RUN_LASTCOMP}" == "1" ]]; then
    if (( index % 2 == 0 )); then
      run_one "baseline_sub${cap}" baseline "${cap}"
      run_one "lastcomp_sub${cap}" l0compact "${cap}"
    else
      run_one "lastcomp_sub${cap}" l0compact "${cap}"
      run_one "baseline_sub${cap}" baseline "${cap}"
    fi
  elif [[ "${RUN_BASELINE}" == "1" ]]; then
    run_one "baseline_sub${cap}" baseline "${cap}"
  else
    run_one "lastcomp_sub${cap}" l0compact "${cap}"
  fi
  index=$((index + 1))
done
printf 'completed_utc=%s\n' "$(date -u '+%FT%TZ')" > "${EXP_DIR}/COMPLETED"
log "all runs completed: ${SUMMARY}"
