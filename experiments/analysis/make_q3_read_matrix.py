#!/usr/bin/env python3
"""Build the Q3 read-workload DB matrix from load summary TSV files."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from paths import LOG_LOADS



def latest_summary(pattern: str) -> Path | None:
    matches = sorted(LOG_LOADS.glob(pattern), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def read_summary(path: Path, system: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if row.get("status") != "ok":
                continue
            rows.append(
                {
                    "system": system,
                    "case_id": row["case_id"],
                    "size_gb": row["size_gb"],
                    "kv_label": row["kv_label"],
                    "key_size": row["key_size"],
                    "value_size": row["value_size"],
                    "distribution": row["distribution"],
                    "unique_ratio": row["unique_ratio"],
                    "zipf_alpha": row["zipf_alpha"],
                    "db_dir": row["db_dir"],
                    "load_log_dir": row["log_dir"],
                    "source_summary": str(path),
                    "load_status": row.get("status", ""),
                    "load_elapsed_sec": row.get("elapsed_sec", ""),
                    "load_peak_rss_kb": row.get("peak_rss_kb", ""),
                    "load_peak_rss_gb": row.get("peak_rss_gb", ""),
                    "load_bench_sec": row.get("bench_sec", ""),
                    "load_bench_ops_sec": row.get("bench_ops_sec", ""),
                    "load_db_size": row.get("db_size", ""),
                    "trace_path": row.get("trace_path", ""),
                }
            )
    return rows


def parse_args() -> argparse.Namespace:
    default_baseline = (
        LOG_LOADS / "trace_baseline_after_91b_snappy_260606_1851" / "summary.tsv"
    )
    default_vcomp = [
        latest_summary("kmv512_final_sweep500_1024b_traceparallel8_*/summary.tsv"),
        latest_summary("kmv512_final_sweep500_91b_traceparallel8_*/summary.tsv"),
    ]
    default_vcomp = [p for p in default_vcomp if p is not None]

    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-summary", type=Path, default=default_baseline)
    parser.add_argument(
        "--vcomp-summary",
        type=Path,
        action="append",
        default=None,
        help="May be provided multiple times.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=LOG_LOADS / "q3_read_db_matrix.tsv",
    )
    parser.add_argument("--sizes", default="500,1000")
    parser.add_argument("--kv-labels", default="91B,1024B")
    parser.add_argument("--distributions", default="unique100,uniform50,zipf99_50")
    parser.add_argument(
        "--include-load-stats",
        action="store_true",
        help="Append loading-time/stat columns. Do not use this file directly with older runners.",
    )
    args = parser.parse_args()
    if args.vcomp_summary is None:
        args.vcomp_summary = default_vcomp
    return args


def main() -> None:
    args = parse_args()
    sizes = set(args.sizes.split(","))
    kv_labels = set(args.kv_labels.split(","))
    distributions = set(args.distributions.split(","))

    rows: list[dict[str, str]] = []
    if args.baseline_summary.exists():
        rows.extend(read_summary(args.baseline_summary, "baseline"))
    for path in args.vcomp_summary:
        if path.exists():
            rows.extend(read_summary(path, "vcomp"))

    rows = [
        row
        for row in rows
        if row["size_gb"] in sizes
        and row["kv_label"] in kv_labels
        and row["distribution"] in distributions
    ]
    rows.sort(
        key=lambda r: (
            int(r["size_gb"]),
            r["kv_label"],
            r["distribution"],
            r["system"],
        )
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "system",
        "case_id",
        "size_gb",
        "kv_label",
        "key_size",
        "value_size",
        "distribution",
        "unique_ratio",
        "zipf_alpha",
        "db_dir",
        "load_log_dir",
        "source_summary",
    ]
    if args.include_load_stats:
        fields.extend(
            [
                "load_status",
                "load_elapsed_sec",
                "load_peak_rss_kb",
                "load_peak_rss_gb",
                "load_bench_sec",
                "load_bench_ops_sec",
                "load_db_size",
                "trace_path",
            ]
        )
    with args.out.open("w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=fields, delimiter="\t", extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)
    print(args.out)
    print(f"rows={len(rows)}")


if __name__ == "__main__":
    main()
