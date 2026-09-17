# Experiments behind the paper

Every empirical figure and headline number in the paper, and the result files that
produced it. Paths are relative to `experiments/` unless marked otherwise.

`build_figure_evidence_bundles.py` still writes `paper_evidence/current/`, but its
index names the figure labels of an earlier draft. This file is the map for the
current draft; the bundles it points at are still valid inputs.

## Section 2 — Background

| Paper | Result |
|---|---|
| `bg_loading_scale.pdf`, `bg_loading_breakdown.pdf` | `paper_evidence/current/fig_bg_loading/`, `results/paper_figure2_phase_breakdown.tsv`, `results/paper_background_compaction_breakdown.tsv` |
| `bg_loading_config.pdf` | `results/paper_figure2_loading_waf.tsv`, `results/baseline_config_effect_uniform_260914.tsv` |
| Loading time 1.4 h (1 TB/1 KB), 13.3 h and 46.2 h (8 TB) | `paper_evidence/current/fig_bg_loading/` |
| Write amplification 12.2x at 500 GB, 20.4x at 8 TB | `results/paper_figure2_loading_waf.tsv` |

## Section 3 — Requirements

| Paper | Result |
|---|---|
| `ch_alternatives_{ds_size,throughput,positive_lookup,filter_checks}.pdf` | `paper_evidence/current/fig_bg_alternative_reads/`, `results/paper_ch3_state_comparison.tsv` |
| Loading time 58.4 / 16.3 / 10.1 min (baseline, flush-only, fillseq) | `results/paper_ch3_state_comparison.tsv` |
| Compaction write bytes 812.0 GB vs 440 GB (YCSB A) | `results/paper_ch3_write_comparison.tsv` |
| `ch_config_{throughput,filter_checks}.pdf` and the five-configuration table | `results/paper_ch3_config_uniform_260914.tsv` (verified: filter checks 3.408 / 4.225 / 2.535 / 4.385 / 2.802; throughput 787,509 / 718,609 / 1,934,925 / 759,549 / 827,650) |
| Per-level shape of each configuration | `results/paper_ch3_config_shape.tsv`, `results/lsm_state_by_config.tsv` |

Note: `results/paper_ch3_config_shape.tsv` carries an `E4-64m4` cell, while the paper's
level-multiplier row comes from `E4-256m4` in `results/paper_ch3_config_uniform_260914.tsv`.
The uniform file is the one the paper follows.

## Section 5 — Evaluation

| Paper | Result |
|---|---|
| 5.1 `eval_speedup.pdf`, speedups 16.6--47.9x | `paper_evidence/current/fig_eval_speedup/` |
| 5.1 `exp_vs_flush.pdf`, `exp_vs_comp.pdf` | `results/paper_eval_flush_vs_prepare.tsv`, `results/paper_eval_compaction_vs_virtual.tsv`, `results/paper_eval_loading_breakdown.tsv` |
| 5.2 `exp_fidelity_level_size.pdf`, `exp_fidelity_sst_cnt.pdf` | `results/paper_eval_fidelity_levels.tsv`, `results/paper_eval_fidelity_loads.tsv` |
| 5.2 `exp_fidelity_{throughput,compaction,filter,positive}.pdf` | `results/paper_eval_fidelity_behavior.tsv`, `results/paper_eval_fidelity_summary.tsv`, `results/paper_eval_fidelity_device.tsv` |
| 5.2 baseline run-to-run band (five loads) | `results/fidelity_workload_5runs.tsv`, `results/paper_eval_band_{loads,raw,summary}.tsv` |
| 5.2 `eval_fidelity_yesbit.pdf`, exact membership bitmap | `results/f2load_membership_bitmap_260915.tsv`, `results/keyset_compare_260915.tsv`, `results/keyset_compare_260915_summary.tsv` |
| 5.3 MixGraph | `results/mixgraph_three_way_260915{,_summary}.tsv`, `results/workload_three_way_260915{,_summary}.tsv`, `results/mixgraph_pairs_260914{,_summary}.tsv` |
| 5.3 `eval_flex_ycsbc.pdf`, `eval_flex_ycsba.pdf` | `results/lsm_state_by_config.tsv`, `results/paper_ch3_config_effect.tsv` |
| 5.4 prediction accuracy over 47,824 captured compactions | `results/accuracy_plr_sweep_260916_{jobs,summary}.tsv`, `results/accuracy_plr_pilot_{jobs,summary}.tsv` |
| 5.5 memory overhead, `exp_memory_overhead.pdf`, `exp_memory_breakdown.pdf` | `results/f2load_memprofile_260914{,_components,_phases,_scaling,_timeseries}.tsv`, `results/f2load_memprofile_260914_model.json`, `results/f2load_memprofile_8cells_260915*.tsv` |
| 5.6 `exp_kmv.pdf`, PLR error bound and KMV budget sweep | `results/param_sensitivity_figure.tsv`, `results/param_sweep_260915{,_plot}.tsv`, `results/plr_error_bound_sweep_260916.tsv` |
| 5.7 Pebble port | `Vcomp_for_sharing` repo: `resources/experiments/20260915-pebble_fastload2_1000gib/` (loading, WAF) and `resources/experiments/20260914-044748_pebble_af_tsv/pebble_ycsb_A-F_1000GiB.tsv` (YCSB A--F) |

## Error-bound sweep, September 16

`results/plr_error_bound_sweep_260916.tsv` extends the section 5.6 sweep from
error bound 32 to 4096 and joins it with the YCSB C measurement on each loaded
database. Loading runs live under `artifacts/log_loads/paramsweep_260916*`,
workload runs under `artifacts/log_runs/ps_plr*_260916c`.

Cost falls until the segment count reaches its floor and then stops: PLR memory
3,076 MB to 8.4 MB, PLR fitting 4.73 s to 3.70 s, both saturating at an error
bound of 256. Final database size, SST count, per-level placement and SST size
distribution do not follow the error bound. The `paramsweep_260916nb` cells
repeat the sweep with the exact-membership bitmap disabled and reach the same
conclusion.

## Runs kept per cell

Each sweep cell keeps its own bundle under `results/ps_plr<e>_kmv<k>_<date>/`
with `manifest.json`, `results.json`, `summary.tsv` and the provenance of the
binary and options it ran with.
