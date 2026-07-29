#!/usr/bin/env python3
"""Parse the 24 q3 coverage dumps and plot per-level coverage %,
baseline vs vcomp, faceted by (size, kv) x distribution.

Usage: python3 plot_q3_24db_coverage.py <COV_DIR> [--out PNG] [--csv CSV]
"""
import argparse
import csv
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from paths import COVERAGE_DUMPS

LEVEL_RE = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+([\d.]+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)%"
)
# filename: <system>__s<size>gb_<kv>_<dist>.cov
NAME_RE = re.compile(r"^(baseline|vcomp)__s(\d+)gb_(\d+B)_(\w+)$")


def parse_cov(path):
    rows = []
    with open(path) as f:
        for line in f:
            m = LEVEL_RE.match(line)
            if m:
                rows.append({
                    "level": int(m.group(1)),
                    "files": int(m.group(2)),
                    "size_mb": float(m.group(3)),
                    "cov_pct": float(m.group(8)),
                })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cov_dir")
    ap.add_argument("--out", default=COVERAGE_DUMPS / "q3_24db_coverage.png")
    ap.add_argument("--csv", default=COVERAGE_DUMPS / "q3_24db_coverage.csv")
    args = ap.parse_args()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.csv).parent.mkdir(parents=True, exist_ok=True)

    cov_dir = Path(args.cov_dir)
    data = {}  # (size, kv, dist) -> {system -> {level -> cov_pct}}
    files_data = {}
    all_rows = []
    for p in sorted(cov_dir.glob("*.cov")):
        m = NAME_RE.match(p.stem)
        if not m:
            continue
        system, size, kv, dist = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        rows = parse_cov(p)
        key = (size, kv, dist)
        data.setdefault(key, {})[system] = {r["level"]: r["cov_pct"] for r in rows}
        files_data.setdefault(key, {})[system] = {r["level"]: r["files"] for r in rows}
        for r in rows:
            all_rows.append({"system": system, "size_gb": size, "kv": kv,
                             "distribution": dist, **r})

    # write csv
    with open(args.csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["system", "size_gb", "kv",
                                          "distribution", "level", "files",
                                          "size_mb", "cov_pct"])
        w.writeheader()
        w.writerows(all_rows)
    print(f"[csv] {args.csv}  ({len(all_rows)} rows)")

    # facet grid: rows = (size,kv), cols = distribution
    row_keys = [(500, "1024B"), (1000, "1024B"), (500, "91B"), (1000, "91B")]
    col_dists = ["uniform50", "unique100", "zipf99_50"]
    fig, axes = plt.subplots(len(row_keys), len(col_dists),
                             figsize=(16, 16), squeeze=False)
    colors = {"baseline": "#4C72B0", "vcomp": "#DD8452"}

    for ri, (size, kv) in enumerate(row_keys):
        for ci, dist in enumerate(col_dists):
            ax = axes[ri][ci]
            key = (size, kv, dist)
            sysmap = data.get(key, {})
            levels = sorted({lv for s in sysmap.values() for lv in s})
            if not levels:
                ax.set_visible(False)
                continue
            x = np.arange(len(levels))
            w = 0.38
            for j, system in enumerate(["baseline", "vcomp"]):
                covs = [sysmap.get(system, {}).get(lv, 0) for lv in levels]
                bars = ax.bar(x + (j - 0.5) * w, covs, w,
                              label=system, color=colors[system])
                for b, c in zip(bars, covs):
                    if c > 0:
                        ax.text(b.get_x() + b.get_width() / 2, c + 1,
                                f"{c:.0f}", ha="center", va="bottom", fontsize=7)
            ax.set_xticks(x)
            ax.set_xticklabels([f"L{lv}" for lv in levels])
            ax.set_ylim(0, 108)
            ax.set_title(f"{size}GB · {kv} · {dist}", fontsize=12)
            if ci == 0:
                ax.set_ylabel("coverage %")
            if ri == 0 and ci == 0:
                ax.legend(loc="lower right", fontsize=10)
            ax.grid(axis="y", alpha=0.3)

    fig.suptitle("Per-level key-space coverage % — baseline vs vcomp (24 DBs)",
                 fontsize=18, y=0.995)
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    fig.savefig(args.out, dpi=120)
    print(f"[png] {args.out}")


if __name__ == "__main__":
    main()
