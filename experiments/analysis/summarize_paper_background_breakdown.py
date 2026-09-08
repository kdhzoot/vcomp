#!/usr/bin/env python3
"""Validate and summarize paper background compaction-breakdown runs."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


CATEGORIES = ("SST Build", "Merge", "I/O", "Other")
REQUIRED_COLUMNS = {
    "job",
    "status",
    "read_us",
    "write_us",
    "merge_us",
    "compress_us",
    "decompress_us",
    "sst_build_us",
    "other_us",
    "total_tracked_us",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        metavar="SERIES=TSV",
        help="Run label and compaction_breakdown_all.tsv path; repeat per series",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def parse_run(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise ValueError(f"invalid --run value: {value}")
    series, raw_path = value.split("=", 1)
    if not series or not raw_path:
        raise ValueError(f"invalid --run value: {value}")
    return series, Path(raw_path)


def nonnegative_int(row: dict[str, str], column: str, source: Path) -> int:
    try:
        value = int(row[column])
    except (KeyError, ValueError) as error:
        raise ValueError(f"{source}: invalid {column} in job {row.get('job')}") from error
    if value < 0:
        raise ValueError(f"{source}: negative {column} in job {row.get('job')}")
    return value


def summarize(series: str, source: Path) -> list[dict[str, object]]:
    if not source.is_file():
        raise FileNotFoundError(source)
    with source.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        columns = set(reader.fieldnames or ())
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise ValueError(f"{source}: missing columns: {sorted(missing)}")
        rows = list(reader)

    if not rows:
        raise ValueError(f"{source}: no compaction rows")

    totals = {category: 0 for category in CATEGORIES}
    seen_jobs: set[str] = set()
    for row in rows:
        job = row["job"]
        if job in seen_jobs:
            raise ValueError(f"{source}: duplicate compaction job {job}")
        seen_jobs.add(job)
        if row["status"] != "ok":
            raise ValueError(f"{source}: unsuccessful compaction job {job}")

        read_us = nonnegative_int(row, "read_us", source)
        write_us = nonnegative_int(row, "write_us", source)
        merge_us = nonnegative_int(row, "merge_us", source)
        build_us = nonnegative_int(row, "sst_build_us", source)
        other_us = nonnegative_int(row, "other_us", source)
        compress_us = nonnegative_int(row, "compress_us", source)
        decompress_us = nonnegative_int(row, "decompress_us", source)
        tracked_us = nonnegative_int(row, "total_tracked_us", source)

        if compress_us != 0 or decompress_us != 0:
            raise ValueError(f"{source}: compression time in no-compression job {job}")
        component_sum = read_us + write_us + merge_us + build_us + other_us
        if component_sum != tracked_us:
            raise ValueError(
                f"{source}: job {job} components {component_sum} != tracked {tracked_us}"
            )

        totals["SST Build"] += build_us
        totals["Merge"] += merge_us
        totals["I/O"] += read_us + write_us
        totals["Other"] += other_us

    tracked_total = sum(totals.values())
    return [
        {
            "series": series,
            "replicates": 1,
            "category": category,
            "time_us": value,
            "time_sec": f"{value / 1_000_000:.6f}",
            "time_hour": f"{value / 3_600_000_000:.9f}",
            "fraction": f"{value / tracked_total:.9f}",
            "tracked_total_us": tracked_total,
            "tracked_total_hour": f"{tracked_total / 3_600_000_000:.9f}",
            "jobs": len(rows),
            "status": "ok_single_run",
            "source": str(source),
        }
        for category, value in totals.items()
    ]


def main() -> None:
    args = parse_args()
    parsed_runs = [parse_run(value) for value in args.run]
    labels = [series for series, _ in parsed_runs]
    if len(set(labels)) != len(labels):
        raise ValueError(f"duplicate series labels: {labels}")

    rows = [row for series, source in parsed_runs for row in summarize(series, source)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
