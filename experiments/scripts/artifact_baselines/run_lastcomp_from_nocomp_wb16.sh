#!/usr/bin/env bash
# Preserve a No-comp DB and compact a metadata-private hard-link checkpoint.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/common.sh"

DB_BENCH="${DB_BENCH:-${REPO_ROOT}/db_bench}"
EXPECTED_SHA256="${EXPECTED_SHA256:-c5964e54fb0afc4779fd17b82f7366bdd8d19be9bd2acae33dd26c77e55019cd}"
SOURCE_DB="${SOURCE_DB:?SOURCE_DB is required}"
SOURCE_LOG="${SOURCE_LOG:?SOURCE_LOG is required}"
SOURCE_ELAPSED_SEC="${SOURCE_ELAPSED_SEC:?SOURCE_ELAPSED_SEC is required}"
TARGET_GIB="${TARGET_GIB:?TARGET_GIB is required}"
RUN_ROOT="${RUN_ROOT:?RUN_ROOT is required}"
DB_DIR="${DB_DIR:?DB_DIR is required}"
READS="${READS:-10000}"
DRY_RUN="${DRY_RUN:-1}"

require_executable "${DB_BENCH}" "frozen vcomp db_bench"
require_dir "${SOURCE_DB}" "No-comp source DB"
require_dir "${SOURCE_LOG}" "No-comp source log"
require_positive_uint SOURCE_ELAPSED_SEC
require_positive_uint TARGET_GIB
require_positive_uint READS
[[ "${DRY_RUN}" == "0" || "${DRY_RUN}" == "1" ]] || die "DRY_RUN must be 0 or 1"
[[ "$(sha256sum "${DB_BENCH}" | awk '{print $1}')" == "${EXPECTED_SHA256}" ]] || \
  die "db_bench binary mismatch"

num_keys=$((TARGET_GIB * 1024 * 1024 * 1024 / 1024))
declare -a COMPACT_CMD REOPEN_CMD
COMPACT_CMD=(
  "${DB_BENCH}"
  --use_existing_db=true
  --statistics=1
  --stats_interval_seconds=60
  --stats_per_interval=1
  --report_interval_seconds=10
  --report_file="${RUN_ROOT}/compact.rep"
  --enable_index_compression=false
  --bloom_bits=10
  --disable_wal=true
  --max_background_jobs=48
  --subcompactions=1
  --write_buffer_size=67108864
  --max_write_buffer_number=16
  --min_write_buffer_number_to_merge=1
  --num="${num_keys}"
  --key_size=24
  --value_size=1000
  --batch_size=1
  --threads=1
  --memtablerep=vector
  --allow_concurrent_memtable_write=true
  --seed=12345678
  --db="${DB_DIR}"
  --use_direct_reads=true
  --use_direct_io_for_flush_and_compaction=true
  --compression_type=none
  --disable_auto_compactions=true
  --level0_file_num_compaction_trigger=1073741824
  --level0_slowdown_writes_trigger=1073741824
  --level0_stop_writes_trigger=1073741824
  --soft_pending_compaction_bytes_limit=0
  --hard_pending_compaction_bytes_limit=0
  --benchmarks=compact,stats,levelstats
)
REOPEN_CMD=(
  "${DB_BENCH}"
  --use_existing_db=true
  --num="${num_keys}"
  --reads="${READS}"
  --key_size=24
  --value_size=1000
  --threads=1
  --memtablerep=vector
  --allow_concurrent_memtable_write=true
  --seed=12345678
  --db="${DB_DIR}"
  --use_direct_reads=true
  --use_direct_io_for_flush_and_compaction=true
  --compression_type=none
  --disable_auto_compactions=true
  --benchmarks=readrandom,stats,levelstats
)
print_command() { printf '%q ' "$@"; printf '\n'; }

if [[ "${DRY_RUN}" == "1" ]]; then
  printf 'Source DB: %s\nTarget DB: %s\n' "${SOURCE_DB}" "${DB_DIR}"
  printf '[one-shot compact]\n'; print_command "${COMPACT_CMD[@]}"
  printf '[reopen]\n'; print_command "${REOPEN_CMD[@]}"
  exit 0
fi

if pgrep -x db_bench >/dev/null 2>&1 || pgrep -x titandb_bench >/dev/null 2>&1; then
  die "another storage benchmark is active"
fi
[[ ! -e "${RUN_ROOT}" ]] || die "log output already exists: ${RUN_ROOT}"
[[ ! -e "${DB_DIR}" ]] || die "DB output already exists: ${DB_DIR}"
mkdir -p "${RUN_ROOT}/raw" "${DB_DIR}"

source_sst_count="$(find "${SOURCE_DB}" -maxdepth 1 -type f -name '*.sst' | wc -l)"
source_sst_bytes="$(find "${SOURCE_DB}" -maxdepth 1 -type f -name '*.sst' -printf '%s\n' | awk '{s+=$1} END{print s+0}')"
[[ "${source_sst_count}" -gt 0 ]] || die "No-comp source contains no SSTs"
rg -q 'rocksdb.compaction.times.micros .* COUNT : 0 ' "${SOURCE_LOG}/bench.out" || \
  die "source artifact is not zero-compaction"
rg -q 'Options.max_write_buffer_number: +16$' "${SOURCE_DB}"/LOG* || \
  die "source artifact is not a 16-buffer DB"

find "${SOURCE_DB}" -maxdepth 1 -type f \
  \( -name CURRENT -o -name IDENTITY -o -name 'MANIFEST-*' -o \
     -name 'OPTIONS-*' -o -name '*.log' \) \
  -exec cp -p -t "${DB_DIR}" -- {} +
find "${SOURCE_DB}" -maxdepth 1 -type f -name '*.sst' -print0 | \
  while IFS= read -r -d '' source_sst; do
    ln -- "${source_sst}" "${DB_DIR}/$(basename "${source_sst}")"
  done
find "${SOURCE_DB}" -maxdepth 1 -type f -name '*.sst' -printf '%i %f\n' | sort \
  > "${RUN_ROOT}/raw/source_sst_inodes.before"
find "${DB_DIR}" -maxdepth 1 -type f -name '*.sst' -printf '%i %f\n' | sort \
  > "${RUN_ROOT}/raw/checkpoint_sst_inodes.before"
cmp "${RUN_ROOT}/raw/source_sst_inodes.before" \
  "${RUN_ROOT}/raw/checkpoint_sst_inodes.before" || die "SST checkpoint is not hard-linked"
find "${SOURCE_DB}" -maxdepth 1 -type f \
  \( -name CURRENT -o -name IDENTITY -o -name 'MANIFEST-*' -o -name 'OPTIONS-*' \) \
  -exec sha256sum {} + | sort > "${RUN_ROOT}/raw/source_metadata.before.sha256"
printf '%s\n' "${source_sst_count}" > "${RUN_ROOT}/raw/source_sst_count.txt"
printf '%s\n' "${source_sst_bytes}" > "${RUN_ROOT}/raw/source_sst_bytes.txt"
{ printf '#!/usr/bin/env bash\n'; print_command "${COMPACT_CMD[@]}"; } \
  > "${RUN_ROOT}/raw/compact_cmd.sh"
{ printf '#!/usr/bin/env bash\n'; print_command "${REOPEN_CMD[@]}"; } \
  > "${RUN_ROOT}/raw/reopen_cmd.sh"
chmod a-w "${RUN_ROOT}/raw/"*cmd.sh
sync
drop_page_cache
cat /proc/diskstats > "${RUN_ROOT}/raw/diskstats.start"
start="$(date +%s)"
set +e
/usr/bin/time -v -o "${RUN_ROOT}/raw/compact_time.out" \
  "${COMPACT_CMD[@]}" > "${RUN_ROOT}/compact.out" 2>&1
rc=$?
set -e
end="$(date +%s)"
cat /proc/diskstats > "${RUN_ROOT}/raw/diskstats.end"
compact_elapsed=$((end - start))
printf '%s\n' "${rc}" > "${RUN_ROOT}/raw/compact_exit_code.txt"
printf '%s\n' "${compact_elapsed}" > "${RUN_ROOT}/raw/compact_elapsed_sec.txt"
[[ "${rc}" -eq 0 ]] || die "one-shot compaction failed: ${rc}"

compact_ops="$(awk '$1=="compact" && $2==":" {for(i=1;i<=NF;i++) if($i=="operations;") v=$(i-1)} END{print v}' "${RUN_ROOT}/compact.out")"
compact_count="$(awk '$1=="rocksdb.compaction.times.micros" {for(i=1;i<=NF;i++) if($i=="COUNT") v=$(i+2)} END{print v}' "${RUN_ROOT}/compact.out")"
compact_sec="$(awk '$1=="compact" && $2==":" {for(i=1;i<=NF;i++) if($i=="seconds") v=$(i-1)} END{print v}' "${RUN_ROOT}/compact.out")"
compact_read="$(awk '$1=="rocksdb.compact.read.bytes" && $2=="COUNT" {v=$4} END{print v+0}' "${RUN_ROOT}/compact.out")"
compact_write="$(awk '$1=="rocksdb.compact.write.bytes" && $2=="COUNT" {v=$4} END{print v+0}' "${RUN_ROOT}/compact.out")"
pending="$(awk '/Estimated pending compaction bytes:/ {v=$5} END{print v+0}' "${RUN_ROOT}/compact.out")"
[[ "${compact_ops}" == "1" && "${compact_count}" == "1" ]] || \
  die "one-shot validation failed: operations=${compact_ops}, compactions=${compact_count}"
[[ "${compact_read}" -gt 0 && "${compact_write}" -gt 0 && "${pending}" -eq 0 ]] || \
  die "final compaction did not settle"

find "${SOURCE_DB}" -maxdepth 1 -type f \
  \( -name CURRENT -o -name IDENTITY -o -name 'MANIFEST-*' -o -name 'OPTIONS-*' \) \
  -exec sha256sum {} + | sort > "${RUN_ROOT}/raw/source_metadata.after.sha256"
cmp "${RUN_ROOT}/raw/source_metadata.before.sha256" \
  "${RUN_ROOT}/raw/source_metadata.after.sha256" || die "source metadata changed"
[[ "$(find "${SOURCE_DB}" -maxdepth 1 -type f -name '*.sst' | wc -l)" == "${source_sst_count}" ]] || \
  die "source SST set changed"

drop_page_cache
set +e
"${REOPEN_CMD[@]}" > "${RUN_ROOT}/reopen.out" 2>&1
reopen_rc=$?
set -e
[[ "${reopen_rc}" -eq 0 ]] || die "reopen validation failed: ${reopen_rc}"
read_counts="$(sed -n 's/.*(\([0-9][0-9]*\) of \([0-9][0-9]*\) found).*/\1 \2/p' "${RUN_ROOT}/reopen.out" | tail -1)"
[[ "${read_counts}" == "${READS} ${READS}" ]] || die "reopen read mismatch: ${read_counts:-missing}"

peak_rss="$(awk -F: '/Maximum resident set size/ {gsub(/^[[:space:]]+/,"",$2); print $2}' "${RUN_ROOT}/raw/compact_time.out")"
final_bytes="$(du -sb "${DB_DIR}" | awk '{print $1}')"
total_elapsed=$((SOURCE_ELAPSED_SEC + compact_elapsed))
printf 'status\ttarget_gib\tsource_nocomp_elapsed_sec\tcompact_wall_sec\tcompact_benchmark_sec\tlastcomp_total_sec\tcompact_count\tcompact_read_bytes\tcompact_write_bytes\tpeak_rss_kb\tfinal_db_bytes\tread_found\tread_requested\tsource_db\tdb_dir\n' > "${RUN_ROOT}/summary.tsv"
printf 'ok\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "${TARGET_GIB}" "${SOURCE_ELAPSED_SEC}" "${compact_elapsed}" "${compact_sec}" \
  "${total_elapsed}" "${compact_count}" "${compact_read}" "${compact_write}" \
  "${peak_rss}" "${final_bytes}" "${READS}" "${READS}" "${SOURCE_DB}" "${DB_DIR}" \
  >> "${RUN_ROOT}/summary.tsv"
touch "${RUN_ROOT}/COMPLETED"
printf 'Last-comp complete: no-comp=%ss + compact=%ss = %ss; DB=%s\n' \
  "${SOURCE_ELAPSED_SEC}" "${compact_elapsed}" "${total_elapsed}" "${DB_DIR}"
