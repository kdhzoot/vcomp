#!/usr/bin/env python3
"""Validate and summarize the Figure 4 uniform-read cache experiment."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


CONFIG_ORDER = {
    "A_cache_zero": 0,
    "B_cache_5pct": 1,
    "C_pinned_zero": 2,
    "D_pinned_5pct": 3,
}
CONFIG_LABELS = {
    "A_cache_zero": "Cache, 0%",
    "B_cache_5pct": "Cache, 5%",
    "C_pinned_zero": "Pinned, 0%",
    "D_pinned_5pct": "Pinned, 5%",
}
SYSTEM_ORDER = {
    "baseline": 0,
    "flush_only": 1,
    "last_comp": 2,
    "fillseq": 3,
    "fillseq_ow": 4,
    "f2load": 5,
}
SYSTEM_LABELS = {
    "baseline": "Baseline",
    "flush_only": "Flush-only",
    "last_comp": "Last-comp",
    "fillseq": "Fillseq",
    "fillseq_ow": "Fillseq+OW",
    "f2load": "F2Load",
}

BENCHMARK_RE = re.compile(
    r"^workloadc\s+:\s+([0-9.]+) micros/op\s+([0-9.]+) ops/sec\s+"
    r"([0-9.]+) seconds\s+([0-9]+) operations;",
    re.MULTILINE,
)
PERCENTILE_RE = re.compile(
    r"^Percentiles: P50:\s*([0-9.]+).*?P99:\s*([0-9.]+)",
    re.MULTILINE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-root",
        action="append",
        required=True,
        type=Path,
        help="Experiment root; repeat for the primary and baseline supplement",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def ticker(text: str, name: str) -> int:
    matches = re.findall(
        rf"^{re.escape(name)} COUNT\s*:\s*([0-9]+)", text, re.MULTILINE
    )
    if not matches:
        raise ValueError(f"missing ticker: {name}")
    return int(matches[-1])


def command_value(command: str, flag: str) -> str:
    match = re.search(rf"--{re.escape(flag)}=([^\s]+)", command)
    if not match:
        raise ValueError(f"missing --{flag} in recorded command")
    return match.group(1).replace("\\", "")


def read_rows(root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    summaries = sorted(root.glob("*/q3_read_summary.tsv"))
    if not summaries:
        raise ValueError(f"no configuration summaries under {root}")
    for summary in summaries:
        config_id = summary.parent.name
        if config_id not in CONFIG_ORDER:
            raise ValueError(f"unknown configuration directory: {config_id}")
        with summary.open(encoding="utf-8", newline="") as handle:
            for raw in csv.DictReader(handle, delimiter="\t"):
                if raw["status"] != "ok":
                    raise ValueError(
                        f"failed run: {config_id}/{raw['system']}={raw['status']}"
                    )
                result_dir = Path(raw["result_dir"])
                stdout = (result_dir / "stdout.txt").read_text()
                command = (result_dir / "raw" / "run_cmd.sh").read_text()
                time_text = (result_dir / "raw" / "time.out").read_text()

                required_command_values = {
                    "benchmarks": "workloadc,stats,levelstats",
                    "duration": "300",
                    "threads": "48",
                    "ops_between_duration_checks": "1",
                    "ycsb_requestdistribution": "uniform",
                    "readonly": "true",
                    "disable_auto_compactions": "true",
                    "open_files": "-1",
                    "use_direct_reads": "true",
                }
                for flag, expected in required_command_values.items():
                    actual = command_value(command, flag)
                    if actual != expected:
                        raise ValueError(
                            f"{config_id}/{raw['system']}: --{flag}={actual}, "
                            f"expected {expected}"
                        )

                benchmark = BENCHMARK_RE.search(stdout)
                percentiles = PERCENTILE_RE.search(stdout)
                rss = re.search(
                    r"Maximum resident set size \(kbytes\):\s*([0-9]+)",
                    time_text,
                )
                if not benchmark or not percentiles or not rss:
                    raise ValueError(
                        f"missing benchmark/histogram/RSS: {config_id}/{raw['system']}"
                    )

                avg_latency_us = float(benchmark.group(1))
                throughput = float(benchmark.group(2))
                measured_seconds = float(benchmark.group(3))
                operations = int(benchmark.group(4))
                if not 299.0 <= measured_seconds <= 302.0:
                    raise ValueError(
                        f"unexpected duration: {config_id}/{raw['system']} "
                        f"{measured_seconds}"
                    )

                bloom_useful = ticker(stdout, "rocksdb.bloom.filter.useful")
                bloom_positive = ticker(
                    stdout, "rocksdb.bloom.filter.full.positive"
                )
                bloom_true_positive = ticker(
                    stdout, "rocksdb.bloom.filter.full.true.positive"
                )
                filter_probes = bloom_useful + bloom_positive

                filter_hit = ticker(stdout, "rocksdb.block.cache.filter.hit")
                filter_miss = ticker(stdout, "rocksdb.block.cache.filter.miss")
                index_hit = ticker(stdout, "rocksdb.block.cache.index.hit")
                index_miss = ticker(stdout, "rocksdb.block.cache.index.miss")
                data_hit = ticker(stdout, "rocksdb.block.cache.data.hit")
                data_miss = ticker(stdout, "rocksdb.block.cache.data.miss")
                cache_events = (
                    filter_hit
                    + filter_miss
                    + index_hit
                    + index_miss
                    + data_hit
                    + data_miss
                )
                disk_read_ios = int(float(raw["disk_read_ios"]))
                disk_read_mib = float(raw["disk_read_mb"])
                cache_counter_valid = not (
                    cache_events == 0 and disk_read_ios > 0
                )

                cache_size = int(command_value(command, "cache_size"))
                metadata_in_cache = (
                    command_value(command, "cache_index_and_filter_blocks")
                    == "true"
                )
                expected_in_cache = config_id.startswith(("A_", "B_"))
                expected_size = 1 if config_id.endswith("zero") else 50 * 1024**3
                if metadata_in_cache != expected_in_cache or cache_size != expected_size:
                    raise ValueError(
                        f"cache mismatch: {config_id}/{raw['system']} "
                        f"size={cache_size}, metadata_in_cache={metadata_in_cache}"
                    )

                rows.append(
                    {
                        "config_id": config_id,
                        "config_label": CONFIG_LABELS[config_id],
                        "metadata_placement": (
                            "cache" if metadata_in_cache else "pinned"
                        ),
                        "cache_fraction": "0%" if cache_size == 1 else "5%",
                        "cache_size_bytes": str(cache_size),
                        "system": raw["system"],
                        "system_label": SYSTEM_LABELS[raw["system"]],
                        "status": raw["status"],
                        "measured_seconds": f"{measured_seconds:.3f}",
                        "operations": str(operations),
                        "throughput_ops_sec": f"{throughput:.6f}",
                        "avg_latency_us": f"{avg_latency_us:.6f}",
                        "p50_latency_us": f"{float(percentiles.group(1)):.6f}",
                        "p99_latency_us": f"{float(percentiles.group(2)):.6f}",
                        "bloom_useful": str(bloom_useful),
                        "bloom_full_positive": str(bloom_positive),
                        "bloom_full_true_positive": str(bloom_true_positive),
                        "filter_probes": str(filter_probes),
                        "filter_probes_per_op": f"{filter_probes / operations:.9f}",
                        "bloom_positive_per_op": (
                            f"{bloom_positive / operations:.9f}"
                        ),
                        "bloom_true_positive_per_op": (
                            f"{bloom_true_positive / operations:.9f}"
                        ),
                        "filter_cache_hit": str(filter_hit),
                        "filter_cache_miss": str(filter_miss),
                        "index_cache_hit": str(index_hit),
                        "index_cache_miss": str(index_miss),
                        "data_cache_hit": str(data_hit),
                        "data_cache_miss": str(data_miss),
                        "cache_counter_valid": (
                            "true" if cache_counter_valid else "false"
                        ),
                        "disk_read_ios": str(disk_read_ios),
                        "disk_read_mib": f"{disk_read_mib:.6f}",
                        "disk_read_ios_per_op": f"{disk_read_ios / operations:.9f}",
                        "disk_read_kib_per_op": (
                            f"{disk_read_mib * 1024 / operations:.9f}"
                        ),
                        "peak_rss_gib": f"{int(rss.group(1)) / 1024**2:.9f}",
                        "result_dir": str(result_dir),
                        "source_db_dir": raw["source_db_dir"],
                    }
                )
    return rows


def main() -> None:
    args = parse_args()
    rows: list[dict[str, str]] = []
    for root in args.run_root:
        rows.extend(read_rows(root))

    keys = [(row["config_id"], row["system"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate configuration/system measurements")
    missing = [
        (config_id, system)
        for config_id in CONFIG_ORDER
        for system in SYSTEM_ORDER
        if (config_id, system) not in set(keys)
    ]
    if missing:
        raise ValueError(f"missing measurements: {missing}")

    baselines = {
        row["config_id"]: row
        for row in rows
        if row["system"] == "baseline"
    }
    for row in rows:
        baseline = baselines[row["config_id"]]
        row["throughput_vs_baseline"] = (
            f"{float(row['throughput_ops_sec']) / float(baseline['throughput_ops_sec']):.9f}"
        )
        row["latency_vs_baseline"] = (
            f"{float(row['avg_latency_us']) / float(baseline['avg_latency_us']):.9f}"
        )
        row["filter_probes_vs_baseline"] = (
            f"{float(row['filter_probes_per_op']) / float(baseline['filter_probes_per_op']):.9f}"
        )

    rows.sort(
        key=lambda row: (
            CONFIG_ORDER[row["config_id"]], SYSTEM_ORDER[row["system"]]
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    print(f"[summary] {args.output} ({len(rows)} validated runs)")


if __name__ == "__main__":
    main()
