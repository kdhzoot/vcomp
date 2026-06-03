#!/usr/bin/env python3
"""Plot completed 5TB VComp wall-clock breakdowns."""

import csv
import os
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = os.path.dirname(__file__)
LOG_LOADS = os.path.join(ROOT, "log_loads")
OUT_PNG = os.path.join(LOG_LOADS, "vcomp_5tb_breakdown_compare_hbar.png")
OUT_CSV = os.path.join(LOG_LOADS, "vcomp_5tb_breakdown_compare.csv")

RUNS = [
    ("Original batch256", "vcomp_260527_1349_5000gb_rocksdbvs_batch256_5tb"),
    ("Size gate 4GB", "vcomp_260601_0608_5000gb_sizegate4gb_5tb_260601_060829"),
    ("BG cap64/delay0", "vcomp_260601_1426_5000gb_bg64_delay0_5tb_260601_142622"),
    ("Active PLR", "vcomp_260601_1851_5000gb_activeplr"),
    ("LogApply detail", "vcomp_260602_123754_5000gb_logapply_detail"),
    ("Latest patch", "vcomp_260602_1354_5000gb_latestpatch_260602_135454"),
]

COMPONENTS = [
    ("keygen", "Key gen", "#264653"),
    ("sort", "Sort", "#2a9d8f"),
    ("plr", "PLR fit", "#e9c46a"),
    ("file_mutex", "FileNo mutex", "#8ab17d"),
    ("metadata", "VSST metadata", "#b7b7a4"),
    ("register", "L0 register", "#d95d39"),
    ("wait", "BG wait", "#adb5bd"),
    ("materialize", "Materialize", "#5f4b8b"),
    ("version_edit", "VersionEdit", "#6c757d"),
    ("other", "Other", "#ced4da"),
]


def read_text(run_dir):
    with open(os.path.join(LOG_LOADS, run_dir, "bench.out"), errors="replace") as f:
        return f.read()


def floats(pattern, text, name):
    match = re.search(pattern, text)
    if not match:
        raise ValueError(f"missing {name}")
    return tuple(float(v) for v in match.groups())


def maybe_int(pattern, text):
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


def maybe_float(pattern, text, group=1):
    match = re.search(pattern, text)
    return float(match.group(group)) if match else None


def parse_run(label, run_dir):
    text = read_text(run_dir)
    keygen, sort, plr, file_mutex, regbuild, addfile, register = floats(
        r"Phase 1 breakdown: keygen=([0-9.]+)s sort=([0-9.]+)s "
        r"plr_fit=([0-9.]+)s mutex=([0-9.]+)s regbuild=([0-9.]+)s "
        r"addfile=([0-9.]+)s register=([0-9.]+)s",
        text,
        "phase1 breakdown",
    )
    total, phase1, wait, phase2 = floats(
        r"Total: ([0-9.]+) sec \(phase1=([0-9.]+) \+ wait=([0-9.]+) "
        r"\+ phase2=([0-9.]+)\)",
        text,
        "total",
    )
    sst_write, version_edit = floats(
        r"Phase 2 breakdown: sst_write=([0-9.]+)s version_edit=([0-9.]+)s",
        text,
        "phase2 breakdown",
    )
    values = {
        "keygen": keygen,
        "sort": sort,
        "plr": plr,
        "file_mutex": file_mutex,
        "metadata": regbuild + addfile,
        "register": register,
        "wait": wait,
        "materialize": sst_write,
        "version_edit": version_edit,
    }
    values["other"] = max(0.0, total - sum(values.values()))
    return {
        "label": label,
        "run_dir": run_dir,
        "total": total,
        "phase1": phase1,
        "wait": wait,
        "phase2": phase2,
        "values": values,
        "keys_written": maybe_int(
            r"Phase 2 \(materialization\): [0-9.]+ sec, ([0-9]+) keys written",
            text,
        ),
        "vssts": maybe_int(r"Virtual SSTs:\s+([0-9]+)", text),
        "bg_jobs": maybe_int(r"BG virtual compaction breakdown: jobs=([0-9]+)", text),
        "bg_avg_ms": maybe_float(
            r"BG virtual compaction breakdown: .* avg=([0-9.]+)ms", text
        ),
        "bg_avg_batch": maybe_float(r"BG virtual commit batching: .* avg_batch=([0-9.]+)", text),
        "bg_queue_wait": maybe_float(r"BG virtual commit batching: .* queue_wait=([0-9.]+)s", text),
        "release_visible_stats": maybe_float(
            r"L0 release thread breakdown: .* visible_stats=([0-9.]+)s", text
        ),
    }


def fmt_time(seconds):
    if seconds >= 3600:
        return f"{seconds / 3600:.1f}h"
    if seconds >= 60:
        return f"{seconds / 60:.1f}m"
    return f"{seconds:.1f}s"


def write_csv(rows):
    fields = [
        "label",
        "run_dir",
        "total",
        "phase1",
        "wait",
        "phase2",
        "keys_written",
        "vssts",
        "bg_jobs",
        "bg_avg_ms",
        "bg_avg_batch",
        "bg_queue_wait",
        "release_visible_stats",
    ] + [key for key, _, _ in COMPONENTS]
    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            out = {k: row.get(k, "") for k in fields if k not in row["values"]}
            out.update(row["values"])
            writer.writerow(out)


def draw(rows):
    plt.rcParams.update(
        {
            "font.size": 13,
            "axes.titlesize": 20,
            "axes.labelsize": 14,
            "xtick.labelsize": 12,
            "ytick.labelsize": 13,
            "legend.fontsize": 11,
        }
    )
    fig, ax = plt.subplots(figsize=(18.8, 8.2))
    max_total = max(row["total"] for row in rows)

    for y, row in enumerate(rows):
        left = 0.0
        for key, label, color in COMPONENTS:
            value = row["values"].get(key, 0.0)
            if value <= 0:
                continue
            ax.barh(
                y,
                value,
                left=left,
                height=0.58,
                color=color,
                edgecolor="white",
                linewidth=0.9,
                label=label if y == 0 else None,
            )
            if value >= max_total * 0.05:
                ax.text(
                    left + value / 2,
                    y,
                    fmt_time(value),
                    ha="center",
                    va="center",
                    color="white",
                    fontsize=10,
                    fontweight="bold",
                )
            left += value
        ax.text(
            row["total"] + max_total * 0.012,
            y,
            fmt_time(row["total"]),
            ha="left",
            va="center",
            fontsize=12,
            fontweight="bold",
        )

    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([row["label"] for row in rows])
    ax.invert_yaxis()
    ax.set_xlim(0, max_total * 1.14)
    ax.set_xlabel("fillvirtual wall-clock time (seconds)")
    ax.set_title("5TB VComp Wall-Clock Breakdown", loc="left", fontweight="bold")
    ax.grid(axis="x", alpha=0.24)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.16),
        ncol=5,
        frameon=False,
        title="Component",
    )
    fig.tight_layout(rect=[0, 0.08, 1, 1])
    fig.savefig(OUT_PNG, dpi=220)


def main():
    rows = [parse_run(label, run_dir) for label, run_dir in RUNS]
    write_csv(rows)
    draw(rows)
    for row in rows:
        print(
            f"{row['label']}: total={row['total']:.3f}s "
            f"phase1={row['phase1']:.3f}s wait={row['wait']:.3f}s "
            f"phase2={row['phase2']:.3f}s keys={row['keys_written']}"
        )
    print(OUT_PNG)
    print(OUT_CSV)


if __name__ == "__main__":
    main()
