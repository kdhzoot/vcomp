# Cached-zero YCSB alternatives with online compaction

Run `ch3_ycsb_cache50_260911_run2`: 5/8 valid full cells. See results.json for explicit timeout/overrun status.

Selected systems: fillseq, flush_only. 0 valid full cells reused from `None`; original measurements and provenance retained. Excluded systems in the manifest were not run in this campaign.

Each cell started from a fresh hardlink clone of its original DB. All original identities were revalidated. Automatic compaction was enabled; these are initial online-use results, not steady state. Numeric KV adaptation, default YCSB distributions, one repetition. Compare against the new same-workload baseline, not historical clean-binary readrandom results.

Raw artifacts: `/home/smrc/virtual_compaction/vcomp/experiments/artifacts/log_runs/ch3_ycsb_cache50_260911_run2`. Build, options, ordering, provenance and exact commands are included in this bundle.
