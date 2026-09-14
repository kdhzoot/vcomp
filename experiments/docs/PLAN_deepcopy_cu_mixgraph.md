# Plan: deep-copied DBs, YCSB C uniform and mixgraph

Status: planned 2026-09-13, **not started**. Waiting for go-ahead.

## Purpose

Compare the ten baseline and ten exact-membership F2Load loadings on two more
workloads while removing physical placement and DB age as a confound. The
baseline DBs were written between 5 and 10 September, the F2Load DBs on the
13th; the 60-second no-cache probe on 13 September showed that where a DB's
files sit on the RAID members can move device read latency by 4x (a hot member
serving the upper levels' index/filter blocks), so a comparison on the original
files would mix that in with the loader. Copying every DB right before it is
measured gives all twenty the same fresh, `cp`-allocated layout.

## Per DB, in this order (twenty DBs, one at a time)

1. **Deep copy** the source DB to `/work/vcomp/exp/deepcopy_<arm>/db` with
   file-level parallel `cp` (`xargs -P 32`, `--preserve=timestamps`), then
   `sync`. Record wall time and GB/s. Write a fresh `db_identity.json` for the
   copy and a campaign bundle whose `db_dir` is the copy (the identity carries
   file metadata, so the source bundle does not validate against a copy).
2. **YCSB C, uniform requests**, 300 s, 48 threads, 50 GiB block cache, from a
   hard-link clone of the copy. Existing runner option
   `--request-distribution uniform`. Read-only, so the copy is unchanged after.
3. **mixgraph**, 300 s, 48 threads, 50 GiB block cache, from a fresh hard-link
   clone of the copy. Parameters are the RocksDB wiki's published ZippyDB
   fit (Cao et al., FAST '20), with the rate limiter off so 300 s runs at full
   speed and the key space set to ours:

       --benchmarks=mixgraph --num=1048576000 --key_size=24
       --keyrange_dist_a=14.18 --keyrange_dist_b=-2.917
       --keyrange_dist_c=0.0164 --keyrange_dist_d=-0.08082 --keyrange_num=30
       --value_k=0.2615 --value_sigma=25.45 --iter_k=2.517 --iter_sigma=14.236
       --mix_get_ratio=0.85 --mix_put_ratio=0.14 --mix_seek_ratio=0.01
       --mix_max_value_size=1024 --sine_mix_rate=false
       --duration=300 --threads=48 --cache_size=53687091200 (+ frozen BASE read options)

   Source: wiki page "RocksDB Trace, Replay, Analyzer, and Workload Generation",
   fetched 2026-09-13. The wiki example uses `num=50000000`, `key_size=48`,
   `reads=420000000` and a 24-hour sine period; those are replaced as above.
4. **Delete the copy** (`deepcopy_<arm>` only; the source DB is never touched)
   and continue with the next DB.

Order of DBs: e01-e10, then b01-b03, n01-n04, r01-r02, run3 (or interleaved
baseline/F2Load if the machine's state over the run is a concern).

## What has to be built first

- Runner: a generic benchmark mode. `run_ycsb_alternatives.py` only accepts
  workloads `a`-`f` and composes `benchmarks=<workload>,stats,levelstats`;
  mixgraph needs the benchmark name and its flag block passed through, with
  staging, cache drop, iostat/diskstats capture, validation and publishing
  reused as they are. YCSB C uniform needs nothing new.
- Driver: copy -> bundle -> runner (C uniform) -> runner (mixgraph) -> delete,
  per DB, with the same chain-log and per-arm validation as
  `run_f2load_band_chain.py`. Protected paths (`f2load_91b_260911`,
  `approved_run3/full/f2load_1kb`, `f2band_260913_em`, the ten baseline
  sources) are refused by pattern, as in the 13 September cleanup.
- TSV builder: extend `build_eval_fidelity_tsv.py` with two workload keys
  (`Cu`, `MG`) so the results land in the same behaviour/device/summary files,
  plus a per-DB copy-time table.

## Checks that make this fair

- The runner drops the page cache with sudo before every cell
  (`cache_reset.log: Page cache dropped (sudo)`), so the 767 GB that `cp` just
  pulled through memory does not serve the measurement.
- C uniform runs before mixgraph on the same copy: mixgraph writes (put ratio
  0.14), so it must go last, and each cell still starts from its own clone.
- Uniform reads over 10^9 keys against a 50 GiB cache leave the data-block hit
  rate near 5 %, so C uniform is device-bound and sensitive to placement -
  which is what the deep copy is there to equalise.
- Expected positive lookup under uniform reads is the fraction of the key
  space present: baseline 63.21 %, F2Load 63.21 % x coverage (about 62.9 %),
  with none of the "which keys are missing" spread seen under zipfian
  (confirmed on e01-e10 in the aborted probe: 62.90-62.99 %).

## Cost

Copy 2-10 min (parallel `cp` untested here; sequential was 610 s on 9 Sept)
+ 2 x (pilot + 300 s + staging) + delete ~1 min = roughly 15-25 min per DB,
**5-8 hours for twenty**. Disk: one extra 767 GB copy at a time.

## Open

- Whether the wiki's `mix_seek_ratio=0.01` / `mix_get_ratio=0.85` should be used
  as-is or the paper's 0.83/0.14/0.03 split; the wiki numbers are what the
  fetched page states.
- `mix_max_scan_len=10000` default: keep.
