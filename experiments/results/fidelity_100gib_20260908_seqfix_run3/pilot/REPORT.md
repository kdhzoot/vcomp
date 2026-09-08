# pilot: 1 GiB fidelity sweep

12/12 loads and exact scans validated. F2 cardinality mismatches are measurements.

| Case | System | Expected unique | Exact unique | Delta | Status |
|---|---|---:|---:|---:|---|
| s1gib_1024B_unique100 | baseline | 1048576 | 1048576 | 0 | validated |
| s1gib_1024B_unique100 | f2load | 1048576 | 1048563 | -13 | validated |
| s1gib_1024B_uniform50 | baseline | 524288 | 524288 | 0 | validated |
| s1gib_1024B_uniform50 | f2load | 524288 | 553312 | 29024 | validated |
| s1gib_1024B_zipf99_50 | baseline | 524288 | 524288 | 0 | validated |
| s1gib_1024B_zipf99_50 | f2load | 524288 | 549222 | 24934 | validated |
| s1gib_91B_unique100 | baseline | 11799360 | 11799360 | 0 | validated |
| s1gib_91B_unique100 | f2load | 11799360 | 11785200 | -14160 | validated |
| s1gib_91B_uniform50 | baseline | 5899680 | 5899680 | 0 | validated |
| s1gib_91B_uniform50 | f2load | 5899680 | 5967212 | 67532 | validated |
| s1gib_91B_zipf99_50 | baseline | 5899680 | 5899680 | 0 | validated |
| s1gib_91B_zipf99_50 | f2load | 5899680 | 5967269 | 67589 | validated |

Descriptor and materialization counts are physical per-file totals; iterator count is visible unique.
Physical-entry excess includes cross-level versions and cannot alone identify synthetic collisions.
All twelve loads started concurrently. Timing is operational only; F2 scheduling may affect approximation.
See summary.tsv, results.json, per-case logs, and the frozen manifest for evidence.
