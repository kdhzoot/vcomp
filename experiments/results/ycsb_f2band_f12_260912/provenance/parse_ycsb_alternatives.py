#!/usr/bin/env python3
"""Parse one db_bench YCSB A--F run without trusting its one-worker message.

The aggregate benchmark and merged ``Microseconds per ...`` histograms cover
all workers. The parenthesized Gets/Puts/Seek/scanned message does not: Stats
retains only one worker's message. Never use that message as a campaign total.

Engine tickers cover the lifetime of the statistics object, including DB-open
and concurrent background work. They are not interchangeable with workload
operation counts. ``successful_gets`` is valid for this campaign's Get/Put-only
data model (no deletes, merges, or MultiGet); it includes RMW's Get operations.
Missing optional statistics are represented by None, not invented zeroes.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


NUMBER = r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
BENCHMARK_RE = re.compile(
    rf"^(workload[a-f])\s*:\s*({NUMBER})\s+micros/op\s+"
    rf"({NUMBER})\s+ops/sec\s+({NUMBER})\s+seconds\s+"
    r"(\d+)\s+operations;",
    re.MULTILINE,
)
TICKER_RE = re.compile(r"^(rocksdb\.[\w.]+)\s+COUNT\s*:\s*(\d+)\s*$", re.M)
OP_HISTOGRAM_RE = re.compile(
    rf"^Microseconds per (\w+):\s*\n"
    rf"Count:\s*(\d+)\s+Average:\s*({NUMBER})\s+StdDev:\s*({NUMBER})\s*\n"
    rf"Min:\s*({NUMBER})\s+Median:\s*({NUMBER})\s+Max:\s*({NUMBER})\s*\n"
    r"Percentiles:([^\n]+)",
    re.MULTILINE,
)
ENGINE_HISTOGRAM_RE = re.compile(
    r"^(rocksdb\.[\w.]+)\s+P50\s*:\s*([^\n]+)", re.MULTILINE
)
HISTOGRAM_FIELD_RE = re.compile(rf"(P\d+(?:\.\d+)?|COUNT|SUM)\s*:\s*({NUMBER})")
ERROR_RE = re.compile(
    r"(?:\b(?:Get|Put|YCSB\s+scan|Scan)\s+error\s*:"
    r"|\b(?:Corruption|IOError|IO error|Invalid argument|Not supported)\s*:"
    r"|\bassertion\b[^\n]*\bfailed\b"
    r"|\bassertion failure\b|\bSegmentation fault\b|\bBus error\b"
    r"|\bterminate called\b|\bAborted \(core dumped\)"
    r"|^\s*(?:FATAL|ERROR)\s*[:\]])",
    re.IGNORECASE | re.MULTILINE,
)

TICKER_FIELDS = {
    "engine_keys_read": "rocksdb.number.keys.read",
    "engine_keys_written": "rocksdb.number.keys.written",
    "engine_bytes_read": "rocksdb.bytes.read",
    "engine_bytes_written": "rocksdb.bytes.written",
    "engine_iterator_bytes_read": "rocksdb.db.iter.bytes.read",
    "engine_seek_calls": "rocksdb.number.db.seek",
    "engine_seek_found": "rocksdb.number.db.seek.found",
    "engine_next_calls": "rocksdb.number.db.next",
    "engine_next_found": "rocksdb.number.db.next.found",
    "memtable_hits": "rocksdb.memtable.hit",
    "l0_hits": "rocksdb.l0.hit",
    "l1_hits": "rocksdb.l1.hit",
    "l2_and_up_hits": "rocksdb.l2andup.hit",
    "filter_cache_hit": "rocksdb.block.cache.filter.hit",
    "filter_cache_miss": "rocksdb.block.cache.filter.miss",
    "index_cache_hit": "rocksdb.block.cache.index.hit",
    "index_cache_miss": "rocksdb.block.cache.index.miss",
    "data_cache_hit": "rocksdb.block.cache.data.hit",
    "data_cache_miss": "rocksdb.block.cache.data.miss",
    "compaction_read_bytes": "rocksdb.compact.read.bytes",
    "compaction_write_bytes": "rocksdb.compact.write.bytes",
    "flush_write_bytes": "rocksdb.flush.write.bytes",
    "stall_micros": "rocksdb.stall.micros",
}
WORKLOAD_OPERATIONS = {
    "a": {"read": "read", "write": "update"},
    "b": {"read": "read", "write": "update"},
    "c": {"read": "read"},
    "d": {"read": "read", "write": "insert"},
    "e": {"seek": "scan", "write": "insert"},
    "f": {"read": "read", "update": "readmodifywrite"},
}


def _workload_name(workload: str) -> str:
    name = re.sub(r"^(?:workload|ycsb)", "", workload.lower().strip())
    if name not in WORKLOAD_OPERATIONS:
        raise ValueError(f"unsupported YCSB workload: {workload!r}")
    return "workload" + name


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"non-finite measurement: {value!r}")
    return result


def parse_metrics(text: str, workload: str) -> dict[str, Any]:
    """Return JSON-safe metrics, or raise ValueError on invalid/failed output.

    This parses a single workload run, not concatenated independent runs. The
    runner must independently check process exit status, timeout, DB identity,
    configuration and requested duration; none can be established from a
    success-looking benchmark line alone. Optional engine statistics can be
    required by checking ``missing_tickers`` in a campaign's validation policy.
    """
    name = _workload_name(workload)
    error = ERROR_RE.search(text)
    if error:
        line_start = text.rfind("\n", 0, error.start()) + 1
        line_end = text.find("\n", error.end())
        raise ValueError("execution error in log: " + text[line_start:line_end if line_end >= 0 else None][:500])

    benchmarks = list(BENCHMARK_RE.finditer(text))
    if len(benchmarks) != 1 or benchmarks[0].group(1) != name:
        raise ValueError(f"expected exactly one {name} aggregate; found {[m.group(1) for m in benchmarks]}")
    benchmark = benchmarks[0]
    latency, throughput, seconds = map(_finite_float, benchmark.group(2, 3, 4))
    operations = int(benchmark.group(5))
    if seconds <= 0 or operations <= 0:
        raise ValueError("nonpositive benchmark duration or operation count")

    histograms: dict[str, dict[str, Any]] = {}
    # Histograms before the aggregate may be interval reports, not run totals.
    after_aggregate = text[benchmark.end():]
    for match in OP_HISTOGRAM_RE.finditer(after_aggregate):
        kind = match.group(1)
        if kind in histograms:
            raise ValueError(f"duplicate aggregate operation histogram: {kind}")
        histograms[kind] = {
            "count": int(match.group(2)),
            "average_us": _finite_float(match.group(3)),
            "stddev_us": _finite_float(match.group(4)),
            "min_us": _finite_float(match.group(5)),
            "median_us": _finite_float(match.group(6)),
            "max_us": _finite_float(match.group(7)),
        }
        for percentile, value in re.findall(rf"(P\d+(?:\.\d+)?):\s*({NUMBER})", match.group(8)):
            histograms[kind][percentile.lower().replace(".", "_") + "_us"] = _finite_float(value)
    declared_histograms = re.findall(r"^Microseconds per (\w+):", after_aggregate, re.M)
    if len(declared_histograms) != len(histograms):
        raise ValueError("incomplete or malformed aggregate operation histogram")

    workload_counts: dict[str, int] | None = None
    if histograms:
        if sum(hist["count"] for hist in histograms.values()) != operations:
            raise ValueError("merged operation histogram counts do not match aggregate operations")
        mapping = WORKLOAD_OPERATIONS[name[-1]]
        unexpected = set(histograms) - mapping.keys()
        if unexpected:
            raise ValueError(f"unexpected operation types for default {name}: {sorted(unexpected)}")
        workload_counts = {
            operation: 0 for operation in ("read", "update", "insert", "scan", "readmodifywrite")
        }
        for kind, hist in histograms.items():
            workload_counts[mapping[kind]] = hist["count"]

    # A stats dump can appear before/after the workload; use the final dump's
    # value for each ticker, never add cumulative snapshots together.
    tickers = {name: int(value) for name, value in TICKER_RE.findall(text)}
    for ticker_name in (
        "rocksdb.no.file.errors",
        "rocksdb.block.checksum.mismatch.count",
        "rocksdb.footer.corruption.count",
        "rocksdb.error.handler.bg.error.count",
        "rocksdb.error.handler.bg.io.error.count",
    ):
        if tickers.get(ticker_name, 0) > 0:
            raise ValueError(f"nonzero engine error ticker: {ticker_name}={tickers[ticker_name]}")

    engine_histograms: dict[str, dict[str, Any]] = {}
    for match in ENGINE_HISTOGRAM_RE.finditer(text):
        fields: dict[str, Any] = {}
        for field, value in HISTOGRAM_FIELD_RE.findall("P50 : " + match.group(2)):
            key = field.lower().replace(".", "_")
            fields[key] = int(value) if field in ("COUNT", "SUM") else _finite_float(value)
        if "count" in fields and "sum" in fields:
            fields["average"] = fields["sum"] / fields["count"] if fields["count"] else None
        engine_histograms[match.group(1)] = fields

    result: dict[str, Any] = {
        "schema_version": 1,
        "workload": name,
        "avg_latency_us": latency,
        "throughput_ops_sec": throughput,
        "measured_seconds": seconds,
        "operations": operations,
        "operation_histograms": histograms,
        "workload_operation_counts": workload_counts,
        "engine_histograms": engine_histograms,
        "tickers": tickers,
        "missing_tickers": sorted(set(TICKER_FIELDS.values()) - tickers.keys()),
        "warnings": [],
    }
    for field, ticker_name in TICKER_FIELDS.items():
        result[field] = tickers.get(ticker_name)
    hit_fields = ("memtable_hits", "l0_hits", "l1_hits", "l2_and_up_hits")
    result["successful_gets"] = (
        sum(result[field] for field in hit_fields)
        if all(result[field] is not None for field in hit_fields) else None
    )
    gets, found = result["engine_keys_read"], result["successful_gets"]
    result["get_found_fraction"] = found / gets if gets and found is not None else None
    result["get_not_found"] = gets - found if gets is not None and found is not None else None
    if gets is not None and found is not None and found > gets:
        raise ValueError("Get hit tickers exceed Get count; incompatible statistics/data model")
    for kind in ("filter", "index", "data"):
        hit, miss = result[kind + "_cache_hit"], result[kind + "_cache_miss"]
        accesses = hit + miss if hit is not None and miss is not None else None
        result[kind + "_cache_accesses"] = accesses
        result[kind + "_cache_hit_fraction"] = hit / accesses if accesses else None
        result[kind + "_cache_misses_per_op"] = miss / operations if miss is not None else None
    if result["missing_tickers"]:
        result["warnings"].append("Optional engine statistics are missing; unavailable metrics are null.")
    if not histograms:
        result["warnings"].append("Merged operation histograms are absent; exact workload operation counts are unavailable.")
    # Histogram names are retained so callers can distinguish pure engine API
    # latency from db_bench's RNG/key-generation-inclusive operation latency.
    return result


def _self_test() -> None:
    def histogram(kind: str, count: int) -> str:
        return (
            f"Microseconds per {kind}:\nCount: {count} Average: 20.0000  StdDev: 5.00\n"
            "Min: 1  Median: 18.0000  Max: 100\n"
            "Percentiles: P50: 18.00 P75: 25.00 P99: 90.00 P99.9: 99.00 P99.99: 100.00\n"
        )

    def sample(letter: str, counts: dict[str, int]) -> str:
        base = (
            f"workload{letter} : 20.000 micros/op 100 ops/sec 1.000 seconds 100 operations; "
            "( Gets:1 Puts:1 Seek:1 ReadModifyWrite:1, reads 0 in 1 found, scanned:999999 )\n"
        )
        return base + "".join(histogram(kind, count) for kind, count in counts.items())

    fixtures = {
        "a": {"read": 50, "write": 50}, "b": {"read": 95, "write": 5},
        "c": {"read": 100}, "d": {"read": 95, "write": 5},
        "e": {"seek": 95, "write": 5}, "f": {"read": 50, "update": 50},
    }
    for letter, counts in fixtures.items():
        parsed = parse_metrics(sample(letter, counts), letter.upper())
        assert parsed["operations"] == 100
        assert sum(parsed["workload_operation_counts"].values()) == 100
        assert parsed["successful_gets"] is None
    assert parse_metrics(sample("f", fixtures["f"]), "ycsbf")["workload_operation_counts"]["readmodifywrite"] == 50
    text = sample("c", fixtures["c"]) + (
        "rocksdb.number.keys.read COUNT : 100\n"
        "rocksdb.memtable.hit COUNT : 10\nrocksdb.l0.hit COUNT : 5\n"
        "rocksdb.l1.hit COUNT : 20\nrocksdb.l2andup.hit COUNT : 25\n"
        "rocksdb.l2.hit COUNT : 25\n"
        "rocksdb.footer.corruption.count COUNT : 0\n"
        "rocksdb.db.get.micros P50 : 8.0 P95 : 20.0 P99 : 30.0 P100 : 100.0 COUNT : 100 SUM : 1000\n"
    )
    parsed = parse_metrics(text, "workloadc")
    assert parsed["successful_gets"] == 60 and parsed["get_not_found"] == 40
    assert parsed["get_found_fraction"] == 0.6
    assert parsed["engine_histograms"]["rocksdb.db.get.micros"]["average"] == 10
    assert "scanned" not in parsed
    assert parse_metrics(text + "rocksdb.number.keys.read COUNT : 110\n", "c")["engine_keys_read"] == 110
    bad_logs = [
        text + "Get error: IO error: broken\n", text + "YCSB scan error: Corruption: bad block\n",
        text + "db_bench: file.cc:1: Assertion `ok' failed.\n",
        text + "rocksdb.no.file.errors COUNT : 1\n",
        text + sample("c", fixtures["c"]), text.replace("Count: 100 Average", "Count: 99 Average"),
        text.replace("Percentiles:", "Damaged:"),
        text.replace("1.000 seconds", "0.000 seconds"), "incomplete log\n",
    ]
    for bad in bad_logs:
        try:
            parse_metrics(bad, "c")
        except ValueError:
            continue
        raise AssertionError("accepted invalid output")
    no_hist = "workloadc : 20.000 micros/op 100 ops/sec 1.000 seconds 100 operations;\n"
    assert parse_metrics(no_hist, "c")["workload_operation_counts"] is None
    print("parse_ycsb_alternatives: self-test passed (A--F, totals, missing stats, hits, histograms, error rejection)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, help="Combined db_bench stdout/stderr")
    parser.add_argument("--workload", help="A--F or workloada--workloadf")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        _self_test()
        return 0
    if args.log is None or args.workload is None:
        parser.error("--log and --workload are required unless --self-test is used")
    try:
        metrics = parse_metrics(args.log.read_text(encoding="utf-8", errors="replace"), args.workload)
    except (OSError, ValueError) as error:
        print(f"validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(metrics, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
