# Common Chapter 2/3 campaign results

Run: `paper_ch23_common_260907_f2_completion1`. 8 load states and 24 read cells validated.

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
| F2Load | 134.731 | `/home/smrc/virtual_compaction/vcomp/experiments/artifacts/log_loads/paper_ch23_common_260907_f2_completion1/full/f2load_1kb/validated.json` |

Figure 2(a) historical bars/lines and Figure 2(c) historical instrumentation remain explicitly separate. Figure 2(a) overlays the new 1,000-GiB baseline times as unconnected diamonds; Figure 2(b) shares its two 1-KB cells with Figure 4.

Generated prose numbers: `paper/tex/ch23_measurements.tex`. Raw runs are preserved under `/home/smrc/virtual_compaction/vcomp/experiments/artifacts/log_loads/paper_ch23_common_260907_f2_completion1/full`. Promotion backup and build log are in that directory.

## F2Load completion protocol

Only F2Load was newly measured in this extension; the previous seven loads and twenty reads are reused byte-for-byte from their validated summaries. The four new F2Load reads use its new completed source DB. The combined campaign contains eight load states and twenty-four read cells.

F2Load took 96.957026 s for initial loading/materialization plus 37.773671 s for reopening with the common clean binary and automatic compaction, for 134.730697 s total (25.99x baseline speedup). Estimated pending bytes fell from 17,255,557,663 to zero. The completion phase read 21.643 GiB and wrote 19.553 GiB of actual SST data. Both phases are included in loading time and device I/O; checkpoint preparation and the between-phase cache reset are excluded. No new keys are inserted during completion. The first materialized DB is preserved separately.

The frozen F2Load SHA-256 is `c5964e54fb0afc4779fd17b82f7366bdd8d19be9bd2acae33dd26c77e55019cd`; completion uses the same clean SHA above. F2Load retains format 6, whereas conventional loaders use format 7. F2Load was measured later than the controls and all cells have one repetition. Its approximate key membership is reported in Figure 5(b); throughput ratios do not isolate layout effects from key-set differences.

| Read configuration | F2Load ops/s | / Baseline | Successful lookups (%) |
| --- | ---: | ---: | ---: |
| A_cache_zero | 77550 | 1.695 | 61.818 |
| B_cache_5pct | 829354 | 1.095 | 61.809 |
| C_pinned_zero | 711376 | 1.038 | 61.812 |
| D_pinned_5pct | 901866 | 1.091 | 61.809 |

Detailed machine-readable protocol: `f2_completion.json` in the result bundle. Analysis sources and hashes are archived alongside it.
