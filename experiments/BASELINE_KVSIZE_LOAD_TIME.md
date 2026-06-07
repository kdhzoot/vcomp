# Baseline Load Time by KV Size

Baseline loading results with `threads=1`, `memtablerep=vector`, and
`compression_type=none`.

| KV | Key / Value | DB size | Status | Elapsed | fillrandom |
| --- | --- | ---: | --- | ---: | ---: |
| 1KB | 24B / 1000B | 500GB | ok | 2,245s (0.62h) | 2,220.101s |
| 1KB | 24B / 1000B | 1TB | ok | 4,889s (1.36h) | 4,862.919s |
| 1KB | 24B / 1000B | 2TB | ok | 10,203s (2.83h) | 10,174.392s |
| 1KB | 24B / 1000B | 4TB | ok | 21,406s (5.95h) | 21,381.248s |
| 1KB | 24B / 1000B | 8TB | ok | 47,704s (13.25h) | 47,667.155s |
| 91B | 48B / 43B | 500GB | ok | 9,842s (2.73h) | 9,750.197s |
| 91B | 48B / 43B | 1TB | ok | 20,151s (5.60h) | 20,091.197s |
| 91B | 48B / 43B | 2TB | ok_recovered | 40,448s (11.24h) | 40,357.810s |
| 91B | 48B / 43B | 4TB | ok | 85,269s (23.69h) | 85,157.895s |

The 91B 8TB run was stopped before completion, so it is not included.

Raw TSV:

```text
eval-vcomp/log_loads/baseline_kvsize_load_time_summary.tsv
```

Source summaries:

```text
eval-vcomp/log_loads/motivation_load_scaling_260602_cleanrocksdb_motivation/summary.tsv
eval-vcomp/log_loads/exp_260604_exp91b_baseline/scaling_91b_none/scaling_91b_summary.tsv
```
