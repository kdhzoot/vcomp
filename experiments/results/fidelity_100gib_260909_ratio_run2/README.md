# 100 GiB fidelity sweep with the dedup-ratio KMV estimate

Completed 2026-09-09 12:20:44 UTC (`full/COMPLETED`). One full 12-load matrix
after its 1 GiB pilot, run with `--global-unique-keys` and the binary recorded
in `manifest.json` (`f2.diff` is the vcomp working-tree diff at launch; the
clean release tree was unmodified). Run1 of the same day failed at the pilot
settle step with exit 127 before loading anything and is not preserved.

The estimator change under test is in `db/virtual_compaction/virtual_sst.cc`:
`EstimateKMVUnionEntries` and `EstimateKMVUnionEntriesForRange` now scale the
summed input count by the sampled dedup ratio `sampled_unique / sampled_entries`
instead of rescaling the sample count by `1/theta` and clamping from above.
`ThetaFraction` is gone.

## Results

Every baseline case matches its trace's verified unique count. F2Load visible
unique counts, with the run3 (2026-09-08, before this change) values alongside:

| Case | Expected | F2Load visible | Error | Run3 error |
|---|---:|---:|---:|---:|
| 1024 B unique100 | 104,857,600 | 103,807,652 | -1.001% | -3.592% |
| 1024 B uniform50 | 52,428,800 | 54,053,710 | +3.099% | +17.837% |
| 1024 B zipf99_50 | 52,428,800 | 50,207,714 | -4.236% | +5.857% |
| 91 B unique100 | 1,179,936,070 | 1,167,764,660 | -1.032% | -3.769% |
| 91 B uniform50 | 589,968,035 | 610,339,435 | +3.453% | +16.893% |
| 91 B zipf99_50 | 589,968,035 | 559,547,993 | -5.156% | +4.802% |

For the unique100 cases `summary.tsv` separates the remaining error:
`stage1_live_descriptor_entries` is within 0.022% / 0.026% of the expected
count, so estimation is no longer the source; the shortfall is
`dropped_live_entries` (1,026,643 and 11,860,234), materialization ranges that
ran out of integer keys under `--vcomp_global_unique_keys`. The 50%-unique
cases carry `sst_entry_sum` above the visible count on both systems because
older versions survive in deeper levels; the F2Load excess there is a
descriptor-count error, not a scan artifact.

All twelve loads ran concurrently and shared the array with an unrelated YCSB
campaign, so the recorded elapsed times are operational only.

`full/results.json`, `full/summary.tsv` and `full/REPORT.md` are the runner's
own outputs; `process_status.json`, `runtime_all.json` and `environment.txt`
record the execution. Per-case logs and DBs remain under
`experiments/artifacts/` and `/work/vcomp/exp/` on the measurement host.
