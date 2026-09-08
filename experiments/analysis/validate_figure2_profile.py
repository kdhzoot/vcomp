#!/usr/bin/env python3
"""Extract and validate Figure 2 flush/compaction profiler records."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


FLUSH_FIELDS = (
    "case", "job", "cf", "reason", "memtables", "input_entries",
    "input_bytes", "output_files", "output_bytes", "status", "mempurge",
    "sort_us", "sst_build_us", "write_us", "compress_us", "other_us",
    "total_tracked_us", "compression",
)
COMPACTION_FIELDS = (
    "case", "job", "cf", "start_level", "output_level", "reason",
    "input_files", "output_files", "input_bytes", "output_bytes", "status",
    "read_us", "write_us", "merge_us", "compress_us", "decompress_us",
    "sst_build_us", "other_us", "total_tracked_us", "prepare_us", "init_us",
    "run_subcompactions_us", "subcompaction_setup_us", "process_kv_us",
    "process_kv_excl_output_us", "open_output_us", "finish_output_us",
    "finalize_subcompaction_us", "collect_errors_us", "sync_dirs_us",
    "verify_output_us", "set_props_us", "aggregate_stats_us", "input_stats_us",
    "record_verify_us", "finalize_run_us", "install_stats_us", "install_edit_us",
    "log_and_apply_us", "install_total_us", "compaction_run_us",
    "compaction_cpu_us", "bytes_read_non_output", "bytes_read_output",
    "bytes_written", "compression",
)
KV_PATTERN = re.compile(r"([A-Za-z0-9_]+)=([^ \t\r\n]+)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--mode", choices=("baseline", "l0only"), required=True)
    parser.add_argument("--db-dir", type=Path, required=True)
    parser.add_argument("--bench-out", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def load_records(paths: list[Path], marker: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for path in paths:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if marker not in line:
                    continue
                row = dict(KV_PATTERN.findall(line))
                key = tuple(sorted(row.items()))
                if row and key not in seen:
                    seen.add(key)
                    rows.append(row)
    return rows


def integer(row: dict[str, str], field: str, kind: str) -> int:
    try:
        value = int(row[field])
    except (KeyError, ValueError) as error:
        raise ValueError(f"{kind} job {row.get('job', '?')}: invalid {field}") from error
    if value < 0:
        raise ValueError(f"{kind} job {row.get('job', '?')}: negative {field}")
    return value


def validate_jobs(rows: list[dict[str, str]], kind: str, components: tuple[str, ...]) -> None:
    seen_jobs: set[str] = set()
    for row in rows:
        job = row.get("job", "")
        if not job or job in seen_jobs:
            raise ValueError(f"{kind}: missing or duplicate job ID {job!r}")
        seen_jobs.add(job)
        if row.get("status") != "ok":
            raise ValueError(f"{kind} job {job}: status={row.get('status')}")
        total = sum(integer(row, field, kind) for field in components)
        if total != integer(row, "total_tracked_us", kind):
            raise ValueError(f"{kind} job {job}: component sum {total} != tracked total")


def write_tsv(path: Path, fields: tuple[str, ...], case: str,
              rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t",
                                extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            output = dict(row)
            output["case"] = case
            if "process_kv_excl_output_us" in fields:
                output["process_kv_excl_output_us"] = str(max(
                    0,
                    integer(output, "process_kv_us", "compaction")
                    - integer(output, "open_output_us", "compaction")
                    - integer(output, "finish_output_us", "compaction"),
                ))
            writer.writerow(output)


def main() -> None:
    args = parse_args()
    log_paths = sorted(path for path in args.db_dir.glob("LOG*") if path.is_file())
    paths = [*log_paths, args.bench_out]
    if not log_paths or not args.bench_out.is_file():
        raise FileNotFoundError("missing RocksDB LOG files or bench.out")

    flush = load_records(paths, "VCOMP_PERF_FLUSH_BREAKDOWN")
    compaction = load_records(paths, "VCOMP_PERF_COMPACTION_BREAKDOWN")
    if not flush:
        raise ValueError("no flush profiler records")
    if args.mode == "baseline" and not compaction:
        raise ValueError("conventional load produced no compaction profiler records")
    if args.mode == "l0only" and compaction:
        raise ValueError("no-compaction load produced compaction profiler records")

    validate_jobs(flush, "flush", ("sort_us", "sst_build_us", "write_us",
                                    "compress_us", "other_us"))
    validate_jobs(compaction, "compaction", ("read_us", "write_us", "merge_us",
                                              "compress_us", "decompress_us",
                                              "sst_build_us", "other_us"))
    if any(integer(row, "compress_us", "flush") for row in flush):
        raise ValueError("compression time observed in no-compression flush")
    if any(integer(row, "compress_us", "compaction") or
           integer(row, "decompress_us", "compaction") for row in compaction):
        raise ValueError("compression time observed in no-compression compaction")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_tsv(args.output_dir / "flush_breakdown.tsv", FLUSH_FIELDS, args.case, flush)
    write_tsv(args.output_dir / "compaction_breakdown.tsv", COMPACTION_FIELDS,
              args.case, compaction)

    metrics = {
        "flush_jobs": len(flush),
        "compaction_jobs": len(compaction),
        "flush_output_bytes": sum(integer(row, "output_bytes", "flush") for row in flush),
        "compaction_output_bytes": sum(integer(row, "bytes_written", "compaction") for row in compaction),
        "flush_tracked_us": sum(integer(row, "total_tracked_us", "flush") for row in flush),
        "compaction_tracked_us": sum(integer(row, "total_tracked_us", "compaction") for row in compaction),
    }
    with (args.output_dir / "profile_metrics.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(metrics)
        writer.writerow(metrics.values())


if __name__ == "__main__":
    main()
