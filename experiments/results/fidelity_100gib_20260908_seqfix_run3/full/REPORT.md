# full: 100 GiB fidelity sweep

12/12 loads and exact scans validated. F2 cardinality mismatches are measurements.

| Case | System | Expected unique | Exact unique | Delta | Status |
|---|---|---:|---:|---:|---|
| s100gib_1024B_unique100 | baseline | 104857600 | 104857600 | 0 | validated |
| s100gib_1024B_unique100 | f2load | 104857600 | 84487007 | -20370593 | validated |
| s100gib_1024B_uniform50 | baseline | 52428800 | 52428800 | 0 | validated |
| s100gib_1024B_uniform50 | f2load | 52428800 | 53609965 | 1181165 | validated |
| s100gib_1024B_zipf99_50 | baseline | 52428800 | 52428800 | 0 | validated |
| s100gib_1024B_zipf99_50 | f2load | 52428800 | 50141610 | -2287190 | validated |
| s100gib_91B_unique100 | baseline | 1179936070 | 1179936070 | 0 | validated |
| s100gib_91B_unique100 | f2load | 1179936070 | 949616388 | -230319682 | validated |
| s100gib_91B_uniform50 | baseline | 589968035 | 589968035 | 0 | validated |
| s100gib_91B_uniform50 | f2load | 589968035 | 587580583 | -2387452 | validated |
| s100gib_91B_zipf99_50 | baseline | 589968035 | 589968035 | 0 | validated |
| s100gib_91B_zipf99_50 | f2load | 589968035 | 558329556 | -31638479 | validated |

Descriptor and materialization counts are physical per-file totals; iterator count is visible unique.
Physical-entry excess includes cross-level versions and cannot alone identify synthetic collisions.
All twelve loads started concurrently. Timing is operational only; F2 scheduling may affect approximation.
See summary.tsv, results.json, per-case logs, and the frozen manifest for evidence.
