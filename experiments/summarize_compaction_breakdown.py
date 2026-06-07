#!/usr/bin/env python3
"""Summarize VCOMP_PERF_COMPACTION_BREAKDOWN records.

The input may be RocksDB LOG files with key=value breakdown lines, raw extracted
breakdown lines, or TSV files generated from those lines.
"""

from __future__ import annotations

import argparse
import csv
import re
import statistics
import sys
from pathlib import Path
from typing import Iterable


TIME_FIELDS = [
    "read_us",
    "write_us",
    "merge_us",
    "compress_us",
    "decompress_us",
    "sst_build_us",
    "other_us",
    "total_tracked_us",
]


def parse_int(value: str | None) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def parse_key_value_lines(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if "VCOMP_PERF_COMPACTION_BREAKDOWN" not in line:
                continue
            row = dict(re.findall(r"([A-Za-z0-9_]+)=([^ \t\r\n]+)", line))
            if row:
                rows.append(row)
    return rows


def parse_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return [dict(row) for row in reader]


def load_rows(paths: Iterable[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(path)
        if path.suffix == ".tsv":
            parsed = parse_tsv(path)
        else:
            parsed = parse_key_value_lines(path)
        for row in parsed:
            key = (
                row.get("job", ""),
                row.get("start_level", ""),
                row.get("output_level", ""),
                row.get("total_tracked_us", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows


def percentile(sorted_values: list[int], pct: float) -> int:
    if not sorted_values:
        return 0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = pct / 100.0 * (len(sorted_values) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(sorted_values) - 1)
    frac = rank - lower
    return int(sorted_values[lower] * (1 - frac) + sorted_values[upper] * frac)


def format_group(rows: list[dict[str, str]], group_name: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    total_us = sum(parse_int(row.get("total_tracked_us")) for row in rows)
    job_count = len(rows)
    for field in TIME_FIELDS:
        values = sorted(parse_int(row.get(field)) for row in rows)
        field_total = sum(values)
        avg_us = statistics.mean(values) if values else 0
        out.append(
            {
                "group": group_name,
                "metric": field,
                "jobs": str(job_count),
                "total_s": f"{field_total / 1_000_000:.6f}",
                "pct_total": f"{(field_total / total_us * 100) if total_us else 0:.2f}",
                "avg_ms_per_job": f"{avg_us / 1000:.6f}",
                "p50_ms": f"{percentile(values, 50) / 1000:.6f}",
                "p95_ms": f"{percentile(values, 95) / 1000:.6f}",
                "p99_ms": f"{percentile(values, 99) / 1000:.6f}",
                "max_ms": f"{(values[-1] if values else 0) / 1000:.6f}",
            }
        )
    return out


def write_tsv(rows: list[dict[str, str]], output: Path | None) -> None:
    fields = [
        "group",
        "metric",
        "jobs",
        "total_s",
        "pct_total",
        "avg_ms_per_job",
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "max_ms",
    ]
    if output is None:
        writer = csv.DictWriter(sys.stdout, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--by-case", action="store_true")
    parser.add_argument("--by-level", action="store_true")
    args = parser.parse_args()

    rows = load_rows(args.inputs)
    summary_rows = format_group(rows, "all")
    if args.by_case:
        groups: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            key = row.get("case", "?")
            groups.setdefault(key, []).append(row)
        for key in sorted(groups):
            summary_rows.extend(format_group(groups[key], key))
    if args.by_level:
        groups: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            key = f"{row.get('start_level', '?')}->{row.get('output_level', '?')}"
            groups.setdefault(key, []).append(row)
        for key in sorted(groups):
            summary_rows.extend(format_group(groups[key], key))
    write_tsv(summary_rows, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
