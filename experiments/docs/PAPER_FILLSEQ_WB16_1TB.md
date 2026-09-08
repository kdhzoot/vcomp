# Fillseq with 16 Write Buffers at 1 TiB

## Question

How does the fixed Figure 4 Fillseq load change when only
`max_write_buffer_number` is increased from its effective default of 2 to 16?

## Controlled configuration

- Exact original Fillseq clean RocksDB binary: commit
  `cae42cd895f3bdeaaed1e58c0d27e2755b6ad308`, SHA-256
  `289f4783ed194761c98e740b9def72a68950c2d31bd172804f10fbd30e314259`.
- 1,000 GiB, 1,048,576,000 records, 24 B keys, 1,000 B values, one writer,
  batch size one, vector memtable, and concurrent memtable writes disabled.
- 64 MiB write buffer, minimum merge count one, 48 background jobs, one
  subcompaction, WAL and compression disabled, and direct I/O enabled.
- Completion boundary:
  `fillseq,flush,compact0,waitforcompaction,stats,levelstats`.
- Sole treatment change: `max_write_buffer_number=16` instead of 2.
- Run a 1 GiB pilot before the 1,000 GiB load. Reopen the settled full DB and
  require 10,000/10,000 sampled reads. Preserve the DB for later read tests.

## Queued execution

The detached runner waits for the already-running Figure 2 profiling suite
PID to exit, then waits for all storage benchmark processes to drain before
starting the pilot and full run.

```bash
setsid -f env \
  WAIT_FOR_PID=4117009 \
  RUN_ID=paper_clean_fillseq_wb16_1000gib_260903_run1 \
  bash experiments/scripts/artifact_baselines/run_clean_rocksdb_fillseq_wb16_1tb.sh \
  > experiments/artifacts/log_loads/paper_clean_fillseq_wb16_1000gib_260903_run1.launch.log \
  2>&1 < /dev/null
```

Outputs:

- Logs: `experiments/artifacts/log_loads/paper_clean_fillseq_wb16_1000gib_260903_run1/`
- DB: `/work/vcomp/exp/paper_clean_fillseq_wb16_1000gib_260903_run1/fillseq_1000gib`

Do not replace the fixed Figure 4 value until the run completes, passes all
checks, and is compared against the original two-buffer Fillseq result.

## 2026-09-03 result

The pilot and full run completed and passed all checks. The 1,000 GiB load
ran from 05:41:33 to 06:02:30 UTC without overlapping another storage
benchmark. It took 1,257 seconds end to end, including a 1,250.636-second
`fillseq` phase, and returned 10,000/10,000 sampled reads after reopening.

Against the original two-buffer Fillseq result, end-to-end time fell from
1,578 to 1,257 seconds (20.34%, 1.255x speedup), while the benchmark phase
fell from 1,572.213 to 1,250.636 seconds (20.45%). Memtable-limit stops fell
from 15,681 to zero and recorded write-stall time fell from 285.857 seconds
to zero. Both runs performed the same single 260,440,154-byte compaction read
and 258,891,776-byte compaction write. Device writes were also unchanged at
1,016.841 versus 1,016.855 GiB (device WAF 1.0168 versus 1.0169). The
improvement is therefore attributable to removing flush-backlog stalls, not
to less I/O or a different final layout.

The validated database is preserved at
`/work/vcomp/exp/paper_clean_fillseq_wb16_1000gib_260903_run1/fillseq_1000gib`.
