# Last-Comp Pre-Final-Compaction Load, 16 Write Buffers

## Question

How quickly can the 1 TiB last-comp method finish its ingestion and mandatory
memtable flush when automatic compaction is disabled and up to 16 write
buffers are allowed?

This run measures and preserves the state immediately before the final full
compaction. It is not an end-to-end last-comp result and must not replace the
Figure 4 Last compaction value unless the final compaction is separately run
and included.

## Configuration

- Binary: the exact `vcomp/db_bench` used by the existing Figure 4 Last
  compaction run; required SHA-256
  `c5964e54fb0afc4779fd17b82f7366bdd8d19be9bd2acae33dd26c77e55019cd`.
- Logical input: 1,000 GiB, 1,048,576,000 records, 24 B keys and 1,000 B
  values, `fillrandom`, seed 12345678, one writer, batch size one.
- Vector memtable, 64 MiB write buffer, `max_write_buffer_number=16`,
  `min_write_buffer_number_to_merge=1`.
- 48 background jobs, one subcompaction, no WAL, no compression, direct reads
  and direct flush/compaction I/O.
- Automatic compaction disabled. L0 file-count stalls and pending-compaction
  byte stalls are disabled so the load cannot deadlock on its intentionally
  accumulating L0.
- Completion boundary: `fillrandom,flush,stats,levelstats`. There is no
  `compact`, `compact0`, or `waitforcompaction` phase.
- Repetitions: one requested full-scale run after a 1 GiB pipeline pilot.

## Validation and failure criteria

The run is valid only if the frozen binary hash matches, the command exits
successfully, RocksDB records the requested buffer count and disabled automatic
compaction, all final SSTs remain in L0, compaction read bytes and compaction
execution count are zero, and no corruption, assertion, OOM, or non-release
build warning occurs. The runner does not reopen the DB because reopening with
different options could trigger the deferred compaction.

The preserved DB is intended for a separately timed final compaction or later
read-workload experiments. Its pre-final-compaction layout is deliberately not
equivalent to a normal leveled RocksDB database.

## Execution and outputs

Runner:

```bash
nohup bash experiments/scripts/load/run_lastcomp_flushonly_wb16_1tb.sh \
  > experiments/artifacts/log_loads/paper_lastcomp_flushonly_wb16_1tib_260902_run1.nohup.log \
  2>&1 &
```

Requested run ID and locations:

- Raw logs:
  `experiments/artifacts/log_loads/paper_lastcomp_flushonly_wb16_1tib_260902_run1/`
- Databases:
  `/work/vcomp/exp/paper_lastcomp_flushonly_wb16_1tib_260902_run1/`
- Machine-readable summary: `summary.tsv` inside the raw-log directory.

Do not promote a timing to `experiments/results/` or update Figure 4 until the
full run and validation complete. If the figure is meant to report end-to-end
Last compaction, also time the final full compaction from this preserved DB.

## 2026-09-02 result

The 1 GiB pilot and 1,000 GiB run both passed the runner's flush-only
validation. The full run completed `fillrandom,flush` in 973 seconds; the
reported `fillrandom` time was 972.990 seconds (1,077,683 ops/s). It left
16,237 SSTs in L0, zero files in L1--L6, and reported zero compaction-read
bytes and zero compaction executions. Peak RSS was 7,014,236 KiB and the
preserved database occupied 1,091,482,498,379 bytes.

Compared with the ingestion phase of the existing Figure 4 Last compaction
run, increasing `max_write_buffer_number` from 2 to 16 reduced ingestion time
from 1,295.026 to 972.990 seconds: 322.036 seconds (24.9%) less, or 1.33x the
throughput. The earlier run reported 16,042 memtable-limit stops totaling
329.733 seconds, whereas the new run reported zero write stalls. The observed
wall-time reduction is therefore explained almost entirely by removing
flush-backlog backpressure on the writer.

This 973-second result is not directly comparable with the old 4,924-second
end-to-end Last compaction value. The old value includes a 3,628.383-second
final full compaction, while the new run intentionally stops before that
phase. The database remains preserved at:

```text
/work/vcomp/exp/paper_lastcomp_flushonly_wb16_1tib_260902_run1/flushonly_1000gib
```
