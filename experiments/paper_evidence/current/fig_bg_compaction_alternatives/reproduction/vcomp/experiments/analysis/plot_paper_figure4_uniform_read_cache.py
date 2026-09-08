#!/usr/bin/env python3
"""Plot loading/read panels for the paper's design-alternatives figure."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LogNorm
from matplotlib.lines import Line2D
from matplotlib.patches import Patch


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
    "Cached, 5%",
    "Pinned, 5%",
)
STRUCTURAL_CONFIG = "D_pinned_5pct"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loading-tsv", required=True, type=Path)
    parser.add_argument("--read-tsv", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def configure_fonts() -> None:
    plt.rcParams.update(
        {
            "font.family": "Times New Roman",
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
                raise ValueError(
                    f"cache-dependent structural metric: {system}/{metric} "
                    f"spread={relative_spread:.3%}"
                )
    return values


def draw_loading(ax: plt.Axes, values: dict[str, float]) -> None:
    ordered = list(reversed(LOADING_ORDER))
    y = np.arange(len(ordered))
    widths = [values[method] for method in ordered]
    ax.barh(
        y,
        widths,
        height=0.64,
        color=[COLORS[method] for method in ordered],
        edgecolor="black",
        linewidth=0.65,
        zorder=3,
    )
    for ypos, width in zip(y, widths):
        ax.text(
            width + 1.5,
            ypos,
            f"{width:.1f}",
            ha="left",
            va="center",
            fontsize=6.8,
        )
    ax.set_yticks(y, [LOADING_LABELS[method] for method in ordered])
    ax.set_xlabel("Loading time (min)")
    ax.set_xlim(0, 96)
    ax.set_xticks([0, 30, 60, 90])
    ax.grid(axis="x", color="#D9D9D9", linewidth=0.65)
    style_axis(ax)


def draw_lookup_work(
    ax: plt.Axes, values: dict[tuple[str, str], dict[str, str]]
) -> None:
    ordered = list(reversed(SYSTEMS))
    y = np.arange(len(ordered))
    probes = [
        float(values[(STRUCTURAL_CONFIG, system)]["filter_probes_per_op"])
        for system in ordered
    ]
    positives = [
        float(values[(STRUCTURAL_CONFIG, system)]["bloom_positive_per_op"])
        for system in ordered
    ]
    ax.barh(
        y,
        probes,
        height=0.64,
        color=[COLORS[system] for system in ordered],
        edgecolor="black",
        linewidth=0.65,
        zorder=3,
    )
    ax.scatter(
        positives,
        y,
        marker="D",
        s=19,
        facecolor="white",
        edgecolor="black",
        linewidth=0.75,
        zorder=4,
    )
    for ypos, value in zip(y, probes):
        label = f"{value / 1000:.1f}K" if value >= 1000 else f"{value:.2f}"
        ax.text(
            value * (1.20 if value > 1.01 else 1.35),
            ypos,
            label,
            ha="left",
            va="center",
            fontsize=6.8,
        )
    ax.set_xscale("log")
    ax.set_xlim(0.45, 40000)
    ax.set_xticks(
        [1, 10, 100, 1000, 10000],
        ["1", "10", "100", "1K", "10K"],
    )
    ax.set_yticks(y, [SYSTEM_LABELS[system] for system in ordered])
    ax.set_xlabel("SSTs / lookup (log)")
    ax.grid(axis="x", which="major", color="#D9D9D9", linewidth=0.65)
    ax.grid(axis="x", which="minor", visible=False)
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
                label="Bloom positive",
            ),
        ],
        loc="lower right",
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
    ordered = list(reversed(SYSTEMS))
    y = np.arange(len(ordered))
    rates = [
        100
        * float(
            values[(STRUCTURAL_CONFIG, system)]["bloom_true_positive_per_op"]
        )
        for system in ordered
    ]
    ax.barh(
        y,
        rates,
        height=0.64,
        color=[COLORS[system] for system in ordered],
        edgecolor="black",
        linewidth=0.65,
        zorder=3,
    )
    for ypos, rate in zip(y, rates):
        ax.text(
            min(rate + 2.3, 102.0),
            ypos,
            f"{rate:.0f}%",
            ha="left" if rate < 99 else "right",
            va="center",
            fontsize=6.8,
        )
    ax.set_xlim(0, 105)
    ax.set_xticks([0, 50, 100])
    ax.set_yticks(y, [])
    ax.set_xlabel("Successful lookups (%)")
    ax.grid(axis="x", color="#D9D9D9", linewidth=0.65)
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
    matrix = np.array(
        [
            [
                float(values[(config, system)]["throughput_ops_sec"])
                for system in SYSTEMS
            ]
            for config in CONFIGS
        ]
    )
    norm = LogNorm(vmin=30, vmax=1_100_000)
    image = ax.imshow(matrix, cmap="YlGnBu", norm=norm, aspect="auto")
    for row in range(matrix.shape[0]):
        for col in range(matrix.shape[1]):
            value = matrix[row, col]
            rgba = image.cmap(norm(value))
            luminance = 0.2126 * rgba[0] + 0.7152 * rgba[1] + 0.0722 * rgba[2]
            ax.text(
                col,
                row,
                human_qps(value),
                ha="center",
                va="center",
                fontsize=6.7,
                color="black" if luminance > 0.55 else "white",
            )
    ax.set_xticks(
        range(len(SYSTEMS)),
        ["Base", "Flush", "Last", "Seq", "Seq+OW", "F2Load"],
        rotation=35,
        ha="right",
        rotation_mode="anchor",
    )
    ax.set_yticks(range(len(CONFIGS)), CONFIG_LABELS)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_linewidth(0.8)
        spine.set_color("black")
    colorbar = ax.figure.colorbar(
        image,
        ax=ax,
        orientation="horizontal",
        location="top",
        fraction=0.12,
        pad=0.08,
        aspect=28,
    )
    colorbar.set_label("Throughput (ops/s; log color)", labelpad=1.5)
    colorbar.set_ticks([100, 1000, 10000, 100000, 1000000])
    colorbar.set_ticklabels(["100", "1K", "10K", "100K", "1M"])
    colorbar.ax.tick_params(labelsize=6.8, length=2.2, pad=1.5)


def main() -> None:
    args = parse_args()
    configure_fonts()
    loading = load_loading(args.loading_tsv)
    reads = load_reads(args.read_tsv)

    fig_a, ax_a = plt.subplots(figsize=(2.25, 2.18))
    draw_loading(ax_a, loading)
    fig_a.tight_layout(pad=0.15)
    save(fig_a, args.output_dir, "bg_alternative_loading")
    plt.close(fig_a)

    fig_b, ax_b = plt.subplots(figsize=(2.30, 2.18))
    draw_lookup_work(ax_b, reads)
    fig_b.tight_layout(pad=0.15)
    save(fig_b, args.output_dir, "bg_alternative_lookup_work")
    plt.close(fig_b)

    fig_c, ax_c = plt.subplots(figsize=(1.30, 2.18))
    draw_hit_rate(ax_c, reads)
    fig_c.tight_layout(pad=0.15)
    save(fig_c, args.output_dir, "bg_alternative_lookup_hits")
    plt.close(fig_c)

    fig_d, ax_d = plt.subplots(figsize=(3.05, 2.18))
    draw_throughput(ax_d, reads)
    fig_d.tight_layout(pad=0.15)
    save(fig_d, args.output_dir, "bg_alternative_read_throughput")
    plt.close(fig_d)

    preview = plt.figure(figsize=(7.0, 2.35))
    grid = preview.add_gridspec(1, 3, width_ratios=(0.34, 0.19, 0.47), wspace=0.47)
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
    save(preview, args.output_dir, "bg_alternative_read_preview")
    plt.close(preview)


if __name__ == "__main__":
    main()
