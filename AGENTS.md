# F2Load Repository Guide

This repository contains the F2Load implementation and its experiment harness.
These instructions travel with Git so a fresh clone can use the same working
conventions without the original server's workspace-level instructions.

## Source and repository boundaries

- Read `README.md` for the current implementation specification. Its current
  state takes precedence over dated historical notes.
- Core code is in `db/virtual_compaction/`: `virtual_sst.{h,cc}` implements
  vSST descriptors, KMV estimation, merging/splitting, and materialization;
  `plr_model.{h,cc}` implements learned rank models.
- Build with `./make.sh`. It cleans and rebuilds with GCC/G++ 11; do not run it
  while an experiment depends on the current executable.
- The parent workspace may contain separate `rocksdb/` (baseline),
  `vcomp-prof/` (instrumented implementation), `himeta/`, and `paper/` trees.
  They are not included in this repository. Keep baseline, implementation,
  and profiler changes/results distinct.
- Inspect this repository's Git status before editing. Preserve unrelated
  changes and experiment artifacts. Never clean up databases or build outputs
  merely to obtain a clean worktree.
- For sibling manuscript work, read its `AGENTS.md` before editing it.

## Experiments and results

- Use `experiments/README.md` for the directory convention, entry points,
  configuration, and server handoff. Use `experiments/results/README.md` to
  locate published measurements and distinguish current from historical runs.
- Put runners under `experiments/scripts/`, analysis under
  `experiments/analysis/`, shared helpers under `experiments/lib/`, and plans
  and explanations under `experiments/docs/`.
- Commit validated results under `experiments/results/<run-id>/` together
  with configuration, tested source revisions and binary hashes, exact
  commands, validation status, units, and interpretation limits. Keep measured
  values and their original provenance unchanged when publishing them.
- `experiments/artifacts/` holds local raw outputs. Databases and large traces
  live outside Git, normally under `/work/vcomp`. Copy selected small evidence
  into a result bundle when needed; do not force-add the whole raw directory.
- Use a new run ID for new measurements. Do not overwrite historical bundles
  or present a rebuilt executable as the previously measured binary.
- Generic shell runners use `experiments/lib/common.sh` for configurable
  paths. Frozen paper campaigns additionally enforce their documented binary,
  host, and completion conditions; preserve those checks.
- Start large workloads only when requested. For a new build or host, qualify
  the configuration with a focused pilot before a full campaign. If applicable,
  follow the sibling paper's experiment workflow as well.
- Keep long-term rules here; put transient task status, commits, and numbers in
  experiment plans, manifests, and result reports.

## Agent coordination

- Delegate independent inspection, log analysis, or validation when parallel
  work can improve speed or quality.
- Give agents explicit file ownership for edits and integrate their results
  before committing. Do not run competing storage benchmarks in parallel.
- Verify changes in proportion to risk; do not launch an experiment merely to
  validate documentation or publish existing measurements.
