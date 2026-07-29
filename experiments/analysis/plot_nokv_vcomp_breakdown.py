#!/usr/bin/env python3
"""Plot no-KV vcomp breakdowns from existing load logs."""

import os
import re

import matplotlib.pyplot as plt

from paths import LOG_LOADS as LOG_LOADS_PATH

LOG_LOADS = str(LOG_LOADS_PATH)
RUNS = [
    {
        "label": "250GB",
        "slug": "250gb",
        "baseline_dir": "baseline_260414_1305_250gb",
        "vcomp_dir": "vcomp_260414_1648_250gb",
    },
    {
        "label": "1TB",
        "slug": "1tb",
        "baseline_dir": "baseline_260408_0639_1000gb",
        "vcomp_dir": "vcomp_260414_1152_1000gb",
    },
    {
        "label": "10TB",
        "slug": "10tb",
        "baseline_dir": "baseline_260430_2232_10240gb",
        "vcomp_dir": "vcomp_260501_1239_10240gb",
    },
]

COMPONENTS = [
    ("keygen", "Key generation", "#254e70"),
    ("sort", "Sort", "#679436"),
    ("plr_fit", "PLR fit", "#f2c14e"),
    ("register", "VSST registration", "#d95d39"),
    ("wait", "BG wait", "#b8b8b8"),
    ("sst_write", "Materialize", "#5f4b8b"),
    ("version_edit", "VersionEdit", "#9a8c98"),
]


def read_log(dirname):
    with open(os.path.join(LOG_LOADS, dirname, "bench.out"), errors="replace") as f:
        return f.read()


def parse_baseline_seconds(text):
    m = re.search(r"^fillrandom\s*:.*?\s([0-9.]+) seconds\s", text, re.M)
    if not m:
        raise ValueError("missing fillrandom line")
    return float(m.group(1))


def parse_vcomp(text):
    phase1_parts = re.search(
        r"Phase 1 breakdown: keygen=([0-9.]+)s sort=([0-9.]+)s "
        r"plr_fit=([0-9.]+)s register=([0-9.]+)s",
        text,
    )
    total = re.search(
        r"Total: ([0-9.]+) sec \(phase1=([0-9.]+) \+ wait=([0-9.]+) "
        r"\+ phase2=([0-9.]+)\)",
        text,
    )
    phase2_parts = re.search(
        r"Phase 2 breakdown: sst_write=([0-9.]+)s version_edit=([0-9.]+)s",
        text,
    )
    if not phase1_parts or not total or not phase2_parts:
        raise ValueError("missing vcomp phase breakdown")

    keygen, sort, plr, register = map(float, phase1_parts.groups())
    phase2_sst, phase2_edit = map(float, phase2_parts.groups())
    total_s, phase1, wait, phase2 = map(float, total.groups())

    phase1_other = max(0.0, phase1 - keygen - sort - plr - register)
    phase2_other = max(0.0, phase2 - phase2_sst - phase2_edit)
    return {
        "total": total_s,
        "keygen": keygen,
        "sort": sort,
        "plr_fit": plr,
        "register": register + phase1_other,
        "wait": wait,
        "sst_write": phase2_sst,
        "version_edit": phase2_edit + phase2_other,
    }


def fmt_seconds(seconds):
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def load_run(run):
    baseline = parse_baseline_seconds(read_log(run["baseline_dir"]))
    vcomp = parse_vcomp(read_log(run["vcomp_dir"]))
    return {
        **run,
        "baseline": baseline,
        "vcomp": vcomp,
        "speedup": baseline / vcomp["total"],
    }


def configure_style():
    plt.rcParams.update({
        "font.size": 17,
        "axes.titlesize": 22,
        "axes.labelsize": 18,
        "xtick.labelsize": 16,
        "ytick.labelsize": 17,
        "legend.fontsize": 14,
        "legend.title_fontsize": 15,
    })


def draw_bar(ax, row, show_legend):
    y = 0
    left = 0.0
    vcomp = row["vcomp"]
    for name, label, color in COMPONENTS:
        seconds = vcomp[name]
        ax.barh(
            y,
            seconds,
            height=0.34,
            left=left,
            label=f"{label}" if show_legend else None,
            color=color,
            edgecolor="white",
            linewidth=1.0,
        )
        if seconds >= vcomp["total"] * 0.08:
            ax.text(
                left + seconds / 2,
                y,
                fmt_seconds(seconds),
                ha="center",
                va="center",
                fontsize=11,
                color="white",
                fontweight="bold",
            )
        left += seconds

    ax.text(
        vcomp["total"] * 1.01,
        y,
        f"{fmt_seconds(vcomp['total'])}\n{row['speedup']:.1f}x faster",
        ha="left",
        va="center",
        fontsize=14,
        fontweight="bold",
    )

    ax.set_yticks([y])
    ax.set_yticklabels([row["label"]])
    ax.set_xlim(0, vcomp["total"] * 1.22)
    ax.set_ylim(-0.5, 0.5)
    ax.grid(axis="x", alpha=0.22)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)


def plot_single(row):
    fig, ax = plt.subplots(figsize=(15.5, 3.6))
    draw_bar(ax, row, show_legend=True)
    ax.set_title(f"{row['label']} No-KV VComp Breakdown")
    ax.set_xlabel("Elapsed time (seconds)")
    ax.legend(
        title="Component",
        loc="center left",
        bbox_to_anchor=(1.03, 0.5),
        frameon=False,
        borderaxespad=0.0,
    )
    fig.tight_layout(rect=[0.0, 0.0, 0.78, 1.0])

    out = os.path.join(LOG_LOADS, f"nokv_vcomp_{row['slug']}_breakdown.png")
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"Saved: {out}")


def plot_multi(rows):
    fig, axes = plt.subplots(len(rows), 1, figsize=(15.5, 7.5))
    for i, (ax, row) in enumerate(zip(axes, rows)):
        draw_bar(ax, row, show_legend=(i == 0))
        ax.set_xlabel("Elapsed time (seconds)")
    fig.suptitle("No-KV VComp Breakdown Across Scales", y=0.98)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        title="Component",
        loc="lower center",
        ncol=4,
        frameon=False,
        bbox_to_anchor=(0.5, 0.0),
    )
    fig.tight_layout(rect=[0.0, 0.1, 1.0, 0.94])
    out = os.path.join(LOG_LOADS, "nokv_vcomp_breakdown_250gb_1tb_10tb.png")
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"Saved: {out}")


def main():
    configure_style()
    rows = [load_run(run) for run in RUNS]
    for row in rows:
        print(
            f"{row['label']} baseline={row['baseline']:.3f}s "
            f"vcomp={row['vcomp']['total']:.3f}s "
            f"speedup={row['speedup']:.1f}x"
        )
        plot_single(row)
    plot_multi(rows)


if __name__ == "__main__":
    main()
