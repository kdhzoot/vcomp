# Motivation Vcomp Compression Summary

500GB loading comparison for vcomp motivation runs.

## Result Table

| KV config | Compression | Baseline elapsed | Vcomp elapsed | Speedup | Baseline DB | Vcomp DB | Size diff |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1KB KV (`key=24,value=1000`) | none | 1,820s | 29s | 62.76x | 348.81 GiB | 340.84 GiB | -2.29% |
| 1KB KV (`key=24,value=1000`) | snappy | 2,485s | 31s | 80.16x | 192.58 GiB | 185.99 GiB | -3.42% |
| 91B KV (`key=48,value=43`) | none | 9,842s | 211s | 46.64x | 372.56 GiB | 365.13 GiB | -1.99% |
| 91B KV (`key=48,value=43`) | snappy | NA | 218s | NA | NA | 138.81 GiB | NA |

## Notes

- 91B snappy baseline with `key_size=48,value_size=43` has not been run yet.
- The old `kv91_snappy` motivation matrix used `key_size=24,value_size=91`, so it is not a valid baseline for the true 91B KV row.
- Vcomp snappy applies compression during Phase 2 SST materialization. Phase 1 virtual compaction still uses the uncompressed virtual size model.

## Raw Sources

| Source | Path |
| --- | --- |
| 1KB baseline matrix | `log_loads/motivation_kv_compression_260604_kv500_matrix/summary.tsv` |
| 1KB vcomp run | `log_loads/260606_170239_vcomp_500gb_1kb_compression/summary.tsv` |
| 91B baseline no-compress | `log_loads/exp_260604_exp91b_baseline/scaling_91b_none/scaling_91b_summary.tsv` |
| 91B vcomp run | `log_loads/260606_172044_vcomp_500gb_91b_compression/summary.tsv` |

Machine-readable summary:

```text
log_loads/motivation_vcomp_compression_summary.tsv
```
