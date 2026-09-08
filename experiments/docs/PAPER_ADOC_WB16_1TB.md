# ADOC with 16 Write Buffers at 1 TiB

**Status:** Completed and validated on 2026-09-03.

## Result

- Wall time: 4,122 seconds (68.70 minutes).
- `fillrandom`: 4,069.977 seconds (67.83 minutes).
- Final pending compaction bytes: zero.
- Reopen validation: 10,000/10,000 keys found.
- Preserved DB size: 778 GiB as reported by `du`.
- Completion time: 2026-09-03 10:00:44 UTC.

Compared with the fixed two-buffer ADOC result (3,840 seconds wall time),
16 buffers were 282 seconds, or 7.34%, slower. This is a sensitivity result;
it does not replace the fixed Figure 4 value without an explicit figure update.

## Question and control

Measure the fixed Figure 4 ADOC-on workload with the sole treatment change
`max_write_buffer_number=2 -> 16`. Compare against the validated 3,840-second
two-buffer control in `paper_adoc_core48_1000gib_260902_run1/adoc_on`.

## Fixed configuration

- ADOC artifact commit `5ed60f50d6cd8259b94e7f842ff06c6ab4df40a1`.
- Release `db_bench` SHA-256
  `68eb52fb2c6db555f47fa877c015015a98ee7ecc879fe3574004c9ec47f47a05`.
- 1,000 GiB, 1,048,576,000 records, 24 B keys, 1,000 B values,
  `fillrandom`, one writer, batch size one, VectorRep, concurrent memtable
  writes disabled, seed 12345678.
- Initial/max ADOC memtable size 64/512 MiB, FEA and TEA enabled, DOTA
  disabled, one-second tuning/report interval.
- 48 initial/max background jobs, `core_num=48`, one subcompaction.
- Soft/hard pending-compaction limits 64/128 GiB.
- WAL and compression disabled; direct reads and direct flush/compaction I/O.
- Completion boundary:
  `fillrandom,flush,compact0,waitforcompaction,stats,levelstats`.
- Sole change: `max_write_buffer_number=16`; minimum merge count remains one.

## Execution and validation

Run ADOC-on only. First run a complete 1 GiB pilot, then the 1,000 GiB load
on an otherwise idle machine. Require FEA/TEA activation, actual buffer count
16 in the RocksDB log, zero final pending compaction bytes, exact release
commit/hash, no fatal/corruption/OOM pattern, and 10,000/10,000 sampled reads
after reopening. Preserve the full DB for later read workloads.

```bash
setsid -f env \
  RUN_ID=paper_adoc_wb16_1000gib_260903_run1 \
  bash experiments/scripts/artifact_baselines/run_adoc_wb16_1tb.sh \
  > experiments/artifacts/log_loads/paper_adoc_wb16_1000gib_260903_run1.launch.log \
  2>&1 < /dev/null
```

Outputs:

- Logs: `experiments/artifacts/log_loads/paper_adoc_wb16_1000gib_260903_run1/`
- Launch log:
  `experiments/artifacts/log_loads/paper_adoc_wb16_1000gib_260903_run1.launch.log`
- DB: `/work/vcomp/exp/paper_adoc_wb16_1000gib_260903_run1/adoc_1000gib/adoc_on`

Do not replace the fixed Figure 4 ADOC value until the full run passes and
the complete 16-buffer sensitivity figure is reviewed.
