# Last-Compaction with 16 Write Buffers at 1 TiB

**Status:** Completed and validated on 2026-09-03.

## Definition

Last-compaction is No-compaction followed by exactly one synchronous,
whole-key-range compaction:

```text
fillrandom -> flush -> one-shot compact -> stop
```

It is not ordinary RocksDB loading with delayed background compactions. The
ingestion prefix must use the identical automatic-compaction-disabled options
as the paired No-comp case.

In this binary, the `db_bench` `compact` benchmark calls
`CompactRange(cro, nullptr, nullptr)` once, sets
`bottommost_level_compaction=kForceOptimized`, and uses the configured
subcompaction cap. The call is synchronous. This is the required one-shot
operation.

## Fixed configuration

Use every binary, workload, memtable, write-buffer, I/O, compaction-disable,
and lifted-stall option from `PAPER_NOCOMP_WB16_1TB.md`. In particular, use
the same frozen `vcomp/db_bench`, vector memtable, 64 MiB write buffers,
`max_write_buffer_number=16`, 48 background jobs, and
`subcompactions=1`.

The only difference from No-comp is appending the single `compact` benchmark
after the mandatory final flush. Automatic compaction remains disabled; a
manual CompactRange is still allowed.

## Execution and time accounting

1. Keep the selected 973-second No-comp DB immutable.
2. Create a metadata-private hard-link checkpoint: immutable SSTs share
   inodes, while CURRENT, MANIFEST, OPTIONS, IDENTITY, and WAL metadata are
   copied privately. Verify source metadata hashes before and after.
3. Invoke `compact` exactly once on the checkpoint and record its
   wall/benchmark time separately.
4. Define the reported Last-comp loading time as:

```text
Last-comp total = 973-second No-comp load + one-shot compaction wall time
```

Exclude runner setup, page-cache dropping, and reopen validation. Also report
the process-level end-to-end wall time as an accounting check; it must agree
with the phase sum apart from sub-second boundary overhead.

Planned identifiers:

- Run ID: `paper_lastcomp_from_nocomp_wb16_1000gib_260903_run1`
- Last-comp logs:
  `experiments/artifacts/log_loads/paper_lastcomp_from_nocomp_wb16_1000gib_260903_run1/lastcomp_1000gib/`
- Last-comp DB:
  `/work/vcomp/exp/paper_lastcomp_from_nocomp_wb16_1000gib_260903_run1/lastcomp_1000gib`

## Validation and comparison

Require exactly one reported `compact` operation and one compaction timing
record, nonzero compaction reads/writes, zero final pending-compaction bytes,
no unintended compaction before the phase boundary, 10,000/10,000 sampled
reads after reopening, and a structurally settled final tree.

Report No-comp and Last-comp as separate bars. Also report the decomposition:

- selected No-comp wall time;
- one-shot compaction time;
- Last-comp total;
- compaction input/output bytes, device writes/WAF, peak RSS, and final size.

The one-shot is run on an exact checkpoint of the selected No-comp SST state,
so the 973-second prefix and compaction operate on the same physical data
layout without rerunning or mutating the source.

Before the 1 TiB one-shot, checkpoint and compact the retained 1 GiB No-comp
pilot to verify source preservation and the one-shot count. Reject on benchmark overlap,
binary/configuration drift, wrong operation/compaction count, compaction work
before the explicit one-shot phase, nonzero final pending bytes, nonzero exit,
corruption/assertion/OOM, or missing reads.

The existing two-buffer Figure 4 Last-comp result (4,924 seconds = 1,295.026
seconds ingestion + 3,628.383 seconds one-shot compaction) is the historical
control. The new result must not be promoted until the paired run passes and
the author chooses the figure-wide buffer policy.

## Result

The corrected checkpoint pilot passed, followed by the 1 TiB one-shot run.
The selected No-comp prefix was 973 seconds and the one-shot compaction took
4,053 seconds wall time (4,051.224 seconds reported by `db_bench`), giving a
Last-comp total of 5,026 seconds, or 83.77 minutes.

The manual `compact` reported exactly one operation and one compaction timing
record. It read 1,095,599,943,352 bytes, wrote 689,567,027,200 bytes, and
ended with zero pending compaction bytes. Peak RSS was 40,079,100 KiB, final
DB size was 689,561,222,147 bytes, and the reopen found 10,000/10,000 sampled
keys. Source metadata hashes and all 16,237 source SSTs were unchanged.

Compared with the two-buffer control, the No-comp prefix improved by about
322 seconds, but one-shot compaction increased by about 425 seconds. Total
Last-comp time therefore increased from 4,924 to 5,026 seconds (2.07%). The
compaction input/output byte counts were effectively unchanged, so this is a
compaction-runtime difference rather than a different amount of logical work.
