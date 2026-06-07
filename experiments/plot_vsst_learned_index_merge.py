#!/usr/bin/env python3
"""Draw a compact method figure for VSST learned-index merging."""

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.lines import Line2D


KEY_PREFIX_SCALE = 10.0
VSST_ENTRIES_K = 64.0
PANEL_A_DATA_SEED = 13

PANEL_A_SEGMENTS = [
    (0.10, 0.33, 0.03, 0.12),
    (0.33, 0.55, 0.12, 0.62),
    (0.55, 0.90, 0.62, 1.00),
]
PANEL_A_COLORS = ["#2f6fb0", "#5d95c8", "#1f5b8e"]

PANEL_B_MODELS = [
    (
        [(0.08, 0.30, 0.00, 0.10), (0.30, 0.52, 0.10, 0.68), (0.52, 0.90, 0.68, 1.00)],
        "#4c78a8",
        "VSST #1",
    ),
    (
        [(0.12, 0.40, 0.00, 0.36), (0.40, 0.68, 0.36, 0.50), (0.68, 0.92, 0.50, 1.00)],
        "#f58518",
        "VSST #2",
    ),
]
PANEL_B_BREAKPOINTS = [0.30, 0.40, 0.52, 0.68]


def key_axis(x):
    return np.asarray(x) * KEY_PREFIX_SCALE


def rank_axis(y, entries_k=VSST_ENTRIES_K):
    return np.asarray(y) * entries_k


def segment_y(x, segment):
    x0, x1, y0, y1 = segment
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def model_value(x, segments):
    y = np.zeros_like(x)
    last_y = 0.0
    for x0, x1, y0, y1 in segments:
        y[x >= x0] = y0
        active = (x >= x0) & (x <= x1)
        y[active] = segment_y(x[active], (x0, x1, y0, y1))
        y[x > x1] = y1
        last_y = y1
    y[x > segments[-1][1]] = last_y
    return y


def font_family():
    try:
        font_manager.findfont("Cambria", fallback_to_default=False)
        return "Cambria"
    except ValueError:
        return "DejaVu Serif"


def add_panel_label(ax, label, title):
    ax.text(
        0.0,
        1.06,
        label,
        transform=ax.transAxes,
        fontsize=16,
        fontweight="bold",
        va="bottom",
        ha="left",
    )
    ax.text(
        0.08,
        1.06,
        title,
        transform=ax.transAxes,
        fontsize=15,
        fontweight="bold",
        va="bottom",
        ha="left",
    )


def draw_single_vsst(ax):
    add_panel_label(ax, "A", "Learned Index in a VSST")

    rng = np.random.default_rng(PANEL_A_DATA_SEED)
    for i, segment in enumerate(PANEL_A_SEGMENTS):
        x0, x1, y0, y1 = segment
        xs = np.linspace(x0, x1, 36)
        ys = segment_y(xs, segment)
        data_x = np.linspace(x0 + 0.015, x1 - 0.015, 6)
        data_y = segment_y(data_x, segment) + rng.normal(0.0, 0.010, data_x.size)
        ax.scatter(
            key_axis(data_x),
            rank_axis(data_y),
            s=15,
            color="#4b5563",
            alpha=0.62,
            zorder=2,
            linewidths=0,
        )
        ax.plot(
            key_axis(xs),
            rank_axis(ys),
            lw=2.8,
            color=PANEL_A_COLORS[i],
            solid_capstyle="round",
            zorder=3,
        )
        ax.text(
            key_axis((x0 + x1) / 2),
            rank_axis((y0 + y1) / 2 + 0.045),
            f"s{i + 1}",
            ha="center",
            fontsize=11,
            color="#1f3d5a",
        )

    for _, x1, _, _ in PANEL_A_SEGMENTS[:-1]:
        ax.axvline(key_axis(x1), color="#9aa6b2", lw=1.0, ls=":")

    ax.legend(
        handles=[Line2D([0], [0], color="#2f6fb0", lw=2.8, label="VSST #1")],
        loc="upper left",
        frameon=True,
        framealpha=0.94,
        edgecolor="#d6dce5",
        fontsize=11.0,
        handlelength=2.2,
    )

    ax.set_xlim(0, KEY_PREFIX_SCALE)
    ax.set_ylim(0, VSST_ENTRIES_K * 1.05)
    ax.set_xticks(np.arange(0, KEY_PREFIX_SCALE + 0.1, 2))
    ax.set_yticks(np.arange(0, VSST_ENTRIES_K + 0.1, 16))
    ax.set_xlabel(r"key prefix ($\times 10^{12}$)")
    ax.set_ylabel("rank in VSST (K entries)")
    ax.grid(True, alpha=0.20)
    ax.spines[["top", "right"]].set_visible(False)


def draw_merge(ax):
    add_panel_label(ax, "B", "Metadata-only Merge")

    x = np.linspace(0.06, 0.94, 400)

    curves = []
    merge_handles = []
    for segments, color, label in PANEL_B_MODELS:
        y = rank_axis(model_value(x, segments))
        curves.append(y)
        merge_handles.append(Line2D([0], [0], color=color, lw=2.1, label=label))
        for segment in segments:
            x0, x1, _, _ = segment
            xs = np.linspace(x0, x1, 28)
            ax.plot(
                key_axis(xs),
                rank_axis(segment_y(xs, segment)),
                lw=2.1,
                color=color,
                alpha=0.78,
            )
        dot_x = np.array([(s[0] + s[1]) / 2 for s in segments])
        dot_y = np.array([(s[2] + s[3]) / 2 for s in segments])
        ax.scatter(
            key_axis(dot_x),
            rank_axis(dot_y),
            s=15,
            color=color,
            alpha=0.64,
            linewidths=0,
        )

    merged = np.sum(curves, axis=0)
    merged_line, = ax.plot(key_axis(x), merged, lw=3.0, color="#1f2329", label="Merged VSST")
    merge_handles.append(merged_line)

    for bp in PANEL_B_BREAKPOINTS:
        ax.axvline(key_axis(bp), color="#aeb8c4", lw=1.0, ls=":")

    ax.legend(
        handles=merge_handles,
        loc="upper left",
        frameon=True,
        framealpha=0.94,
        edgecolor="#d6dce5",
        fontsize=11.0,
        handlelength=2.2,
    )

    ax.set_xlim(0, KEY_PREFIX_SCALE)
    ax.set_ylim(0, VSST_ENTRIES_K * 2.05)
    ax.set_xticks(np.arange(0, KEY_PREFIX_SCALE + 0.1, 2))
    ax.set_yticks(np.arange(0, VSST_ENTRIES_K * 2 + 0.1, 32))
    ax.set_xlabel(r"key prefix ($\times 10^{12}$)")
    ax.set_ylabel("rank in merged run (K entries)")
    ax.grid(True, alpha=0.20)
    ax.spines[["top", "right"]].set_visible(False)


def pad_columns(columns):
    width = max(len(values) for values in columns.values())
    rows = []
    for i in range(width):
        rows.append({name: values[i] if i < len(values) else "" for name, values in columns.items()})
    return rows


def write_table(path, columns, delimiter):
    rows = pad_columns(columns)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(columns.keys()), delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def panel_a_excel_columns():
    rng = np.random.default_rng(PANEL_A_DATA_SEED)
    key_point_x = []
    key_point_y = []
    columns = {}

    for i, segment in enumerate(PANEL_A_SEGMENTS):
        x0, x1, _, _ = segment
        data_x = np.linspace(x0 + 0.015, x1 - 0.015, 6)
        data_y = segment_y(data_x, segment) + rng.normal(0.0, 0.010, data_x.size)
        key_point_x.extend(key_axis(data_x).tolist())
        key_point_y.extend(rank_axis(data_y).tolist())

        xs = np.linspace(x0, x1, 36)
        columns[f"segment_s{i + 1}_x_key_prefix_e12"] = key_axis(xs).tolist()
        columns[f"segment_s{i + 1}_y_rank_k_entries"] = rank_axis(segment_y(xs, segment)).tolist()

    columns = {
        "key_points_x_key_prefix_e12": key_point_x,
        "key_points_y_rank_k_entries": key_point_y,
        **columns,
        "boundary_s1_s2_x_key_prefix_e12": [float(key_axis(PANEL_A_SEGMENTS[0][1]))] * 2,
        "boundary_s1_s2_y_rank_k_entries": [0.0, VSST_ENTRIES_K],
        "boundary_s2_s3_x_key_prefix_e12": [float(key_axis(PANEL_A_SEGMENTS[1][1]))] * 2,
        "boundary_s2_s3_y_rank_k_entries": [0.0, VSST_ENTRIES_K],
    }
    return columns


def panel_b_excel_columns():
    x = np.linspace(0.06, 0.94, 160)
    columns = {}
    curves = []
    for segments, _color, label in PANEL_B_MODELS:
        y = rank_axis(model_value(x, segments))
        curves.append(y)
        safe_label = label.lower().replace(" #", "").replace(" ", "_")
        columns[f"{safe_label}_x_key_prefix_e12"] = key_axis(x).tolist()
        columns[f"{safe_label}_y_rank_k_entries"] = y.tolist()

        dot_x = np.array([(s[0] + s[1]) / 2 for s in segments])
        dot_y = np.array([(s[2] + s[3]) / 2 for s in segments])
        columns[f"{safe_label}_control_points_x_key_prefix_e12"] = key_axis(dot_x).tolist()
        columns[f"{safe_label}_control_points_y_rank_k_entries"] = rank_axis(dot_y).tolist()

    merged = np.sum(curves, axis=0)
    columns["merged_vsst_x_key_prefix_e12"] = key_axis(x).tolist()
    columns["merged_vsst_y_rank_k_entries"] = merged.tolist()

    for i, bp in enumerate(PANEL_B_BREAKPOINTS, 1):
        columns[f"boundary_{i}_x_key_prefix_e12"] = [float(key_axis(bp))] * 2
        columns[f"boundary_{i}_y_rank_k_entries"] = [0.0, VSST_ENTRIES_K * 2]

    return columns


def write_excel_export(out_dir, name):
    excel_dir = out_dir / f"{name}_excel_data"
    excel_dir.mkdir(parents=True, exist_ok=True)
    combined_csv = excel_dir / "vsst_learned_index_merge_data.csv"
    combined_tsv = excel_dir / "vsst_learned_index_merge_data.tsv"
    readme = excel_dir / "README.md"

    combined_columns = {}
    for name, values in panel_a_excel_columns().items():
        combined_columns[f"panel_a_{name}"] = values
    for name, values in panel_b_excel_columns().items():
        combined_columns[f"panel_b_{name}"] = values
    write_table(combined_csv, combined_columns, ",")
    write_table(combined_tsv, combined_columns, "\t")
    readme.write_text(
        "\n".join(
            [
                "# Excel plotting data",
                "",
                "Use an Excel XY Scatter chart. TSV files are the recommended input.",
                "",
                "Panel A:",
                "- Plot `panel_a_key_points_x_key_prefix_e12` vs `panel_a_key_points_y_rank_k_entries` as markers only.",
                "- Plot each `panel_a_segment_s*_x_key_prefix_e12` vs `panel_a_segment_s*_y_rank_k_entries` as lines.",
                "- Optional: plot `panel_a_boundary_*` as dotted vertical lines.",
                "",
                "Panel B:",
                "- Plot `panel_b_vsst1_*`, `panel_b_vsst2_*`, and `panel_b_merged_vsst_*` as line series.",
                "- Optional: plot `panel_b_*_control_points_*` as small markers.",
                "- Optional: plot `panel_b_boundary_*` as dotted vertical lines.",
                "",
                "Axis labels:",
                "- X: key prefix (x 10^12)",
                "- Y: rank (K entries)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return combined_tsv, combined_csv, readme


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="eval-vcomp/figures")
    parser.add_argument("--name", default="vsst_learned_index_merge")
    parser.add_argument("--excel-data", action="store_true", help="also write Excel-friendly CSV files")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / f"{args.name}.png"
    pdf = out_dir / f"{args.name}.pdf"

    plt.rcParams.update(
        {
            "font.family": font_family(),
            "font.serif": ["Cambria", "DejaVu Serif"],
            "axes.labelsize": 12,
            "xtick.labelsize": 10.5,
            "ytick.labelsize": 10.5,
            "figure.facecolor": "white",
        }
    )

    fig, axes = plt.subplots(1, 2, figsize=(11.8, 4.7))
    fig.subplots_adjust(left=0.075, right=0.985, top=0.84, bottom=0.16, wspace=0.28)

    draw_single_vsst(axes[0])
    draw_merge(axes[1])

    fig.savefig(png, dpi=240)
    fig.savefig(pdf)
    print(png)
    print(pdf)
    if args.excel_data:
        for path in write_excel_export(out_dir, args.name):
            print(path)


if __name__ == "__main__":
    main()
