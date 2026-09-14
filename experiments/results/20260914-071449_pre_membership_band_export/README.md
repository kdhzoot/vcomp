# Historical pre-membership comparison

This is a re-export of existing RocksDB/F2Load results, not a Pebble port
experiment or a new measurement. Each arm is an independently loaded 1000 GiB
input (24-byte keys, 1000-byte values). YCSB A-F used 48 threads, 50 GiB cache
and 300 seconds per full cell. Rows represent independent loaded states;
matching row numbers do not imply a paired experimental design.

- `initial_10_raw.tsv` and `initial_10_summary.tsv`: ten baseline arms versus
  the original F2Load f01-f10 runs, before exact-membership implementation.
- `figure_selected_10_raw.tsv` and `figure_selected_10_summary.tsv`: the ten
  baseline arms versus f01,f02,f03,f04,f05,f09,f11,f12,f13,f15, the later
  figure's selected subset of fifteen PLR-mode loadings. The historical
  `plot_ch3_band25.py` selected this subset by iteratively removing the arm
  farthest from the running median of workload C positive lookup rate.
  f11-f15 used a different loader build with membership implemented but
  disabled; see `experiments/docs/PAPER_F2LOAD_REPEAT_F11_F15_260912.md`.

Both exports retain f01. An older analysis (`plot_baseline_band.py`) excluded
its entire workload campaign because unrelated work on the host interfered
with measurement. That exclusion is not silently applied to either ten-arm
cohort here. All rows are preserved, including f01's C throughput of
1,143,040 ops/s. The input key set was not preserved exactly in PLR mode;
throughput differences must not be attributed solely to tree shape.

Raw values retain the historical TSV's units and rounding. Summary means and
minima/maxima are computed from those values. `compaction_write_per_op` is
bytes written during the YCSB workload, not loading bytes or WAF. Empty
columns preserve the original reporting exclusions: E has no point-lookup
metrics, and C/D/E have no reported compaction-write-per-op metric.

The original table is `../paper_ch3_band25_raw.tsv`. Every exported arm's six
successful full cells, operation counts and throughput values are checked
against its original `ycsb_*` result JSON. `source_manifest.json` records the
source files, hashes and retained arm IDs. Regenerate with:

```
python3 experiments/analysis/export_pre_membership_band.py
```

Loading/write/WAF aggregates are intentionally not copied from the old
`loads.json` records: f01-f05 retain loading fields from an earlier template;
f06-f10 include updated elapsed time but stale phase/I/O fields. Later
f11-f15 records were rebuilt from their own evidence and omit unavailable
write/WAF metrics. These stale fields cannot support a valid ten-arm WAF
comparison without reconstructing the original measurements.
