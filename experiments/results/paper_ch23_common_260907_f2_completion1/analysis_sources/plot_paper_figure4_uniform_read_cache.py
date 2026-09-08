#!/usr/bin/env python3
"""Plot loading/read panels for the paper's design-alternatives figure."""

from __future__ import annotations

import argparse
import csv
import math
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from paper_alternative_tikz import loading_label, membership_percent, throughput_limits, write_native_figures


SYSTEMS = (
    "baseline",
    "flush_only",
    "last_comp",
    "fillseq",
    "fillseq_ow",
    "f2load",
)
SYSTEM_LABELS = {
    "baseline": "Baseline",
    "flush_only": "Flush-only",
    "last_comp": "Last-comp",
    "fillseq": "Fillseq",
    "fillseq_ow": "Fillseq+OW",
    "f2load": "F2Load",
}
COLORS = {
    "baseline": "#5B5B5B",
    "adoc": "#8E5AA9",
    "blobdb": "#D55E00",
    "flush_only": "#E69F00",
    "last_comp": "#F0A83B",
    "fillseq": "#009E73",
    "fillseq_ow": "#4AAE91",
    "f2load": "#0072B2",
}
LOADING_NAME_TO_ID = {
    "Baseline": "baseline",
    "ADOC": "adoc",
    "BlobDB (GC off)": "blobdb",
    "Flush only": "flush_only",
    "Last compaction": "last_comp",
    "Fillseq": "fillseq",
    "Fillseq + 10% overwrite": "fillseq_ow",
    "F2Load": "f2load",
}
LOADING_LABELS = {
    "baseline": "Baseline",
    "adoc": "ADOC",
    "blobdb": "BlobDB",
    "flush_only": "Flush-only",
    "last_comp": "Last-comp",
    "fillseq": "Fillseq",
    "fillseq_ow": "Fillseq+OW",
    "f2load": "F2Load",
}
LOADING_ORDER = tuple(LOADING_LABELS)
CONFIGS = (
    "A_cache_zero",
    "C_pinned_zero",
    "B_cache_5pct",
    "D_pinned_5pct",
)
CONFIG_LABELS = (
    "Cached, ≈0",
    "Pinned, ≈0",
    "Cached, 50 GiB",
    "Pinned, 50 GiB",
)
CONFIG_COLORS = ("#4477AA", "#66CCEE", "#EE6677", "#CCBB44")
STRUCTURAL_CONFIG = "D_pinned_5pct"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loading-tsv", required=True, type=Path)
    parser.add_argument("--read-tsv", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--exclude-f2load", action="store_true",
                        help="Plot the explicitly completed non-F2Load scope only.")
    return parser.parse_args()


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def configure_fonts() -> None:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Times New Roman", "Nimbus Roman", "DejaVu Serif"],
            "font.size": 8.0,
            "axes.labelsize": 8.5,
            "xtick.labelsize": 7.5,
            "ytick.labelsize": 7.5,
            "axes.linewidth": 0.8,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def style_axis(ax: plt.Axes) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(direction="out", length=2.8, width=0.8)
    ax.set_axisbelow(True)


def save(fig: plt.Figure, output_dir: Path, stem: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / f"{stem}.pdf", bbox_inches="tight", pad_inches=0.015)
    fig.savefig(
        output_dir / f"{stem}.png",
        dpi=300,
        bbox_inches="tight",
        pad_inches=0.015,
    )


def load_loading(path: Path) -> dict[str, float]:
    values: dict[str, float] = {}
    for row in read_tsv(path):
        method = LOADING_NAME_TO_ID.get(row["method"])
        if method is None:
            continue
        if method in values:
            raise ValueError(f"duplicate loading method: {method}")
        if int(row["repetitions"]) != 1:
            raise ValueError(f"unexpected repetitions for {method}")
        values[method] = float(row["loading_min"])
    missing = set(LOADING_ORDER) - set(values)
    if missing:
        raise ValueError(f"missing loading methods: {sorted(missing)}")
    return values


def load_reads(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    rows = read_tsv(path)
    values = {(row["config_id"], row["system"]): row for row in rows}
    if len(values) != len(rows):
        raise ValueError("duplicate read cells")
    expected = {(config, system) for config in CONFIGS for system in SYSTEMS}
    if set(values) != expected:
        raise ValueError(
            f"read matrix mismatch: missing={sorted(expected-set(values))}, "
            f"extra={sorted(set(values)-expected)}"
        )
    for system in SYSTEMS:
        for metric in (
            "filter_probes_per_op",
            "bloom_positive_per_op",
            "bloom_true_positive_per_op",
        ):
            observed = [float(values[(config, system)][metric]) for config in CONFIGS]
            relative_spread = (max(observed) - min(observed)) / np.mean(observed)
            if relative_spread > 0.01:
                warnings.warn(
                    f"Inspect finite-sample structural spread: {system}/{metric} "
                    f"spread={relative_spread:.3%}"
                )
    return values


def draw_loading(ax: plt.Axes, values: dict[str, float]) -> None:
    ordered = list(LOADING_ORDER)
    x = np.arange(len(ordered))
    heights = [values[method] for method in ordered]
    ax.bar(
        x,
        heights,
        width=0.64,
        color=[COLORS[method] for method in ordered],
        edgecolor="black",
        linewidth=0.65,
        zorder=3,
    )
    for xpos, height in zip(x, heights):
        ax.text(
            xpos,
            height + 1.5,
            loading_label(height),
            ha="center",
            va="bottom",
            fontsize=6.8,
        )
    ax.set_xticks(x, [LOADING_LABELS[method] for method in ordered],
                  rotation=45, ha="right", rotation_mode="anchor")
    ax.set_ylabel("Loading time (min)")
    upper = max(90, math.ceil(max(heights) * 1.1 / 30) * 30)
    ax.set_ylim(0, upper + 6)
    ax.set_yticks(range(0, upper+1, 30))
    ax.grid(axis="y", color="#D9D9D9", linewidth=0.65)
    style_axis(ax)


def draw_lookup_work(
    ax: plt.Axes, values: dict[tuple[str, str], dict[str, str]]
) -> None:
    ordered = list(SYSTEMS)
    x = np.arange(len(ordered))
    probes = [
        float(values[(STRUCTURAL_CONFIG, system)]["filter_probes_per_op"])
        for system in ordered
    ]
    positives = [
        float(values[(STRUCTURAL_CONFIG, system)]["bloom_positive_per_op"])
        for system in ordered
    ]
    ax.bar(
        x,
        np.minimum(probes, 5),
        width=0.64,
        color=[COLORS[system] for system in ordered],
        edgecolor="black",
        linewidth=0.65,
        zorder=3,
    )
    positive_axis = ax.twinx()
    positive_axis.scatter(
        x,
        100 * np.array(positives) / np.array(probes),
        marker="D",
        s=19,
        facecolor="white",
        edgecolor="black",
        linewidth=0.75,
        zorder=4,
        clip_on=False,
    )
    positive_axis.set_ylim(0, 100)
    positive_axis.set_yticks([0, 25, 50, 75, 100])
    positive_axis.set_ylabel("Filter positive (%)")
    positive_axis.spines["top"].set_visible(False)
    positive_axis.tick_params(direction="out", length=2.8, width=0.8)
    for xpos, value in zip(x, probes):
        label = f"{value / 1000:.1f}K" if value >= 1000 else f"{value:.2f}"
        ax.text(
            xpos,
            min(value, 5) + 0.10,
            label,
            ha="center",
            va="bottom",
            fontsize=6.8,
        )
        if value > 5:
            for offset in (-0.07, 0.07):
                ax.plot([xpos - 0.34, xpos + 0.34],
                        [4.55 + offset, 4.82 + offset], color="white",
                        linewidth=3, zorder=5)
                ax.plot([xpos - 0.34, xpos + 0.34],
                        [4.55 + offset, 4.82 + offset], color="black",
                        linewidth=0.6, zorder=6)
    ax.set_ylim(0, 5)
    ax.set_yticks(range(6))
    ax.set_xticks(x, [SYSTEM_LABELS[system] for system in ordered],
                  rotation=55, ha="right", rotation_mode="anchor")
    ax.set_ylabel("Filter checks / lookup")
    ax.grid(axis="y", which="major", color="#D9D9D9", linewidth=0.65)
    ax.grid(axis="y", which="minor", visible=False)
    ax.legend(
        handles=[
            Patch(facecolor="#BDBDBD", edgecolor="black", label="Filter checks"),
            Line2D(
                [0],
                [0],
                marker="D",
                markersize=4.2,
                markerfacecolor="white",
                markeredgecolor="black",
                linestyle="none",
                label="Filter positive",
            ),
        ],
        loc="lower center",
        bbox_to_anchor=(0.65, 1.14),
        fontsize=6.6,
        frameon=False,
        handlelength=1.3,
        borderpad=0.1,
        labelspacing=0.25,
    )
    style_axis(ax)


def draw_hit_rate(
    ax: plt.Axes, values: dict[tuple[str, str], dict[str, str]]
) -> None:
    ordered = list(SYSTEMS)
    x = np.arange(len(ordered))
    rates = [
        membership_percent(values[(STRUCTURAL_CONFIG, system)])
        for system in ordered
    ]
    ax.bar(
        x,
        rates,
        width=0.64,
        color=[COLORS[system] for system in ordered],
        edgecolor="black",
        linewidth=0.65,
        zorder=3,
    )
    for xpos, rate in zip(x, rates):
        ax.text(
            xpos,
            rate + 2.3,
            f"{rate:.1f}" if rate < 99 else "100",
            ha="center",
            va="bottom",
            fontsize=6.2,
        )
    ax.set_ylim(0, 115)
    ax.set_yticks([0, 50, 100])
    ax.set_xticks(x, [SYSTEM_LABELS[system] for system in ordered],
                  rotation=55, ha="right", rotation_mode="anchor")
    ax.set_ylabel("Successful lookups (%)")
    ax.grid(axis="y", color="#D9D9D9", linewidth=0.65)
    style_axis(ax)


def human_qps(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 100_000:
        return f"{value / 1000:.0f}K"
    if value >= 1000:
        return f"{value / 1000:.1f}K"
    return f"{value:.0f}"


def draw_throughput(
    ax: plt.Axes, values: dict[tuple[str, str], dict[str, str]]
) -> None:
    x = np.arange(len(SYSTEMS))
    width = 0.18
    for index, (config, label, color) in enumerate(
        zip(CONFIGS, CONFIG_LABELS, CONFIG_COLORS)
    ):
        qps = np.array([
            float(values[(config, system)]["throughput_ops_sec"])
            for system in SYSTEMS
        ])
        baseline_qps = float(values[(config, "baseline")]["throughput_ops_sec"])
        ax.bar(x + (index - 1.5) * width, qps / baseline_qps,
               width=width, color=color, edgecolor="black", linewidth=0.4,
               label=label, zorder=3)
    ax.set_xticks(x, [SYSTEM_LABELS[system] for system in SYSTEMS],
                  rotation=55, ha="right", rotation_mode="anchor")
    upper, flush_bound = throughput_limits(values, CONFIGS, SYSTEMS)
    ax.set_ylim(0, upper)
    ax.set_yticks(range(upper+1))
    ax.set_ylabel("Throughput / Baseline")
    ax.axhline(1, color="red", linewidth=0.8, zorder=5)
    ax.text(SYSTEMS.index("flush_only"), 0.13, f"<{flush_bound:.3f}", ha="center",
            va="bottom", fontsize=6.0)
    ax.grid(axis="y", which="major", color="#D9D9D9", linewidth=0.65)
    ax.grid(axis="y", which="minor", visible=False)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=2,
              frameon=False, fontsize=6.0, handlelength=1.1,
              columnspacing=0.8, labelspacing=0.3)
    style_axis(ax)


def main() -> None:
    global SYSTEMS, LOADING_ORDER
    args = parse_args()
    if args.exclude_f2load:
        SYSTEMS = tuple(s for s in SYSTEMS if s != "f2load")
        LOADING_ORDER = tuple(s for s in LOADING_ORDER if s != "f2load")
    configure_fonts()
    loading = load_loading(args.loading_tsv)
    reads = load_reads(args.read_tsv)
    write_native_figures(args.output_dir, loading, reads, LOADING_ORDER,
                         SYSTEMS, LOADING_LABELS, SYSTEM_LABELS, COLORS,
                         CONFIGS, CONFIG_COLORS, STRUCTURAL_CONFIG)

    fig_a, ax_a = plt.subplots(figsize=(3.4, 2.35))
    draw_loading(ax_a, loading)
    fig_a.tight_layout(pad=0.15)
    save(fig_a, args.output_dir, "bg_alternative_loading")
    plt.close(fig_a)

    fig_b, ax_b = plt.subplots(figsize=(2.9, 2.65))
    draw_lookup_work(ax_b, reads)
    fig_b.tight_layout(pad=0.15)
    save(fig_b, args.output_dir, "bg_alternative_lookup_work")
    plt.close(fig_b)

    fig_c, ax_c = plt.subplots(figsize=(2.4, 2.65))
    draw_hit_rate(ax_c, reads)
    fig_c.tight_layout(pad=0.15)
    save(fig_c, args.output_dir, "bg_alternative_lookup_hits")
    plt.close(fig_c)

    fig_d, ax_d = plt.subplots(figsize=(3.15, 2.65))
    draw_throughput(ax_d, reads)
    fig_d.tight_layout(pad=0.15)
    save(fig_d, args.output_dir, "bg_alternative_read_throughput")
    plt.close(fig_d)

    preview = plt.figure(figsize=(7.0, 2.8))
    grid = preview.add_gridspec(1, 3, width_ratios=(0.32, 0.27, 0.37), wspace=0.95)
    axes = [preview.add_subplot(grid[0, index]) for index in range(3)]
    draw_lookup_work(axes[0], reads)
    draw_hit_rate(axes[1], reads)
    draw_throughput(axes[2], reads)
    for label, ax in zip(("(a)", "(b)", "(c)"), axes):
        ax.text(
            -0.12,
            1.04,
            label,
            transform=ax.transAxes,
            fontweight="bold",
            ha="right",
            va="bottom",
        )
    preview.subplots_adjust(left=0.065, right=0.99, bottom=0.32, top=0.77)
    save(preview, args.output_dir, "bg_alternative_read_preview")
    plt.close(preview)


if __name__ == "__main__":
    main()
