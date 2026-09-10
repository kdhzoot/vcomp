# F2Load state fidelity against naturally accumulated baselines

User request (2026-09-09): compare an F2Load-built 1 TB state with the loaded
baselines on every fidelity dimension RocksDB exposes — throughput, latency,
filter checks and positives per lookup, found fraction, compaction bytes,
device I/O — then explain the residuals. The F2Load DB is the dedup-ratio
reload `f2load_1tb_260909_ratio`; the baseline arm is the promoted run3 load.
No existing DB was modified. Every read cell starts from a fresh hardlink clone.

## Sources

| Arm | DB | Load record |
|---|---|---|
| baseline | `paper_ch23_common_260905_approved_run3/full/baseline_1kb` | `paper_ch23_common_260905_approved_run3` |
| f2load (after) | `f2load_1tb_260909_ratio` | `f2load_1tb_260909_ratio`, bundle `results/f2load_1tb_260909_ratio_bundle` |
| f2load (before) | `paper_ch23_common_260907_f2_completion1/full/f2load_1kb` | `paper_ch23_common_260907_f2_completion1` |

Both F2Load loads replay the promoted phase-1/phase-2 commands; only the binary
differs (the 260909 binary carries the size model, global-unique keys and the
dedup-ratio estimate). Campaigns: `ycsb_50g_f2load_baseline_260909` (before),
`ycsb_50g_f2ratio_260909` (after), each `run_ycsb_alternatives.py` with
`--cache-size 53687091200`, 48 threads, 300 s cells, A-F, arms interleaved.

## Method

All counters are normalised per operation: the cells run for fixed wall time,
so a faster arm performs more operations and every absolute counter scales
with it. `analysis/compare_f2load_fidelity.py` prints the matrix;
`analysis/plot_f2load_fidelity.py` draws `results/f2load_fidelity_260909.png`.
Bloom counters come from the raw tickers (`rocksdb.bloom.filter.useful` +
`full.positive` + `full.true.positive`). Index-cache-miss ratios were removed
from the figure: the absolute rate is 2e-5 to 5e-4 per operation in every
workload, so the ratio is noise.

## Results (F2Load / baseline, before -> after the dedup-ratio change)

| metric | A | B | C | D | E | F |
|---|---|---|---|---|---|---|
| throughput | 1.008 -> 0.982 | 1.024 -> 0.988 | 1.129 -> 1.042 | 1.090 -> 1.058 | 1.260 -> 1.151 | 0.988 -> 0.989 |
| avg latency | 0.992 -> 1.019 | 0.977 -> 1.012 | 0.886 -> 0.959 | 0.918 -> 0.945 | 0.794 -> 0.869 | 1.012 -> 1.011 |
| filter checks / op | 1.003 -> 0.983 | 0.984 -> 0.975 | 0.842 -> 0.991 | 0.850 -> 0.957 | - | 0.950 -> 0.973 |
| filter positives / op | 0.991 -> 0.996 | 0.984 -> 0.993 | 1.048 -> 1.145 | 0.986 -> 0.995 | - | 0.984 -> 0.996 |
| found fraction | 0.994 -> 0.999 | 0.993 -> 1.000 | 1.043 -> 1.109 | 0.998 -> 1.001 | - | 0.994 -> 1.000 |
| compaction write B / op | 0.980 -> 1.012 | 1.032 -> 1.042 | - | - | - | 1.018 -> 1.035 |
| data cache miss / op | 0.982 -> 1.009 | 0.993 -> 1.013 | 0.972 -> 1.002 | 0.977 -> 1.004 | 0.834 -> 0.999 | 1.010 -> 1.024 |
| device read p50 | 0.967 -> 0.968 | 0.961 -> 0.959 | 0.954 -> 0.952 | 0.963 -> 0.959 | 0.848 -> 0.849 | 0.973 -> 0.974 |
| mean abs. deviation | 1.29% -> 1.40% | 2.04% -> 1.91% | 8.07% -> 5.66% | 5.69% -> 2.95% | 19.61% -> 10.87% | 1.87% -> 1.73% |

A, B and F sit inside the 3.0% spread the four baseline repeats show on A.
C, D and E moved toward the baseline on every logical counter. The row that did
not move is device read latency, which the next two sections show was measuring
flash placement rather than tree state; under the fair copy protocol these
throughput ratios become 0.983 / 0.998 / 1.002 / 1.020 / 0.993 / 0.989.
One regression: C's found fraction is 0.669 against the baseline's 0.603 under
uniform reads while the distinct-key counts agree within 0.1%; the
model-generated key set differs in position, and only a uniform sampler sees
it. A and D (zipfian) agree to 0.06%.

## Where the SST count differs

Whole-DB size distributions, `os.path.getsize` over every SST:

| | clean n01 | vcomp ratio |
|---|---:|---:|
| files | 13,483 | 13,247 |
| mean | 58.267 MiB | 59.269 MiB |
| 60-72 MiB median | 64.391 MiB | 63.963 MiB |
| > 72 MiB | 1,030 | 1,218 |
| < 60 MiB | 4,405 | 4,074 |

Full files are 0.665% smaller in vcomp: clean's hard cut compares flushed
data bytes (`rep_->offset`, `target_file_size_is_upper_bound=false`) against
the cap and finishes above it by the index/filter tail, while
`SSTSizeModel::MaxEntries` bounds the finished size. The count gap is the 331
fewer under-filled files, L5 -240 and L4 -98 by MANIFEST replay. At L5 every
under-filled file in both engines is the last output of its job (1,453/1,453
and 1,734/1,734); L6 is empty, so `max_output_file_size` is 1x and all three
grandparent cuts are dead there. The verified term is remainder re-absorption:
vcomp's L4->L5 jobs read back 533 of their own under-filled born-L5 files,
clean's 63; net born-L5 cohort -189 of the -331. The three grandparent cuts are
present verbatim in vcomp's physical compaction path (`compaction_outputs.cc`
differs from the release only in a timestamp hunk); the virtual splitter
implements only the dynamic 50->90% pre-cut and lacks the `max_compaction_bytes`
and `target/8` cuts, which are live only at L1-L4.

## The gap is physical placement, not tree state

Workload E per operation, both arms: 0.950 seeks, 47.97 nexts, 9.00 data-block
misses, 6.07 index-block hits, 4.46 preads. The same work, but each pread on the
baseline DB takes p50 88 us / p99 169 us against 75 / 110 on the F2Load DB.
Per-member NVMe `iostat` during the cells: 31 devices, load balanced to
CV <= 0.006, 99.8-99.9% util on both arms, r_await 90 vs 70 us at the same
13.9 KiB request size. A QD1 `O_DIRECT` probe over 400 random SSTs of each DB
gives identical raw latency (p50 73.8 vs 73.7 us), so the difference exists
only under concurrency. Extent fragmentation points the other way (vcomp files
have 10 extents, clean 2-4) and is irrelevant to 4 KiB reads on a 1 MiB-chunk
RAID0. Both arms use `use_direct_reads` and direct flush/compaction I/O, and
the runner drops the page cache before every cell, so the page cache serves
nothing.

### The 2x2 control

Each DB was copied with `cp -a` into `physcopy_260909/` as one sequential
stream; the originals were never opened for writing. Four A-F campaigns then
differ only in which arm read a copy. Every campaign interleaves its two arms
in one session, so a column is a within-session comparison. 36/36 full cells
validated, and `swap_pages_in` is 0 in all of them.

| byte-copied arm | run id | A | B | C | D | E | F | mean \|ratio-1\| |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| neither (as loaded) | `ycsb_50g_f2ratio_260909` | 0.982 | 0.988 | 1.042 | 1.058 | 1.151 | 0.989 | 4.87% |
| baseline | `ycsb_50g_physcopy_A_full_260910` | 0.975 | 1.014 | 0.991 | 1.005 | 0.993 | 1.002 | 1.02% |
| F2Load | `ycsb_50g_physcopy_B_full_260910` | 1.015 | 1.032 | 1.031 | 1.043 | 1.152 | 0.999 | 4.57% |
| both | `ycsb_50g_physcopy_C_full_260910` | 0.983 | 0.998 | 1.002 | 1.020 | 0.993 | 0.989 | **0.98%** |

Rewriting the baseline removes the gap; rewriting F2Load does not touch it
(E stays at 1.152). Each (system, treatment) combination was measured in two
of the four sessions, so the effect of the copy can be pooled:

| | A | B | C | D | E | F |
|---|---:|---:|---:|---:|---:|---:|
| baseline, copy vs as loaded | +0.1% | +2.3% | +4.0% | +4.3% | **+15.8%** | +0.1% |
| F2Load, copy vs as loaded | +2.1% | +0.1% | -0.3% | -0.1% | -0.1% | -0.4% |

The size of the gain tracks how device-bound the workload is: C, D and E all
sit at p99 165-169 us as loaded and converge on 109 us after the copy, while A
and F are already at ~500 us where the device tail is not what limits them.
The F2Load row is flat, which is the null control — copying does not make a DB
faster in general, it repairs a placement that conventional loading damaged.
Figure: `results/physcopy_matrix_260910.png` (E-pair only:
`results/physcopy_control_e_260909.png`).

The baseline was written over 0.8 h by 48 concurrent compaction streams that
created 206,050 SSTs and deleted 192,589 of them (14.5 TB to the device for a
0.82 TB result, device WAF 13.5), so its surviving files are what the drives'
own garbage collection left behind; the F2Load DB was written once, in 28 s.
`/work` receives TRIM only from the weekly `fstrim` timer, and the drives
reported 93.0% of their namespace as live at every load, so all baselines were
written with the drives near full regardless of the 61% filesystem fill. That
is a plausible amplifier, but not the cause: the F2Load DB and both copies were
written under the same drive condition and are clean.

### Consequence for the comparison

Read from fresh copies, F2Load's state is indistinguishable from a naturally
accumulated one across A-F: every ratio is within 2% of 1.0 and the mean
absolute deviation is 0.98%, below the 3.0% spread the four baseline repeats
show on A alone. The residual A and F values (0.983, 0.989) are inside that
spread. Read as loaded, the same comparison would credit F2Load with a
spurious 15% scan advantage. Any read comparison of the two loaders must
therefore measure both from fresh copies, and that is now the protocol.

## Repeatability of the baseline itself

Four identical 1 TB loads (`baseline_coverage_260908_night_n01..n04`) with A-F
at 50 GiB cache. A and F respond to disk fill (n04's A: 321K -> 722K ops/s
from 94% to 58% fill); re-measured at 58% the A spread is 3.0%. B-E are
insensitive (-4.5% to +3.1% on n01 across fills). L1 coverage over the same
loads is 9.82-88.78%, decided by 4 of ~13,400 SSTs; it is not a fidelity metric.

## Open items

- The materializer writes `format_version=6` SSTs while the baselines are 7;
  filter/index layout is otherwise identical (kBinarySearch, `bloomfilter:10`,
  whole-key, `partition_filters=false`, 4 KiB blocks).
- `ycsb_50g_physcopy_A_full_260909` stopped after three cells because the
  swap guard compared `pswpin` exactly and 64 pages of unrelated swap-in
  tripped it. The guard now fails on any `pswpout` and tolerates `pswpin`
  below `SWAP_IN_TOLERANCE_PAGES`, recording the delta per cell; the three
  2026-09-10 campaigns replaced that run and all reported zero.
- C's uniform found-fraction difference is a property of model-generated keys
  and needs a statement in the paper rather than an engine change.
- The residual 1% on unique100 is `dropped_live_entries`, not estimation.

## Artifacts

`results/ycsb_50g_f2load_baseline_260909`, `results/ycsb_50g_f2ratio_260909`,
`results/ycsb_50g_physcopy_A_e_260909`, `results/ycsb_50g_physcopy_B_e_260909`,
`results/ycsb_50g_physcopy_{A,B,C}_full_260910`,
`results/physcopy_260909_bundle_{A,B,C}` (source bundles),
`results/physcopy_matrix_260910.png`, `results/physcopy_control_e_260909.png`,
`results/f2load_1tb_260909_ratio_bundle`, `results/f2load_fidelity_260909.png`,
`results/ycsb_across_loads_260909.png`, `results/baseline_repeat_variance_260909.png`,
`results/fidelity_100gib_260909_ratio_run2`. Raw logs remain under
`experiments/artifacts/` on the measurement host.
