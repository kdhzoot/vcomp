# Common Chapter 2/3 campaign results

Run: `paper_ch23_common_260905_approved_run3`. 7 load states and 20 read cells validated.

F2Load remains deferred and is omitted from the new common comparison; its old results are not substituted.

The Figure 5 DBs are the exact sources of the corresponding new Figure 4 loading bars. All readers and conventional load alternatives use clean release SHA-256 `8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b`. Readers use uniform readrandom and the common generic read-only path.

| Method | Seconds | Source |
| --- | ---: | --- |
| Baseline | 3501.417 | `/home/smrc/virtual_compaction/vcomp/experiments/artifacts/log_loads/paper_ch23_common_260905_approved_run3/full/baseline_1kb/validated.json` |
| ADOC | 4122.000 | `experiments/artifacts/log_loads/paper_adoc_wb16_1000gib_260903_run1/validated_summary.tsv` |
| BlobDB (GC off) | 1712.000 | `experiments/artifacts/log_loads/paper_blobdb_wb16_1000gib_260903_run1/blobdb_1000gib/validated_summary.tsv` |
| Flush only | 979.726 | `/home/smrc/virtual_compaction/vcomp/experiments/artifacts/log_loads/paper_ch23_common_260905_approved_run3/full/flush_only_1kb/validated.json` |
| Last compaction | 5122.548 | `/home/smrc/virtual_compaction/vcomp/experiments/artifacts/log_loads/paper_ch23_common_260905_approved_run3/full/last_comp_1kb/validated.json` |
| Fillseq | 962.644 | `/home/smrc/virtual_compaction/vcomp/experiments/artifacts/log_loads/paper_ch23_common_260905_approved_run3/full/fillseq_1kb/validated.json` |
| Fillseq + 10% overwrite | 1314.603 | `/home/smrc/virtual_compaction/vcomp/experiments/artifacts/log_loads/paper_ch23_common_260905_approved_run3/full/fillseq_ow_1kb/validated.json` |

Figure 2(a) historical bars/lines and Figure 2(c) historical instrumentation remain explicitly separate. Figure 2(a) overlays the new 1,000-GiB baseline times as unconnected diamonds; Figure 2(b) shares its two 1-KB cells with Figure 4.

Generated prose numbers: `paper/tex/ch23_measurements.tex`. Raw runs are preserved under `/home/smrc/virtual_compaction/vcomp/experiments/artifacts/log_loads/paper_ch23_common_260905_approved_run3/full`. Promotion backup and build log are in that directory.

## Complete loading matrix (new measurements)

| State | Seconds | Minutes | SST WAF |
| --- | ---: | ---: | ---: |
| baseline_1kb | 3501.417 | 58.357 | 12.7137 |
| baseline_91b | 11426.155 | 190.436 | 15.3037 |
| fillseq_1kb | 962.644 | 16.044 | 1.0162 |
| fillseq_ow_1kb | 1314.603 | 21.910 | 1.7564 |
| flush_only_1kb | 979.726 | 16.329 | 1.0165 |
| flush_only_91b | 8711.426 | 145.190 | 1.0868 |
| last_comp_1kb | 5122.548 | 85.376 | 1.6587 |

All seven source DB identities matched their saved metadata and SST identities before promotion. The twenty read summaries were reparsed from raw aggregate Get/filter counters. Figure 2(b) and Figure 4 share the exact 1-KB Conventional/Flush-only timing values; every Figure 5 row references its corresponding Figure 4 loading DB.

## Read results (ops/s)

| State | Cached, 1 B | Cached, 50 GiB | Pinned, 1 B | Pinned, 50 GiB |
| --- | ---: | ---: | ---: | ---: |
| baseline | 45751 | 757310 | 685109 | 826749 |
| flush_only | 39 | 1550 | 3642 | 3690 |
| last_comp | 160800 | 902532 | 692628 | 940142 |
| fillseq | 121787 | 568807 | 438079 | 587518 |
| fillseq_ow | 73241 | 536377 | 438941 | 568757 |

One 300-second measurement per cell, 48 threads. Pinned metadata resides outside the LRU cache budget. Plot ratios use the Baseline from the same column. These are descriptive results, not confidence intervals or repeated-run averages.

## Paper validation and reproduction

The final PDF compiles with no unresolved references or citations. Figures 2, 4 and 5 were visually inspected on the rendered paper pages; clipped filter bars retain actual values, positive percentages use the right axis/diamonds, and normalized throughput uses a red reference line at 1. The existing bibliography overfull box (1.21742 pt) remains unrelated to this update.

The scope-aware promotion command is `python3 experiments/analysis/promote_paper_ch23_common.py --run-root experiments/artifacts/log_loads/paper_ch23_common_260905_approved_run3/full --exclude-f2load`. The first promotion and subsequent focused manuscript correction are recorded in the preserved staging tree and build logs; analysis sources are archived with this bundle. The same command deliberately refuses to overwrite an already promoted campaign.
