# Paper Figure 4 Fixed Configuration

**Current validated campaign:** `paper_ch23_common_260907_f2_completion1`. Numerical entries and source-DB mappings below from earlier runs are historical. Use [PAPER_CHAPTER23_COMMON_RESULTS.md](PAPER_CHAPTER23_COMMON_RESULTS.md) and the current promoted TSVs for Figures 2(b), 4 and 5. All 24 included Figure 5 cells were rerun with the common clean release readrandom driver; historical YCSB values are not mixed. 

**Historical campaign, superseded on 2026-09-07:** `paper_ch23_common_260905_approved_run3`. Numerical entries and source-DB mappings below from earlier runs are historical. Use [PAPER_CHAPTER23_COMMON_RESULTS.md](PAPER_CHAPTER23_COMMON_RESULTS.md) and the current promoted TSVs for Figures 2(b), 4 and 5. All 20 included Figure 5 cells were rerun with the common clean release readrandom driver; historical YCSB values are not mixed. F2Load is deferred and omitted from the current common comparison.

**Chapter 2/3 alignment, 2026-09-05:** The author selected this figure's clean
release Baseline and 16-buffer configuration as the reference for alignment
with Figure 2. The plan and method-specific build exceptions are in
[PAPER_CHAPTER23_COMMON_BASELINE.md](PAPER_CHAPTER23_COMMON_BASELINE.md).
The plot was changed to vertical bars; its measured values remain unchanged.

**Status:** Reopened and fixed on 2026-09-03 UTC. The author explicitly chose
the complete 16-write-buffer result set and added the Flush-only result. Do not
replace a plotted value with another diagnostic or tuning-sweep result unless
the figure configuration is explicitly reopened again.

## Fixed scope

Figure 4 uses the single-run measurements in
`experiments/results/paper_figure4_loading_time_1tb_single.tsv`. The plotted
bars are Baseline, ADOC, BlobDB with GC disabled, Flush only, Last compaction,
Fillseq, Fillseq plus 10% overwrite, and F2Load.

All measured entries load 1,000 GiB of logical KV data: 1,048,576,000 records,
24-byte keys, and 1,000-byte values. They use one foreground writer, a vector
memtable, WAL disabled, compression disabled, direct reads, direct I/O for
flush and compaction, and 48 background jobs. Every conventional `db_bench`
path uses a 64 MiB initial write buffer and
`max_write_buffer_number=16`. F2Load uses its custom `fillvirtual` path and
does not consume RocksDB's write-buffer-count option. Commands use batch size
one and seed 12345678 where the engine exposes those options. Loading time
includes the method's terminal flush/compaction and wait boundary recorded by
its raw runner. Each ordinary final LSM state passed its recorded reopen
check. The Flush-only state was structurally validated and then used as the
immutable source of the separately reopened and validated Last-comp
checkpoint; a direct random-read run over its 16,237 overlapping L0 files was
not recorded.

These are absolute design-alternative measurements, not a version-matched
causal comparison: the bars come from the exact preserved implementation and
build required by each method. Do not silently substitute a different RocksDB
version, memtable representation, buffered-I/O run, thread sweep, or pending
compaction threshold.

## Fixed entries

| Entry | Time | Fixed implementation and method-specific settings | Evidence |
| --- | ---: | --- | --- |
| Baseline | 3,320 s | Clean RocksDB commit `f455ab7bd6a8c67f00d48075bb310f131d9fae5f`; release binary SHA-256 `8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b`; `fillrandom`; one subcompaction | `paper_clean_vector_wb16_1000gib_260903_run1/baseline_bg48` |
| ADOC | 4,122 s | Author artifact commit `5ed60f50d6cd8259b94e7f842ff06c6ab4df40a1`; release binary SHA-256 `68eb52fb2c6db555f47fa877c015015a98ee7ecc879fe3574004c9ec47f47a05`; FEA/TEA on; initial/max memtable 64/512 MiB; initial/max jobs 48/48; one subcompaction; soft/hard pending limits 64/128 GiB | `paper_adoc_wb16_1000gib_260903_run1/adoc_1000gib/adoc_on` |
| BlobDB (GC off) | 1,712 s | Integrated BlobDB in the same clean RocksDB build as Baseline; `min_blob_size=128`; 1 GiB blob-file limit; no blob compression; `blob_file_starting_level=0`; GC and compaction readahead disabled; one subcompaction | `paper_blobdb_wb16_1000gib_260903_run1/blobdb_1000gib` |
| Flush only | 973 s | Same vcomp binary as Last compaction; automatic compaction and ordinary stall thresholds disabled; 16,237 L0 files and zero compaction reads at completion | `paper_lastcomp_flushonly_wb16_1tib_260902_run1/flushonly_1000gib` |
| Last compaction | 5,026 s | vcomp commit `f9e281caa65943140385aa99fff1066da4117be8`; 973 s Flush-only prefix plus one 4,053 s synchronous full-range `compact`; one subcompaction | `paper_lastcomp_from_nocomp_wb16_1000gib_260903_run1/lastcomp_1000gib` |
| Fillseq | 1,257 s | Clean RocksDB 11.1.0 commit `cae42cd895f3bdeaaed1e58c0d27e2755b6ad308`; one full-key-space `fillseq`; one subcompaction | `paper_clean_fillseq_wb16_1000gib_260903_run1/fillseq_1000gib` |
| Fillseq + 10% overwrite | 1,745 s | Same clean RocksDB build and settings as Fillseq; full-key-space `fillseq`, settle, then 104,857,600 random `overwrite` operations over the same key space, followed by settle | `paper_clean_fillseq_overwrite_wb16_1000gib_260903_run1/fillseq_overwrite_1000gib` |
| F2Load | 102 s | Preserved legacy F2Load binary; `fillvirtual`; PLR error bound 8; 64 MiB virtual flush size; register batch maximum 256; 8 phase-1 shards; 48 materialization workers | `motivation_vcomp_speedup_1kb_260609_vcomp1kb_reload/vcomp_1000gb_1kb_none` |

The legacy F2Load artifact did not record a Git commit or binary checksum. Its
exact command and raw output are preserved, so the bar remains fixed to that
measurement; a future rerun must be treated as a new result rather than
silently replacing it.

The BlobDB point is an initial-loading configuration with garbage collection
disabled, not a GC-inclusive steady-state result. It retained 338.2 GB of
reported blob garbage (1.5x blob space amplification) at completion. The run
created 16,237 blob files containing 1,107,263,165,510 bytes and recorded zero
GC relocation bytes.

## Canonical ADOC reproduction settings

The plotted ADOC command makes the selected write-buffer count and pending
limits explicit:

```text
TARGET_GB=1000
BG_JOBS=48
CORE_NUM=48
WRITE_BUFFER_SIZE=67108864
MAX_MEMTABLE_SIZE=536870912
MAX_WRITE_BUFFER_NUMBER=16
MIN_WRITE_BUFFER_NUMBER_TO_MERGE=1
SOFT_PENDING_COMPACTION_BYTES_LIMIT=68719476736
HARD_PENDING_COMPACTION_BYTES_LIMIT=137438953472
SUBCOMPACTIONS=1
SYSTEM_ORDER=adoc_on
```

The completed 100 GiB thread/memtable sweeps are diagnostics only. The stopped
1,000 GiB run under
`paper_adoc_1000gib_bg48_core48_mem512_buf8_pending128_256_260902_run1`
changed both the write-buffer count and pending limits, reached only a partial
load, and is explicitly excluded from Figure 4.
