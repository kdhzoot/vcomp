#!/usr/bin/env python3
"""Extract load-time compaction write statistics from RocksDB LOG files."""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from pathlib import Path


EVENT_RE = re.compile(r"EVENT_LOG_v1\s+(\{.*\})\s*$")
KV_RE = re.compile(r"([A-Za-z0-9_]+)=([^ \t\r\n]+)")
TS_RE = re.compile(r"^(\d{4}/\d{2}/\d{2}-\d{2}:\d{2}:\d{2}\.\d+)")
VCOMP_LEGACY_RE = re.compile(
    r"Virtual compaction L(?P<start_level>\d+) -> L(?P<output_level>\d+): "
    r"(?P<input_files>\d+) inputs \[(?P<input_level_summary>[^\]]*)\] "
    r"\((?P<input_segments>\d+) segs\), (?P<output_files>\d+) outputs, "
    r"(?P<output_entries>\d+) entries \(naive (?P<input_entries>\d+), "
    r"dedup (?P<dedup_entries>\d+)\), total=(?P<total_us>\d+)us "
    r"gather=(?P<gather_us>\d+)us merge=(?P<merge_us>\d+)us "
    r"split=(?P<split_us>\d+)us mutex_wait=(?P<mutex_wait_us>\d+)us "
    r"edit=(?P<edit_us>\d+)us log_apply=(?P<log_apply_us>\d+)us "
    r"commit_queue=(?P<commit_queue_us>\d+)us commit_batch=(?P<commit_batch>\d+)"
)


JOB_FIELDS = [
    "system",
    "case_id",
    "ts",
    "job",
    "start_level",
    "output_level",
    "reason",
    "input_files",
    "input_level_summary",
    "input_segments",
    "input_entries",
    "input_bytes",
    "output_sst_count",
    "output_entries",
    "output_bytes",
    "key_min",
    "key_max",
    "dedup_entries",
    "dedup_rate",
    "target_sst_size",
    "avg_entry_size",
    "use_kmv",
    "total_us",
    "gather_us",
    "merge_us",
    "split_us",
    "mutex_wait_us",
    "edit_us",
    "log_apply_us",
    "commit_queue_us",
    "commit_batch",
]

OUTPUT_FIELDS = [
    "system",
    "case_id",
    "ts",
    "job",
    "start_level",
    "output_level",
    "out_idx",
    "file",
    "entries",
    "bytes",
    "key_min",
    "key_max",
    "split_pos",
    "split_has_prev",
    "split_prev_key_max",
    "split_key_min",
    "split_gap",
]

LEVEL_FIELDS = [
    "system",
    "case_id",
    "start_level",
    "output_level",
    "compaction_count",
    "input_bytes_total",
    "output_bytes_total",
    "input_entries_total",
    "output_entries_total",
    "output_files_total",
    "dedup_entries_total",
    "dedup_rate",
    "input_bytes_avg",
    "input_bytes_p50",
    "input_bytes_p95",
    "input_bytes_max",
    "output_bytes_avg",
    "output_bytes_p50",
    "output_bytes_p95",
    "output_bytes_max",
    "output_files_avg",
    "output_files_p50",
    "output_files_p95",
    "output_files_max",
]


def timestamp(line: str) -> str:
    match = TS_RE.match(line)
    return match.group(1) if match else ""


def to_int(value: str | int | None) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    rank = pct / 100.0 * (len(values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(values) - 1)
    frac = rank - lo
    return int(values[lo] * (1.0 - frac) + values[hi] * frac)


def normalize_job(row: dict[str, str], system: str, case_id: str) -> dict[str, str]:
    out = {field: "" for field in JOB_FIELDS}
    out["system"] = system
    out["case_id"] = case_id
    for key, value in row.items():
        if key in out:
            out[key] = str(value)
    if "output_files" in row:
        out["output_sst_count"] = str(row["output_files"])
    return out


def parse_vcomp_log(path: Path, system: str, case_id: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    jobs: list[dict[str, str]] = []
    legacy_jobs: list[dict[str, str]] = []
    outputs: list[dict[str, str]] = []
    legacy_job_id = 0
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if "VCOMP_WRITE_JOB" in line:
                row = dict(KV_RE.findall(line))
                row["ts"] = timestamp(line)
                jobs.append(normalize_job(row, system, case_id))
            elif "VCOMP_WRITE_OUTPUT" in line:
                row = {field: "" for field in OUTPUT_FIELDS}
                row.update(dict(KV_RE.findall(line)))
                row["system"] = system
                row["case_id"] = case_id
                row["ts"] = timestamp(line)
                outputs.append(row)
            elif "Virtual compaction L" in line:
                match = VCOMP_LEGACY_RE.search(line)
                if not match:
                    continue
                row = match.groupdict()
                row["ts"] = timestamp(line)
                row["job"] = str(legacy_job_id)
                legacy_job_id += 1
                input_entries = to_int(row.get("input_entries"))
                dedup_entries = to_int(row.get("dedup_entries"))
                row["dedup_rate"] = (
                    f"{dedup_entries / input_entries:.8f}"
                    if input_entries
                    else "0.00000000"
                )
                legacy_jobs.append(normalize_job(row, system, case_id))
    if not jobs:
        jobs = legacy_jobs
    return jobs, outputs


def parse_baseline_log(path: Path, system: str, case_id: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    started: dict[str, dict[str, str]] = {}
    jobs: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            match = EVENT_RE.search(line)
            if not match:
                continue
            try:
                event = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            job = str(event.get("job", ""))
            if event.get("event") == "compaction_started":
                files_by_level: dict[int, int] = {}
                for key, value in event.items():
                    if key.startswith("files_L") and isinstance(value, list):
                        try:
                            files_by_level[int(key[7:])] = len(value)
                        except ValueError:
                            pass
                start_level = min(files_by_level) if files_by_level else ""
                input_level_summary = ",".join(
                    f"L{level}={files_by_level[level]}" for level in sorted(files_by_level)
                )
                started[job] = {
                    "ts": timestamp(line),
                    "job": job,
                    "start_level": str(start_level),
                    "reason": str(event.get("compaction_reason", "")),
                    "input_files": str(sum(files_by_level.values())),
                    "input_level_summary": input_level_summary,
                    "input_bytes": str(event.get("input_data_size", "")),
                }
            elif event.get("event") == "compaction_finished":
                base = started.get(job, {"job": job, "ts": timestamp(line)})
                row = dict(base)
                row["output_level"] = str(event.get("output_level", ""))
                row["output_sst_count"] = str(event.get("num_output_files", ""))
                row["output_bytes"] = str(event.get("total_output_size", ""))
                row["input_entries"] = str(event.get("num_input_records", ""))
                row["output_entries"] = str(event.get("num_output_records", ""))
                dropped = max(
                    0,
                    to_int(row.get("input_entries")) - to_int(row.get("output_entries")),
                )
                row["dedup_entries"] = str(dropped)
                input_entries = to_int(row.get("input_entries"))
                row["dedup_rate"] = (
                    f"{dropped / input_entries:.8f}" if input_entries else "0.00000000"
                )
                row["total_us"] = str(event.get("compaction_time_micros", ""))
                jobs.append(normalize_job(row, system, case_id))
    return jobs, []


def level_summary(jobs: list[dict[str, str]]) -> list[dict[str, str]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}
    for row in jobs:
        key = (
            row["system"],
            row["case_id"],
            row["start_level"],
            row["output_level"],
        )
        groups.setdefault(key, []).append(row)

    summary: list[dict[str, str]] = []
    for (system, case_id, start_level, output_level), rows in sorted(groups.items()):
        input_bytes = [to_int(row.get("input_bytes")) for row in rows]
        output_bytes = [to_int(row.get("output_bytes")) for row in rows]
        output_files = [to_int(row.get("output_sst_count")) for row in rows]
        input_entries_total = sum(to_int(row.get("input_entries")) for row in rows)
        output_entries_total = sum(to_int(row.get("output_entries")) for row in rows)
        dedup_entries_total = sum(to_int(row.get("dedup_entries")) for row in rows)
        row = {
            "system": system,
            "case_id": case_id,
            "start_level": start_level,
            "output_level": output_level,
            "compaction_count": str(len(rows)),
            "input_bytes_total": str(sum(input_bytes)),
            "output_bytes_total": str(sum(output_bytes)),
            "input_entries_total": str(input_entries_total),
            "output_entries_total": str(output_entries_total),
            "output_files_total": str(sum(output_files)),
            "dedup_entries_total": str(dedup_entries_total),
            "dedup_rate": (
                f"{dedup_entries_total / input_entries_total:.8f}"
                if input_entries_total
                else "0.00000000"
            ),
            "input_bytes_avg": f"{statistics.mean(input_bytes):.2f}" if input_bytes else "0",
            "input_bytes_p50": str(percentile(input_bytes, 50)),
            "input_bytes_p95": str(percentile(input_bytes, 95)),
            "input_bytes_max": str(max(input_bytes) if input_bytes else 0),
            "output_bytes_avg": f"{statistics.mean(output_bytes):.2f}" if output_bytes else "0",
            "output_bytes_p50": str(percentile(output_bytes, 50)),
            "output_bytes_p95": str(percentile(output_bytes, 95)),
            "output_bytes_max": str(max(output_bytes) if output_bytes else 0),
            "output_files_avg": f"{statistics.mean(output_files):.2f}" if output_files else "0",
            "output_files_p50": str(percentile(output_files, 50)),
            "output_files_p95": str(percentile(output_files, 95)),
            "output_files_max": str(max(output_files) if output_files else 0),
        }
        summary.append(row)
    return summary


def write_tsv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--system", required=True, choices=("baseline", "vcomp"))
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    if args.system == "vcomp":
        jobs, outputs = parse_vcomp_log(args.log, args.system, args.case_id)
    else:
        jobs, outputs = parse_baseline_log(args.log, args.system, args.case_id)

    write_tsv(args.out_dir / "write_jobs.tsv", JOB_FIELDS, jobs)
    write_tsv(args.out_dir / "write_outputs.tsv", OUTPUT_FIELDS, outputs)
    write_tsv(args.out_dir / "write_level_summary.tsv", LEVEL_FIELDS, level_summary(jobs))
    print(f"jobs={len(jobs)} outputs={len(outputs)} out_dir={args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
