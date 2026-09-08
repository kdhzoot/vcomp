# Virtual Compaction Experiments

This directory contains the runners, analysis code, documentation, and curated
results used to compare this virtual-compaction implementation with baseline
RocksDB.

## Layout

- `scripts/`: workload runners grouped by load, read, trace, profiling, and
  paper campaigns.
- `analysis/`: parsers, aggregation scripts, and plotting tools.
- `docs/`: experiment plans, runbooks, and historical reports.
- `results/<run-id>/`: validated tables, configuration, verification reports,
  and selected provenance tracked by Git. See [the result index](results/README.md).
- `artifacts/`: raw logs and per-run outputs. This directory is ignored by Git.
- `paper_evidence/`: historical figure-oriented evidence snapshots. Their
  `current/` name does not imply that they match the latest manuscript or run;
  copied raw logs and rendered assets remain local.
- `lib/common.sh`: shared repository, binary, database, and artifact locations.

Large databases and traces remain under `/work/vcomp` by default. Generic
runners support these path overrides; the examples assume the repository root:

```bash
export VCOMP_DB_BENCH="$PWD/db_bench"
export BASELINE_DB_BENCH="$PWD/../rocksdb/db_bench"
export VCOMP_PROF_DB_BENCH="$PWD/../vcomp-prof/db_bench"
export VCOMP_DB_ROOT=/work/vcomp
export VCOMP_ARTIFACT_ROOT="$PWD/experiments/artifacts"
```

The runners locate shared files independently of the working directory.
Example commands from the repository root:

```bash
MODE=vcomp TARGET_DB_GB=1 DB_ROOT=/work/vcomp/exp \
  experiments/scripts/load/load.sh

WORKLOAD=readrandom DB_DIR=/work/vcomp/vcomp_1000gb DB_SIZE_GB=1000 \
  experiments/scripts/read/run.sh
```

## Publishing results

Use the same run ID in raw output directories, the result bundle, and the
experiment report. A new measurement gets a new ID; a published historical
measurement is not rewritten to describe a later build or configuration.

Keep the following together under `results/<run-id>/`:

- A short report (`RESULTS.md` or `README.md`) stating scope, units, repetitions,
  conclusions, and limitations.
- Machine-readable measurements (TSV/CSV/JSON) and the configuration manifest.
- Validation results, tested source revisions, binary hashes, and exact
  workload commands. Original absolute paths are provenance, not portable
  locations that must exist when reading the result.
- Relevant plotting sources/figures, if generated, and selected small source
  evidence. Preserve archived analysis sources as the version used at promotion.

Commit these bundles with their runners and analysis changes. Keep full raw
logs, high-frequency monitoring, executables, SST/DB files, and large traces
outside Git. Existing top-level result files remain supported historical inputs
or convenience copies; the result index identifies the authoritative bundles.

On the server retaining the original artifacts of a validated Chapter 2/3
campaign, archive its commands and source provenance without running a workload
or modifying the paper:

```bash
VCOMP_RUN_ID=paper_ch23_common_260907_f2_completion1
python3 experiments/analysis/archive_result_provenance.py \
  --run-root "experiments/artifacts/log_loads/$VCOMP_RUN_ID/full" \
  --result-dir "experiments/results/$VCOMP_RUN_ID"
```

## Working on another server

Clone the implementation, instructions, scripts, and published results together:

```bash
git clone --branch main-vcomp git@github.com:kdhzoot/vcomp.git
cd vcomp
```

The repository-level `AGENTS.md` carries the working rules. No original Codex
session or raw database copy is needed to inspect the published measurements.
The original server uses the SSH host alias `github-kdh`; the clone URL above
uses the standard GitHub host and the new server's own authentication.

For example, regenerate the latest loading/read graphs from committed tables
without the original artifacts or sibling manuscript:

```bash
VCOMP_RESULT_BUNDLE=experiments/results/paper_ch23_common_260907_f2_completion1
python3 experiments/analysis/plot_paper_figure4_uniform_read_cache.py \
  --loading-tsv "$VCOMP_RESULT_BUNDLE/paper_figure4_loading_time_1tb_single.tsv" \
  --read-tsv "$VCOMP_RESULT_BUNDLE/paper_figure4_uniform_read_cache_5m_single.tsv" \
  --output-dir experiments/artifacts/figure_preview
```

This uses the current plotting style and emits native TikZ plus PDF/PNG
previews. Archived analysis sources retain the earlier promotion's style.
To verify the copied execution evidence using only files in the clone:

```bash
python3 experiments/analysis/archive_result_provenance.py \
  --result-dir "$VCOMP_RESULT_BUNDLE" --verify-only
```

Generic runners accept the path overrides shown above. They require separately
built baseline/profiler executables when those implementations are selected.
`./make.sh` builds F2Load with GCC/G++ 11; Python analysis scripts may additionally
need NumPy/Matplotlib, and native figure export needs LaTeX/TikZ and Ghostscript.

The historical Chapter 2/3 campaign under `scripts/paper/` is frozen: it expects
the qualified `../rocksdb-f455-release/db_bench`, exact binary hashes, and
`/work/vcomp/exp` storage. It does not inherit every `common.sh` path override.
Its checkpoint protocol needs source and destination on the same filesystem.
The Figure 2 four-cell profiler runner also retains `/work` free-space checks,
an `md0` device default, and a high open-file limit. These runners document the
measured configuration; qualify a new host/build with a new run ID and pilot
before making fresh comparisons. Resume/completion tools reference the original
campaign and DB identities and are not fresh-clone entry points.

Manuscript-updating scripts (`ch23_paper_update.py`, `ch23_f2_paper_update.py`,
and the promotion controller) also modify the sibling `paper/` tree. Use the
published tables to inspect results; invoke manuscript updates only as part of
an explicitly requested paper change after reading that tree's instructions.
