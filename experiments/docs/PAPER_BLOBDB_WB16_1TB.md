# BlobDB with 16 Write Buffers at 1 TiB

## Question

How does the fixed Figure 4 RocksDB BlobDB GC-off load change when only
`max_write_buffer_number` is increased from 2 to 16?

This is a sensitivity run. It does not replace the fixed Figure 4 result
unless the author explicitly selects it after validation.

## Controlled configuration

- RocksDB 11.1.0 commit `f455ab7bd6a8c67f00d48075bb310f131d9fae5f`;
  release `db_bench` SHA-256
  `8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b`.
- 1,000 GiB logical input, 1,048,576,000 records, 24 B keys, 1,000 B values,
  `fillrandom`, seed 12345678, one writer, batch size one.
- Vector memtable, concurrent memtable writes enabled, 64 MiB write buffer,
  minimum merge count one, 48 background jobs, one subcompaction.
- WAL and compression disabled; direct reads and direct flush/compaction I/O.
- Integrated BlobDB enabled: minimum blob 128 B, 1 GiB blob files, blob
  compression none, GC disabled, readahead zero, starting level zero.
- Completion boundary:
  `fillrandom,flush,compact0,waitforcompaction,stats,levelstats`.
- Sole treatment change: `max_write_buffer_number=16` instead of 2.
- A 1 GiB pilot must pass option, settling, and reopen-read checks before the
  1,000 GiB run starts. The full database must return 10,000/10,000 sampled
  reads after reopening and is retained for later read workloads.

## Execution

```bash
setsid -f env \
  RUN_ID=paper_blobdb_wb16_1000gib_260903_run1 \
  bash experiments/scripts/artifact_baselines/run_clean_blobdb_wb16_1tb.sh \
  > experiments/artifacts/log_loads/paper_blobdb_wb16_1000gib_260903_run1.launch.log \
  2>&1 < /dev/null
```

Outputs:

- Logs: `experiments/artifacts/log_loads/paper_blobdb_wb16_1000gib_260903_run1/`
- DBs: `/work/vcomp/exp/paper_blobdb_wb16_1000gib_260903_run1/`

Reject the run on configuration drift, a non-release binary, nonzero exit,
failure to settle, corruption/assertion/OOM, nonzero GC relocation counters,
or missing sampled reads.

## 2026-09-03 result

The 1 GiB pilot and 1,000 GiB run completed and passed all checks. The full
load took 1,712 seconds, including a 1,664.772-second `fillrandom` phase, and
returned 10,000/10,000 sampled reads after reopening. GC relocation remained
zero. The retained DB is
`/work/vcomp/exp/paper_blobdb_wb16_1000gib_260903_run1/blobdb_1000gib`.

Against the fixed Figure 4 BlobDB run with two buffers, end-to-end time fell
from 1,843 to 1,712 seconds (7.11%, 1.077x speedup), and `fillrandom` time
fell from 1,807.799 to 1,664.772 seconds (7.91%). Memtable-limit stops fell
from 16,279 to zero, while total write-stall time fell from 806.093 to
610.233 seconds (24.30%). L0 delay events rose from 1,495 to 4,860, showing
that backpressure shifted from hard memtable stops to short L0 slowdowns.

The number of compactions fell from 6,205 to 5,638 (9.14%). Compaction reads
and writes fell by 5.94% and 6.07%, respectively; aggregate compaction time
fell only 1.64% because mean compaction duration rose from 0.965 to 1.044
seconds. Host-visible device writes fell from 1,529.923 to 1,495.221 GiB,
corresponding to device WAF 1.530 and 1.495.

Blob output itself was unchanged: both runs wrote 1,107,263,165,510 blob
bytes in 16,237 files and read no blob bytes during compaction. Final DB size
differed by only 0.0027%, so the speedup is not caused by less user data or a
different BlobDB layout.

For context, increasing the same buffer count on the clean non-BlobDB
baseline reduced time by 4.38% (3,472 to 3,320 seconds). BlobDB remained
1.884x faster than the matched two-buffer baseline and 1.939x faster than the
matched 16-buffer baseline.
