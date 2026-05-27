#!/usr/bin/env python3
"""Plot 250GB Twitter trace with-KV vcomp breakdown."""

import os
import re

import matplotlib.pyplot as plt


ROOT = os.path.dirname(__file__)
LOG_LOADS = os.path.join(ROOT, "log_loads")
BASELINE_DIR = "baseline_tl_cluster012_240M_250gb"
VCOMP_DIR = "vcomp_tl_cluster012_240M_rangeshard100_direct"


def read_log(dirname):
    with open(os.path.join(LOG_LOADS, dirname, "bench.out"), errors="replace") as f:
        return f.read()


def parse_baseline_seconds(text):
    m = re.search(r"^twitterload\s*:.*?\s([0-9.]+) seconds\s", text, re.M)
    if not m:
        raise ValueError("missing twitterload line")
    return float(m.group(1))


def parse_vcomp(text):
    phase1_parts = re.search(
        r"Phase 1 breakdown: keygen=([0-9.]+)s sort=([0-9.]+)s "
        r"plr_fit=([0-9.]+)s mutex=([0-9.]+)s regbuild=([0-9.]+)s "
        r"addfile=([0-9.]+)s register=([0-9.]+)s",
        text,
    )
    total = re.search(
        r"Total: ([0-9.]+) sec \(phase1=([0-9.]+) \+ wait=([0-9.]+) "
        r"\+ phase2=([0-9.]+)\)",
        text,
    )
    phase2a = re.search(
        r"Phase 2a breakdown: trace_read=([0-9.]+)s sort_dedup=([0-9.]+)s "
        r"assign=([0-9.]+)s sst_build=([0-9.]+)s",
        text,
    )
    phase2a_total = re.search(
        r"Phase 2a \(materialize \+ SST write\): ([0-9.]+) sec", text
    )
    phase2_parts = re.search(
        r"Phase 2 breakdown: sst_write=([0-9.]+)s version_edit=([0-9.]+)s",
        text,
    )
    if not all([phase1_parts, total, phase2a, phase2a_total, phase2_parts]):
        raise ValueError("missing vcomp phase breakdown")

    keygen, sort, plr, mutex, regbuild, addfile, register = map(
        float, phase1_parts.groups()
    )
    total_s, phase1, wait, phase2 = map(float, total.groups())
    trace_read, sort_dedup, assign, materialize = map(float, phase2a.groups())
    phase2a_s = float(phase2a_total.group(1))
    _, version_edit = map(float, phase2_parts.groups())

    phase1_other = max(0.0, phase1 - keygen - sort - plr - mutex - regbuild - addfile - register)
    materialize_overhead = max(
        0.0, phase2a_s - trace_read - sort_dedup - assign - materialize
    )
    phase2_other = max(0.0, phase2 - phase2a_s - version_edit)

    return {
        "total": total_s,
        "keygen": keygen,
        "shape_sort": sort,
        "plr_fit": plr,
        "vsst_register": mutex + regbuild + addfile + register + phase1_other,
        "bg_wait": wait,
        "trace_read": trace_read,
        "kv_sort_dedup": sort_dedup,
        "assign": assign,
        "materialize": materialize,
        "materialize_overhead": materialize_overhead,
        "version_edit": version_edit + phase2_other,
    }


def main():
    baseline = parse_baseline_seconds(read_log(BASELINE_DIR))
    vcomp = parse_vcomp(read_log(VCOMP_DIR))
    speedup = baseline / vcomp["total"]

    print(
        f"250GB Twitter+KV baseline={baseline:.3f}s "
        f"vcomp={vcomp['total']:.3f}s speedup={speedup:.2f}x"
    )

    components = [
        ("keygen", "Key replay + shard write", "#264653"),
        ("shape_sort", "Shape sort", "#2a9d8f"),
        ("plr_fit", "PLR fit", "#e9c46a"),
        ("vsst_register", "VSST registration", "#f4a261"),
        ("bg_wait", "BG wait", "#b8b8b8"),
        ("trace_read", "Shard read", "#457b9d"),
        ("kv_sort_dedup", "KV sort/dedup", "#e76f51"),
        ("assign", "Partition assign", "#8ab17d"),
        ("materialize", "Materialize write", "#6d597a"),
        ("materialize_overhead", "Materialize overhead", "#9a8c98"),
        ("version_edit", "VersionEdit", "#6c757d"),
    ]

    plt.rcParams.update({
        "font.size": 17,
        "axes.titlesize": 21,
        "axes.labelsize": 18,
        "xtick.labelsize": 16,
        "ytick.labelsize": 17,
        "legend.fontsize": 13,
        "legend.title_fontsize": 14,
    })
    fig, ax = plt.subplots(figsize=(22.0, 3.7))

    y = 0
    left = 0.0
    for name, label, color in components:
        seconds = vcomp[name]
        if seconds <= 0:
            continue
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
        vcomp["total"] + 2.0,
        y,
        f"{vcomp['total']:.1f}s",
        ha="left",
        va="center",
        fontsize=17,
        fontweight="bold",
    )

    fig.suptitle("250GB Twitter Trace with KV: Range-Sharded VComp Breakdown", y=0.96)
    fig.text(
        0.39,
        0.84,
        f"Baseline {baseline:.1f}s -> VComp {vcomp['total']:.1f}s "
        f"({speedup:.2f}x faster)",
        ha="center",
        va="center",
        fontsize=17,
    )
    ax.set_xlabel("Elapsed time (seconds)")
    ax.set_yticks([y])
    ax.set_yticklabels(["fillvirtual"])
    ax.set_xlim(0, vcomp["total"] * 1.18)
    ax.set_ylim(-0.5, 0.5)
    ax.grid(axis="x", alpha=0.22)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.legend(
        title="Component",
        loc="center left",
        bbox_to_anchor=(1.02, 0.5),
        ncol=2,
        columnspacing=1.5,
        handletextpad=0.6,
        frameon=False,
        borderaxespad=0.0,
    )
    fig.tight_layout(rect=[0.03, 0.0, 0.66, 0.84])

    out = os.path.join(LOG_LOADS, "twitter_kv_vcomp_250gb_breakdown.png")
    fig.savefig(out, dpi=150)
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
