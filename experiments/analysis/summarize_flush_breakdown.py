#!/usr/bin/env python3
"""Validate and summarize VCOMP_PERF_FLUSH_BREAKDOWN records."""

from __future__ import annotations

import argparse
import csv
import re
import statistics
import sys
from pathlib import Path
from typing import Iterable


TIME_FIELDS = (
    "sort_us",
    "sst_build_us",
    "write_us",
    "compress_us",
    "other_us",
    "total_tracked_us",
)
COMPONENT_FIELDS = TIME_FIELDS[:-1]
REQUIRED_FIELDS = {"job", "status", *TIME_FIELDS}


def parse_int(row: dict[str, str], field: str, source: Path) -> int:
    try:
        value = int(row[field])
    except (KeyError, ValueError) as error:
        raise ValueError(
            f"{source}: invalid {field} in flush job {row.get('job', '?')}"
        ) from error
    if value < 0:
        raise ValueError(
            f"{source}: negative {field} in flush job {row.get('job', '?')}"
        )
    return value


def parse_key_value_lines(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if "VCOMP_PERF_FLUSH_BREAKDOWN" not in line:
                continue
            row = dict(re.findall(r"([A-Za-z0-9_]+)=([^ \t\r\n]+)", line))
            if row:
                rows.append(row)
    return rows


def parse_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        columns = set(reader.fieldnames or ())
        missing = REQUIRED_FIELDS - columns
        if missing:
            raise ValueError(f"{path}: missing columns: {sorted(missing)}")
        return [dict(row) for row in reader]


def load_rows(paths: Iterable[Path]) -> list[tuple[Path, dict[str, str]]]:
    rows: list[tuple[Path, dict[str, str]]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
        parsed = parse_tsv(path) if path.suffix == ".tsv" else parse_key_value_lines(path)
        for row in parsed:
            key = tuple(sorted(row.items()))
            if key in seen:
                continue
            seen.add(key)
            rows.append((path, row))
    return rows


def validate(rows: list[tuple[Path, dict[str, str]]]) -> None:
    if not rows:
        raise ValueError("no flush breakdown records")
    for source, row in rows:
        missing = REQUIRED_FIELDS - row.keys()
        if missing:
            raise ValueError(f"{source}: missing fields: {sorted(missing)}")
        if row["status"] != "ok":
            raise ValueError(
                f"{source}: unsuccessful flush job {row.get('job', '?')}"
            )
        component_sum = sum(parse_int(row, field, source) for field in COMPONENT_FIELDS)
        tracked = parse_int(row, "total_tracked_us", source)
        if component_sum != tracked:
            raise ValueError(
                f"{source}: flush job {row['job']} components {component_sum} "
                f"!= tracked {tracked}"
            )
    if sum(int(row["total_tracked_us"]) for _, row in rows) == 0:
        raise ValueError("flush tracked time is zero")


def percentile(sorted_values: list[int], pct: float) -> int:
    if not sorted_values:
        return 0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = pct / 100.0 * (len(sorted_values) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = rank - lower
    return int(
        sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction
    )


def summarize(rows: list[tuple[Path, dict[str, str]]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    total_us = sum(int(row["total_tracked_us"]) for _, row in rows)
    for field in TIME_FIELDS:
        values = sorted(int(row[field]) for _, row in rows)
        field_total = sum(values)
        output.append(
            {
                "metric": field,
                "jobs": str(len(rows)),
                "total_s": f"{field_total / 1_000_000:.6f}",
                "pct_total": f"{field_total / total_us * 100:.2f}",
                "avg_ms_per_job": f"{statistics.mean(values) / 1000:.6f}",
                "p50_ms": f"{percentile(values, 50) / 1000:.6f}",
                "p95_ms": f"{percentile(values, 95) / 1000:.6f}",
                "p99_ms": f"{percentile(values, 99) / 1000:.6f}",
                "max_ms": f"{values[-1] / 1000:.6f}",
            }
        )
    return output


def write_tsv(rows: list[dict[str, str]], output: Path | None) -> None:
    fields = list(rows[0])
    if output is None:
        writer = csv.DictWriter(sys.stdout, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    rows = load_rows(args.inputs)
    validate(rows)
    write_tsv(summarize(rows), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
