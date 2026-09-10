# full: 100 GiB fidelity sweep

12/12 loads and exact scans validated. F2 cardinality mismatches are measurements.

| Case | System | Expected unique | Exact unique | Delta | Status |
|---|---|---:|---:|---:|---|
| s100gib_1024B_unique100 | baseline | 104857600 | 104857600 | 0 | validated |
| s100gib_1024B_unique100 | f2load | 104857600 | 103807652 | -1049948 | validated |
| s100gib_1024B_uniform50 | baseline | 52428800 | 52428800 | 0 | validated |
| s100gib_1024B_uniform50 | f2load | 52428800 | 54053710 | 1624910 | validated |
| s100gib_1024B_zipf99_50 | baseline | 52428800 | 52428800 | 0 | validated |
| s100gib_1024B_zipf99_50 | f2load | 52428800 | 50207714 | -2221086 | validated |
| s100gib_91B_unique100 | baseline | 1179936070 | 1179936070 | 0 | validated |
| s100gib_91B_unique100 | f2load | 1179936070 | 1167764660 | -12171410 | validated |
| s100gib_91B_uniform50 | baseline | 589968035 | 589968035 | 0 | validated |
| s100gib_91B_uniform50 | f2load | 589968035 | 610339435 | 20371400 | validated |
| s100gib_91B_zipf99_50 | baseline | 589968035 | 589968035 | 0 | validated |
| s100gib_91B_zipf99_50 | f2load | 589968035 | 559547993 | -30420042 | validated |

Descriptor and materialization counts are physical per-file totals; iterator count is visible unique.
Physical-entry excess includes cross-level versions and cannot alone identify synthetic collisions.
All twelve loads started concurrently. Timing is operational only; F2 scheduling may affect approximation.
See summary.tsv, results.json, per-case logs, and the frozen manifest for evidence.
