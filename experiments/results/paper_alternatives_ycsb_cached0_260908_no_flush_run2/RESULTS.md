# Cached-zero YCSB alternatives with online compaction

Run `paper_alternatives_ycsb_cached0_260908_no_flush_run2`: 30/30 valid full cells. See results.json for explicit timeout/overrun status.

Selected systems: baseline, last_comp, fillseq, fillseq_ow, f2load. 1 valid full cells reused from `paper_alternatives_ycsb_cached0_260908_run1`; original measurements and provenance retained. Excluded systems in the manifest were not run in this campaign.

Each cell started from a fresh hardlink clone of its original DB. All original identities were revalidated. Automatic compaction was enabled; these are initial online-use results, not steady state. Numeric KV adaptation, default YCSB distributions, one repetition. Compare against the new same-workload baseline, not historical clean-binary readrandom results.

Raw artifacts: `/home/smrc/virtual_compaction/vcomp/experiments/artifacts/log_runs/paper_alternatives_ycsb_cached0_260908_no_flush_run2`. Build, options, ordering, provenance and exact commands are included in this bundle.
