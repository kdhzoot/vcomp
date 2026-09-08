# Clean RocksDB background-job sensitivity at 100 GiB

## Question

Determine whether the clean RocksDB baseline in the loading evaluation is
over-provisioned by `max_background_jobs=48`, and locate the throughput knee
under smaller background-job budgets.

This is an exploratory sensitivity experiment. It must not be used to claim
that a 100 GiB run reproduces the pending-compaction behavior of the 1 TiB
load. In particular, the default hard pending-compaction threshold is 128 GiB.

## Controlled configuration

- Source/binary: clean RocksDB at commit
  `f455ab7bd6a8c67f00d48075bb310f131d9fae5f`, optimized build.
- Workload: `fillrandom`, 100 GiB logical input, 104,857,600 records.
- KV: 24-byte key and 1,000-byte value; compression and WAL disabled.
- Foreground: one writer, batch size one, seed 12345678.
- Memtable representation: vector; concurrent memtable writes disabled.
- `write_buffer_size=64 MiB`.
- `max_write_buffer_number=8`; `min_write_buffer_number_to_merge=1`.
- Background-job sweep: 8, 16, and 24.
- One subcompaction; direct reads and direct flush/compaction I/O.
- RocksDB's default L0 and pending-compaction thresholds remain unchanged.
- Each run uses a fresh DB and ends with
  `flush,compact0,waitforcompaction,stats,levelstats`.
- SMT remains off. No CPU or NUMA pinning is introduced because the prior
  baseline was unpinned.

Only `max_background_jobs` varies inside the sweep. Increasing
`max_write_buffer_number` from the historical value of two is a deliberate
change shared by every point, so these results are not directly interchangeable
with the existing 48-job/two-buffer result.

## Execution and validation

The reusable runner is
`experiments/scripts/artifact_baselines/run_clean_vector_bgjob_sweep.sh`.
It first executes a 1 GiB end-to-end pilot at eight jobs. The 100 GiB sweep is
started only if the pilot succeeds. Runs are serialized; a pre-existing
`db_bench` or `titandb_bench` causes the runner to wait when
`WAIT_FOR_IDLE=1` is selected.

Raw outputs and generated commands are stored below
`experiments/artifacts/log_loads/`; databases are stored below `/work/vcomp/exp`
and are preserved for possible read workloads.

## Metrics

- `fillrandom` time and throughput;
- end-to-end time through settled compaction;
- total and cause-specific write stalls;
- peak estimated pending compaction bytes;
- flush and compaction counts, time, bytes, and WAF;
- average/peak running compactions and flushes from the one-second report;
- CPU, RSS, and device utilization/bandwidth.

The primary comparison is the saturation curve across 8/16/24 jobs. A later
48-job point with eight write buffers is desirable to connect this sweep to the
old setting, and a 48-job/two-buffer control is required before attributing a
difference specifically to the buffer-count change.

After the clean baseline's 24-job point, run ADOC-on at 100 GiB with
initial `max_background_jobs=24`, `core_num=48`, and the same
`max_write_buffer_number=8`. Keep ADOC's FEA/TEA enabled, initial 64 MiB write
buffer, 512 MiB maximum memtable size, and all common workload/I/O settings
unchanged. TEA is allowed to raise the live background-job count up to the
48-core limit, so 24 is an initial value rather than a fixed resource cap. The
write-buffer count remains common, while ADOC dynamically tunes memtable size
and background jobs. A mistakenly started core-24 run was stopped after 41
seconds and is excluded rather than overwritten.

## Repetitions and limitations

The first pass is one run per point. Any point used in a paper figure must be
repeated after selecting the relevant range. Because this machine has 48
online physical cores and a multi-NVMe RAID0, results describe a high-end
server with an imposed software resource cap, not a physically smaller server.

## First-pass results (2026-09-02)

All requested clean-baseline points completed and settled with zero final
pending compaction bytes. The ADOC-on point completed, reopened successfully,
and found all 10,000 validation reads. Times are single-run observations.

| System | BG jobs | ADOC core | Fill (s) | Settled (s) | Stall (s) | Peak pending (GiB) | Device WAF |
|---|---:|---:|---:|---:|---:|---:|---:|
| Clean baseline | 8 | - | 333.122 | 360 | 224.428 | 64.942 | 7.509 |
| Clean baseline | 16 | - | 308.465 | 324 | 199.901 | 60.659 | 7.382 |
| Clean baseline | 24 | - | 294.074 | 304 | 187.756 | 46.528 | 7.503 |
| ADOC-on | 24 -> 48 | 48 | 311.257 | 367 | 203.247 | 107.472 | 5.336 |

For context, a historical 100 GiB baseline with 48 background jobs took
294.850 seconds for fill and 306 seconds settled. It used a different `vcomp`
binary baseline path and only two write buffers, so it is not a controlled
point in this sweep. Its near-equality to the clean 24-job/eight-buffer result
is suggestive of saturation around 24 jobs, but a clean 48-job/eight-buffer run
is needed to establish that claim.

Across the controlled clean sweep, going from 8 to 16 jobs reduced fill time by
7.40%, and going from 16 to 24 reduced it by another 4.67%. Pending-byte
slowdowns occurred only at eight jobs (231); peak pending debt declined from
64.942 to 46.528 GiB as the job cap increased. L0 slowdown counts were 1,115,
878, and 487 for 8, 16, and 24 jobs. No clean sweep point experienced a hard
pending-byte or memtable stop.

Starting from 24 jobs with a common eight-buffer count, ADOC's TEA reached 48
jobs after 14 seconds and used 48 for 251 of 311 reported fill seconds. Even
with this additional thread budget, ADOC reduced device WAF by 28.9% (7.503 to
5.336) but increased fill time by 5.84% and settled time by 20.72% relative to
the fixed-24-job clean baseline.
ADOC used 312 compactions averaging 2.708 seconds, versus the clean baseline's
2,554 compactions averaging 0.501 seconds. The ratio of summed compaction time
to settled wall time was 2.302 for ADOC and 4.212 for clean RocksDB. Thus ADOC
performed substantially less I/O but exposed coarser, less-parallel compaction
work, accumulated 2.31x the peak pending debt, and required 55.7 seconds after
fill versus 9.9 seconds for the clean baseline to settle.

Raw clean-sweep results are under
`experiments/artifacts/log_loads/paper_clean_vector_bgjob_100gib_260902_153749/`.
The ADOC result is under
`experiments/artifacts/log_loads/paper_adoc_100gib_bg24_core48_buf8_260902_run1/`.
The excluded core-24 ADOC attempt was terminated after 41 seconds and remains
under `paper_adoc_100gib_bg24_buf8_260902_run1/` for auditability.
