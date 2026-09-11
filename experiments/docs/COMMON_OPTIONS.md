# Frozen experiment configuration

Every Chapter 2/3 measurement shares the option set below. It is defined once in
`experiments/lib/ch23_common.py` (`BASE`, `NO_COMPACT`, `load_options`,
`read_options`) and every runner builds its command from it, so a second machine
reproduces the same runs by using that module rather than by retyping flags.

## Binaries

| role | path | RocksDB | SHA-256 |
|---|---|---|---|
| clean | `rocksdb-f455-release/db_bench` | 11.1.0 | `8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b` |
| F2Load | `vcomp/db_bench` | 11.1.0 + virtual compaction | `c5964e54fb0afc4779fd17b82f7366bdd8d19be9bd2acae33dd26c77e55019cd` |
| profiler | frozen per campaign under `<run>/bin/db_bench` | 10.10.1 | `20d67c38612cee9a34d9ef7114c81bc8366b2d4cf99c598b642e63f21ab74266` |

The profiler build is the one that reports per-level and cache tickers. Runners
copy the binary they use into the campaign directory and verify its hash before
the first cell, so a campaign cannot silently change compilers.

## Shared flags (`BASE`)

Statistics and reporting

    --statistics=1 --stats_per_interval=1 --stats_interval_seconds=60
    --report_interval_seconds=1

Write path

    --disable_wal=true --write_buffer_size=67108864
    --max_write_buffer_number=16 --min_write_buffer_number_to_merge=1
    --memtablerep=vector --allow_concurrent_memtable_write=true
    --batch_size=1 --threads=1

I/O and format

    --use_direct_reads=true --use_direct_io_for_flush_and_compaction=true
    --compression_type=none --format_version=7
    --enable_index_compression=false --bloom_bits=10

LSM-Tree shape

    --compaction_style=0 --compaction_pri=3 --num_levels=7
    --level_compaction_dynamic_level_bytes=false
    --max_bytes_for_level_base=268435456 --max_bytes_for_level_multiplier=10
    --target_file_size_base=67108864

Compaction triggers

    --max_background_jobs=48 --subcompactions=1
    --level0_file_num_compaction_trigger=4
    --level0_slowdown_writes_trigger=20 --level0_stop_writes_trigger=36
    --soft_pending_compaction_bytes_limit=68719476736
    --hard_pending_compaction_bytes_limit=137438953472
    --disable_auto_compactions=false

Seed: `--seed=12345678` for loading, `--seed=87654321` for every workload run.
Direct I/O is on, so the page cache does not carry state between cells.

## Dataset

1 TB logical: `--num=1048576000 --key_size=24 --value_size=1000`.
The 91 B variant uses `--key_size=48 --value_size=43` with `num` recomputed as
`gib * 2^30 / kv`. `fillrandom` draws each key uniformly with replacement from a
domain sized equal to the insert count, so roughly 63.2 percent of the domain
ends up present; `fillseq` writes every key once.

## Loading alternatives

| state | benchmarks | overrides |
|---|---|---|
| baseline | `fillrandom,flush,compact0,waitforcompaction,stats,levelstats` | - |
| flush-only | `fillrandom,flush,stats,levelstats` | `NO_COMPACT` |
| last compaction | `compact,stats,levelstats` | `NO_COMPACT`, `--use_existing_db=true` |
| fillseq | `fillseq,flush,compact0,waitforcompaction,stats,levelstats` | - |
| fillseq +10% OW | as fillseq, then a second phase of `overwrite` | - |
| F2Load | `fillvirtual,flush,compact0,waitforcompaction,stats,levelstats` | `--format_version=6`, `--use_virtual_compaction=true --plr_error_bound=8 --memtable_flush_size=64 --vcomp_register_batch_max=256 --vcomp_visible_l0_batch_mb=0 --vcomp_phase1_shards=8 --vcomp_materialize_workers=48` |

`NO_COMPACT` disables automatic compaction and lifts every L0 trigger to 2^30,
so flush-only accumulates all SSTs at L0 without stalling during loading.

## Read-only workload

    --benchmarks=readrandom,stats,levelstats --read_random_exp_range=0
    --use_existing_db=true --readonly=true --disable_auto_compactions=true
    --threads=48 --duration=300 --reads=10000000000
    --ops_between_duration_checks=1 --open_files=-1
    --merge_operator=put --stats_level=3 --histogram=true --perf_level=3
    --stats_interval_seconds=30 --report_interval_seconds=10

`--merge_operator=put` is required, not cosmetic: it selects `DBImplReadOnly`
for every state. Without it RocksDB picks `CompactedDBImpl` for the states that
hold a single level, and that path reports no statistics, which would make
flush-only and last compaction incomparable with the rest.

Four cache configurations, run for every state:

| config | `--cache_size` | `--cache_index_and_filter_blocks` |
|---|---|---|
| `A_cache_zero` | 1 | true |
| `B_cache_5pct` | 53687091200 | true |
| `C_pinned_zero` | 1 | false |
| `D_pinned_5pct` | 53687091200 | false |

`--pin_l0_filter_and_index_blocks_in_cache=false` throughout. The 50 GiB cache
is 5 percent of the dataset.

## Write workload

`experiments/scripts/paper/run_ch3_write_followup.py`. It stages a hard-linked
copy of the loaded DB and writes only to the copy, so the source is never
opened; `db_identity()` is compared before and after every run.

    --benchmarks=overwrite,stats,levelstats --use_existing_db=true
    --duration=300 --ops_between_duration_checks=1 --seed=87654321
    --stats_level=3 --open_files=-1

`BASE` re-enables automatic compaction here, so a state loaded with `NO_COMPACT`
faces the triggers a real workload would face. Flush-only stops writes at DB
open under those triggers, which is the measurement rather than a failure.

## YCSB workload

`experiments/scripts/read/run_ycsb_alternatives.py`, workloads A to F, 48
threads, 300 s per cell, `--num=1048576000`, zipfian, cache as above.

## Reference machine

    2 x Intel Xeon Gold 6336Y, 24 cores each, 48 online (48-95 offline)
    1.0 TiB DRAM
    /work: md0 raid0 over 31 x Samsung MZTL23T8HCLS 3.5 TB NVMe, 108.3 TiB
    Ubuntu, Linux 5.4.0-216-generic

A second machine does not have to match this, but it has to report it: loading
time and read throughput both depend on the device, so cross-machine numbers
belong in separate columns rather than in one average.
