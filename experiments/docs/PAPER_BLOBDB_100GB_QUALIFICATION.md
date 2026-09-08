# Paper BlobDB 100 GiB Qualification

**Status:** Completed one controlled smoke pair on 2026-09-02 UTC. Both
databases reopened successfully and returned 10,000/10,000 sampled keys.

## Question

Measure whether RocksDB Integrated BlobDB accelerates the Figure 4
`fillrandom` workload when every non-BlobDB option is held equal to the fixed
clean baseline. This qualification is not promoted to Figure 4 by itself.

## Controlled pair

Both members use the clean f455 RocksDB release binary, 100 GiB logical input
(104,857,600 records, 24-byte keys, 1,000-byte values), one foreground writer,
64 MiB write buffers, the default two-buffer limit, vector memtable, 48
background jobs, one subcompaction, WAL and compression disabled, direct reads,
and direct flush/compaction I/O. Each run must settle and find 10,000/10,000
sampled keys after reopen.

The BlobDB member changes only these column-family options:

```text
enable_blob_files=true
min_blob_size=128
blob_file_size=1073741824
blob_compression_type=none
enable_blob_garbage_collection=false
blob_file_starting_level=0
blob_compaction_readahead_size=0
prepopulate_blob_cache=0 (default)
```

GC is disabled because the experiment asks for maximum initial loading speed.
Report retained blob garbage/space amplification as a limitation; do not claim
that this is a GC-on steady-state result.

## Order and evidence

Run clean baseline first and BlobDB second. Drop the page cache before every
load and reopen. Store DBs under `/work/vcomp/exp/` and raw evidence under
`experiments/artifacts/log_loads/`. Compare elapsed and fillrandom time, stall
time/count, device writes, RocksDB compaction read/write bytes, final DB/blob
bytes, blob file count, GC relocation counters, and reopen validation.

## Smoke result

Both runs used clean RocksDB commit
`f455ab7bd6a8c67f00d48075bb310f131d9fae5f` and db_bench SHA-256
`8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b`.
These are single-run qualification results, not final paper points.

| Metric | Clean baseline | Integrated BlobDB, GC off | Change |
|---|---:|---:|---:|
| Settled elapsed time | 336 s | 185 s | 1.816x faster |
| `fillrandom` time | 322.336 s | 166.466 s | 1.936x faster |
| Throughput | 325,305 ops/s | 629,905 ops/s | 1.936x higher |
| Write-stall time | 215.601 s | 67.620 s | 68.6% lower |
| Compaction read bytes | 697.071 GB | 29.150 GB | 95.8% lower |
| Compaction write bytes | 667.175 GB | 27.136 GB | 95.9% lower |
| Host-visible `/dev/md0` writes | 770.392 GiB | 135.283 GiB | 82.4% lower |
| Device WAF | 7.704 | 1.353 | 82.4% lower |
| Final DB bytes | 82,369,172,534 | 113,498,089,303 | 37.8% higher |
| Peak RSS | 1,243,220 KiB | 1,008,004 KiB | 18.9% lower |
| Reopen validation | 10,000/10,000 | 10,000/10,000 | both passed |

Device WAF uses the paper definition: host-visible `md0` bytes written divided
by `104857600 * (24 + 1000)` logical bytes. The BlobDB run created 1,624 blob
files containing 110,695,452,752 bytes. RocksDB reported 34.9 GB of blob
garbage and 1.5x blob space amplification at completion; GC relocation counters
were zero, as required by the configuration.

The qualification supports the expected mechanism: separating 1,000-byte
values reduced RocksDB compaction read and write traffic by about 96%, which
substantially reduced memtable/L0 stalls and load time. The speed result trades
away garbage reclamation. Repeat the selected configuration before using it as
a paper result, and evaluate GC-on or a post-load GC phase if the comparison is
intended to include space recovery.

## Preserved evidence

- Machine-readable result:
  `experiments/results/paper_blobdb_100gb_qualification.tsv`
- Raw logs:
  `experiments/artifacts/log_loads/paper_blobdb_100gib_fig4fair_260902_run1/`
- Databases:
  `/work/vcomp/exp/paper_blobdb_100gib_fig4fair_260902_run1/baseline` and
  `/work/vcomp/exp/paper_blobdb_100gib_fig4fair_260902_run1/blobdb`

## GC-on follow-up

**Status:** Interrupted at user request on 2026-09-02 UTC after about 820
seconds. The last complete statistics interval reported 85M/104.9M ingested
keys and 700.383 seconds of cumulative stall (88.9%). The partial 97 GiB DB
and raw evidence are preserved and are not treated as a completed result.

Hold the completed GC-off configuration fixed and change only
`enable_blob_garbage_collection=true`. Use the same clean binary, seed,
workload, vector memtable, direct-I/O policy, and background-job limits. Store
the follow-up separately under the run ID
`paper_blobdb_100gib_fig4fair_gcon_260902_run1`. Compare settled and
`fillrandom` time against both members of the completed pair, and additionally
report GC files, keys, and bytes relocated, final blob garbage/space
amplification, compaction traffic, stalls, and host-visible device WAF.

## 1 TiB GC-off follow-up

**Status:** Completed and promoted to Figure 4 on 2026-09-02 UTC. The load,
terminal compaction wait, and runner completed in 1,843 seconds; `fillrandom`
reported 1,807.799 seconds and 580,029 ops/s. Reopen validation found
10,000/10,000 sampled keys.

Scale the completed 100 GiB GC-off configuration to 1,000 GiB logical KV
input (1,048,576,000 records) without changing the clean binary, seed, KV
sizes, vector memtable, 64 MiB/default-two-buffer policy, 48 background jobs,
one subcompaction, compression/WAL policy, direct-I/O policy, or BlobDB
options. Keep garbage collection disabled. Preserve the DB and validate
10,000/10,000 sampled keys after reopen.

Use run ID `paper_blobdb_1tib_fig4fair_gcoff_260902_run1`, with raw evidence
under `experiments/artifacts/log_loads/` and the DB under `/work/vcomp/exp/`.

The final DB contains 16,237 blob files and 1,107,263,165,510 blob bytes.
RocksDB reported 338.2 GB of blob garbage and 1.5x blob space amplification;
GC key/byte relocation counters were both zero. This point therefore measures
fast initial loading with GC disabled and must not be described as a
GC-inclusive steady-state result.
