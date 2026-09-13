# Chapter 3 comparison with sparse fillseq

`plot_metrics.tsv` contains baseline, flush_only, and fillseq for 300-second YCSB A and C. The baseline remains n01, as in the existing chapter-3 figure. Only fillseq is replaced with the newly loaded sparse dataset. Command options and profiler hashes match for every compared workload.

`write_metrics.tsv` contains the separate fixed-count YCSB A comparison. Both completed runs issue 210,000,000 operations, including 105,003,686 writes, then wait for compaction. Final pending compaction bytes are zero. Baseline comes from the historical fixed-A experiment, and fillseq comes from the new sparse fixed-A measurement. Throughput and latency describe workload execution; compaction counters include the subsequent drain.

The figure label is `fillseq`; `source_system=fillseq_sparse` identifies its experimental provenance. Flush-only timed A failed with a timeout, so metric cells are blank. It has no completed fixed-count A experiment; that row is `not_run`, with blank metrics. Missing values must not be plotted as zero. `source_run` and `log_dir` identify each measured row.

Filter checks are `(bloom.filter.useful + bloom.filter.full.positive) / number.keys.read`. Positive lookups are successful Get calls divided by all Get requests, expressed as a percentage. Device I/O columns are md0 diskstats deltas over the process window. See provenance.json for full definitions and source hashes.

Reproduce with `python3 experiments/analysis/export_ch3_sparse_comparison.py --out NEW_OUTPUT_DIRECTORY` from vcomp/. The exporter creates a new directory and never overwrites earlier measurements.
