# Plan: mixgraph on ten baseline and ten no-bitmap F2Load loadings

Status: **queued 2026-09-14**, supervisor pid 4141734, waiting behind the
memory-overhead campaign (`run_f2load_memory_profile.py`, pid 3905047, on its
last cell `4000gib_100b`). The driver blocks on three conditions together --
the campaign lock, no `db_bench` process, and that supervisor having exited --
then starts on its own. Watch
`artifacts/queues/mixgraph_pairs_260914/STATUS.json` and
`artifacts/log_runs/mixgraph_pairs_260914.launch.log`; per-arm results
accumulate in
`artifacts/log_loads/mixgraph_pairs_260914/chain_results.json`.
Stop it with `kill 4141734` while it is still waiting.

## Purpose

Add mixgraph to the paired baseline / F2Load comparison that YCSB A-F already
covers, and report its compaction write volume. mixgraph is 14 % puts, so the
cell leaves pending compaction behind; the number that matters is the
compaction bytes *after* that work drains, not the bytes accumulated at the
300-second mark.

This supersedes the YCSB-C-uniform half of
[PLAN_deepcopy_cu_mixgraph.md](PLAN_deepcopy_cu_mixgraph.md), which was never
started. The mixgraph parameters and the deep-copy rationale there are reused
unchanged.

## Arms

**Baseline (10, already on disk, deep-copied before measurement)**

| arm | source DB |
|-----|-----------|
| b01 | `/work/vcomp/exp/baseline_coverage_band_260910/full/repeat_01/baseline_1kb` |
| b02 | `/work/vcomp/exp/baseline_coverage_band_260910/full/repeat_02/baseline_1kb` |
| b03 | `/work/vcomp/exp/baseline_coverage_band_260910/full/repeat_03/baseline_1kb` |
| n01 | `/work/vcomp/exp/baseline_coverage_260908_night_n01/full/repeat_01/baseline_1kb` |
| n02 | `/work/vcomp/exp/baseline_coverage_260908_night_n02/full/repeat_01/baseline_1kb` |
| n03 | `/work/vcomp/exp/baseline_coverage_260908_night_n03/full/repeat_01/baseline_1kb` |
| n04 | `/work/vcomp/exp/baseline_coverage_260908_night_n04/full/repeat_01/baseline_1kb` |
| r01 | `/work/vcomp/exp/baseline_coverage_260908_repeat1/full/repeat_01/baseline_1kb` |
| r02 | `/work/vcomp/exp/baseline_coverage_260908_repeat1/full/repeat_02/baseline_1kb` |
| run3 | `/work/vcomp/exp/paper_ch23_common_260905_approved_run3/full/baseline_1kb` |

All ten were verified present on 2026-09-14. **None of them is ever written to
or deleted by this campaign.**

**F2Load, no membership bitmap (10, loaded fresh)**

The previous no-bitmap series (`f06`-`f15`, `/work/vcomp/exp/f2band_260912/*`)
was deleted after its YCSB campaigns; only the exact-membership series
(`e01`-`e10`) survives on disk. Ten fresh no-bitmap loadings are therefore
made for this campaign, named `g01`-`g10` under
`/work/vcomp/exp/f2band_260914_nb/`, with
`scripts/paper/run_f2load_band_chain.py` and **no** `--exact-membership`
(so `load_extra` is empty, exactly as `f06`-`f15` were loaded). Load cost is
about 60 s each.

**Decision (2026-09-14): the F2Load arm is measured on the freshly loaded DB,
without an intervening deep copy.** Consequence to record with the results:
the baseline arm reads from `cp`-allocated files and the F2Load arm from
loader-allocated files, so any residual placement effect is not equalised
between the two arms. It *is* equalised within each arm, which is what the
per-arm spread needs.

## Per DB, in this order

Arms are interleaved (`b01, g01, b02, g02, ...`) so that machine drift over the
six-to-eight hour campaign does not land entirely on one system.

**Baseline arm**

1. **Deep copy** the source DB to `/work/vcomp/exp/mgcopy_260914/<arm>` with
   file-level parallel `cp` (`xargs -P 32`, `--preserve=timestamps`), then
   `sync`. Record wall time and GB/s in `copies.jsonl`. Write a fresh
   `db_identity.json` for the copy and a campaign bundle whose `db_dir` is the
   copy.
2. **mixgraph**, 300 s, from a hard-link clone of the copy (the runner's own
   staging), then drain (below).
3. **Delete the copy** only. `mgcopy_260914` is the sole deletable root for
   this arm; the ten sources are refused by path pattern.

**F2Load arm**

1. **Load** `g<nn>` into `/work/vcomp/exp/f2band_260914_nb/<arm>`, no bitmap,
   validate as `run_f2load_band_chain.py` already does (levels, SST count,
   `num_keys`, peak RSS).
2. **mixgraph**, 300 s, from a hard-link clone of that DB, then drain.
3. **Delete the loaded DB** and its clone.

## Configuration

Identical to the existing YCSB campaigns; nothing is retuned for mixgraph.
From `scripts/read/run_ycsb_alternatives.py` `BASE` + `options()`:

- `cache_size=53687091200` (50 GiB), `cache_type=lru_cache`,
  `cache_index_and_filter_blocks=true`, no pinning
- `threads=48`, `duration=300`, `seed=87654321`,
  `ops_between_duration_checks=1`
- `num=1048576000`, `key_size=24`, `value_size=1000` (taken from the source
  load record)
- `use_existing_db=true`, `readonly=false`, `disable_auto_compactions=false`,
  `memtablerep=skip_list`, `max_background_jobs=48`, `subcompactions=1`
- direct reads and direct flush/compaction I/O, `disable_wal=true`,
  `compression=none`, `bloom_bits=10`, `format_version=7`
- `stats_level=3`, `perf_level=3`, `statistics=1`, `histogram=true`,
  `report_interval_seconds=1`, `stats_interval_seconds=30`
- frozen profiler binary, sha256
  `20d67c38612cee9a34d9ef7114c81bc8366b2d4cf99c598b642e63f21ab74266`

## mixgraph parameters

**Decision (2026-09-14): follow the published command as closely as the
dataset allows.** The workload block is taken verbatim from this repository's
own prefix-dist command (`scripts/read/run_q3_read_workloads.sh:169`), which is
the wiki's ZippyDB fit plus the `key_dist` pair. Nothing in the workload model
is retuned for our key-value size:

    mix_get_ratio=0.83      mix_put_ratio=0.14   mix_seek_ratio=0.03
    key_dist_a=0.002312     key_dist_b=0.3467
    keyrange_dist_a=14.18   keyrange_dist_b=-2.917
    keyrange_dist_c=0.0164  keyrange_dist_d=-0.08082   keyrange_num=30
    value_k=0.2615          value_sigma=25.45
    iter_k=2.517            iter_sigma=14.236

`mix_max_value_size` (1024) and `mix_max_scan_len` (10000) are left at their
db_bench defaults, as the published command leaves them, and `sine_mix_rate`
stays off (the wiki command sets `sine_a/b/d` but never `-sine_mix_rate=true`,
so its rate limiter is inert too). `num=1048576000` and `key_size=24` come from
the load record because the dataset is what it is; `--threads=48`,
`--duration=300` and the 50 GiB cache come from the frozen YCSB configuration.

What that means for this dataset, all of it read off
`MixGraph()` in `vcomp/tools/db_bench_tool.cc:10531-10730` and confirmed
against the June cells' own output line
(`avg size: 36.3 value, 571.1 scan`):

**Puts write ~36-byte values, not 1 KB. `--value_size` is ignored by
mixgraph.** Each Put draws `ParetoCdfInversion(u, value_theta, value_k,
value_sigma)` = `theta + sigma*(u^-k - 1)/k`, clamped to
`[10, min(1 MiB, mix_max_value_size)]` with a modulo wrap above the cap. Under
the published fit that is a mean of 36 B and a median of 20 B, written into a
dataset whose existing records are 1000 B. Accepted deliberately: the value
model is part of the published workload, and pinning values at 1000 B (which
`value_theta=1000, value_sigma=0` would do exactly) would be our parameter, not
Cao et al.'s. State it with the results -- the puts shrink the records they
overwrite, so the compaction bytes reported here are not the bytes a 1 KB
overwrite workload would produce. YCSB A and F in the same campaign series
already cover 1 KB overwrites.

**Seeks dominate the I/O, by construction.** Scan length is
`ParetoCdfInversion(u, 0, 2.517, 14.236) % mix_max_scan_len`; with `k=2.517`
the distribution has no finite mean, so at the default cap it averages 568
entries (p99 = 8486) -- about 568 KB read per seek at 1 KB values. At a 3 %
seek ratio that is roughly 17 KB of scan per operation against 0.83 gets, which
is why the June 10 TB cells reported 13.4 GB/s at 812 k ops/s. Expect this
campaign's cells to be scan-bandwidth-bound and to report far lower ops/s than
the YCSB cells; the comparison between arms is still like-for-like.

**Key hotness needs `key_dist_a`/`key_dist_b`, and they are passed.**
`use_random_modeling` is set when either is zero (both default to `0.0`), and
the `if (use_random_modeling)` branch is taken *before*
`else if (use_prefix_modeling)`, so with the wiki command as printed
`keyrange_dist_*` is fitted and then never consulted -- keys come out uniform
over `num`. With the `key_dist` pair present, `DistGetKeyID()` runs: a key-range
is chosen from the two-term exponential, then the offset inside it from the
power distribution. This is the difference the name "prefix_dist" refers to.

Expected regime from the hotness model: the two-term exponential puts about
**78 % of all accesses in key-range 1 of 30**
(`14.18*e^-2.917 + 0.0164*e^-0.08082` = 0.78), which is 1/30 of the key space
-- roughly 22 M resident keys, about 23 GB, so the get hot set fits inside the
50 GiB block cache. Gets are therefore largely cache-resident after warm-up,
and the arms separate through the scan, put and compaction paths. Record this
with the results: mixgraph here is not a device-bound point-read test, unlike
YCSB C uniform.

The `ycsb_*` options are dropped for mixgraph cells, as the runner already
does.

**Known quirk, left alone:** the single draw `u = (ini_rand % num) / num` seeds
the key, the query type, the value size and the scan length, so all four are
correlated within an operation. It is how the published benchmark behaves.

## Provenance: this command against the published one

Three commands, all descended from Cao et al., FAST '20:

**(A) RocksDB wiki, "RocksDB Trace, Replay, Analyzer, and Workload
Generation"** (refetched 2026-09-14; the page carries exactly one mixgraph
example): `keyrange_dist_*` + `keyrange_num=30`, `value_k=0.2615`,
`value_sigma=25.45`, `iter_k=2.517`, `iter_sigma=14.236`,
`mix_get/put/seek=0.85/0.14/0.01`, `sine_a/b/d` set, `reads=420000000`,
`num=50000000`, `key_size=48`, `cache_size=256 MiB`, default threads (1).
It passes **no** `key_dist_a`/`key_dist_b`.

**(B) This repository's own prefix-dist command**, in
`scripts/read/run_q3_read_workloads.sh:169` and used for the mixgraph cells
bundled under `paper_evidence/current/fig_exp_scale`: the wiki parameters plus
`key_dist_a=0.002312 key_dist_b=0.3467`, and `0.83/0.14/0.03` for the query
mix, at `--threads=48 --duration=300`.

**(C) This plan.** The workload block of (B), unchanged. The only additions
are outside the workload model: the dataset's own `num`/`key_size`, the frozen
YCSB cache/threads/duration, and `waitforcompaction` appended to the benchmark
string so the compaction bytes can be read after the drain.

The differences and why:

| | (A) wiki | (B) our q3 runs | (C) this plan |
|---|---|---|---|
| `keyrange_dist_a..d`, `keyrange_num` | 14.18 / -2.917 / 0.0164 / -0.08082, 30 | same | same |
| `key_dist_a`, `key_dist_b` | unset -> **0** | 0.002312, 0.3467 | 0.002312, 0.3467 |
| effective key selection | uniform over `num` | key-range hotness + in-range power | same as (B) |
| `value_k`, `value_sigma`, `value_theta` | 0.2615, 25.45, 0 | same | same |
| measured average value | ~35 B | **36.3 B** (observed) | ~36 B expected |
| `mix_max_scan_len` | unset -> 10000 | unset -> 10000 | 10000 (default) |
| measured average scan | ~560 | **571.1** (observed) | ~570 expected |
| `mix_get/put/seek` | 0.85 / 0.14 / 0.01 | 0.83 / 0.14 / 0.03 | 0.83 / 0.14 / 0.03 |
| rate limiter | `sine_a/b/d` set but `sine_mix_rate` unset, so **off** | off | off, explicitly |
| `num`, `key_size` | 5e7, 48 | 1.07e10, 24 | 1.049e9, 24 |
| threads, length | 1, `reads=4.2e8` | 48, `duration=300` | 48, `duration=300` + drain |
| benchmark string | `mixgraph` | `mixgraph,stats,levelstats` | `mixgraph,stats,waitforcompaction,stats,levelstats` |

The observed columns are from
`artifacts/log_runs/q3_flex_read10tb_realistic_fmix_260610/*/mixgraph/stdout.txt`,
whose aggregate line reads `avg size: 36.3 value, 571.1 scan` -- direct
confirmation both that `--value_size=1000` was ignored (it was passed) and that
the uncapped scan length averages ~570 entries.

**So (C) is (B), parameter for parameter.** Every flag that defines the
workload -- the key-range hotness, the in-range key hotness, the query mix, the
value-size fit, the scan-length fit and both caps -- is the published
command's. What differs from (A) is only what (B) already differed by: the
`key_dist` pair that switches the hotness model on, the query mix, the dataset
scale, and the thread count.

**Query mix (2026-09-14): `0.83/0.14/0.03`, matching (B).** The wiki's
`0.85/0.14/0.01` and this repo's `0.83/0.14/0.03` both circulate; keeping (B)
means the new cells can be read against the mixgraph logs already bundled for
`fig:exp-scale`.

**Deliberately not copied from (A):** `threads=1`. Mark Callaghan's note that
mixgraph should be run single-threaded is about result interpretation, and
RocksDB issue #8215 reports a mutex teardown error at `--threads=4`; our own
48-thread mixgraph cells completed cleanly at 300 s in June, and 48 threads is
what every YCSB cell in this campaign series uses, so it stays.

## Draining pending compaction, and the compaction-bytes number

The benchmark string becomes

    mixgraph,stats,waitforcompaction,stats,levelstats

The first `stats` records the state at the 300-second mark;
`waitforcompaction` then blocks until the pending work is gone, and the second
`stats` is the dump the metrics are read from. The parser already takes each
ticker's **last** dump, so `compaction_write_bytes`
(`rocksdb.compact.write.bytes`) and `flush_write_bytes` come out
drain-inclusive with no parser change. This is the protocol
`run_ch3_write_fixed_ops.py` / `run_ch31_uniform.py` use for YCSB A.

Reported per cell: `throughput_ops_sec`, `avg_latency_us`, operation counts by
kind (mixgraph reports Gets / Puts / Seeks as read / update / scan),
`compaction_write_bytes`, `compaction_read_bytes`, `flush_write_bytes`,
`pending_bytes_end`, `positive_lookup_pct`, `filter_checks_per_lookup`, final
level shape, and the 300-second and post-drain values side by side so the
drain's own contribution is visible.

## Code changes (done 2026-09-14)

1. `scripts/read/run_ycsb_alternatives.py`, `options()`: for
   `workload == 'mixgraph'`, set
   `benchmarks='mixgraph,stats,waitforcompaction,stats,levelstats'`. The YCSB
   string stays byte-identical so the existing campaigns' option audits still
   match.
2. Same file, the `MIXGRAPH` dict: add `key_dist_a=0.002312` and
   `key_dist_b=0.3467`, and change the query mix to `0.83/0.14/0.03`, so the
   dict matches `run_q3_read_workloads.sh:169`. Everything else in the dict is
   already the published fit and stays. No campaign has published mixgraph
   results from the current dict, so nothing downstream depends on it.
3. Same file, the watchdog: `full_watchdog_sec` (manifest) and the `run_cell`
   timeout are `duration + 600`, which the drain can exceed. Add a
   `--drain-timeout` (default 7200 s, the value `run_ch31_uniform.py` allows
   YCSB A) and use `duration + drain_timeout` for mixgraph cells only.
4. Same file: a five-second mixgraph pilot cell is added for mixgraph
   campaigns. The existing pilot is YCSB C, which touches none of the mixgraph
   flags, so a bad parameter or a missing drain would otherwise only surface
   after a deep copy and a 300 s cell.
5. New driver `scripts/paper/run_mixgraph_pairs_260914.py`: per arm,
   copy-or-load -> bundle -> one mixgraph campaign -> delete, with the
   chain log and per-arm validation of `run_deepcopy_cu_mixgraph.py` (whose
   `deep_copy`, `bundle_for`, `campaign` and `remove_copy` are reused as they
   are, with `COPY_ROOT` repointed and `PROTECTED` extended).
6. TSV builder: one `MG` workload key in `build_eval_fidelity_tsv.py`, plus a
   per-arm copy/load-time column.

Run ids: `mg_base_<arm>_260914` and `mg_nb_<arm>_260914`.

Item 6 is the only one still outstanding; it is a post-processing step and does
not block the chain.

**Load binary for the F2Load arms.** `g01`-`g10` are loaded with the current
`vcomp/db_bench` (sha `a04221e7...`), not the 2026-09-12 build that produced
`f06`-`f15` (sha `3d71cc8f...`). The driver prints each load's SST count beside
that series' range (13,167-13,290) so a build that no longer reproduces the
same tree shape is visible immediately.

## Validation gates (a cell is accepted only if all hold)

- exit code 0, not timed out, no execution error in the log
- exactly one `mixgraph` aggregate line; operation histograms complete and
  summing to the aggregate operation count
- measured seconds within 5 s of 300
- `waitforcompaction` present with `status (OK)`, and
  `Estimated pending compaction bytes: 0` in the final dump
- no `missing_tickers`; all engine error tickers zero
- the source DB's identity is unchanged after the cell (the runner already
  re-hashes it), and for the baseline arm the *original* source is never even
  opened
- `rocksdb.bloom.filter.prefix.checked == 0`
- page cache dropped before the cell
  (`cache_reset.log: Page cache dropped (sudo)`)

Pilot cells (3 s / 5 s) run first per campaign, as in every other campaign.

## Safety

- The ten baseline sources, `f2band_260913_em`, `f2load_91b_260911`,
  `approved_run3`, `baseline_coverage*`, `paper_ch23_common*` are all in
  `PROTECTED`; deletion is refused unless the resolved path is under
  `/work/vcomp/exp/mgcopy_260914/` or `/work/vcomp/exp/f2band_260914_nb/`.
- Every db_bench invocation carries `--use_existing_db=true`; no cell is ever
  pointed at a source directory.
- `--dry-run` prints the full copy/load/delete plan per arm and exits.

## Cost and disk

Per baseline arm: copy 5-10 min + pilot + 300 s + drain + staging/validate +
delete ~1 min, roughly **20-30 min**. Per F2Load arm: load ~1 min + pilot +
300 s + drain + delete, roughly **10-15 min**. Twenty arms: **6-8 hours**.

Disk: at most one 767 GB copy and one 767 GB fresh load alive at a time, plus
the clone's newly written SSTs. 33 TB free on `/work` as of 2026-09-14.

## Open

- Drain time after a 300 s mixgraph cell is unmeasured. YCSB A at the same
  settings wrote ~816 GB of compaction bytes inside 300 s; mixgraph's put share
  is lower, so the drain is expected to be minutes, but the first arm's drain
  should be watched before the remaining nineteen are queued.
- A one-minute mixgraph smoke run on a throwaway clone should confirm, before
  the twenty arms are queued, that the aggregate line reports roughly
  `avg size: 36 value, 570 scan` (the same figures the June cells printed,
  i.e. the published parameters took effect), that the Gets found-fraction is
  well above the 63 % uniform-coverage figure (which is how the hotspot model
  shows up), and how long the drain takes.
