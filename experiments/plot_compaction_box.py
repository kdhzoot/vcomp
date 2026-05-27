#!/usr/bin/env python3
"""
Box plots of per-compaction input file count, grouped by
(start_level -> output_level) pair, baseline vs vcomp.

Also a second panel: number of compactions per run for each pair.
"""
import argparse
import csv
import glob
import statistics as st
from collections import defaultdict
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


def loadall(dirpath):
    rows = []
    for p in sorted(glob.glob(f"{dirpath}/*.csv")):
        rows += list(csv.DictReader(open(p)))
    return rows


def aggregate(rows):
    # inputs[(sl,ol)] = list across all events
    inputs = defaultdict(list)
    # cnt_per_run[(sl,ol)] = list of compaction counts per run (one per run)
    cnt_by_run = defaultdict(lambda: defaultdict(int))
    for r in rows:
        try:
            sl, ol = int(r["start_level"]), int(r["output_level"])
            n = int(r["n_inputs_total"]) if r["n_inputs_total"] else 0
        except (ValueError, KeyError):
            continue
        inputs[(sl, ol)].append(n)
        cnt_by_run[r["run"]][(sl, ol)] += 1
    # Rebuild cnt_per_run as a list aligned to runs.
    runs = sorted(cnt_by_run)
    cnt_per_run = defaultdict(list)
    pairs = set()
    for run in runs:
        for p in cnt_by_run[run]:
            pairs.add(p)
    for run in runs:
        for p in pairs:
            cnt_per_run[p].append(cnt_by_run[run].get(p, 0))
    return inputs, cnt_per_run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("baseline_dir")
    ap.add_argument("vcomp_dir")
    ap.add_argument("--out", default="compaction_logs/compaction_box.png")
    args = ap.parse_args()

    bl = loadall(args.baseline_dir)
    vc = loadall(args.vcomp_dir)

    bl_inputs, bl_cnt = aggregate(bl)
    vc_inputs, vc_cnt = aggregate(vc)

    # Pairs to display: union, sorted by (start_level, output_level), skip L0->L0
    # but show it separately since baseline has none.
    pairs = sorted(set(bl_inputs) | set(vc_inputs))
    # Drop trivial moves (output == start) for clarity? keep L0->L0 since it's
    # the intra-L0 compaction signal.

    fig, axes = plt.subplots(1, 2, figsize=(15, 6))
    width = 0.38
    bl_color = "#1f77b4"
    vc_color = "#d62728"

    # ── Panel 1: input file count distribution ──
    ax = axes[0]
    xs = list(range(len(pairs)))
    bl_data = [bl_inputs.get(p, []) for p in pairs]
    vc_data = [vc_inputs.get(p, []) for p in pairs]
    bp1 = ax.boxplot(
        bl_data, positions=[x - width/2 for x in xs], widths=width*0.9,
        patch_artist=True, showfliers=False,
        boxprops=dict(facecolor=bl_color, alpha=0.55, edgecolor=bl_color),
        medianprops=dict(color="black", linewidth=1.5),
        whiskerprops=dict(color=bl_color), capprops=dict(color=bl_color),
    )
    bp2 = ax.boxplot(
        vc_data, positions=[x + width/2 for x in xs], widths=width*0.9,
        patch_artist=True, showfliers=False,
        boxprops=dict(facecolor=vc_color, alpha=0.55, edgecolor=vc_color),
        medianprops=dict(color="black", linewidth=1.5),
        whiskerprops=dict(color=vc_color), capprops=dict(color=vc_color),
    )
    ax.set_xticks(xs)
    ax.set_xticklabels([f"L{p[0]}→L{p[1]}" for p in pairs])
    ax.set_ylabel("Input File Count per Compaction")
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    if bp1["boxes"]:
        bp1["boxes"][0].set_label("baseline")
    if bp2["boxes"]:
        bp2["boxes"][0].set_label("vcomp")
    ax.legend(loc="upper right")

    # ── Panel 2: compactions per run ──
    ax = axes[1]
    bl_cnt_data = [bl_cnt.get(p, []) for p in pairs]
    vc_cnt_data = [vc_cnt.get(p, []) for p in pairs]
    ax.boxplot(
        bl_cnt_data, positions=[x - width/2 for x in xs], widths=width*0.9,
        patch_artist=True, showfliers=True,
        boxprops=dict(facecolor=bl_color, alpha=0.55, edgecolor=bl_color),
        medianprops=dict(color="black", linewidth=1.5),
        whiskerprops=dict(color=bl_color), capprops=dict(color=bl_color),
        flierprops=dict(marker="o", markersize=4, markerfacecolor=bl_color,
                        markeredgecolor="none", alpha=0.6),
    )
    ax.boxplot(
        vc_cnt_data, positions=[x + width/2 for x in xs], widths=width*0.9,
        patch_artist=True, showfliers=True,
        boxprops=dict(facecolor=vc_color, alpha=0.55, edgecolor=vc_color),
        medianprops=dict(color="black", linewidth=1.5),
        whiskerprops=dict(color=vc_color), capprops=dict(color=vc_color),
        flierprops=dict(marker="o", markersize=4, markerfacecolor=vc_color,
                        markeredgecolor="none", alpha=0.6),
    )
    ax.set_xticks(xs)
    ax.set_xticklabels([f"L{p[0]}→L{p[1]}" for p in pairs])
    ax.set_ylabel("Compactions per Run")
    ax.grid(axis="y", linestyle=":", alpha=0.5)

    fig.tight_layout()
    fig.savefig(args.out, dpi=130)
    print(f"wrote {args.out}")

if __name__ == "__main__":
    main()
