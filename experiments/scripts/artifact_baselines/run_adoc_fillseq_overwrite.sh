#!/usr/bin/env bash
# Run a version-matched ADOC-on/ADOC-off pair for a two-phase workload:
#   1. fill the full key space once with fillseq;
#   2. reopen the settled DB and issue random overwrite operations equal to
#      10% of the record count over the same key space.
#
# db_bench has one process-wide --writes value, so the two phases must be
# separate invocations. Both systems use the same phase boundary and options.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

ADOC_ROOT="${ADOC_ROOT:-${ARTIFACT_ROOT}/external_baselines/adoc}"
ADOC_DB_BENCH="${ADOC_DB_BENCH:-${ADOC_ROOT}/db_bench}"
TARGET_GIB="${TARGET_GIB:-1000}"
KEY_SIZE="${KEY_SIZE:-24}"
VALUE_SIZE="${VALUE_SIZE:-1000}"
NUM_KEYS="${NUM_KEYS:-}"
OVERWRITE_PERCENT="${OVERWRITE_PERCENT:-10}"
RUN_ID="${RUN_ID:-$(date '+%y%m%d_%H%M%S')}"
RUN_ROOT="${RUN_ROOT:-${ARTIFACT_ROOT}/log_loads/paper_adoc_fillseq_overwrite_${TARGET_GIB}gib_${RUN_ID}}"
DB_ROOT="${DB_ROOT:-${VCOMP_DB_ROOT%/}/paper_adoc_fillseq_overwrite_${TARGET_GIB}gib_${RUN_ID}}"

BG_JOBS="${BG_JOBS:-48}"
SUBCOMPACTIONS="${SUBCOMPACTIONS:-1}"
WRITE_BUFFER_SIZE="${WRITE_BUFFER_SIZE:-67108864}"
MAX_MEMTABLE_SIZE="${MAX_MEMTABLE_SIZE:-536870912}"
REPORT_INTERVAL_SECONDS="${REPORT_INTERVAL_SECONDS:-1}"
DOTA_TUNING_GAP="${DOTA_TUNING_GAP:-1}"
SYSTEM_ORDER="${SYSTEM_ORDER:-adoc_on adoc_off}"
DRY_RUN="${DRY_RUN:-1}"
ADOC_RELEASE_BUILD_CONFIRMED="${ADOC_RELEASE_BUILD_CONFIRMED:-0}"

FILLSEQ_BENCHMARKS="fillseq,flush,compact0,waitforcompaction,stats,levelstats"
OVERWRITE_BENCHMARKS="waitforcompaction,overwrite,flush,compact0,waitforcompaction,stats,levelstats"
MEMTABLE_REP="vector"
BATCH_SIZE=1
THREADS=1
READS=10000

require_executable "${ADOC_DB_BENCH}" "ADOC artifact db_bench"
require_positive_uint TARGET_GIB
require_positive_uint KEY_SIZE
require_positive_uint VALUE_SIZE
require_positive_uint OVERWRITE_PERCENT
require_positive_uint BG_JOBS
require_positive_uint SUBCOMPACTIONS
require_positive_uint WRITE_BUFFER_SIZE
require_positive_uint MAX_MEMTABLE_SIZE
require_positive_uint REPORT_INTERVAL_SECONDS
require_positive_uint DOTA_TUNING_GAP
[[ "${OVERWRITE_PERCENT}" -le 100 ]] || \
  die "OVERWRITE_PERCENT must be at most 100: ${OVERWRITE_PERCENT}"
[[ "${DRY_RUN}" == "0" || "${DRY_RUN}" == "1" ]] || \
  die "DRY_RUN must be 0 or 1: ${DRY_RUN}"
[[ "${ADOC_RELEASE_BUILD_CONFIRMED}" == "0" || \
   "${ADOC_RELEASE_BUILD_CONFIRMED}" == "1" ]] || \
  die "ADOC_RELEASE_BUILD_CONFIRMED must be 0 or 1"
[[ "${SYSTEM_ORDER}" == "adoc_off adoc_on" || \
   "${SYSTEM_ORDER}" == "adoc_on adoc_off" || \
   "${SYSTEM_ORDER}" == "adoc_off" || \
   "${SYSTEM_ORDER}" == "adoc_on" ]] || \
  die "Unsupported SYSTEM_ORDER: ${SYSTEM_ORDER}"

KV_SIZE=$((KEY_SIZE + VALUE_SIZE))
if [[ -z "${NUM_KEYS}" ]]; then
  NUM_KEYS=$((TARGET_GIB * 1024 * 1024 * 1024 / KV_SIZE))
fi
require_positive_uint NUM_KEYS
OVERWRITE_WRITES=$((NUM_KEYS * OVERWRITE_PERCENT / 100))
[[ "${OVERWRITE_WRITES}" -gt 0 ]] || die "Calculated overwrite count is zero"
FILL_LOGICAL_BYTES=$((NUM_KEYS * KV_SIZE))
OVERWRITE_LOGICAL_BYTES=$((OVERWRITE_WRITES * KV_SIZE))

check_db_bench_interface() {
  local help_text flag benchmark
  help_text="$("${ADOC_DB_BENCH}" --help 2>&1 || true)"
  for flag in statistics stats_interval_seconds stats_per_interval \
      report_interval_seconds report_file enable_index_compression bloom_bits \
      disable_wal max_background_jobs subcompactions write_buffer_size \
      max_memtable_size num writes reads key_size value_size batch_size threads \
      memtablerep allow_concurrent_memtable_write seed db use_existing_db \
      use_direct_reads use_direct_io_for_flush_and_compaction compression_type \
      DOTA_enabled DOTA_tuning_gap FEA_enable TEA_enable benchmarks; do
    [[ "${help_text}" == *"-${flag}"* ]] || \
      die "ADOC db_bench does not expose --${flag}: ${ADOC_DB_BENCH}"
  done
  for benchmark in fillseq overwrite flush compact0 waitforcompaction \
      readrandom stats levelstats; do
    [[ "${help_text}" == *"${benchmark}"* ]] || \
      die "ADOC db_bench does not expose benchmark '${benchmark}'"
  done
}

print_command() {
  printf '%q ' "$@"
  printf '\n'
}

declare -a COMMON_CMD FILLSEQ_CMD OVERWRITE_CMD REOPEN_CMD
build_common_command() {
  local fea="$1"
  local tea="$2"
  local db_dir="$3"
  local report_file="$4"

  COMMON_CMD=(
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
  )
}

build_fillseq_command() {
  build_common_command "$1" "$2" "$3" "$4"
  FILLSEQ_CMD=(
    "${COMMON_CMD[@]}"
    --writes="${NUM_KEYS}"
    --benchmarks="${FILLSEQ_BENCHMARKS}"
  )
}

build_overwrite_command() {
  build_common_command "$1" "$2" "$3" "$4"
  OVERWRITE_CMD=(
    "${COMMON_CMD[@]}"
    --use_existing_db=true
    --writes="${OVERWRITE_WRITES}"
    --benchmarks="${OVERWRITE_BENCHMARKS}"
  )
}

build_reopen_command() {
  local db_dir="$1"
  REOPEN_CMD=(
    "${ADOC_DB_BENCH}"
    --use_existing_db=true
    --num="${NUM_KEYS}"
    --reads="${READS}"
    --key_size="${KEY_SIZE}"
    --value_size="${VALUE_SIZE}"
    --threads=1
    --memtablerep="${MEMTABLE_REP}"
    --allow_concurrent_memtable_write=false
    --seed=12345678
    --db="${db_dir}"
    --use_direct_reads=true
    --use_direct_io_for_flush_and_compaction=true
    --compression_type=none
    --DOTA_enabled=false
    --FEA_enable=false
    --TEA_enable=false
    --benchmarks=waitforcompaction,readrandom,stats,levelstats
  )
}

write_command_file() {
  local output="$1"
  shift
  { printf '#!/usr/bin/env bash\n'; print_command "$@"; } > "${output}"
  chmod a-w "${output}"
}

benchmark_seconds() {
  local benchmark="$1"
  local output="$2"
  awk -v benchmark="${benchmark}" \
    '$1 == benchmark && $2 == ":" {for (i=1;i<=NF;i++) if ($i=="seconds") v=$(i-1)} END{print v}' \
    "${output}"
}

benchmark_operations() {
  local benchmark="$1"
  local output="$2"
  awk -v benchmark="${benchmark}" \
    '$1 == benchmark && $2 == ":" {for (i=1;i<=NF;i++) if ($i=="operations;") v=$(i-1)} END{print v}' \
    "${output}"
}

final_wait_finished() {
  local output="$1"
  local final_state
  final_state="$(awk '/^waitforcompaction\(.*\): (active|finished)/ {v=$0} END{print v}' "${output}")"
  [[ "${final_state}" == *": finished" ]]
}

report_records() {
  local report_file="$1"
  awk 'END{print (NR > 0 ? NR-1 : 0)}' "${report_file}"
}

report_changes() {
  local report_file="$1"
  awk -F, -v batch="$((WRITE_BUFFER_SIZE / 1024 / 1024))" -v jobs="${BG_JOBS}" \
    'NR>1 && NF>=4 && ($3 != batch || $4 != jobs) {n++} END{print n+0}' \
    "${report_file}"
}

pattern_count() {
  local pattern="$1"
  shift
  awk -v pattern="${pattern}" 'index($0, pattern) {n++} END{print n+0}' "$@"
}

print_configuration() {
  printf 'ADOC db_bench:       %s\n' "${ADOC_DB_BENCH}"
  printf 'ADOC version:        '
  "${ADOC_DB_BENCH}" --version 2>&1 | head -1
  printf 'Target live data:    %s GiB logical\n' "${TARGET_GIB}"
  printf 'Records/key space:   %s\n' "${NUM_KEYS}"
  printf 'Fillseq writes:      %s (%s bytes)\n' "${NUM_KEYS}" "${FILL_LOGICAL_BYTES}"
  printf 'Overwrite writes:    %s (%s%%, %s bytes)\n' \
    "${OVERWRITE_WRITES}" "${OVERWRITE_PERCENT}" "${OVERWRITE_LOGICAL_BYTES}"
  printf 'KV size:             %s B key + %s B value = %s B\n' \
    "${KEY_SIZE}" "${VALUE_SIZE}" "${KV_SIZE}"
  printf 'Initial engine state: jobs=%s, memtable=%s B, subcompactions=%s\n' \
    "${BG_JOBS}" "${WRITE_BUFFER_SIZE}" "${SUBCOMPACTIONS}"
  printf 'Reporter:             interval=%ss, tuning_gap=%ss\n' \
    "${REPORT_INTERVAL_SECONDS}" "${DOTA_TUNING_GAP}"
  printf 'Run order:            %s\n' "${SYSTEM_ORDER}"
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
    db_dir="${DB_ROOT}/${system}"
    printf '\n[%s fillseq]\n' "${system}"
    build_fillseq_command "${fea}" "${tea}" "${db_dir}" \
      "${RUN_ROOT}/${system}/fillseq.rep"
    print_command "${FILLSEQ_CMD[@]}"
    printf '[%s overwrite]\n' "${system}"
    build_overwrite_command "${fea}" "${tea}" "${db_dir}" \
      "${RUN_ROOT}/${system}/overwrite.rep"
    print_command "${OVERWRITE_CMD[@]}"
    printf '[%s validation]\n' "${system}"
    build_reopen_command "${db_dir}"
    print_command "${REOPEN_CMD[@]}"
  done
  exit 0
fi

[[ "${ADOC_RELEASE_BUILD_CONFIRMED}" == "1" ]] || die \
  "Refusing a performance run with an unconfirmed release build"
require_no_db_bench "ADOC fillseq/overwrite comparison"
[[ ! -e "${RUN_ROOT}" ]] || die "Log output already exists: ${RUN_ROOT}"
[[ ! -e "${DB_ROOT}" ]] || die "DB output already exists: ${DB_ROOT}"
mkdir -p "${RUN_ROOT}/runner_snapshot" "${DB_ROOT}"

cp -- "$0" "${RUN_ROOT}/runner_snapshot/run_adoc_fillseq_overwrite.sh"
chmod a-w "${RUN_ROOT}/runner_snapshot/run_adoc_fillseq_overwrite.sh"
sha256sum "${ADOC_DB_BENCH}" \
  "${RUN_ROOT}/runner_snapshot/run_adoc_fillseq_overwrite.sh" \
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
  cat /proc/mdstat
} > "${RUN_ROOT}/environment.txt"

printf 'system\tstatus\ttarget_gib\tnum_keys\toverwrite_writes\tfea\ttea\tconfigured_jobs\tconfigured_subcompactions\ttotal_elapsed_sec\tfillseq_wall_sec\tfillseq_benchmark_sec\toverwrite_wall_sec\toverwrite_benchmark_sec\tpeak_rss_kb\tfillseq_wait_state\toverwrite_wait_state\tfinal_db_bytes\ttuner_records\ttuner_config_changes\tfea_triggers\ttea_triggers\tvalidation_elapsed_sec\tread_found\tread_requested\tlog_dir\tdb_dir\n' \
  > "${RUN_ROOT}/summary.tsv"

IOSTAT_PID=""
cleanup_iostat() {
  stop_process "${IOSTAT_PID:-}"
  IOSTAT_PID=""
}
trap cleanup_iostat EXIT
trap 'cleanup_iostat; exit 130' INT
trap 'cleanup_iostat; exit 143' TERM

run_phase() {
  local time_file="$1"
  local output_file="$2"
  shift 2
  set +e
  /usr/bin/time -v -o "${time_file}" "$@" > "${output_file}" 2>&1
  local rc=$?
  set -e
  return "${rc}"
}

run_one() {
  local system="$1"
  local fea="$2"
  local tea="$3"
  local log_dir="${RUN_ROOT}/${system}"
  local db_dir="${DB_ROOT}/${system}"
  local raw_dir="${log_dir}/raw"
  local fillseq_report="${log_dir}/fillseq.rep"
  local overwrite_report="${log_dir}/overwrite.rep"
  local fillseq_out="${log_dir}/fillseq.out"
  local overwrite_out="${log_dir}/overwrite.out"
  local reopen_out="${log_dir}/reopen.out"
  local total_start total_end total_elapsed fillseq_start fillseq_end
  local overwrite_start overwrite_end validation_start validation_end
  local fillseq_wall overwrite_wall validation_elapsed rc
  local fillseq_sec overwrite_sec fillseq_ops overwrite_ops
  local fillseq_rss overwrite_rss peak_rss final_db_bytes
  local fillseq_records overwrite_records tuner_records tuner_changes
  local fea_triggers tea_triggers tuner_initializations
  local read_found read_requested

  [[ ! -e "${log_dir}" ]] || die "Log directory exists: ${log_dir}"
  [[ ! -e "${db_dir}" ]] || die "DB directory exists: ${db_dir}"
  mkdir -p "${raw_dir}" "${db_dir}"

  build_fillseq_command "${fea}" "${tea}" "${db_dir}" "${fillseq_report}"
  write_command_file "${raw_dir}/fillseq_cmd.sh" "${FILLSEQ_CMD[@]}"
  build_overwrite_command "${fea}" "${tea}" "${db_dir}" "${overwrite_report}"
  write_command_file "${raw_dir}/overwrite_cmd.sh" "${OVERWRITE_CMD[@]}"
  build_reopen_command "${db_dir}"
  write_command_file "${raw_dir}/reopen_cmd.sh" "${REOPEN_CMD[@]}"

  printf '\n[%s] starting at %s\n' "${system}" "$(date -u '+%FT%TZ')"
  drop_page_cache
  cat /proc/diskstats > "${raw_dir}/diskstats.start"
  cat /proc/stat > "${raw_dir}/procstat.start"
  cat /proc/vmstat > "${raw_dir}/vmstat.start"
  if command -v iostat >/dev/null 2>&1; then
    iostat -dx 1 > "${raw_dir}/iostat.log" &
    IOSTAT_PID=$!
  fi

  total_start="$(date +%s)"
  printf '%s\n' "${total_start}" > "${raw_dir}/start_epoch.txt"

  printf '[%s] phase 1 fillseq command: ' "${system}"
  print_command "${FILLSEQ_CMD[@]}"
  fillseq_start="$(date +%s)"
  if run_phase "${raw_dir}/fillseq_time.out" "${fillseq_out}" \
      "${FILLSEQ_CMD[@]}"; then
    :
  else
    rc=$?
    printf '%s\tfailed:fillseq:%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "${system}" "${rc}" "${TARGET_GIB}" "${NUM_KEYS}" \
      "${OVERWRITE_WRITES}" "${fea}" "${tea}" "${BG_JOBS}" \
      "${SUBCOMPACTIONS}" >> "${RUN_ROOT}/summary.tsv"
    return "${rc}"
  fi
  fillseq_end="$(date +%s)"
  fillseq_wall=$((fillseq_end - fillseq_start))
  printf '%s\n' "${fillseq_wall}" > "${raw_dir}/fillseq_elapsed_sec.txt"

  printf '[%s] phase 2 overwrite command: ' "${system}"
  print_command "${OVERWRITE_CMD[@]}"
  overwrite_start="$(date +%s)"
  if run_phase "${raw_dir}/overwrite_time.out" "${overwrite_out}" \
      "${OVERWRITE_CMD[@]}"; then
    :
  else
    rc=$?
    printf '%s\tfailed:overwrite:%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "${system}" "${rc}" "${TARGET_GIB}" "${NUM_KEYS}" \
      "${OVERWRITE_WRITES}" "${fea}" "${tea}" "${BG_JOBS}" \
      "${SUBCOMPACTIONS}" >> "${RUN_ROOT}/summary.tsv"
    return "${rc}"
  fi
  overwrite_end="$(date +%s)"
  overwrite_wall=$((overwrite_end - overwrite_start))
  printf '%s\n' "${overwrite_wall}" > "${raw_dir}/overwrite_elapsed_sec.txt"

  total_end="$(date +%s)"
  total_elapsed=$((total_end - total_start))
  printf '%s\n' "${total_end}" > "${raw_dir}/end_epoch.txt"
  printf '%s\n' "${total_elapsed}" > "${raw_dir}/total_elapsed_sec.txt"
  cleanup_iostat
  cat /proc/diskstats > "${raw_dir}/diskstats.end"
  cat /proc/stat > "${raw_dir}/procstat.end"
  cat /proc/vmstat > "${raw_dir}/vmstat.end"

  if rg -n 'Corruption:|Segmentation fault|Assertion .*failed|FATAL|Out of memory' \
      "${fillseq_out}" "${overwrite_out}" > "${log_dir}/validation_errors.txt"; then
    die "${system} emitted a fatal validation pattern"
  fi
  final_wait_finished "${fillseq_out}" || \
    die "${system} fillseq phase did not report a settled boundary"
  final_wait_finished "${overwrite_out}" || \
    die "${system} overwrite phase did not report a settled boundary"

  fillseq_sec="$(benchmark_seconds fillseq "${fillseq_out}")"
  overwrite_sec="$(benchmark_seconds overwrite "${overwrite_out}")"
  fillseq_ops="$(benchmark_operations fillseq "${fillseq_out}")"
  overwrite_ops="$(benchmark_operations overwrite "${overwrite_out}")"
  [[ "${fillseq_ops}" == "${NUM_KEYS}" ]] || \
    die "${system} fillseq operation mismatch: ${fillseq_ops} != ${NUM_KEYS}"
  [[ "${overwrite_ops}" == "${OVERWRITE_WRITES}" ]] || \
    die "${system} overwrite operation mismatch: ${overwrite_ops} != ${OVERWRITE_WRITES}"

  tuner_initializations="$(pattern_count 'Using FEAT tuner' "${fillseq_out}" "${overwrite_out}")"
  fea_triggers="$(pattern_count 'FEA is triggered' "${fillseq_out}" "${overwrite_out}")"
  tea_triggers="$(pattern_count 'TEA is triggered' "${fillseq_out}" "${overwrite_out}")"
  if [[ "${system}" == "adoc_on" ]]; then
    [[ "${tuner_initializations}" -ge 2 ]] || \
      die "ADOC-on did not initialize the tuner in both phases"
    if [[ "${fea_triggers}" -eq 0 || "${tea_triggers}" -eq 0 ]]; then
      printf '[WARN] %s trigger counts: FEA=%s TEA=%s\n' \
        "${system}" "${fea_triggers}" "${tea_triggers}" >&2
    fi
  elif [[ "${tuner_initializations}" -ne 0 ]]; then
    die "ADOC-off unexpectedly initialized the FEAT tuner"
  fi

  validation_start="$(date +%s)"
  "${REOPEN_CMD[@]}" > "${reopen_out}" 2>&1
  validation_end="$(date +%s)"
  validation_elapsed=$((validation_end - validation_start))
  printf '%s\n' "${validation_elapsed}" > "${raw_dir}/validation_elapsed_sec.txt"
  final_wait_finished "${reopen_out}" || \
    die "${system} reopen validation did not settle"
  read_found="$(awk '/readrandom.*found/ {for(i=1;i<=NF;i++) if($i=="of") {gsub(/\(/,"",$(i-1)); v=$(i-1)}} END{print v}' "${reopen_out}")"
  read_requested="${READS}"
  [[ "${read_found}" == "${read_requested}" ]] || \
    die "${system} reopen validation found ${read_found}/${read_requested} keys"

  fillseq_rss="$(extract_peak_rss_kb "${raw_dir}/fillseq_time.out")"
  overwrite_rss="$(extract_peak_rss_kb "${raw_dir}/overwrite_time.out")"
  peak_rss="$(awk -v a="${fillseq_rss:-0}" -v b="${overwrite_rss:-0}" \
    'BEGIN{print (a > b ? a : b)}')"
  final_db_bytes="$(du -sb "${db_dir}" | awk '{print $1}')"
  fillseq_records="$(report_records "${fillseq_report}")"
  overwrite_records="$(report_records "${overwrite_report}")"
  tuner_records=$((fillseq_records + overwrite_records))
  tuner_changes=0
  if [[ "${system}" == "adoc_on" ]]; then
    tuner_changes=$((
      $(report_changes "${fillseq_report}") +
      $(report_changes "${overwrite_report}")
    ))
  fi

  local -a summary_fields=(
    "${system}" ok "${TARGET_GIB}" "${NUM_KEYS}" "${OVERWRITE_WRITES}"
    "${fea}" "${tea}" "${BG_JOBS}" "${SUBCOMPACTIONS}"
    "${total_elapsed}" "${fillseq_wall}" "${fillseq_sec}"
    "${overwrite_wall}" "${overwrite_sec}" "${peak_rss}"
    finished finished "${final_db_bytes}" "${tuner_records}"
    "${tuner_changes}" "${fea_triggers}" "${tea_triggers}"
    "${validation_elapsed}" "${read_found}" "${read_requested}"
    "${log_dir}" "${db_dir}"
  )
  (IFS=$'\t'; printf '%s\n' "${summary_fields[*]}") >> "${RUN_ROOT}/summary.tsv"
  printf '[%s] completed: total=%ss fillseq=%ss overwrite=%ss read=%s/%s\n' \
    "${system}" "${total_elapsed}" "${fillseq_wall}" "${overwrite_wall}" \
    "${read_found}" "${read_requested}"
}

for system in ${SYSTEM_ORDER}; do
  require_no_db_bench "${system} phase"
  case "${system}" in
    adoc_off) run_one adoc_off false false ;;
    adoc_on) run_one adoc_on true true ;;
  esac
done

printf 'completed_utc=%s\n' "$(date -u '+%FT%TZ')" > "${RUN_ROOT}/COMPLETED"
printf 'Completed ADOC fillseq/overwrite pair. Summary: %s\n' \
  "${RUN_ROOT}/summary.tsv"
