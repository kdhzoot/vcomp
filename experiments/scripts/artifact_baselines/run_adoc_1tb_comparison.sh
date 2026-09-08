#!/usr/bin/env bash
# Run a version-matched ADOC-off/ADOC-on pair with the workload used for the
# paper's 1 TB loading figure. The historical figure command is preserved at:
#   artifacts/log_loads/motivation_load_scaling_260602_cleanrocksdb_motivation/
#     baseline_1000gb/raw/load_cmd.sh
#
# The historical command relied on db_bench defaults for one writer, batch
# size 1, a 64 MiB write buffer, and one subcompaction. This runner makes those
# defaults explicit and uses the paper configuration's vector memtable. ADOC
# needs a one-second reporter interval because its artifact executes tuning
# from the reporter thread. ADOC-off receives the same reporting options so
# the matched pair differs only in FEA_enable/TEA_enable.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

ADOC_ROOT="${ADOC_ROOT:-${ARTIFACT_ROOT}/external_baselines/adoc}"
ADOC_DB_BENCH="${ADOC_DB_BENCH:-${ADOC_ROOT}/db_bench}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
RUN_ROOT="${RUN_ROOT:-${ARTIFACT_ROOT}/log_loads/paper_adoc_1tb_${RUN_ID}}"
DB_ROOT="${DB_ROOT:-${VCOMP_DB_ROOT%/}/paper_adoc_1tb_${RUN_ID}}"

# Figure workload: 1000 GiB logical input of 24 B keys + 1000 B values.
TARGET_GB="${TARGET_GB:-1000}"
KEY_SIZE="${KEY_SIZE:-24}"
VALUE_SIZE="${VALUE_SIZE:-1000}"
NUM_KEYS="${NUM_KEYS:-}"
BG_JOBS="${BG_JOBS:-48}"
CORE_NUM="${CORE_NUM:-${BG_JOBS}}"
SUBCOMPACTIONS="${SUBCOMPACTIONS:-1}"
WRITE_BUFFER_SIZE="${WRITE_BUFFER_SIZE:-67108864}"
MAX_MEMTABLE_SIZE="${MAX_MEMTABLE_SIZE:-536870912}"
MAX_WRITE_BUFFER_NUMBER="${MAX_WRITE_BUFFER_NUMBER:-2}"
MIN_WRITE_BUFFER_NUMBER_TO_MERGE="${MIN_WRITE_BUFFER_NUMBER_TO_MERGE:-1}"
SOFT_PENDING_COMPACTION_BYTES_LIMIT="${SOFT_PENDING_COMPACTION_BYTES_LIMIT:-68719476736}"
HARD_PENDING_COMPACTION_BYTES_LIMIT="${HARD_PENDING_COMPACTION_BYTES_LIMIT:-137438953472}"
REPORT_INTERVAL_SECONDS="${REPORT_INTERVAL_SECONDS:-1}"
DOTA_TUNING_GAP="${DOTA_TUNING_GAP:-1}"
SYSTEM_ORDER="${SYSTEM_ORDER:-adoc_off adoc_on}"
DRY_RUN="${DRY_RUN:-1}"
ADOC_RELEASE_BUILD_CONFIRMED="${ADOC_RELEASE_BUILD_CONFIRMED:-0}"

BENCHMARKS="fillrandom,flush,compact0,waitforcompaction,stats,levelstats"
MEMTABLE_REP="vector"
BATCH_SIZE=1
THREADS=1

require_executable "${ADOC_DB_BENCH}" "ADOC artifact db_bench"
require_positive_uint TARGET_GB
require_positive_uint KEY_SIZE
require_positive_uint VALUE_SIZE
require_positive_uint BG_JOBS
require_positive_uint CORE_NUM
require_positive_uint SUBCOMPACTIONS
require_positive_uint WRITE_BUFFER_SIZE
require_positive_uint MAX_MEMTABLE_SIZE
require_positive_uint MAX_WRITE_BUFFER_NUMBER
require_positive_uint MIN_WRITE_BUFFER_NUMBER_TO_MERGE
require_positive_uint SOFT_PENDING_COMPACTION_BYTES_LIMIT
require_positive_uint HARD_PENDING_COMPACTION_BYTES_LIMIT
require_positive_uint REPORT_INTERVAL_SECONDS
require_positive_uint DOTA_TUNING_GAP
[[ "${DRY_RUN}" == "0" || "${DRY_RUN}" == "1" ]] || \
  die "DRY_RUN must be 0 or 1: ${DRY_RUN}"
[[ "${ADOC_RELEASE_BUILD_CONFIRMED}" == "0" || \
   "${ADOC_RELEASE_BUILD_CONFIRMED}" == "1" ]] || \
  die "ADOC_RELEASE_BUILD_CONFIRMED must be 0 or 1"
[[ "${SYSTEM_ORDER}" == "adoc_off adoc_on" || \
   "${SYSTEM_ORDER}" == "adoc_on adoc_off" || \
   "${SYSTEM_ORDER}" == "adoc_off" || \
   "${SYSTEM_ORDER}" == "adoc_on" ]] || \
  die "SYSTEM_ORDER must be 'adoc_off adoc_on', 'adoc_on adoc_off', 'adoc_off', or 'adoc_on'"

KV_SIZE=$((KEY_SIZE + VALUE_SIZE))
if [[ -z "${NUM_KEYS}" ]]; then
  NUM_KEYS=$((TARGET_GB * 1024 * 1024 * 1024 / KV_SIZE))
fi
require_positive_uint NUM_KEYS
LOGICAL_BYTES=$((NUM_KEYS * KV_SIZE))

check_db_bench_interface() {
  local help_text flag
  help_text="$("${ADOC_DB_BENCH}" --help 2>&1 || true)"
  for flag in statistics stats_interval_seconds stats_per_interval \
      report_interval_seconds report_file enable_index_compression bloom_bits \
      disable_wal max_background_jobs subcompactions write_buffer_size \
      core_num max_memtable_size num key_size value_size batch_size threads memtablerep \
      max_write_buffer_number min_write_buffer_number_to_merge \
      soft_pending_compaction_bytes_limit hard_pending_compaction_bytes_limit \
      allow_concurrent_memtable_write seed db use_direct_reads \
      use_direct_io_for_flush_and_compaction compression_type DOTA_enabled \
      DOTA_tuning_gap FEA_enable TEA_enable benchmarks; do
    [[ "${help_text}" == *"-${flag}"* ]] || \
      die "ADOC db_bench does not expose --${flag}: ${ADOC_DB_BENCH}"
  done
  for benchmark in fillrandom flush compact0 waitforcompaction stats levelstats; do
    [[ "${help_text}" == *"${benchmark}"* ]] || \
      die "ADOC db_bench does not expose benchmark '${benchmark}'"
  done
}

print_command() {
  printf '%q ' "$@"
  printf '\n'
}

declare -a LOAD_CMD
build_load_command() {
  local fea="$1"
  local tea="$2"
  local db_dir="$3"
  local report_file="$4"

  LOAD_CMD=(
    "${ADOC_DB_BENCH}"
    --statistics=1
    --stats_interval_seconds=60
    --stats_per_interval=1
    --report_interval_seconds="${REPORT_INTERVAL_SECONDS}"
    --report_file="${report_file}"
    --enable_index_compression=false
    --bloom_bits=10
    --disable_wal=true
    --max_background_jobs="${BG_JOBS}"
    --subcompactions="${SUBCOMPACTIONS}"
    --write_buffer_size="${WRITE_BUFFER_SIZE}"
    --max_write_buffer_number="${MAX_WRITE_BUFFER_NUMBER}"
    --min_write_buffer_number_to_merge="${MIN_WRITE_BUFFER_NUMBER_TO_MERGE}"
    --soft_pending_compaction_bytes_limit="${SOFT_PENDING_COMPACTION_BYTES_LIMIT}"
    --hard_pending_compaction_bytes_limit="${HARD_PENDING_COMPACTION_BYTES_LIMIT}"
    --core_num="${CORE_NUM}"
    --max_memtable_size="${MAX_MEMTABLE_SIZE}"
    --num="${NUM_KEYS}"
    --key_size="${KEY_SIZE}"
    --value_size="${VALUE_SIZE}"
    --batch_size="${BATCH_SIZE}"
    --threads="${THREADS}"
    --memtablerep="${MEMTABLE_REP}"
    --allow_concurrent_memtable_write=false
    --seed=12345678
    --db="${db_dir}"
    --use_direct_reads=true
    --use_direct_io_for_flush_and_compaction=true
    --compression_type=none
    --DOTA_enabled=false
    --DOTA_tuning_gap="${DOTA_TUNING_GAP}"
    --FEA_enable="${fea}"
    --TEA_enable="${tea}"
    --benchmarks="${BENCHMARKS}"
  )
}

print_configuration() {
  printf 'ADOC db_bench: %s\n' "${ADOC_DB_BENCH}"
  printf 'ADOC version:  '
  "${ADOC_DB_BENCH}" --version 2>&1 | head -1
  printf 'Target label:  %s GiB logical input\n' "${TARGET_GB}"
  printf 'Logical bytes: %s\n' "${LOGICAL_BYTES}"
  printf 'Records:       %s\n' "${NUM_KEYS}"
  printf 'KV size:       %s B key + %s B value = %s B\n' \
    "${KEY_SIZE}" "${VALUE_SIZE}" "${KV_SIZE}"
  printf 'Initial state:  jobs=%s, ADOC thread cap=%s, memtable=%s B x %s, subcompactions=%s\n' \
    "${BG_JOBS}" "${CORE_NUM}" "${WRITE_BUFFER_SIZE}" \
    "${MAX_WRITE_BUFFER_NUMBER}" "${SUBCOMPACTIONS}"
  printf 'Pending limits: soft=%s B, hard=%s B\n' \
    "${SOFT_PENDING_COMPACTION_BYTES_LIMIT}" "${HARD_PENDING_COMPACTION_BYTES_LIMIT}"
  printf 'Reporter:       interval=%ss, tuning_gap=%ss\n' \
    "${REPORT_INTERVAL_SECONDS}" "${DOTA_TUNING_GAP}"
  printf 'Run order:      %s\n' "${SYSTEM_ORDER}"
}

check_db_bench_interface
print_configuration

if [[ "${DRY_RUN}" == "1" ]]; then
  printf '\nDRY_RUN=1; no DB or log directories will be created.\n'
  for system in ${SYSTEM_ORDER}; do
    case "${system}" in
      adoc_off) fea=false; tea=false ;;
      adoc_on) fea=true; tea=true ;;
    esac
    build_load_command "${fea}" "${tea}" \
      "${DB_ROOT}/${system}" "${RUN_ROOT}/${system}/report.rep"
    printf '\n[%s]\n' "${system}"
    print_command "${LOAD_CMD[@]}"
  done
  exit 0
fi

[[ "${ADOC_RELEASE_BUILD_CONFIRMED}" == "1" ]] || die \
  "Refusing a performance run with an unconfirmed build. Rebuild like the baseline with 'make clean' followed by 'CC=gcc-11 CXX=g++-11 make static_lib db_bench -j\$(nproc)', verify it, then set ADOC_RELEASE_BUILD_CONFIRMED=1."
require_no_db_bench "ADOC 1 TB comparison"
[[ ! -e "${RUN_ROOT}" ]] || die "Log output already exists: ${RUN_ROOT}"
[[ ! -e "${DB_ROOT}" ]] || die "DB output already exists: ${DB_ROOT}"
mkdir -p "${RUN_ROOT}/runner_snapshot" "${DB_ROOT}"

cp -- "$0" "${RUN_ROOT}/runner_snapshot/run_adoc_1tb_comparison.sh"
chmod a-w "${RUN_ROOT}/runner_snapshot/run_adoc_1tb_comparison.sh"
sha256sum "${ADOC_DB_BENCH}" \
  "${RUN_ROOT}/runner_snapshot/run_adoc_1tb_comparison.sh" \
  > "${RUN_ROOT}/SHA256SUMS"
git -C "${ADOC_ROOT}" rev-parse HEAD > "${RUN_ROOT}/adoc_commit.txt"
git -C "${ADOC_ROOT}" status --short > "${RUN_ROOT}/adoc_git_status.txt"
git -C "${ADOC_ROOT}" diff > "${RUN_ROOT}/adoc.patch"
{
  date -u
  uname -a
  "${ADOC_DB_BENCH}" --version 2>&1 | head -1
  free -h
  swapon --show
  df -h "${DB_ROOT}"
} > "${RUN_ROOT}/environment.txt"

printf 'system\tstatus\ttarget_gib\tnum_keys\tfea\ttea\tconfigured_jobs\tconfigured_core_num\tconfigured_subcompactions\telapsed_sec\tfillrandom_sec\tpeak_rss_kb\tpending_bytes\tfinal_db_bytes\ttuner_records\ttuner_config_changes\tread_found\tread_requested\tlog_dir\tdb_dir\n' \
  > "${RUN_ROOT}/summary.tsv"

IOSTAT_PID=""
cleanup_iostat() {
  stop_process "${IOSTAT_PID:-}"
  IOSTAT_PID=""
}
trap cleanup_iostat EXIT
trap 'cleanup_iostat; exit 130' INT
trap 'cleanup_iostat; exit 143' TERM

run_one() {
  local system="$1"
  local fea="$2"
  local tea="$3"
  local log_dir="${RUN_ROOT}/${system}"
  local db_dir="${DB_ROOT}/${system}"
  local raw_dir="${log_dir}/raw"
  local report_file="${log_dir}/report.rep"
  local bench_out="${log_dir}/bench.out"
  local start_ts end_ts elapsed_sec rc fillrandom_sec peak_rss_kb
  local pending_bytes final_db_bytes tuner_records tuner_changes
  local read_found read_requested

  [[ ! -e "${log_dir}" ]] || die "Log directory exists: ${log_dir}"
  [[ ! -e "${db_dir}" ]] || die "DB directory exists: ${db_dir}"
  mkdir -p "${raw_dir}" "${db_dir}"
  build_load_command "${fea}" "${tea}" "${db_dir}" "${report_file}"
  { printf '#!/usr/bin/env bash\n'; print_command "${LOAD_CMD[@]}"; } \
    > "${raw_dir}/load_cmd.sh"
  chmod a-w "${raw_dir}/load_cmd.sh"

  printf '\n[%s] starting at %s\n' "${system}" "$(date -u '+%FT%TZ')"
  printf '[%s] command: ' "${system}"
  print_command "${LOAD_CMD[@]}"

  drop_page_cache
  cat /proc/diskstats > "${raw_dir}/diskstats.start"
  cat /proc/stat > "${raw_dir}/procstat.start"
  cat /proc/vmstat > "${raw_dir}/vmstat.start"
  if command -v iostat >/dev/null 2>&1; then
    iostat -dx 1 > "${raw_dir}/iostat.log" &
    IOSTAT_PID=$!
  fi

  start_ts="$(date +%s)"
  printf '%s\n' "${start_ts}" > "${raw_dir}/start_epoch.txt"
  set +e
  /usr/bin/time -v -o "${raw_dir}/time.out" \
    "${LOAD_CMD[@]}" > "${bench_out}" 2>&1
  rc=$?
  set -e
  end_ts="$(date +%s)"
  cleanup_iostat

  printf '%s\n' "${end_ts}" > "${raw_dir}/end_epoch.txt"
  elapsed_sec=$((end_ts - start_ts))
  printf '%s\n' "${elapsed_sec}" > "${raw_dir}/elapsed_sec.txt"
  cat /proc/diskstats > "${raw_dir}/diskstats.end"
  cat /proc/stat > "${raw_dir}/procstat.end"
  cat /proc/vmstat > "${raw_dir}/vmstat.end"

  if [[ "${rc}" -ne 0 ]]; then
    printf '%s\tfailed:%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t\t\t\t\t\t\t\t\t%s\t%s\n' \
      "${system}" "${rc}" "${TARGET_GB}" "${NUM_KEYS}" "${fea}" "${tea}" \
      "${BG_JOBS}" "${CORE_NUM}" "${SUBCOMPACTIONS}" "${elapsed_sec}" "${log_dir}" "${db_dir}" \
      >> "${RUN_ROOT}/summary.tsv"
    return "${rc}"
  fi

  if rg -n 'Corruption:|Segmentation fault|Assertion .*failed|FATAL|Out of memory' \
      "${bench_out}" > "${log_dir}/validation_errors.txt"; then
    die "${system} emitted a fatal validation pattern; see ${log_dir}/validation_errors.txt"
  fi
  if [[ "${system}" == "adoc_on" ]]; then
    rg -q 'Using FEAT tuner' "${bench_out}" || die "ADOC tuner did not initialize"
    rg -q 'FEA is triggered' "${bench_out}" || die "FEA did not activate"
    rg -q 'TEA is triggered' "${bench_out}" || die "TEA did not activate"
  elif rg -q 'Using FEAT tuner' "${bench_out}"; then
    die "ADOC-off unexpectedly initialized the FEAT tuner"
  fi

  fillrandom_sec="$(awk '$1 == "fillrandom" && $2 == ":" {for (i=1;i<=NF;i++) if ($i=="seconds") v=$(i-1)} END{print v}' "${bench_out}")"
  peak_rss_kb="$(extract_peak_rss_kb "${raw_dir}/time.out")"
  pending_bytes="$(awk -F': ' '/^Estimated pending compaction bytes:/ {v=$2} END{if(v=="")v="NA"; print v}' "${bench_out}")"
  if [[ "${pending_bytes}" == "NA" ]]; then
    # RocksDB 7.7 does not print the newer "Estimated pending compaction
    # bytes" line. Its db_bench waitforcompaction benchmark reports its own
    # completion state; accept only when the final such state is "finished".
    local final_wait_state
    final_wait_state="$(awk '/^waitforcompaction\(.*\): (active|finished)/ {v=$0} END{print v}' "${bench_out}")"
    [[ "${final_wait_state}" == *": finished" ]] && pending_bytes=0
  fi
  [[ "${pending_bytes}" == "0" ]] || \
    die "${system} did not reach the settled-state boundary (pending bytes: ${pending_bytes})"
  final_db_bytes="$(du -sb "${db_dir}" | awk '{print $1}')"
  tuner_records=0
  tuner_changes=0
  if [[ -f "${report_file}" ]]; then
    tuner_records="$(awk 'END{print (NR > 0 ? NR-1 : 0)}' "${report_file}")"
    if [[ "${system}" == "adoc_on" ]]; then
      tuner_changes="$(awk -F, -v batch="$((WRITE_BUFFER_SIZE / 1024 / 1024))" -v jobs="${BG_JOBS}" \
        'NR>1 && NF>=4 && ($3 != batch || $4 != jobs) {n++} END{print n+0}' "${report_file}")"
    fi
  fi

  local -a reopen_cmd=(
    "${ADOC_DB_BENCH}"
    --use_existing_db=true
    --num="${NUM_KEYS}"
    --reads=10000
    --key_size="${KEY_SIZE}"
    --value_size="${VALUE_SIZE}"
    --threads=1
    --seed=12345678
    --db="${db_dir}"
    --use_direct_reads=true
    --compression_type=none
    --DOTA_enabled=false
    --FEA_enable=false
    --TEA_enable=false
    --benchmarks=readrandom,stats,levelstats
  )
  { printf '#!/usr/bin/env bash\n'; print_command "${reopen_cmd[@]}"; } \
    > "${raw_dir}/reopen_cmd.sh"
  chmod a-w "${raw_dir}/reopen_cmd.sh"
  "${reopen_cmd[@]}" > "${log_dir}/reopen.out" 2>&1
  read_found="$(awk '/readrandom.*found/ {for(i=1;i<=NF;i++) if($i=="of") {gsub(/\(/,"",$(i-1)); v=$(i-1)}} END{print v}' "${log_dir}/reopen.out")"
  read_requested=10000
  [[ -n "${read_found}" && "${read_found}" -gt 0 ]] || \
    die "${system} reopen/read validation found no keys"

  printf '%s\tok\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${system}" "${TARGET_GB}" "${NUM_KEYS}" "${fea}" "${tea}" \
    "${BG_JOBS}" "${CORE_NUM}" "${SUBCOMPACTIONS}" "${elapsed_sec}" "${fillrandom_sec}" \
    "${peak_rss_kb}" "${pending_bytes}" "${final_db_bytes}" "${tuner_records}" \
    "${tuner_changes}" "${read_found}" "${read_requested}" "${log_dir}" "${db_dir}" \
    >> "${RUN_ROOT}/summary.tsv"
  printf '[%s] completed in %ss; tuner config-change records=%s\n' \
    "${system}" "${elapsed_sec}" "${tuner_changes}"
}

for system in ${SYSTEM_ORDER}; do
  case "${system}" in
    adoc_off) run_one adoc_off false false ;;
    adoc_on) run_one adoc_on true true ;;
  esac
done

printf 'completed_utc=%s\n' "$(date -u '+%FT%TZ')" > "${RUN_ROOT}/COMPLETED"
printf 'Completed ADOC pair. Summary: %s\n' "${RUN_ROOT}/summary.tsv"
