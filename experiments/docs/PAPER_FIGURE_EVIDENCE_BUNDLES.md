# Paper Figure Evidence Bundles

## Snapshot status and Git contents

The existing `paper_evidence/current/` collection is a historical snapshot. Its
builder still references older campaigns; it does not automatically follow
later measurements or manuscript edits. Use the
[published result index](../results/README.md) for the latest Chapter 2/3 data.

Git includes displayed-value tables, source-data snapshots, reproduction
sources, and provenance manifests. Copied `source_logs/` and `figure_assets/`
remain Git-ignored. Thus a provenance row marked `copied` describes the original
local bundle build and does not guarantee that the copied file is in a clone.

## Purpose

Each empirical figure active at snapshot time has a copy-only evidence
bundle. A bundle keeps the values shown by the figure together with copies of
the execution logs from which those values were obtained. Source artifacts are
never moved or modified.

The generated bundles live in a dedicated paper-evidence collection, separate
from both raw run artifacts and promoted aggregate results:

```text
experiments/paper_evidence/current/
```

The top-level `index.tsv` lists the snapshot's paper figures, including conceptual
figures and pending placeholders that have no experimental data.

## Bundle layout

Each empirical figure directory contains:

- `displayed_values.tsv`: the numeric values encoded by the captured figure;
- `figure_assets/`: copies of the PDF/PNG/TeX assets used by the paper;
- `source_data/`: copies of intermediate or promoted TSV inputs;
- `source_logs/`: copies of the relevant run logs, benchmark output, commands,
  validation output, and raw compaction records;
- `reproduction/`: analysis, plotting, runner, and experiment-plan sources
  needed to regenerate the promoted summary or figure;
- `provenance.tsv`: original path, bundle path, byte size, and SHA-256 for every
  copied file.

The original experiment logs remain under `experiments/artifacts/`; the bundle
contains copies. High-frequency monitoring files such as `iostat.log` and
`rss_monitor.tsv` are
not duplicated because they can make a bundle several gigabytes large without
being the direct source of a plotted value. Their originals remain under
`experiments/artifacts/`. Small start/end disk-stat snapshots are copied when
available.

## Rebuild

Rebuilding requires the original local artifacts and sibling `paper/` tree.
The builder's source selections must be reviewed for a new campaign; it does
not discover later runs automatically. Run from the `vcomp` repository:

```bash
python3 experiments/scripts/paper/build_figure_evidence_bundles.py
```

The script only copies source files and writes derived TSVs below the bundle
root. It does not delete, move, or rewrite the original experiment artifacts.
Existing bundle files with the same names are refreshed in place.

## Current audit note

The current `eval_speedup` image annotates the 8 TiB, 91 B F2Load bar as
103 minutes. The retained 2026-06-10 run is 6,830.974 seconds (113.85 minutes),
and the exact run that produced the 103-minute annotation has not yet been
identified. The bundle records both the displayed annotation and this retained
candidate measurement rather than silently treating them as identical.
