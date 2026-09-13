# F2Load f11–f15 sequential loading and YCSB campaign

The user authorized five additional independent F2Load loadings on 2026-09-12,
with each loading followed immediately by YCSB A–F before the next loading.
All five loaded source databases must remain on disk after measurement.

Each arm uses the preceding f06–f10 settings: 1000 GiB logical input, 24-byte
keys and 1000-byte values, loading seed 12345678, eight Phase-1 shards and 48
materialization workers. Loading executes
`fillvirtual,flush,compact0,waitforcompaction,stats,levelstats` without a separate
clean-RocksDB settle phase. YCSB uses the existing frozen profiler, 48 threads,
a 50 GiB cache, and 300 seconds per workload. The existing runner makes a fresh
private clone for each workload, resets the page cache, and verifies the loaded
source identity. Successful temporary workload clones are disposable; the five
loaded source DBs are retained by `--keep-db`.

The current loader differs from f06–f10. Its SHA-256 is
`5600f604c11fe0f21dca7dc91a80aa5c5123b7fd9249affabbfa30433430638e`,
on vcomp commit `260182ae8fa44dc98ae091461b278ea581289a0f` with a local
`tools/db_bench_tool.cc` change adding exact-membership materialization.
The new option remains at its runtime-confirmed default, false. The previous
loader hash was `3d71cc8f84b6a79517122af2d5f027bff027442c2e455561c78af76b186aff0e`.
These measurements therefore carry a distinct loader-build provenance;
binary-identical performance is not assumed. The executable and source patch
are archived before execution. A fresh 16 GiB loading and short read-only reopen
must pass before starting f11. The runner then also performs its usual C/A/E
pilots on each 1000 GiB source.

The YCSB profiler remains commit `dbb0a44a65344f263356c507ec09762c8ed81a71`,
binary SHA-256 `20d67c38612cee9a34d9ef7114c81bc8366b2d4cf99c598b642e63f21ab74266`.
All new destination paths were absent at preflight; `/work` had approximately
20.9 TiB free and no benchmark was active.

Run from the vcomp repository:

```bash
python3 -u experiments/scripts/paper/run_f2load_band_chain.py \
  --arms f11,f12,f13,f14,f15 --date 260912 \
  --db-root /work/vcomp/exp/f2band_260912 --keep-db \
  --load-binary experiments/artifacts/log_loads/f2band_260912_f11_f15_keep/db_bench
```

Source DBs: `/work/vcomp/exp/f2band_260912/f11` through `f15`.
Control manifest, executable, provenance, qualification, and chain log:
`experiments/artifacts/log_loads/f2band_260912_f11_f15_keep/`.
Per-arm loading evidence: `experiments/artifacts/log_loads/f2band_260912/fNN/`.
Per-arm YCSB evidence: `experiments/artifacts/log_runs/ycsb_f2band_fNN_260912/`.
The existing runner publishes small result bundles under
`experiments/results/ycsb_f2band_fNN_260912/`.

The chain must stop on any load failure, source identity change, or campaign
that lacks six unique, successful, fully instrumented A–F measurements.
The f2load entry in each new loading bundle is rebuilt from its own evidence,
without stale template settle phases or validation claims. Completion means
five preserved source identities and 30 valid full YCSB cells; pilot cells are
excluded from the full-cell count. Raw process diskstats and iostat evidence
retain the data needed for SSD read latency analysis.

The previous five sequential arms took about 2 hours 40 minutes in total.
That is an estimate for this queue, not a completion claim.

The 16 GiB qualification passed: loading took 6.03 seconds and read-only reopen
plus measurement took 3.58 seconds. The read log records successful finds and
nonzero returned value bytes; the source identity remained unchanged. The
qualification DB is retained at
`/work/vcomp/exp/f2band_260912_qualification_16g`.
The completion validator accepted f10's real evidence and rejected simulated
duration-overrun and duplicate-workload records.

The detached sequential queue started at 2026-09-12 15:40:05 UTC, supervised by
PID 2577855. `status.json` in the control directory records the chain exit and
final verification of all five retained DBs. Per-arm campaign status files
record the current workload. Actual completion remains subject to those files;
the estimate at launch is approximately 18:21 UTC.
