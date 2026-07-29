#!/usr/bin/env python3
"""
Walk artifacts/log_runs/ and produce a single CSV summarizing every run.

Each row = one run (one subdirectory under artifacts/log_runs/<db>/).

Usage:
  python3 parse_runs.py [LOG_RUNS_DIR] [--out OUT.csv]

Defaults:
  LOG_RUNS_DIR = ./artifacts/log_runs
  OUT          = ./artifacts/log_runs/runs_summary.csv
"""

import argparse
import csv
import os
import re
import sys
from pathlib import Path

from paths import LOG_RUNS

# ---------- column order ----------
COLUMNS = [
    # identification
    "db", "mode", "db_size_gb", "workload", "threads", "cache_pct",
    "timestamp", "result_dir",
    # top-line
    "elapsed_s", "throughput_ops_s", "micros_per_op",
    "found_count", "total_ops", "found_ratio",
    # get latency
    "get_p50_us", "get_p95_us", "get_p99_us", "get_p100_us",
    "get_count", "get_sum_us",
    # sst read latency
    "sst_read_p50_us", "sst_read_p95_us", "sst_read_p99_us",
    "sst_read_count", "sst_read_sum_us",
    # block cache
    "filter_miss", "filter_hit", "filter_per_get",
    "index_miss", "index_hit", "index_per_get",
    "data_miss", "data_hit", "data_per_get",
    # bloom
    "bloom_useful", "bloom_full_positive", "bloom_true_positive", "bloom_fpr",
    # per-level hit
    "l0_hit", "l1_hit", "l2_hit", "l3_hit", "l4_hit", "l5_hit", "l6_hit",
    # md0 io
    "disk_read_kb", "disk_write_kb",
    # rocksdb byte counters
    "rocks_user_read_bytes", "rocks_compact_read_bytes",
    "rocks_compact_write_bytes", "rocks_flush_write_bytes",
    # amplification
    "raf", "waf",
    # cpu (recomputed)
    "cpu_user_pct", "cpu_sys_pct", "cpu_iowait_pct", "cpu_idle_pct",
    "cpu_util_pct", "ctx_switches_per_sec",
]

# ---------- helpers ----------

DIR_RE = re.compile(
    r"^(?P<workload>[a-z0-9]+)_(?P<threads>\d+)t_(?P<cache>\d+)p_(?P<ts>\d{6}_\d{4})$"
)

TICKER_RE = re.compile(r"^(rocksdb\.[a-z0-9._]+)\s+COUNT\s*:\s*(\d+)")
HISTO_RE = re.compile(
    r"^(rocksdb\.[a-z0-9._]+)\s+P50\s*:\s*([\d.]+)\s+P95\s*:\s*([\d.]+)\s+"
    r"P99\s*:\s*([\d.]+)\s+P100\s*:\s*([\d.]+)\s+COUNT\s*:\s*(\d+)\s+SUM\s*:\s*(\d+)"
)
BENCH_RE = re.compile(
    r"^readrandom\s+:\s+([\d.]+)\s+micros/op\s+(\d+)\s+ops/sec\s+([\d.]+)\s+seconds\s+"
    r"(\d+)\s+operations;.*?\((\d+)\s+of\s+(\d+)\s+found\)"
)


def safe_div(a, b):
    if b in (0, None) or a is None:
        return ""
    return a / b


def parse_stdout(path):
    """Parse db_bench stdout.txt → {ticker: int, histo: tuple, bench: tuple}."""
    tickers = {}
    histos = {}
    bench = None
    if not path.exists():
        return tickers, histos, bench
    with open(path, "r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = HISTO_RE.match(line)
            if m:
                name = m.group(1)
                histos[name] = {
                    "p50": float(m.group(2)),
                    "p95": float(m.group(3)),
                    "p99": float(m.group(4)),
                    "p100": float(m.group(5)),
                    "count": int(m.group(6)),
                    "sum": int(m.group(7)),
                }
                continue
            m = TICKER_RE.match(line)
            if m:
                tickers[m.group(1)] = int(m.group(2))
                continue
            m = BENCH_RE.match(line)
            if m and bench is None:
                bench = {
                    "micros_per_op": float(m.group(1)),
                    "ops_per_sec": int(m.group(2)),
                    "seconds": float(m.group(3)),
                    "operations": int(m.group(4)),
                    "found": int(m.group(5)),
                    "total": int(m.group(6)),
                }
    return tickers, histos, bench


def parse_summary(path):
    """Parse summary.txt → dict."""
    out = {}
    if not path.exists():
        return out
    with open(path, "r") as f:
        for line in f:
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def parse_md0(path):
    """Return (sectors_read, sectors_written) for md0, or (None, None)."""
    if not path.exists():
        return None, None
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) < 14:
                continue
            if parts[2] == "md0":
                # field index 5 = sectors read, field index 9 = sectors written
                # (0:major 1:minor 2:name 3:reads 4:merged 5:sectors_read ...)
                return int(parts[5]), int(parts[9])
    return None, None


def parse_procstat(path):
    """Return (cpu_fields_list, ctxt_int) where cpu_fields_list = [user,nice,sys,idle,iowait,irq,softirq,steal,guest,guest_nice]."""
    cpu = None
    ctxt = None
    if not path.exists():
        return cpu, ctxt
    with open(path) as f:
        for line in f:
            if line.startswith("cpu "):
                parts = line.split()[1:]
                cpu = [int(x) for x in parts]
            elif line.startswith("ctxt "):
                ctxt = int(line.split()[1])
            if cpu is not None and ctxt is not None:
                break
    return cpu, ctxt


def parse_db_name(db):
    """vcomp_1000gb → (mode='vcomp', size_gb=1000)."""
    m = re.match(r"^(?P<mode>[a-z]+)_(?P<size>\d+)gb$", db)
    if not m:
        return db, ""
    return m.group("mode"), int(m.group("size"))


# ---------- per-run extraction ----------

def extract_row(db_name, run_dir):
    name = run_dir.name
    m = DIR_RE.match(name)
    if not m:
        return None

    workload = m.group("workload")
    threads = int(m.group("threads"))
    cache_pct = int(m.group("cache"))
    ts = m.group("ts")

    mode, size_gb = parse_db_name(db_name)

    summary = parse_summary(run_dir / "summary.txt")
    tickers, histos, bench = parse_stdout(run_dir / "stdout.txt")

    elapsed = None
    if "elapsed" in summary:
        try:
            elapsed = float(summary["elapsed"].rstrip("s"))
        except ValueError:
            elapsed = None

    # md0 deltas (sectors → KB: ×0.5)
    sr0, sw0 = parse_md0(run_dir / "diskstats.start")
    sr1, sw1 = parse_md0(run_dir / "diskstats.end")
    disk_read_kb = (sr1 - sr0) / 2 if sr0 is not None and sr1 is not None else ""
    disk_write_kb = (sw1 - sw0) / 2 if sw0 is not None and sw1 is not None else ""

    # procstat deltas (cpu jiffies)
    cpu0, ctxt0 = parse_procstat(run_dir / "procstat.start")
    cpu1, ctxt1 = parse_procstat(run_dir / "procstat.end")
    cpu_user = cpu_sys = cpu_iow = cpu_idle = cpu_util = ctx_per_s = ""
    if cpu0 and cpu1 and len(cpu0) == len(cpu1):
        d = [cpu1[i] - cpu0[i] for i in range(len(cpu0))]
        total = sum(d)
        if total > 0:
            user = d[0] + d[1]            # user + nice
            system = d[2] + d[5] + d[6]   # system + irq + softirq
            idle_only = d[3]
            iowait = d[4]
            cpu_user = round(100 * user / total, 2)
            cpu_sys = round(100 * system / total, 2)
            cpu_iow = round(100 * iowait / total, 2)
            cpu_idle = round(100 * idle_only / total, 2)
            cpu_util = round(100 * (1 - idle_only / total), 2)
    if ctxt0 is not None and ctxt1 is not None and elapsed:
        ctx_per_s = round((ctxt1 - ctxt0) / elapsed, 1)

    # rocksdb byte counters
    rocks_user_read = tickers.get("rocksdb.bytes.read", 0)
    rocks_comp_read = tickers.get("rocksdb.compact.read.bytes", 0)
    rocks_comp_write = tickers.get("rocksdb.compact.write.bytes", 0)
    rocks_flush_write = tickers.get("rocksdb.flush.write.bytes", 0)

    # amplification
    raf = ""
    waf = ""
    if disk_read_kb != "" and rocks_user_read > 0:
        raf = round((disk_read_kb * 1024) / rocks_user_read, 3)
    rocks_total_write = rocks_comp_write + rocks_flush_write
    if disk_write_kb != "" and rocks_total_write > 0:
        waf = round((disk_write_kb * 1024) / rocks_total_write, 3)

    # block cache
    fm = tickers.get("rocksdb.block.cache.filter.miss", 0)
    fh = tickers.get("rocksdb.block.cache.filter.hit", 0)
    im = tickers.get("rocksdb.block.cache.index.miss", 0)
    ih = tickers.get("rocksdb.block.cache.index.hit", 0)
    dm = tickers.get("rocksdb.block.cache.data.miss", 0)
    dh = tickers.get("rocksdb.block.cache.data.hit", 0)

    get_h = histos.get("rocksdb.db.get.micros", {})
    sst_h = histos.get("rocksdb.sst.read.micros", {})
    get_count = get_h.get("count", 0)

    def per_get(x):
        return round(x / get_count, 4) if get_count else ""

    # bloom
    b_useful = tickers.get("rocksdb.bloom.filter.useful", 0)
    b_full_pos = tickers.get("rocksdb.bloom.filter.full.positive", 0)
    b_true_pos = tickers.get("rocksdb.bloom.filter.full.true.positive", 0)
    b_fpr = ""
    if b_full_pos > 0:
        b_fpr = round(1.0 - (b_true_pos / b_full_pos), 4)

    # bench (fall back to summary if needed)
    found = total = mu_per_op = ops_per_s = ""
    if bench:
        found = bench["found"]
        total = bench["total"]
        mu_per_op = bench["micros_per_op"]
        ops_per_s = bench["ops_per_sec"]
    found_ratio = ""
    if found != "" and total:
        found_ratio = round(found / total, 4)

    row = {
        "db": db_name,
        "mode": mode,
        "db_size_gb": size_gb,
        "workload": workload,
        "threads": threads,
        "cache_pct": cache_pct,
        "timestamp": ts,
        "result_dir": str(run_dir),
        "elapsed_s": round(elapsed, 2) if elapsed else "",
        "throughput_ops_s": ops_per_s,
        "micros_per_op": mu_per_op,
        "found_count": found,
        "total_ops": total,
        "found_ratio": found_ratio,
        "get_p50_us": get_h.get("p50", ""),
        "get_p95_us": get_h.get("p95", ""),
        "get_p99_us": get_h.get("p99", ""),
        "get_p100_us": get_h.get("p100", ""),
        "get_count": get_count,
        "get_sum_us": get_h.get("sum", ""),
        "sst_read_p50_us": sst_h.get("p50", ""),
        "sst_read_p95_us": sst_h.get("p95", ""),
        "sst_read_p99_us": sst_h.get("p99", ""),
        "sst_read_count": sst_h.get("count", ""),
        "sst_read_sum_us": sst_h.get("sum", ""),
        "filter_miss": fm,
        "filter_hit": fh,
        "filter_per_get": per_get(fm + fh),
        "index_miss": im,
        "index_hit": ih,
        "index_per_get": per_get(im + ih),
        "data_miss": dm,
        "data_hit": dh,
        "data_per_get": per_get(dm + dh),
        "bloom_useful": b_useful,
        "bloom_full_positive": b_full_pos,
        "bloom_true_positive": b_true_pos,
        "bloom_fpr": b_fpr,
        "l0_hit": tickers.get("rocksdb.l0.hit", 0),
        "l1_hit": tickers.get("rocksdb.l1.hit", 0),
        "l2_hit": tickers.get("rocksdb.l2.hit", 0),
        "l3_hit": tickers.get("rocksdb.l3.hit", 0),
        "l4_hit": tickers.get("rocksdb.l4.hit", 0),
        "l5_hit": tickers.get("rocksdb.l5.hit", 0),
        "l6_hit": tickers.get("rocksdb.l6.hit", 0),
        "disk_read_kb": disk_read_kb if disk_read_kb == "" else int(disk_read_kb),
        "disk_write_kb": disk_write_kb if disk_write_kb == "" else int(disk_write_kb),
        "rocks_user_read_bytes": rocks_user_read,
        "rocks_compact_read_bytes": rocks_comp_read,
        "rocks_compact_write_bytes": rocks_comp_write,
        "rocks_flush_write_bytes": rocks_flush_write,
        "raf": raf,
        "waf": waf,
        "cpu_user_pct": cpu_user,
        "cpu_sys_pct": cpu_sys,
        "cpu_iowait_pct": cpu_iow,
        "cpu_idle_pct": cpu_idle,
        "cpu_util_pct": cpu_util,
        "ctx_switches_per_sec": ctx_per_s,
    }
    return row


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log_runs", nargs="?", default=str(LOG_RUNS))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    root = Path(args.log_runs)
    if not root.is_dir():
        print(f"[ERROR] {root} is not a directory", file=sys.stderr)
        sys.exit(1)
    out = Path(args.out) if args.out else root / "runs_summary.csv"

    rows = []
    for db_dir in sorted(root.iterdir()):
        if not db_dir.is_dir():
            continue
        for run_dir in sorted(db_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            row = extract_row(db_dir.name, run_dir)
            if row:
                rows.append(row)

    rows.sort(key=lambda r: (r["db"], r["workload"], r["timestamp"]))

    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"[OK] {len(rows)} runs → {out}")


if __name__ == "__main__":
    main()
