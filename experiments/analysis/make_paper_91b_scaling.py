#!/usr/bin/env python3
"""Merge validated true-91B baseline runs and plot measured loading time."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, Iterable, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


EXPECTED_SIZES = (500, 1000, 2000, 4000, 8000)
ACCEPTED_STATUS = {"ok", "ok_recovered"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--historical-summary", type=Path, required=True)
    parser.add_argument("--new-summary", type=Path, required=True)
    parser.add_argument("--output-tsv", type=Path, required=True)
    parser.add_argument("--output-pdf", type=Path, required=True)
    parser.add_argument("--output-png", type=Path, required=True)
    return parser.parse_args()


def read_rows(path: Path) -> Iterable[Dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle, delimiter="\t")


def validate_row(row: Dict[str, str], source: Path) -> Dict[str, str]:
    expected = {
        "system": "baseline",
        "kv": "91B",
        "key_size": "48",
        "value_size": "43",
        "compression_type": "none",
        "threads": "1",
        "memtable": "vector",
    }
    mismatches = [
        f"{key}={row.get(key)!r} (expected {value!r})"
        for key, value in expected.items()
        if row.get(key) != value
    ]
    if mismatches:
        raise ValueError(f"Invalid row in {source}: " + ", ".join(mismatches))
    if row.get("status") not in ACCEPTED_STATUS:
        raise ValueError(f"Unusable status in {source}: {row.get('status')!r}")
    for field in ("elapsed_sec", "bench_sec", "bench_ops_sec"):
        if not row.get(field):
            raise ValueError(f"Missing {field} in {source}")
        if float(row[field]) <= 0:
            raise ValueError(f"Non-positive {field} in {source}: {row[field]}")
    return row


def select_rows(historical: Path, new: Path) -> List[Dict[str, str]]:
    selected: Dict[int, Dict[str, str]] = {}
    for source, allowed_sizes in (
        (historical, set(EXPECTED_SIZES[:-1])),
        (new, {EXPECTED_SIZES[-1]}),
    ):
        for raw in read_rows(source):
            if not raw.get("size_gb"):
                continue
            size = int(raw["size_gb"])
            if size not in allowed_sizes:
                continue
            if size in selected:
                raise ValueError(f"Duplicate {size} GB row across input summaries")
            row = validate_row(raw, source)
            row = dict(row)
            row["source_summary"] = str(source.resolve())
            selected[size] = row

    missing = [size for size in EXPECTED_SIZES if size not in selected]
    if missing:
        raise ValueError(f"Missing required measured sizes: {missing}")
    return [selected[size] for size in EXPECTED_SIZES]


def write_summary(rows: List[Dict[str, str]], path: Path) -> None:
    fields = [
        "size_gb",
        "size_label",
        "status",
        "elapsed_sec",
        "elapsed_hours",
        "bench_sec",
        "bench_hours",
        "bench_ops_sec",
        "db_size",
        "total_write_gb",
        "ingest_gb",
        "compaction_write_gb",
        "compaction_wamp",
        "run_id",
        "log_dir",
        "db_dir",
        "source_summary",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            size = int(row["size_gb"])
            writer.writerow(
                {
                    "size_gb": size,
                    "size_label": "500 GB" if size == 500 else f"{size // 1000} TB",
                    "status": row["status"],
                    "elapsed_sec": row["elapsed_sec"],
                    "elapsed_hours": f"{float(row['elapsed_sec']) / 3600:.4f}",
                    "bench_sec": row["bench_sec"],
                    "bench_hours": f"{float(row['bench_sec']) / 3600:.4f}",
                    "bench_ops_sec": row["bench_ops_sec"],
                    "db_size": row.get("db_size", ""),
                    "total_write_gb": row.get("total_write_gb", ""),
                    "ingest_gb": row.get("ingest_gb", ""),
                    "compaction_write_gb": row.get("compaction_write_gb", ""),
                    "compaction_wamp": row.get("compaction_wamp", ""),
                    "run_id": row.get("run_id", ""),
                    "log_dir": row.get("log_dir", ""),
                    "db_dir": row.get("db_dir", ""),
                    "source_summary": row["source_summary"],
                }
            )


def plot(rows: List[Dict[str, str]], pdf: Path, png: Path) -> None:
    labels = ["500 GB", "1 TB", "2 TB", "4 TB", "8 TB"]
    hours = [float(row["elapsed_sec"]) / 3600 for row in rows]

    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": 8,
            "axes.labelsize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    fig, ax = plt.subplots(figsize=(3.3, 2.25))
    bars = ax.bar(
        labels,
        hours,
        width=0.68,
        color="#0072B2",
        edgecolor="black",
        linewidth=0.8,
        zorder=3,
    )
    ax.set_xlabel("DB size")
    ax.set_ylabel("Loading time (hours)")
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#D9D9D9", linewidth=0.7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_ylim(0, max(hours) * 1.16)
    for bar, value in zip(bars, hours):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + max(hours) * 0.025,
            f"{value:.1f}",
            ha="center",
            va="bottom",
            fontsize=7.5,
        )
    fig.tight_layout(pad=0.4)
    for path in (pdf, png):
        path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(pdf, bbox_inches="tight")
    fig.savefig(png, dpi=300, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    args = parse_args()
    rows = select_rows(args.historical_summary, args.new_summary)
    write_summary(rows, args.output_tsv)
    plot(rows, args.output_pdf, args.output_png)


if __name__ == "__main__":
    main()
