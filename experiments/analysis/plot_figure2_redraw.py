#!/usr/bin/env python3
"""Plot the two-panel Figure 2 redraw from audited TSV inputs."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.patches import Patch, Rectangle


SIZES_GIB = (500, 1000, 2000, 4000, 8000)
SIZE_LABELS = ("0.5", "1", "2", "4", "8")
SERIES = ("1KB", "91B")
BREAKDOWN_CATEGORIES = ("SST Build", "Merge", "I/O", "Other")

BLUE = "#0072B2"
PALE_YELLOW = "#FFF3A6"
GREEN = "#009E73"
LIGHT_BLUE = "#8ECAE6"
GRID = "#D9D9D9"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loading-tsv", type=Path, required=True)
    parser.add_argument("--breakdown-tsv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--breakdown-second-label",
        default="91B",
        help="Display label for the second breakdown series",
    )
    return parser.parse_args()


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def load_loading_data(path: Path) -> dict[str, list[float]]:
    rows = read_tsv(path)
    values: dict[tuple[str, int], float] = {}
    for row in rows:
        key = (row["series"], int(row["dataset_GiB"]))
        if key in values:
            raise ValueError(f"duplicate loading point: {key}")
        if row["point_type"] != "measured":
            raise ValueError(f"non-measured loading point: {key}")
        values[key] = float(row["elapsed_hour"])

    missing = [
        (series, size)
        for series in SERIES
        for size in SIZES_GIB
        if (series, size) not in values
    ]
    if missing:
        raise ValueError(f"missing loading points: {missing}")
    return {
        series: [values[(series, size)] for size in SIZES_GIB]
        for series in SERIES
    }


def load_breakdown_data(
    path: Path, second_label: str
) -> tuple[list[str], dict[str, list[float]]]:
    rows = read_tsv(path)
    available_series = {row["series"] for row in rows}
    second_series = "91B" if "91B" in available_series else "legacy_115B"
    series_order = ["1KB", second_series]
    labels = ["1KB", second_label]
    values: dict[tuple[str, str], float] = {}
    for row in rows:
        if row["series"] not in series_order:
            continue
        key = (row["series"], row["category"])
        if key in values:
            raise ValueError(f"duplicate breakdown point: {key}")
        values[key] = float(row["time_hour"])

    missing = [
        (series, category)
        for series in series_order
        for category in BREAKDOWN_CATEGORIES
        if (series, category) not in values
    ]
    if missing:
        raise ValueError(f"missing breakdown points: {missing}")
    return labels, {
        category: [values[(series, category)] for series in series_order]
        for category in BREAKDOWN_CATEGORIES
    }


def style_axis(ax: Axes) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(direction="out", length=3.2, width=0.8)
    ax.set_axisbelow(True)


def draw_loading(ax: Axes, data: dict[str, list[float]]) -> None:
    x = list(range(len(SIZES_GIB)))
    width = 0.34
    log_bottom = 0.5

    ax.bar(
        [value - width / 2 for value in x],
        [value - log_bottom for value in data["1KB"]],
        width=width,
        bottom=log_bottom,
        color=BLUE,
        edgecolor="black",
        linewidth=0.9,
        label="1KB",
        zorder=3,
    )
    ax.bar(
        [value + width / 2 for value in x],
        [value - log_bottom for value in data["91B"]],
        width=width,
        bottom=log_bottom,
        color=LIGHT_BLUE,
        edgecolor="black",
        linewidth=0.9,
        hatch="////",
        label="91B",
        zorder=3,
    )

    ax.set_yscale("log")
    ax.set_ylim(log_bottom, 100)
    ax.set_yticks([1, 10, 100], labels=["1", "10", "100"])
    ax.set_xticks(x, SIZE_LABELS)
    ax.set_xlabel("DB size (TB)")
    ax.set_ylabel("Loading time\n(hour; log-scale)")
    ax.grid(axis="y", which="major", color=GRID, linewidth=0.7)
    ax.grid(axis="y", which="minor", visible=False)
    ax.legend(
        loc="upper left",
        ncol=2,
        frameon=False,
        handlelength=1.0,
        handleheight=0.8,
        columnspacing=0.8,
        handletextpad=0.35,
        borderaxespad=0.35,
    )
    style_axis(ax)


def draw_breakdown(
    ax: Axes, labels: list[str], data: dict[str, list[float]]
) -> None:
    y = [1, 0]
    left = [0.0, 0.0]
    height = 0.55
    styles = {
        "SST Build": {"facecolor": BLUE, "edgecolor": "black", "hatch": None},
        "Merge": {
            "facecolor": PALE_YELLOW,
            "edgecolor": "black",
            "hatch": None,
        },
        "I/O": {"facecolor": "white", "edgecolor": GREEN, "hatch": "////"},
        "Other": {"facecolor": GREEN, "edgecolor": "black", "hatch": None},
    }

    for category in BREAKDOWN_CATEGORIES:
        values = data[category]
        style = styles[category]
        ax.barh(
            y,
            values,
            left=left,
            height=height,
            facecolor=style["facecolor"],
            edgecolor=style["edgecolor"],
            linewidth=0.9,
            hatch=style["hatch"],
            zorder=3,
        )
        if category == "I/O":
            for ypos, start, width in zip(y, left, values):
                ax.add_patch(
                    Rectangle(
                        (start, ypos - height / 2),
                        width,
                        height,
                        facecolor="none",
                        edgecolor="black",
                        linewidth=0.9,
                        zorder=4,
                    )
                )
        left = [start + width for start, width in zip(left, values)]

    ax.set_yticks(y, labels)
    ax.set_ylabel("KV size")
    ax.set_xlabel("Cumulative compaction time (hour)")
    ax.set_xlim(0, 12)
    ax.set_xticks(range(0, 13, 2))
    ax.set_ylim(-0.65, 1.65)
    ax.grid(axis="x", color=GRID, linewidth=0.7)
    handles = [
        Patch(facecolor=BLUE, edgecolor="black", label="SST Build"),
        Patch(facecolor=PALE_YELLOW, edgecolor="black", label="Merge"),
        Patch(facecolor="white", edgecolor=GREEN, hatch="////", label="I/O"),
        Patch(facecolor=GREEN, edgecolor="black", label="Other"),
    ]
    ax.legend(
        handles=handles,
        loc="lower right",
        bbox_to_anchor=(1.0, 1.02),
        ncol=4,
        frameon=False,
        handlelength=0.8,
        columnspacing=0.75,
        handletextpad=0.3,
        borderaxespad=0.0,
    )
    style_axis(ax)


def save_figure(fig: plt.Figure, pdf: Path, png: Path) -> None:
    pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(pdf, bbox_inches="tight", pad_inches=0.02)
    fig.savefig(png, dpi=300, bbox_inches="tight", pad_inches=0.02)


def main() -> None:
    args = parse_args()
    loading = load_loading_data(args.loading_tsv)
    breakdown_labels, breakdown = load_breakdown_data(
        args.breakdown_tsv, args.breakdown_second_label
    )

    plt.rcParams.update(
        {
            "font.family": "Times New Roman",
            "font.size": 8.5,
            "axes.labelsize": 9.5,
            "xtick.labelsize": 8.5,
            "ytick.labelsize": 8.5,
            "legend.fontsize": 8.0,
            "axes.linewidth": 0.8,
            "hatch.linewidth": 0.8,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    fig_a, ax_a = plt.subplots(figsize=(3.28, 2.20))
    draw_loading(ax_a, loading)
    fig_a.tight_layout(pad=0.25)
    save_figure(
        fig_a,
        args.output_dir / "bg_loading_scale_redraw.pdf",
        args.output_dir / "bg_loading_scale_redraw.png",
    )
    plt.close(fig_a)

    fig_c, ax_c = plt.subplots(figsize=(3.28, 1.05))
    draw_breakdown(ax_c, breakdown_labels, breakdown)
    fig_c.tight_layout(pad=0.25)
    save_figure(
        fig_c,
        args.output_dir / "bg_loading_breakdown_redraw.pdf",
        args.output_dir / "bg_loading_breakdown_redraw.png",
    )
    plt.close(fig_c)

    preview = plt.figure(figsize=(3.35, 3.70))
    grid = preview.add_gridspec(2, 1, height_ratios=(1.75, 0.9), hspace=0.72)
    ax_top = preview.add_subplot(grid[0])
    ax_bottom = preview.add_subplot(grid[1])
    draw_loading(ax_top, loading)
    draw_breakdown(ax_bottom, breakdown_labels, breakdown)
    ax_top.text(-0.17, 1.02, "(a)", transform=ax_top.transAxes, fontweight="bold")
    ax_bottom.text(-0.17, 1.02, "(b)", transform=ax_bottom.transAxes, fontweight="bold")
    save_figure(
        preview,
        args.output_dir / "figure2_redraw_preview.pdf",
        args.output_dir / "figure2_redraw_preview.png",
    )
    plt.close(preview)


if __name__ == "__main__":
    main()
