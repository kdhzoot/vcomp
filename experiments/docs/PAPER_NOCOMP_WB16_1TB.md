# No-Compaction with 16 Write Buffers at 1 TiB

**Status:** Completed earlier and selected for reuse. Do not rerun.

## Definition

No-compaction is exactly the ingestion prefix shared with Last-compaction:

```text
fillrandom -> flush -> stop
```

Automatic compaction remains disabled for the entire run. There is no
`compact`, `compact0`, or `waitforcompaction` benchmark. All completed SSTs
must remain in L0.

## Fixed configuration

- Exact existing Figure 4 No-comp/Last-comp binary:
  `/home/smrc/virtual_compaction/vcomp/db_bench` at commit
  `f9e281caa65943140385aa99fff1066da4117be8`, SHA-256
  `c5964e54fb0afc4779fd17b82f7366bdd8d19be9bd2acae33dd26c77e55019cd`.
- Virtual compaction is not enabled.
- 1,000 GiB logical input; 1,048,576,000 records; 24 B keys and 1,000 B
  values; `fillrandom`; seed 12345678; one writer; batch size one.
- Vector memtable, concurrent memtable writes enabled, 64 MiB write buffer,
  `max_write_buffer_number=16`, and minimum merge count one.
- 48 background jobs, one subcompaction, WAL disabled, compression disabled,
  Bloom 10, index compression disabled, and direct I/O enabled.
- Automatic compaction disabled. L0 file-count and pending-compaction-byte
  slow/stop thresholds are lifted so deliberately accumulated L0 files cannot
  deadlock ingestion.

## Validation and reuse

1. The retained run timed only `fillrandom,flush,stats,levelstats`; both wall
   and benchmark-reported `fillrandom` time are preserved.
2. It contains exactly 1,048,576,000 operations, zero compaction executions, zero
   compaction read/write bytes, 16,237 L0 SSTs, no files below L0, and the
   expected effective OPTIONS values.
3. Preserve the L0-only DB. A direct sampled-read run over its 16,237
   overlapping L0 files was not recorded. The state was instead structurally
   validated and used unchanged as the source of the Last-comp checkpoint,
   whose post-compaction reopen found 10,000/10,000 sampled keys.

Selected artifact:

- Logs:
  `experiments/artifacts/log_loads/paper_lastcomp_flushonly_wb16_1tib_260902_run1/flushonly_1000gib/`
- DB:
  `/work/vcomp/exp/paper_lastcomp_flushonly_wb16_1tib_260902_run1/flushonly_1000gib`

## Existing qualification

The completed flush-only artifact
`paper_lastcomp_flushonly_wb16_1tib_260902_run1/flushonly_1000gib` already
implements this definition and completed in 973 seconds with 16,237 L0 files,
zero compaction bytes, and zero write stalls. It is the selected No-comp
result and immutable source for the queued Last-comp checkpoint.

Reject the run for benchmark overlap, binary/configuration drift, any
unintended compaction, a non-L0 output file, wrong operation count, nonzero
exit, corruption/assertion/OOM, or missing sampled reads.
