#!/usr/bin/env python3
"""Plot a one-page summary for vcomp accuracy trace JSON files."""

import argparse
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def compaction_type(row):
    return f"L{row['start_level']} -> L{row['output_level']}"


def percentile(values, p):
    if len(values) == 0:
        return 0.0
    return float(np.percentile(values, p))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    trace_dir = Path(args.trace_dir)
    out = Path(args.out)
    rows = [
        json.loads(path.read_text())
        for path in sorted(trace_dir.glob("vcomp_accuracy_job_*.json"))
    ]
    if not rows:
        raise SystemExit(f"no trace json files in {trace_dir}")

    actual_counts = np.array([row["actual_outputs"] for row in rows], dtype=float)
    predicted_counts = np.array([row["predicted_outputs"] for row in rows], dtype=float)
    abs_byte_error = np.array(
        [abs(float(row["byte_error_pct"])) for row in rows], dtype=float
    )
    boundary_error = []
    for row in rows:
        matched = min(len(row["predicted"]), len(row["actual"]))
        for i in range(matched):
            pred = row["predicted"][i]
            actual = row["actual"][i]
            boundary_error.append(abs(pred["key_min"] - actual["key_min"]))
            boundary_error.append(abs(pred["key_max"] - actual["key_max"]))
    boundary_error = np.array(boundary_error, dtype=float)
    count_errors = [int(row["count_error"]) for row in rows]

    types = []
    for row in rows:
        typ = compaction_type(row)
        if typ not in types:
            types.append(typ)
    types = sorted(types, key=lambda t: (int(t.split()[0][1:]), int(t.split()[-1][1:])))

    palette = {
        "L0 -> L0": "#d98255",
        "L0 -> L1": "#c94f5d",
        "L1 -> L2": "#4f9a8a",
        "L2 -> L3": "#4e79a7",
        "L3 -> L4": "#8f79b8",
    }

    fig, axes = plt.subplots(2, 2, figsize=(15.5, 10.0))
    fig.subplots_adjust(left=0.075, right=0.92, top=0.88, bottom=0.09,
                        wspace=0.28, hspace=0.34)
    fig.suptitle("Virtual Compaction Accuracy on Real Compaction Inputs",
                 fontsize=22, fontweight="bold", y=0.965)
    fig.text(
        0.5,
        0.925,
        f"100GB, 1KB KV, no compression, {len(rows):,} real compaction jobs",
        ha="center",
        fontsize=14,
        color="#333333",
    )

    # A. Output count parity
    ax = axes[0, 0]
    for typ in types:
        subset = [row for row in rows if compaction_type(row) == typ]
        ax.scatter(
            [row["actual_outputs"] for row in subset],
            [row["predicted_outputs"] for row in subset],
            s=22,
            alpha=0.58,
            color=palette.get(typ, "#777777"),
            label=f"{typ} (n={len(subset)})",
        )
    max_count = int(max(actual_counts.max(), predicted_counts.max()))
    ax.plot([0, max_count + 1], [0, max_count + 1], "--", lw=1.5,
            color="#222222", label="perfect match")
    ax.set_title("A. Output SST Count Parity", fontsize=16, fontweight="bold")
    ax.set_xlabel("Actual output SST count", fontsize=13)
    ax.set_ylabel("Predicted output SST count", fontsize=13)
    ax.set_xlim(-1, max_count + 2)
    ax.set_ylim(-1, max_count + 2)
    ax.grid(True, alpha=0.24)
    ax.legend(fontsize=9.5, frameon=False, loc="upper left")

    # B. Relative output bytes error CDF
    ax = axes[0, 1]
    xs = np.sort(abs_byte_error)
    ys = np.arange(1, len(xs) + 1) / len(xs)
    ax.plot(xs, ys, lw=3, color="#1f5b89")
    p50 = percentile(abs_byte_error, 50)
    p95 = percentile(abs_byte_error, 95)
    for value, label, ypos in [(p50, "p50", 0.20), (p95, "p95", 0.42)]:
        ax.axvline(value, ls="--", lw=1.5, color="#b65a48")
        ax.text(
            value,
            ypos,
            f"{label} {value:.2f}%",
            rotation=90,
            ha="right",
            va="bottom",
            fontsize=11,
            color="#8e3d32",
        )
    ax.set_title("B. Output Size Error CDF", fontsize=16, fontweight="bold")
    ax.set_xlabel("|Predicted bytes - actual bytes| / actual bytes (%)",
                  fontsize=13)
    ax.set_ylabel("Fraction of compaction jobs", fontsize=13)
    ax.set_xlim(left=0)
    ax.set_ylim(0, 1.02)
    ax.grid(True, alpha=0.24)

    # C. Boundary error CDF
    ax = axes[1, 0]
    xs = np.sort(boundary_error)
    ys = np.arange(1, len(xs) + 1) / len(xs)
    ax.plot(xs, ys, lw=3, color="#6d5a8a")
    ax.set_xscale("symlog", linthresh=100)
    p50_boundary = percentile(boundary_error, 50)
    p95_boundary = percentile(boundary_error, 95)
    for value, label, ypos in [
        (p50_boundary, "p50", 0.20),
        (p95_boundary, "p95", 0.42),
    ]:
        ax.axvline(value, ls="--", lw=1.5, color="#7a5fa2")
        ax.text(
            value,
            ypos,
            f"{label} {value:.0f}",
            rotation=90,
            ha="right",
            va="bottom",
            fontsize=11,
            color="#5a4773",
        )
    ax.set_title("C. Output Boundary Error CDF", fontsize=16,
                 fontweight="bold")
    ax.set_xlabel("|Predicted boundary key - actual boundary key| "
                  "(uint64-prefix domain)", fontsize=13)
    ax.set_ylabel("Fraction of matched output boundaries", fontsize=13)
    ax.set_ylim(0, 1.02)
    ax.grid(True, alpha=0.24, which="both")

    # D. Output count error histogram
    ax = axes[1, 1]
    labels = sorted(set(count_errors))
    values = [count_errors.count(label) for label in labels]
    bars = ax.bar([str(label) for label in labels], values, color="#5d837f")
    for bar, value in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + max(values) * 0.015,
            f"{value:,}",
            ha="center",
            va="bottom",
            fontsize=12,
        )
    ax.set_title("D. Output Count Error Distribution", fontsize=16,
                 fontweight="bold")
    ax.set_xlabel("Predicted output count - actual output count", fontsize=13)
    ax.set_ylabel("Compaction jobs", fontsize=13)
    ax.grid(True, axis="y", alpha=0.24)

    summary = (
        f"Summary: output count mean {predicted_counts.mean():.3f} predicted vs "
        f"{actual_counts.mean():.3f} actual; output size error mean "
        f"{abs_byte_error.mean():.2f}%, p95 {p95:.2f}%, max "
        f"{abs_byte_error.max():.2f}%."
    )
    fig.text(0.075, 0.025, summary, fontsize=12.5, color="#333333")

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=220)
    print(out)


if __name__ == "__main__":
    main()
