# Published experiment results

This directory is tracked by Git. It contains measurements and selected
evidence, so a fresh clone can inspect results without the original DBs or
`experiments/artifacts/`. The full collection and its Git policy are described
in [the experiment guide](../README.md).

## Current Chapter 2/3 comparison

Use [paper_ch23_common_260907_f2_completion1/RESULTS.md](paper_ch23_common_260907_f2_completion1/RESULTS.md)
for the latest combined campaign: eight load states and twenty-four read cells.
It incorporates the earlier controls and adds the completed F2Load load and
four reads. F2Load loading includes both materialization and timed physical
completion; key-membership and single-repetition limitations remain part of
the interpretation.

| Contents | File in the current bundle |
| --- | --- |
| Loading measurements | [loads.json](paper_ch23_common_260907_f2_completion1/loads.json) |
| Read measurements | [reads.json](paper_ch23_common_260907_f2_completion1/reads.json) |
| Configuration and binary hashes | [manifest.json](paper_ch23_common_260907_f2_completion1/manifest.json) |
| Completion protocol | [f2_completion.json](paper_ch23_common_260907_f2_completion1/f2_completion.json) |
| Final validation | [FINAL_AUDIT.json](paper_ch23_common_260907_f2_completion1/FINAL_AUDIT.json) |
| Shared loading/WAF table | [paper_figure2_loading_waf.tsv](paper_ch23_common_260907_f2_completion1/paper_figure2_loading_waf.tsv) |
| Loading alternatives | [paper_figure4_loading_time_1tb_single.tsv](paper_ch23_common_260907_f2_completion1/paper_figure4_loading_time_1tb_single.tsv) |
| Read/cache matrix | [paper_figure4_uniform_read_cache_5m_single.tsv](paper_ch23_common_260907_f2_completion1/paper_figure4_uniform_read_cache_5m_single.tsv) |

The filenames preserve the figure numbering at collection time. Numerical
records, audit statements, and archived plotting sources describe that
promotion; later manuscript styling can differ. `analysis_sources/` contains
the archived analysis programs. `provenance/` contains selected execution
commands and source/build records, with an inventory of copied files and hashes.
The original paths recorded inside these files intentionally remain unchanged.

## Earlier controls and other results

- [paper_ch23_common_260905_approved_run3/RESULTS.md](paper_ch23_common_260905_approved_run3/RESULTS.md)
  is the preceding seven-load/twenty-read campaign reused by the latest bundle.
- [paper_figure2_phase_breakdown.tsv](paper_figure2_phase_breakdown.tsv) and
  [paper_background_compaction_breakdown.tsv](paper_background_compaction_breakdown.tsv)
  retain historical instrumentation measurements. Their sizes, builds, and
  buffer configurations must not be equated with the new common campaign.
- Other top-level TSV/CSV files and result subdirectories are preserved
  historical measurements or plotting inputs. Consult their source fields and
  the corresponding [experiment documents](../docs/) before combining them.
- [../paper_evidence/current/](../paper_evidence/current/) is an older
  figure-oriented snapshot, not the authoritative latest campaign. Git includes
  its displayed values, source tables, reproduction sources, and manifests;
  copied raw logs and rendered assets named in its manifests remain local.

For additional measurements, see [RESULTS.md](../docs/RESULTS.md). New result
bundles follow `results/<run-id>/`; retain units, exact settings, repetition
count, tested revision, binary identity, and validation status alongside values.
