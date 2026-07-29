#!/usr/bin/env python3
"""Build load comparison CSV across baseline and VComp versions."""

import csv
import os
import re

from paths import LOG_LOADS as LOG_LOADS_PATH

LOG_LOADS = str(LOG_LOADS_PATH)
OUT_CSV = os.path.join(LOG_LOADS, "load_comparison_versions.csv")

RUNS = [
    ("250 GB", 250, "baseline", "baseline", "baseline_260414_1305_250gb"),
    ("250 GB", 250, "old_vcomp", "vcomp_260414_plot", "vcomp_260414_1648_250gb"),
    ("250 GB", 250, "activeplr", "vcomp_260602_activeplr", "vcomp_260602_0101_250gb_activeplr"),
    ("250 GB", 250, "latest_patch", "vcomp_260602_incrmeta_nocopy", "vcomp_260602_1340_250gb_incrmeta_nocopy"),
    ("250 GB", 250, "cap16_delay100", "vcomp_260602_cap16_delay100", "vcomp_260602_1641_250gb_cap16_delay100_260602_164127"),
    ("1 TB", 1000, "baseline", "baseline", "baseline_260409_1144_1000gb"),
    ("1 TB", 1000, "old_vcomp", "vcomp_260414_plot", "vcomp_260414_1346_1000gb"),
    ("1 TB", 1000, "activeplr", "vcomp_260601_activeplr", "vcomp_260601_1847_1000gb_activeplr"),
    ("1 TB", 1000, "latest_patch", "vcomp_260602_incrmeta_nocopy", "vcomp_260602_1341_1000gb_incrmeta_nocopy"),
    ("1 TB", 1000, "cap16_delay100", "vcomp_260602_cap16_delay100", "vcomp_260602_1519_1000gb_cap16_delay100_260602_151933"),
    ("5 TB", 5120, "baseline", "baseline", "baseline_260430_1619_5120gb"),
    ("5 TB", 5120, "old_vcomp", "vcomp_260430_plot", "vcomp_260430_2201_5120gb"),
    ("5 TB", 5000, "activeplr", "vcomp_260601_activeplr", "vcomp_260601_1851_5000gb_activeplr"),
    ("5 TB", 5000, "latest_patch", "vcomp_260602_latest_patch", "vcomp_260602_1354_5000gb_latestpatch_260602_135454"),
    ("5 TB", 5000, "cap16_delay100", "vcomp_260602_cap16_delay100", "vcomp_260602_1520_5000gb_cap16_delay100_260602_152052"),
    ("10 TB", 10240, "baseline", "baseline", "baseline_260430_2232_10240gb"),
    ("10 TB", 10240, "old_vcomp", "vcomp_260501_plot", "vcomp_260501_1239_10240gb"),
    ("10 TB", 10000, "activeplr", "vcomp_260601_activeplr", "vcomp_260601_1913_10000gb_activeplr"),
    ("10 TB", 10000, "latest_patch", "vcomp_260602_latest_patch", None),
    ("10 TB", 10000, "cap16_delay100", "vcomp_260602_cap16_delay100", "vcomp_260602_1558_10000gb_cap16_delay100_260602_155842"),
]

SIZE_UNIT_TO_GB = {
    "KB": 1.0 / (1024 * 1024),
    "MB": 1.0 / 1024,
    "GB": 1.0,
    "TB": 1024.0,
}


def read_text(path):
    with open(path, errors="replace") as f:
        return f.read()


def parse_benchmark(text):
    line = None
    for candidate in text.splitlines():
        if candidate.startswith("fillrandom") or candidate.startswith("fillvirtual"):
            line = candidate
    if line is None:
        return "", "", "", ""
    benchmark = line.split(":", 1)[0].strip()
    match = re.search(
        r"([0-9.]+)\s+micros/op\s+([0-9]+)\s+ops/sec\s+([0-9.]+)\s+seconds\s+([0-9]+)\s+operations;\s+([0-9.]+)\s+MB/s",
        line,
    )
    if match:
        return benchmark, int(match.group(4)), float(match.group(3)), float(match.group(5))
    match = re.search(
        r"([0-9.]+)\s+micros/op\s+([0-9]+)\s+ops/sec\s+([0-9.]+)\s+seconds\s+([0-9]+)\s+operations;\s+([0-9.]+)\s+MB/s",
        line.replace("  ", " "),
    )
    if match:
        return benchmark, int(match.group(4)), float(match.group(3)), float(match.group(5))
    raise ValueError(f"could not parse benchmark line: {line}")


def parse_num_from_cmd(text):
    match = re.search(r"--num(?:=|\s+)([0-9]+)", text)
    return int(match.group(1)) if match else None


def parse_sum(text):
    sum_lines = [line for line in text.splitlines() if line.startswith(" Sum")]
    if not sum_lines:
        return 0.0, 0.0
    parts = sum_lines[-1].split()
    db_size_gb = float(parts[2]) * SIZE_UNIT_TO_GB[parts[3]]
    comp_write_gb = float(parts[8])
    return db_size_gb, comp_write_gb


def parse_ingest(text):
    matches = list(re.finditer(r"ingest:\s+([0-9.]+)\s+GB", text))
    return float(matches[-1].group(1)) if matches else 0.0


def parse_run(size_class, requested_gb, series, version, source_dir):
    if source_dir is None:
        return {
            "size_class": size_class,
            "requested_gb": requested_gb,
            "series": series,
            "version": version,
            "available": "false",
            "source_dir": "",
            "note": f"{version} not run",
        }

    base = os.path.join(LOG_LOADS, source_dir)
    text = read_text(os.path.join(base, "bench.out"))
    elapsed_path = os.path.join(base, "raw", "elapsed_sec.txt")
    elapsed_sec = int(read_text(elapsed_path).strip()) if os.path.exists(elapsed_path) else None
    benchmark, ops, benchmark_sec, throughput_mb_s = parse_benchmark(text)
    keys = parse_num_from_cmd(text) or ops
    final_lsm_size_gb, comp_write_gb = parse_sum(text)
    ingest_gb = parse_ingest(text)
    if comp_write_gb == 0.0 and ingest_gb == 0.0:
        plot_total_write_gb = final_lsm_size_gb
    else:
        plot_total_write_gb = comp_write_gb + ingest_gb

    return {
        "size_class": size_class,
        "requested_gb": requested_gb,
        "series": series,
        "version": version,
        "available": "true",
        "source_dir": source_dir,
        "benchmark": benchmark,
        "keys": keys,
        "elapsed_sec": elapsed_sec,
        "elapsed_hours": round(elapsed_sec / 3600.0, 4) if elapsed_sec is not None else "",
        "benchmark_sec": round(benchmark_sec, 3),
        "throughput_mb_s": round(throughput_mb_s, 1),
        "comp_write_gb": round(comp_write_gb, 1),
        "ingest_gb": round(ingest_gb, 2),
        "final_lsm_size_gb": round(final_lsm_size_gb, 2),
        "plot_total_write_gb": round(plot_total_write_gb, 2),
        "plot_total_write_tb": round(plot_total_write_gb / 1024.0, 4),
        "note": "",
    }


def add_ratios(rows):
    baselines = {
        row["size_class"]: row
        for row in rows
        if row.get("series") == "baseline" and row.get("available") == "true"
    }
    for row in rows:
        if row.get("available") != "true" or row.get("series") == "baseline":
            continue
        baseline = baselines[row["size_class"]]
        row["speedup_vs_baseline_raw"] = round(baseline["elapsed_sec"] / row["elapsed_sec"], 4)
        row["speedup_vs_baseline_per_gb"] = round(
            (baseline["elapsed_sec"] / baseline["requested_gb"])
            / (row["elapsed_sec"] / row["requested_gb"]),
            4,
        )
        row["write_reduction_vs_baseline_raw"] = round(
            baseline["plot_total_write_gb"] / row["plot_total_write_gb"], 4
        )
        row["write_reduction_vs_baseline_per_gb"] = round(
            (baseline["plot_total_write_gb"] / baseline["requested_gb"])
            / (row["plot_total_write_gb"] / row["requested_gb"]),
            4,
        )


def main():
    rows = [parse_run(*run) for run in RUNS]
    add_ratios(rows)
    fields = [
        "size_class",
        "requested_gb",
        "series",
        "version",
        "available",
        "source_dir",
        "benchmark",
        "keys",
        "elapsed_sec",
        "elapsed_hours",
        "benchmark_sec",
        "throughput_mb_s",
        "comp_write_gb",
        "ingest_gb",
        "final_lsm_size_gb",
        "plot_total_write_gb",
        "plot_total_write_tb",
        "speedup_vs_baseline_raw",
        "speedup_vs_baseline_per_gb",
        "write_reduction_vs_baseline_raw",
        "write_reduction_vs_baseline_per_gb",
        "note",
    ]
    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    print(OUT_CSV)


if __name__ == "__main__":
    main()
