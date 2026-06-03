#!/usr/bin/env python3
"""Plot wall-clock breakdowns for key 1TB VComp design variants."""

import csv
import os
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = os.path.dirname(__file__)
LOG_LOADS = os.path.join(ROOT, "log_loads")
OUT_PNG = os.path.join(LOG_LOADS, "vcomp_1tb_all_versions_with_bg_sweep_hbar.png")
OUT_CSV = os.path.join(LOG_LOADS, "vcomp_1tb_all_versions_with_bg_sweep.csv")

RUNS = [
    ("MemManifest slow", "vcomp_260527_1211_1000gb_memmanifest_1tb"),
    ("Lock fix", "vcomp_260527_1245_1000gb_memmanifest_unlock_fix_1tb"),
    ("Reg batch256", "vcomp_260527_1312_1000gb_regbatch256_1tb"),
    ("BG jobs 48", "vcomp_260531_1420_1000gb_bgjobs48_regbatch256_1tb_260531_140447"),
    ("Batch LogApply", "vcomp_260531_1503_1000gb_batchpatch_1tb_260531_150343"),
    ("L0 cap", "vcomp_260531_1555_1000gb_l0cap_1tb_260531_155557"),
    ("Closed-loop", "vcomp_260531_1711_1000gb_closedloop_1000gb_260531_171108"),
    ("Size gate 4GB", "vcomp_260601_0605_1000gb_sizegate4gb_1tb_260601_060501"),
    ("Commit batch", "vcomp_260601_0651_1000gb_commitbatch_1tb_260601_065139"),
    ("Release overlap", "vcomp_260601_0726_1000gb_release_overlap_1tb_260601_072600"),
    ("FNum no-lock", "vcomp_260601_0740_1000gb_fnum_nolock_phase1_1tb_260601_074000"),
    ("FNum reserve", "vcomp_260601_0749_1000gb_fnum_reserve_1tb_260601_074933"),
    ("Intra-L0 off", "vcomp_260601_1203_1000gb_intra_l0_off_1tb_260601_120348"),
    ("Active release", "vcomp_260601_1231_1000gb_active_release_1tb_260601_123116"),
    ("Reserve active", "vcomp_260601_1240_1000gb_reserve_active_1tb_260601_124051"),
    ("BG cap256/delay50", "vcomp_260601_1328_1000gb_bg256_1tb_260601_132851"),
    ("BG cap16/delay50", "vcomp_260601_1333_1000gb_bg16_1tb_260601_133318"),
    ("BG cap64/delay0", "vcomp_260601_1403_1000gb_bg64_delay0_1tb_260601_140316"),
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


def read_text(path):
    with open(path, errors="replace") as f:
        return f.read()


def floats(pattern, text, name):
    match = re.search(pattern, text)
    if not match:
        raise ValueError(f"missing {name}")
    return tuple(float(v) for v in match.groups())


def maybe_int(pattern, text):
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


def parse_run(label, run_dir):
    bench = os.path.join(LOG_LOADS, run_dir, "bench.out")
    text = read_text(bench)
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
    keys_written = maybe_int(r"Phase 2 \(materialization\): [0-9.]+ sec, ([0-9]+) keys written", text)
    vssts = maybe_int(r"Virtual SSTs:\s+([0-9]+)", text)

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
        "keys_written": keys_written,
        "vssts": vssts,
    }


def fmt_time(seconds):
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.1f}m"


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
    ] + [key for key, _, _ in COMPONENTS]
    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            out = {
                "label": row["label"],
                "run_dir": row["run_dir"],
                "total": row["total"],
                "phase1": row["phase1"],
                "wait": row["wait"],
                "phase2": row["phase2"],
                "keys_written": row["keys_written"] or "",
                "vssts": row["vssts"] or "",
            }
            out.update(row["values"])
            writer.writerow(out)


def draw(rows):
    plt.rcParams.update(
        {
            "font.size": 14,
            "axes.titlesize": 20,
            "axes.labelsize": 15,
            "xtick.labelsize": 12,
            "ytick.labelsize": 13,
            "legend.fontsize": 11,
        }
    )
    fig, ax = plt.subplots(figsize=(23, 13.5))
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
                height=0.55,
                color=color,
                edgecolor="white",
                linewidth=0.9,
                label=label if y == 0 else None,
            )
            if value >= max_total * 0.055:
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
    ax.set_xlim(0, max_total * 1.17)
    ax.set_xlabel("fillvirtual wall-clock time (seconds)")
    ax.set_title("1TB VComp Design Variants: Wall-Clock Breakdown", loc="left", fontweight="bold")
    ax.grid(axis="x", alpha=0.23)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.08),
        ncol=5,
        frameon=False,
        title="Component",
    )
    fig.text(
        0.01,
        0.01,
        "Note: L0 gate wait is not stacked because it overlaps foreground Phase 1 in the release thread.",
        fontsize=10,
        color="#555555",
    )
    fig.tight_layout(rect=(0, 0.055, 1, 1))
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
    print(f"Wrote {OUT_PNG}")
    print(f"Wrote {OUT_CSV}")


if __name__ == "__main__":
    main()
