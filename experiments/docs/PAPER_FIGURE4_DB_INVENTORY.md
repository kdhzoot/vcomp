# Paper Figure 4 Database Inventory

**Current validated campaign:** `paper_ch23_common_260907_f2_completion1`. Numerical entries and source-DB mappings below from earlier runs are historical. Use [PAPER_CHAPTER23_COMMON_RESULTS.md](PAPER_CHAPTER23_COMMON_RESULTS.md) and the current promoted TSVs for Figures 2(b), 4 and 5. All 24 included Figure 5 cells were rerun with the common clean release readrandom driver; historical YCSB values are not mixed. 

**Historical campaign, superseded on 2026-09-07:** `paper_ch23_common_260905_approved_run3`. Numerical entries and source-DB mappings below from earlier runs are historical. Use [PAPER_CHAPTER23_COMMON_RESULTS.md](PAPER_CHAPTER23_COMMON_RESULTS.md) and the current promoted TSVs for Figures 2(b), 4 and 5. All 20 included Figure 5 cells were rerun with the common clean release readrandom driver; historical YCSB values are not mixed. F2Load is deferred and omitted from the current common comparison.

**Audited:** 2026-09-04 UTC after rebuilding the missing F2Load database.

The plotted results and their exact configurations are fixed by
`PAPER_FIGURE4_FIXED_CONFIGURATION.md`.

This inventory maps the 1 TB loading results used by the current paper figure
to preserved database directories for later read-only workloads. A directory
is marked usable only when it exists and contains both `CURRENT` and a
`MANIFEST-*` file. Raw timing evidence is not a substitute for a preserved DB.

| Figure entry | Database directory | State | Loading evidence |
| --- | --- | --- | --- |
| Baseline | `/work/vcomp/exp/paper_clean_vector_wb16_1000gib_260903_run1/baseline_bg48` | Preserved, settled, and reopen-validated | `experiments/artifacts/log_loads/paper_clean_vector_wb16_1000gib_260903_run1/validated_summary.tsv` |
| ADOC | `/work/vcomp/exp/paper_adoc_wb16_1000gib_260903_run1/adoc_1000gib/adoc_on` | Preserved, settled, and reopen-validated | `experiments/artifacts/log_loads/paper_adoc_wb16_1000gib_260903_run1/validated_summary.tsv` |
| BlobDB (GC off) | `/work/vcomp/exp/paper_blobdb_wb16_1000gib_260903_run1/blobdb_1000gib` | Preserved, settled, and reopen-validated | `experiments/artifacts/log_loads/paper_blobdb_wb16_1000gib_260903_run1/blobdb_1000gib/validated_summary.tsv` |
| Flush only | `/work/vcomp/exp/paper_lastcomp_flushonly_wb16_1tib_260902_run1/flushonly_1000gib` | Preserved L0-only state; structurally validated and used as the immutable source of the validated Last-comp checkpoint | `experiments/artifacts/log_loads/paper_lastcomp_flushonly_wb16_1tib_260902_run1/summary.tsv` |
| Last compaction | `/work/vcomp/exp/paper_lastcomp_from_nocomp_wb16_1000gib_260903_run1/lastcomp_1000gib` | Preserved after exactly one full-range compaction and reopen-validated | `experiments/artifacts/log_loads/paper_lastcomp_from_nocomp_wb16_1000gib_260903_run1/lastcomp_1000gib/summary.tsv` |
| Fillseq | `/work/vcomp/exp/paper_clean_fillseq_wb16_1000gib_260903_run1/fillseq_1000gib` | Preserved, settled, and reopen-validated | `experiments/artifacts/log_loads/paper_clean_fillseq_wb16_1000gib_260903_run1/fillseq_1000gib/summary.tsv` |
| Fillseq + 10% overwrite | `/work/vcomp/exp/paper_clean_fillseq_overwrite_wb16_1000gib_260903_run1/fillseq_overwrite_1000gib` | Preserved, settled, and reopen-validated | `experiments/artifacts/log_loads/paper_clean_fillseq_overwrite_wb16_1000gib_260903_run1/fillseq_overwrite_1000gib/summary.tsv` |
| F2Load | `/work/vcomp/exp/motivation_speedup_1kb/vcomp_1000gb_1kb_none_260904_figure4_db_rebuild2` | Preserved same-option reproduction; 766 GiB, 12,769 SSTs, and reopen-validated | `experiments/artifacts/log_loads/paper_figure4_f2load_db_rebuild_260904_run2/motivation_vcomp_speedup_summary.tsv` |

The preserved F2Load database is a reproduction made with the current `vcomp`
commit `f9e281caa65943140385aa99fff1066da4117be8` and `db_bench` SHA-256
`c5964e54fb0afc4779fd17b82f7366bdd8d19be9bd2acae33dd26c77e55019cd`.
It uses the fixed Figure 4 runtime options, including 16 write buffers in the
recorded command (although `fillvirtual` does not consume that option), and
completed in 103 seconds. The plotted 102-second value remains sourced from
the legacy run rather than being silently replaced by this reproduction.

Additional databases:

| Experiment | Database directory | State |
| --- | --- | --- |
| Historical two-buffer No compaction | `/work/vcomp/exp/paper_fig3_1tb_single_260831_0612/no_comp` | Preserved; superseded by the plotted 16-buffer Flush-only result |
| DiffKV buffered auxiliary run | `/work/vcomp/exp/paper_diffkv_1000gib_260901_buffered_run1` | Preserved, but not a promoted Figure 4 result |
| ADOC 128/256 GiB diagnostic | `/work/vcomp/exp/paper_adoc_1000gib_bg48_core48_mem512_buf8_pending128_256_260902_run1/adoc_on` | Stopped at a partial load; excluded from Figure 4 and unusable for read evaluation |

Do not write into any preserved figure DB when running later read workloads.
Use `--use_existing_db=true` and a new evidence directory for every workload.
