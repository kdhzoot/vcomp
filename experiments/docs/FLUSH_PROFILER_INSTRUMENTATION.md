# Flush Profiler Instrumentation Rationale

## Purpose and accounting boundary

The flush profiler explains the background work that turns immutable
memtables into an L0 SST. It emits one record for each non-empty
`FlushJob::Run()`:

```text
sort_us + sst_build_us + write_us + compress_us + other_us
    = total_tracked_us
```

All fields are elapsed wall-clock time accumulated per flush job. They are
non-overlapping by construction. Summing records from concurrent background
jobs produces cumulative job time, not end-to-end loading time.

The instrumentation is in `vcomp-prof`; it is not part of the production
`vcomp` loading path.

Current source anchors (line numbers are for the present profiling tree):

| Measurement | Source anchor |
| --- | --- |
| Whole-job start/profile scope | `db/flush_job.cc:279-294` |
| Whole-job end and record emission | `db/flush_job.cc:439-446` |
| Record formatting and `Other` | `db/flush_job.cc:145-183` |
| Actual VectorRep sorts | `memtable/vectorrep.cc:200-220` |
| Exclusive `BuildTable` wrapper | `db/flush_job.cc:1064-1078` |
| Buffered append calls | `file/writable_file_writer.cc:623-630` |
| Direct positioned-append calls | `file/writable_file_writer.cc:846-854` |
| Sync/Fsync calls | `file/writable_file_writer.cc:528-536` |
| Compressor call | `table/block_based/block_based_table_builder.cc:1907-1912` |
| Exclusive-counter implementation | `monitoring/vcomp_compaction_profiler.h:110-159` |

## Flush call path

For the Figure 2 configuration (`memtablerep=vector`), the relevant path is:

```text
FlushJob::Run
  -> FlushJob::WriteLevel0Table
       -> MemTable::NewIterator
            -> MemTableIterator
                 -> VectorRep::GetIterator       (does not sort yet)
       -> NewMergingIterator
       -> BuildTable
            -> MergingIterator::SeekToFirst
                 -> MemTableIterator::SeekToFirst
                      -> VectorRep::Iterator::SeekToFirst
                           -> VectorRep::Iterator::DoSort
                                -> std::sort
            -> NewWritableFile + WritableFileWriter
            -> NewTableBuilder
            -> CompactionIterator scan
                 -> OutputValidator::Add
                 -> TableBuilder::Add
            -> TableBuilder::Finish
            -> WritableFileWriter::Sync
            -> WritableFileWriter::Close
            -> optional output-file validation
  -> TryInstallMemtableFlushResults             (manifest/version install)
  -> emit VCOMP_PERF_FLUSH_BREAKDOWN
```

The indirection between `BuildTable::SeekToFirst` and `VectorRep::DoSort` is
important: `VectorRep::GetIterator` explicitly defers sorting until the first
seek. Timing iterator construction would therefore miss the actual sort.

## Instrumentation points and reasons

### 1. Whole-job scope: `FlushJob::Run`

Locations:

- `vcomp-prof/db/flush_job.cc`, immediately after the empty-memtable return;
- the endpoint is after SST creation, status handling, manifest installation,
  flush I/O statistics, and construction of the normal `flush_finished` event.

Why the start is here:

- an empty `FlushJob` performs no flush work and must not create a zero-work
  record;
- the selected immutable memtables are already known, so their entry and byte
  counts can be attached to the same job;
- a thread-local profiling scope can cover every nested sort, compression, and
  file-write call made by that background flush thread.

Why the endpoint is near the end of `Run`:

- stopping at the end of `BuildTable` would omit status checks, memtable
  rollback/install, the manifest/version edit, and flush bookkeeping;
- these costs are part of the background flush job but are not Sort, Write,
  Compress, or table construction, so they belong in `Other`;
- the profiler's own `VCOMP_PERF_FLUSH_BREAKDOWN` log write is issued after the
  end timestamp and does not charge itself to the job.

Operations inside this scope but outside `BuildTable` include MemPurge
selection, `WriteLevel0Table` setup, DB-mutex release/reacquisition, error and
shutdown checks, `TryInstallMemtableFlushResults`, manifest/version
installation, statistics, and event construction.

### 2. Sort: the two actual `std::sort` calls in `VectorRep::Iterator::DoSort`

Location: `vcomp-prof/memtable/vectorrep.cc`.

`VectorRep` stores inserted entries in an unsorted vector. Its iterator is
lazy: `GetIterator` creates an iterator without sorting, while
`SeekToFirst`, `Seek`, `SeekToLast`, or `Valid` calls `DoSort`. During flush,
`BuildTable` first seeks the merging iterator; the merging iterator seeks each
memtable child, eventually reaching `DoSort`.

The timer surrounds `std::sort` rather than `SeekToFirst` because the latter
also performs iterator positioning, child-heap initialization, validation,
and other work. There are two sort sites:

- the immutable-memtable path sorts the shared bucket once while holding the
  `VectorRep` write lock and sets `vrep_->sorted_`;
- the detached/copy iterator path sorts its private bucket.

Putting the timer at both actual sort calls measures only sorting and preserves
the existing `sorted_` guard, so an already-sorted memtable contributes zero
additional sort time.

### 3. SST Build: exclusive wrapper around `BuildTable`

Location: `vcomp-prof/db/flush_job.cc`, around the single `BuildTable` call in
`WriteLevel0Table`.

This is the narrowest existing function that owns the complete creation of one
flush SST. Within the call, RocksDB:

1. seeks the input iterator, which triggers the lazy VectorRep sort;
2. aggregates range tombstones and creates the output file;
3. constructs `WritableFileWriter` and the configured `TableBuilder`;
4. uses `CompactionIterator` to apply snapshot, deletion, merge, filter, and
   BlobDB rules while scanning the sorted input;
5. validates each output record and calls `TableBuilder::Add`;
6. builds data blocks, filters, indexes, properties, checksums, meta blocks,
   the metaindex, and footer;
7. finishes the builder, syncs and closes the SST, records its metadata and
   checksum, and optionally reopens it for paranoid validation.

The profiler snapshots all nested counters before entering `BuildTable`, times
the complete call, and subtracts counter deltas before adding the result to
`sst_build_us`. For this flush path, the effective definition is:

```text
SST Build = elapsed(BuildTable) - Sort - Write - Compress
```

This placement prevents Sort, Write, and Compress from being double-counted.
It also means `SST Build` is a residual operational category, not a pure CPU
counter. File creation, iterator processing, block/index/filter construction,
rate-limiter waits, `Close`/`Truncate`, and any uninstrumented I/O within
`BuildTable` remain in this category.

Timing `TableBuilder::Add` for every record was deliberately avoided: a 1 TB
91 B run executes roughly 11.8 billion additions, so two clock calls per entry
would materially perturb the workload. It would also miss final data blocks,
indexes, filters, properties, and the footer produced by `Finish`. One outer
exclusive interval captures the complete operation with job-scale timer
overhead.

### 4. Write: underlying append and sync operations

Location: `vcomp-prof/file/writable_file_writer.cc`.

The timer is placed directly around the filesystem-facing calls:

- buffered I/O: `FSWritableFile::Append` in `WriteBuffered`;
- direct I/O: `FSWritableFile::PositionedAppend` in `WriteDirect`;
- durability: `FSWritableFile::Sync` or `Fsync` in `SyncInternal`.

The position is below `WritableFileWriter::Append` because that higher-level
method also performs buffer management, checksum preparation, and block
assembly. Timing it as Write would incorrectly move CPU-side SST construction
into the I/O category.

The timer begins after rate-limiter token acquisition. Consequently, Write is
the elapsed filesystem call time, while rate-limiter waiting remains in the
exclusive SST Build residual. `Close`, direct-I/O `Truncate`, checksum
finalization, file creation, and the specialized
`WriteBufferedWithChecksum`/`WriteDirectWithChecksum` paths are not directly
charged to Write. The planned experiment must retain the current checksum
handoff configuration; changing it requires a new coverage check.

### 5. Compress: compressor invocation

Location: `vcomp-prof/table/block_based/block_based_table_builder.cc`, inside
`BlockBasedTableBuilder::CompressAndVerifyBlock`.

The timer surrounds only `Compressor::CompressBlock`. Buffer sizing, choosing
whether a block is compressible, optional decompression verification, block
trailer/checksum work, and emitting the block remain SST Build work. This
avoids counting table-builder control flow as compression. For Figure 2,
`compression_type=none`, so `compress_us` must be zero and acts as a validation
check.

### 6. Other and record emission

Location: `FlushJob::LogFlushBreakdown` in
`vcomp-prof/db/flush_job.cc`.

The four explicit counters are converted from nanoseconds to microseconds and
then:

```text
Other = total FlushJob::Run elapsed
        - Sort - SST Build - Write - Compress
```

The record also carries `job`, column family, flush reason, memtable count,
input entries/bytes, output files/bytes, status, MemPurge status, and
compression type. Failed jobs remain visible as `status=error`; analysis must
reject them rather than silently summing partial work.

## Why thread-local counters are used

Flush and compaction both reach common low-level table and file code. Passing a
profiler object through every RocksDB interface would require broad invasive
API changes. `VCompCompactionProfileScope` instead installs counters only on
the current background worker thread and restores the previous pointer on
scope exit. The low-level hooks are inert when no profile scope is active, and
concurrent flush jobs write to separate per-job atomic counters.

The exclusive helper records nested counter values before and after a parent
operation. Its parent counter receives only elapsed time not already assigned
to a nested category. This is the mechanism that makes the component-sum
invariant meaningful.

## Interpretation limits

- The breakdown covers background `FlushJob` work, not foreground memtable
  insertion. It cannot by itself claim to decompose total loading latency.
- Phase times are wall-clock times summed over jobs. Because jobs overlap,
  cumulative phase time can exceed end-to-end loading time.
- `SST Build` is explicitly the residual described above; do not call it pure
  sorting-free CPU time.
- The main experiment uses VectorRep. With another memtable representation,
  `sort_us=0` does not imply that ordering is free; it only means the VectorRep
  sort hook was not reached.
- MemPurge must stay disabled. A successful MemPurge does not create an SST and
  is not comparable to the planned normal flush records.
- `input_bytes` is the selected memtables' `GetDataSize()` sum. It is useful
  for per-job auditing but is not the application logical-byte denominator
  used for loading WAF.
- Write time is not write amplification. WAF is computed independently from
  bytes written, while this profiler reports time.

## Validation requirements

For every successful flush job:

- all fields are present and non-negative;
- the component sum equals `total_tracked_us` exactly after microsecond
  conversion;
- the output SST count and bytes are plausible for the configured write buffer;
- `compress_us=0` for the no-compression Figure 2 runs;
- at least one VectorRep flush has non-zero `sort_us`, `sst_build_us`, and
  `write_us`.

Use `experiments/analysis/summarize_flush_breakdown.py` to enforce the status,
non-negativity, and component-sum checks.
