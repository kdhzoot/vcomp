# F2Load 91 B loading scale-up, 2026-09-11

F2Load had only ever been loaded at the 1 KB KV size. This run measures the 91 B
configuration (`key_size=48`, `value_size=43`), which holds the same bytes in
11.25 times as many keys, at five dataset sizes.

Runner: `scripts/paper/run_f2load_91b.py`. Options come from
`lib/ch23_common.load_options(gib, 91, ..., 'f2load')`, so every flag matches the
frozen configuration in `docs/COMMON_OPTIONS.md`. The F2Load build is
`3d71cc8f84b6a79517122af2d5f027bff027442c2e455561c78af76b186aff0e`, which is
newer than the hash the frozen table pins; the runner registers the build it
measures rather than relaxing that table.

| size | loading | GiB/s | SSTs | final bytes | peak RSS | levels | validation |
|---|---|---|---|---|---|---|---|
| 500 GiB | 1.33 min | 6.3 | 6,612 | 0.37 TiB | 1.30 GiB | L1-L5 | 675/1000 |
| 1000 GiB | 2.64 min | 6.3 | 14,280 | 0.79 TiB | 1.74 GiB | L1-L5 | 665/1000 |
| 2000 GiB | 5.11 min | 6.5 | 28,432 | 1.48 TiB | 2.10 GiB | L1-L5 | 637/1000 |
| 4000 GiB | 12.99 min | 5.1 | 52,476 | 2.80 TiB | 9.25 GiB | L1-L6 | 653/1000 |
| 8000 GiB | 49.84 min | 2.7 | 103,148 | 5.54 TiB | 42.29 GiB | L1-L6 | 628/1000 |

Scaling is linear to 2 TB and breaks after it. Doubling the dataset costs 1.98x
and 1.94x up to 2 TB, then 2.54x and 3.84x. Peak RSS turns first and turns
harder: 1.33x and 1.21x, then 4.41x and 4.57x. The break coincides with the tree
reaching $L_6$, which it does at 4 TB.

Validation reads 1000 keys with the load seed through the clean release, so it
also proves stock RocksDB can open the state. The 63-67% found fraction is the
known F2Load membership gap, not a 91 B effect: the 1 TB 1 KB load records
629/1000 under the same check while baseline records 10000/10000.

## Comparing against baseline

Baseline 91 B has its own five-point series in
`artifacts/log_loads/exp_260604_exp91b_baseline` and
`exp_260822_paper_bg_91b_8tb_direct` (2.71 / 5.58 / 11.21 / 23.65 / 46.20 h).
**Those runs pass no write-buffer flags**, so they took the db_bench default of
two memtables, against the frozen configuration's 16. At 1 TB that difference
shows up as 2 h 03 m of cumulative stall (36.6% of the run) versus 51 m 25 s
(27.0%), and the two campaigns report 5.58 h and 3.17 h for the same load. A
speedup that divides one series by the other mixes configurations, and the
mixture favours F2Load, since the older baseline spent a third of its run
stalled on a memtable count the paper does not use. The resolution is to
re-measure baseline under the frozen configuration rather than to re-measure
F2Load under the old one; until then these speedups stay out of the paper.
