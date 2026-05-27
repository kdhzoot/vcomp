#!/usr/bin/env python3
"""
Parse RocksDB info LOG into per-compaction CSV.

Handles both formats:
  - baseline (real compaction):
      EVENT_LOG_v1 {"event": "compaction_started", "files_L0": [..], "files_L1": [..],
                    "score": .., "input_data_size": .., "compaction_reason": ".."}
  - vcomp (virtual compaction):
      "Virtual compaction L<X> -> L<Y>: N inputs (M segs), K outputs, E entries ..."

Output CSV columns:
    run, mode, ts_us, start_level, output_level, n_inputs_total,
    n_inputs_start, n_inputs_next, n_outputs, input_entries, reason

Usage:
    python3 parse_compaction_log.py LOG_PATH --mode {baseline,vcomp} --run NAME [--out CSV]
"""
import argparse
import csv
import json
import re
from pathlib import Path

# baseline: timestamp ... EVENT_LOG_v1 {...json...}
BASELINE_RE = re.compile(
    r'^(\d{4}/\d{2}/\d{2}-\d{2}:\d{2}:\d{2}\.\d+).*EVENT_LOG_v1\s+(\{.*\})\s*$'
)
# vcomp: "Virtual compaction L0 -> L1: 4 inputs (X segs), 4 outputs, 262105 entries (naive ..., dedup ...)..."
VCOMP_RE = re.compile(
    r'^(\d{4}/\d{2}/\d{2}-\d{2}:\d{2}:\d{2}\.\d+).*'
    r'Virtual compaction L(\d+) -> L(\d+):\s+(\d+) inputs(?:\s*\(\d+ segs\))?,\s+(\d+) outputs,\s+(\d+) entries'
)

def parse_baseline(line):
    m = BASELINE_RE.match(line)
    if not m: return None
    ts, js = m.group(1), m.group(2)
    try:
        d = json.loads(js)
    except json.JSONDecodeError:
        return None
    if d.get("event") != "compaction_started":
        return None
    files_per_level = {}
    for k, v in d.items():
        if k.startswith("files_L") and isinstance(v, list):
            try:
                lvl = int(k[len("files_L"):])
                files_per_level[lvl] = len(v)
            except ValueError:
                pass
    if not files_per_level:
        return None
    start_level = min(files_per_level.keys())
    # output_level is implicit: typically start_level+1.  RocksDB doesn't
    # explicitly log output_level on compaction_started; we'll record the
    # highest level seen as the next-level proxy (which equals start_level+1
    # except for trivial moves and intra-level merges).
    next_level = max(start_level + 1, max(files_per_level.keys()))
    n_total = sum(files_per_level.values())
    n_start = files_per_level.get(start_level, 0)
    n_next  = files_per_level.get(next_level, 0)
    return {
        "ts": ts,
        "start_level": start_level,
        "output_level": next_level,
        "n_inputs_total": n_total,
        "n_inputs_start": n_start,
        "n_inputs_next": n_next,
        "n_outputs": "",
        "input_entries": d.get("input_data_size", ""),
        "reason": d.get("compaction_reason", ""),
    }

def parse_vcomp(line):
    m = VCOMP_RE.match(line)
    if not m: return None
    ts, sl, ol, ni, no, ne = m.groups()
    sl, ol = int(sl), int(ol)
    return {
        "ts": ts,
        "start_level": sl,
        "output_level": ol,
        "n_inputs_total": int(ni),
        "n_inputs_start": "",
        "n_inputs_next": "",
        "n_outputs": int(no),
        "input_entries": int(ne),
        "reason": "",
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", help="path to RocksDB LOG file")
    ap.add_argument("--mode", choices=("baseline","vcomp"), required=True)
    ap.add_argument("--run", required=True, help="run name to tag rows with")
    ap.add_argument("--out", help="output CSV (default: stdout)")
    args = ap.parse_args()

    parser = parse_baseline if args.mode == "baseline" else parse_vcomp

    rows = []
    with open(args.log, errors="replace") as f:
        for line in f:
            r = parser(line)
            if r is None: continue
            r["run"] = args.run
            r["mode"] = args.mode
            rows.append(r)

    fields = ["run", "mode", "ts", "start_level", "output_level",
              "n_inputs_total", "n_inputs_start", "n_inputs_next",
              "n_outputs", "input_entries", "reason"]
    if args.out:
        with open(args.out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    else:
        import sys
        w = csv.DictWriter(sys.stdout, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    import sys
    print(f"parsed {len(rows)} compactions from {args.log}", file=sys.stderr)

if __name__ == "__main__":
    main()
