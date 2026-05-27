#!/usr/bin/env python3
"""
Side-by-side box plot of per-level coverage % for two (or more) sets of
coverage_dumps/ directories. Each directory should contain one .cov per
DB and a coverage.csv produced by parse_coverage.py.

Usage:
    python3 plot_coverage_box.py LABEL1=DIR1 LABEL2=DIR2 [...] [--out PATH]
"""
import argparse
import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({
    "font.size": 16,
    "axes.titlesize": 18,
    "axes.labelsize": 17,
    "xtick.labelsize": 15,
    "ytick.labelsize": 15,
    "legend.fontsize": 15,
})


def load(csv_path: Path):
    """Return dict level -> list of cov_pct."""
    by_level = {}
    by_level_files = {}
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            lvl = int(row["level"])
            by_level.setdefault(lvl, []).append(float(row["cov_pct"]))
            by_level_files.setdefault(lvl, []).append(int(row["files"]))
    return by_level, by_level_files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("groups", nargs="+", help="LABEL=DIR pairs")
    ap.add_argument("--out", default="coverage_dumps/coverage_box.png")
    args = ap.parse_args()

    groups = []  # (label, by_level_cov, by_level_files)
    for spec in args.groups:
        label, _, d = spec.partition("=")
        csv_path = Path(d) / "coverage.csv"
        cov, files = load(csv_path)
        groups.append((label, cov, files))

    # union of levels (sorted), excluding L0
    all_levels = sorted({l for _, c, _ in groups for l in c if l > 0})

    fig, ax = plt.subplots(1, 1, figsize=(8, 5.5))

    # ---- coverage % ----
    n_groups = len(groups)
    width = 0.8 / n_groups
    colors = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd"]
    positions_per_level = {}
    for gi, (label, cov, _files) in enumerate(groups):
        data = [cov.get(l, []) for l in all_levels]
        positions = [li + (gi - (n_groups - 1) / 2) * width
                     for li in range(len(all_levels))]
        bp = ax.boxplot(
            data, positions=positions, widths=width * 0.9,
            patch_artist=True, showfliers=True,
            boxprops=dict(facecolor=colors[gi % len(colors)], alpha=0.55,
                          edgecolor=colors[gi % len(colors)]),
            medianprops=dict(color="black", linewidth=1.5),
            whiskerprops=dict(color=colors[gi % len(colors)]),
            capprops=dict(color=colors[gi % len(colors)]),
            flierprops=dict(marker="o", markersize=3,
                            markerfacecolor=colors[gi % len(colors)],
                            markeredgecolor="none", alpha=0.6),
        )
        # overlay per-run scatter for visibility of N=30
        for li, vals in enumerate(data):
            xs = [positions[li] + (hash(v) % 100 - 50) / 5000.0 for v in vals]
            ax.scatter(xs, vals, s=8, color=colors[gi % len(colors)],
                       alpha=0.45, edgecolor="none", zorder=3)
        bp["boxes"][0].set_label(label)

    ax.set_xticks(range(len(all_levels)))
    ax.set_xticklabels([f"L{l}" for l in all_levels])
    ax.set_ylabel("Level Coverage (%)")
    ax.set_ylim(0, 105)
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    ax.legend(loc="lower right")

    fig.tight_layout()
    fig.savefig(args.out, dpi=130)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
