# Clean Baseline with 16 Write Buffers at 1 TiB

## Question

How does the Figure 4 clean RocksDB baseline change when only
`max_write_buffer_number` is increased from 2 to 16?

This is a sensitivity run and does not replace the fixed Figure 4 Baseline
until the author explicitly selects it.

## Controlled configuration

- Exact Figure 4 Baseline release binary: RocksDB 11.1.0 commit
  `f455ab7bd6a8c67f00d48075bb310f131d9fae5f`, binary SHA-256
  `8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b`.
- 1,000 GiB logical input; 1,048,576,000 records; 24 B keys and 1,000 B
  values; `fillrandom`; seed 12345678.
- One writer, batch size one, vector memtable, concurrent memtable-write option
  enabled, 64 MiB write buffer, minimum merge count one.
- 48 background jobs, one subcompaction, WAL disabled, compression disabled,
  direct reads and direct flush/compaction I/O.
- Completion boundary: `fillrandom,flush,compact0,waitforcompaction,stats,levelstats`.
- Sole treatment change: `max_write_buffer_number=16` instead of 2.
- One 1 GiB pilot followed by one 1,000 GiB run. The full DB is reopened only
  after settling and must return 10,000/10,000 sampled random reads.

## Execution

```bash
setsid -f env \
  RUN_ID=paper_clean_vector_wb16_1000gib_260903_run1 \
  bash experiments/scripts/artifact_baselines/run_clean_vector_baseline_wb16_1tb.sh \
  > experiments/artifacts/log_loads/paper_clean_vector_wb16_1000gib_260903_run1.launch.log \
  2>&1 < /dev/null
```

Outputs:

- Logs: `experiments/artifacts/log_loads/paper_clean_vector_wb16_1000gib_260903_run1/`
- DBs: `/work/vcomp/exp/paper_clean_vector_wb16_1000gib_260903_run1/`
- Final result: `validated_summary.tsv` after load, settling, and reopen checks.

Reject the run on binary/configuration drift, non-release warnings, nonzero
exit, failure to settle, final pending compaction, corruption/OOM/assertion,
or missing sampled reads. Keep the existing Figure 4 value unchanged until
all checks pass and the result is reviewed.

## 2026-09-03 result

The pilot and full run completed successfully. The full load took 3,320
seconds, including a 3,301.192-second `fillrandom` phase, reached zero pending
compaction bytes, and returned 10,000/10,000 sampled reads after reopening.
Peak RSS was 6,191,184 KiB and final DB size was 824,455,159,575 bytes.

Against the fixed Figure 4 buffer-count-two Baseline, end-to-end time fell from
3,472 to 3,320 seconds: 152 seconds (4.38%) less, corresponding to a 1.046x
speedup. `fillrandom` time fell by 149.201 seconds (4.32%). Memtable-limit
stops fell from 27,097 to zero and total write-stall time fell from 2,379.466
to 2,205.299 seconds (7.32%). Backpressure shifted to slowdown events: L0
slowdowns increased from 534 to 2,976 and pending-compaction-byte slowdowns
from 4,419 to 10,328.

RocksDB compaction WAF changed from 12.6 to 12.5. Compaction reads and writes
fell by 0.85% and 0.86%, respectively. Host-visible `/dev/md0` writes instead
rose slightly from 13,415.19 to 13,470.77 GiB, giving device WAF 13.415 and
13.471; this 0.41% difference is small enough to treat as run variation rather
than an I/O-amplification improvement. The final DB sizes differed by only
0.0037%.

Validated summary:
`experiments/artifacts/log_loads/paper_clean_vector_wb16_1000gib_260903_run1/validated_summary.tsv`.
