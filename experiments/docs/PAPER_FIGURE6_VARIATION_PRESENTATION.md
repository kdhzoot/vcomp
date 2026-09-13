# Figure 6: presentation of the observed baseline variation

## Question and scope

Figure 6 (`fig:ch-band`) supports Section 3.2's claim that independently loaded
baseline states form a reasonably consistent target. This is a presentation
preview of existing measurements, not a new experiment or an F2Load comparison.
The active paper figure and prose are preserved.

## Evidence and controls

- Use the same ten baseline loads as `analysis/plot_ch3_band.py`, with its
  established source mapping n01, n02, n03, n04, r01, r02, run3, b01, b02, b03.
  Assert that exactly one validated source matches each tag.
- Input: 1,000 GiB; 24 B keys and 1,000 B values; existing uniform synthetic
  loading configuration. All load binaries have SHA-256
  `8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b`.
  Preserve each validated load's `source_identity_file` reference in provenance
  for its original tested revision and build settings.
- State measurements: total SST count, L3/L4/L5 file counts, final SST bytes,
  and loading WAF, directly from the original `validated.json` files. WAF is
  loading work, not a property of the final state alone.
- YCSB: the frozen baseline subset of the 90-cell fidelity table at
  `artifacts/analysis/ycsb_repeat_fidelity_260911_f05/raw_metrics.tsv`.
  Workloads A–F, 48 threads, 300 s, 50 GiB block cache, automatic compaction,
  fresh SST hardlink clone/private mutable metadata and page-cache reset per
  workload. Source DBs were byte-copied before the campaign.
- YCSB binary SHA-256:
  `20d67c38612cee9a34d9ef7114c81bc8366b2d4cf99c598b642e63f21ab74266`;
  recorded profiler revision: `dbb0a44a65344f263356c507ec09762c8ed81a71`.
  Exact commands and environment remain in the selected result bundles cited
  by the frozen table's provenance. Measurements were made on the same server;
  no new warm-up, settling, hardware configuration, or execution order is added.
- No pilot or benchmark is required: this task only re-renders existing data.
  Fail on missing, duplicate, invalid, or mismatched sources; do not impute.

## Presentation

Two columns of six compact interval plots use actual units, with one dot per
load, a min–max interval, and a mean tick. The columns cover state/loading
metrics and subsequent YCSB throughput. Each row prints its observed range
and its relative width, `(max - min) / mean * 100`. Keep two decimal places so
the final-size variation is not rounded to zero. Each horizontal axis is
independently zoomed; its endpoints are explicit. Vertical offsets only
separate observations and carry no measured quantity.

Use the percentage labels, not the independently zoomed line lengths, to
compare relative variation. This observed min–max range is not a confidence
interval or a statistically established fidelity acceptance threshold.

## Reproduction and review output

```sh
python3 experiments/analysis/plot_figure6_intervals.py
```

Output: `artifacts/analysis/figure6_intervals_260911/`, including a PNG preview,
vector PDF, `measurements.tsv`, `ranges.tsv`, and source hashes/provenance.
The renderer writes the numerical tables before the image. Verify 120 metric
observations, ten per row, against the original sources and inspect layout.
The preview is sized for both paper columns; do not shrink it into one column.
No active TeX, caption, or final publication asset is replaced in this task.

## Recommended overview and validation

The detail view alone makes interval lengths incomparable because its axes are
independently zoomed. A second output, `figure6_spread.{pdf,png}`, therefore
directly plots each observed relative width on a shared 0–10% axis, alongside
the actual min–max in the original display units. Its marks summarize ten
observations each; they are not individual runs or confidence intervals.
This is a variation metric rather than a normalized performance comparison.

Both renderings use the same verified 120 observations. The exact endpoints
and relative widths were recomputed from `measurements.tsv` and checked against
`ranges.tsv`. The final-size width is 0.0396%, displayed as 0.04%; L3 file-count
width is 2.9253%, displayed as 2.93%; YCSB C has a width of 8.2375%, displayed as
8.24%. Keep the distinction between state variation and YCSB execution noise:
one YCSB measurement per loaded state does not isolate their causal effects.
