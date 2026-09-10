# Baseline loading repeats: YCSB C

Two independently loaded baseline DBs, one 300-second C run each, after separate 3-second pilots. All full cells validated. Same frozen profiler binary, Zipfian distribution, 48 threads, cached-zero metadata, and automatic compaction as the reference.

Original baseline and F2Load C logs are archived under `provenance/reference_ycsb_c/`; they are historical reference measurements, not reruns in this campaign. Exact commands, binary and source identities, small raw logs and configuration accompany `results.json`. See `PLAN.md` for interpretation limits.

Every cell used a fresh hardlink clone. Original DB identities remained unchanged; only validated disposable clones were removed. No manuscript change or commit/push was performed.

## Comparison

Both new full runs completed on 2026-09-08 (campaign completion 12:56:06 UTC).
Measured durations were 300.023 and 300.019 seconds. Both final level layouts
matched their starting sources; compaction read/write, flush write, engine
writes and stall counters were all zero. Known source metadata hashes and SST
inode/size/mtime identities remained unchanged after the complete campaign.

| DB | Throughput (ops/s) | L1 domain coverage (%) | L1 file reads/Get | Mean L1 file read (us) | Mean Get (us) | p99 Get (us) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Original baseline, historical read | 28513 | 88.783878 | 0.920015 | 1066.7830 | 1683.3684 | 4388.16 |
| Baseline repeat 01, new read | 59985 | 25.650492 | 0.277836 | 206.4531 | 800.1672 | 1622.37 |
| Baseline repeat 02, new read | 72440 | 25.653483 | 0.234263 | 113.5967 | 662.6055 | 1271.25 |
| F2Load, historical read | 77766 | 24.924221 | 0.238422 | 129.5651 | 617.2202 | 1248.19 |

The two new baselines are 2.104x and 2.541x the original baseline throughput.
Comparing the same historical F2Load measurement against them gives 1.296x and
1.074x, versus 2.727x against the original. Thus the large original gap is not
reproduced across these independently loaded baseline layouts. Preserve all
three baseline results; these observations do not justify deleting the original
as invalid or claiming that F2Load generally provides a 2.7x read advantage.

Lower L1 range coverage is accompanied by fewer L1 file reads/Get. Per-read
latency also changes substantially, so coverage alone is not a complete causal
explanation. The two repeat L1 range unions have almost identical sizes but
different positions within the Zipfian query domain. CPU-only query-prefix
analysis before these runs predicted different query-weighted coverage; do not
treat geometric coverage as a direct physical-read count or a successful-hit
rate. Compaction did not change the layouts during any of these C measurements.

The three baseline Get-found fractions are similar (60.288%, 60.294%, 60.300%);
the historical F2Load fraction is 62.850%. One read repetition per new layout,
historical reference timing, different consumed query-prefix lengths, and
different F2Load key membership remain limitations. A contemporaneous rerun of
the original baseline/F2Load would be needed to further separate temporal I/O
variation from layout effects; it was not part of these two new runs.

`comparison.json` and `comparison.tsv` contain normalized block cache misses,
all per-level read histograms, definitions and source hashes. The analysis
script is archived under `provenance/analyze_baseline_repeat_ycsb_c.py`.
Reproduction command (outputs are exclusive and may not already exist):

```bash
python3 experiments/analysis/analyze_baseline_repeat_ycsb_c.py \
  --run-id baseline_repeat_ycsb_c_260908_run1
```
