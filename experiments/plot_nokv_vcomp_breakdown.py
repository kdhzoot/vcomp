#!/usr/bin/env python3
"""Plot 250GB no-KV vcomp breakdown."""

import os
import re

import matplotlib.pyplot as plt


ROOT = os.path.dirname(__file__)
LOG_LOADS = os.path.join(ROOT, "log_loads")
BASELINE_DIR = "baseline_260414_1305_250gb"
VCOMP_DIR = "vcomp_260414_1648_250gb"


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
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def main():
    baseline = parse_baseline_seconds(read_log(BASELINE_DIR))
    vcomp = parse_vcomp(read_log(VCOMP_DIR))
    speedup = baseline / vcomp["total"]

    print(
        f"250 GB baseline={baseline:.3f}s "
        f"vcomp={vcomp['total']:.3f}s speedup={speedup:.1f}x"
    )

    components = [
        ("keygen", "Key generation", "#254e70"),
        ("sort", "Sort", "#679436"),
        ("plr_fit", "PLR fit", "#f2c14e"),
        ("register", "VSST registration", "#d95d39"),
        ("wait", "BG wait", "#b8b8b8"),
        ("sst_write", "Materialize", "#5f4b8b"),
        ("version_edit", "VersionEdit", "#9a8c98"),
    ]

    plt.rcParams.update({
        "font.size": 17,
        "axes.titlesize": 22,
        "axes.labelsize": 18,
        "xtick.labelsize": 16,
        "ytick.labelsize": 17,
        "legend.fontsize": 14,
        "legend.title_fontsize": 15,
    })
    fig, ax = plt.subplots(figsize=(15.5, 3.6))

    y = 0
    left = 0.0
    for name, label, color in components:
        seconds = vcomp[name]
        ax.barh(
            y,
            seconds,
            height=0.34,
            left=left,
            label=f"{label}  {seconds:.1f}s",
            color=color,
            edgecolor="white",
            linewidth=1.0,
        )
        left += seconds

    ax.text(
        vcomp["total"] + 0.2,
        y,
        f"{vcomp['total']:.2f}s",
        ha="left",
        va="center",
        fontsize=17,
        fontweight="bold",
    )

    ax.set_title("250GB No-KV VComp Breakdown")
    ax.set_xlabel("Elapsed time (seconds)")
    ax.set_yticks([y])
    ax.set_yticklabels(["fillvirtual"])
    ax.set_xlim(0, vcomp["total"] * 1.15)
    ax.set_ylim(-0.5, 0.5)
    ax.grid(axis="x", alpha=0.22)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.legend(
        title="Component",
        loc="center left",
        bbox_to_anchor=(1.03, 0.5),
        frameon=False,
        borderaxespad=0.0,
    )
    fig.tight_layout(rect=[0.0, 0.0, 0.78, 1.0])

    out = os.path.join(LOG_LOADS, "nokv_vcomp_250gb_breakdown.png")
    fig.savefig(out, dpi=150)
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
