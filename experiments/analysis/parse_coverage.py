#!/usr/bin/env python3
"""
Parse coverage_dumps/*.cov files into a per-(run,level) CSV and print
per-level summary stats (mean, std, min, max).

Usage:
    python3 parse_coverage.py <DUMP_DIR> [--out <CSV>]
"""
import argparse
import csv
import re
import statistics
from pathlib import Path

# Per-level summary line:
#   "  1      4      225.6   65804895  225932775  22872787  22872787   14.28%"
LEVEL_RE = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+([\d.]+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)%"
)
# Per-file L1 dump:
#   "    [0]     65804895 ..     71258804 width=     5453909 gap=+0"
L1_FILE_RE = re.compile(
    r"^\s*\[(\d+)\]\s+(\d+)\s+\.\.\s+(\d+)\s+width=\s*(\d+)\s+gap=([+-]?\d+)"
)

def parse_file(path: Path):
    """Return (level_rows, l1_files).
    level_rows: list of dict {level, files, size_mb, key_min, key_max,
                              union_span, files_span, cov_pct}
    l1_files: list of dict {idx, key_lo, key_hi, width, gap}
    """
    level_rows = []
    l1_files = []
    with open(path) as f:
        for line in f:
            m = L1_FILE_RE.match(line)
            if m:
                l1_files.append({
                    "idx": int(m.group(1)),
                    "key_lo": int(m.group(2)),
                    "key_hi": int(m.group(3)),
                    "width": int(m.group(4)),
                    "gap": int(m.group(5)),
                })
                continue
            m = LEVEL_RE.match(line)
            if m:
                level_rows.append({
                    "level": int(m.group(1)),
                    "files": int(m.group(2)),
                    "size_mb": float(m.group(3)),
                    "key_min": int(m.group(4)),
                    "key_max": int(m.group(5)),
                    "union_span": int(m.group(6)),
                    "files_span": int(m.group(7)),
                    "cov_pct": float(m.group(8)),
                })
    return level_rows, l1_files


def summarize(values, label):
    if not values:
        print(f"  {label}: (no data)")
        return
    mean = statistics.fmean(values)
    sd   = statistics.stdev(values) if len(values) >= 2 else 0.0
    print(f"  {label}: n={len(values)} mean={mean:7.3f} "
          f"std={sd:6.3f}  min={min(values):7.3f}  max={max(values):7.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump_dir", help="dir of .cov files")
    ap.add_argument("--out", default=None, help="output CSV (default: <dump_dir>/coverage.csv)")
    args = ap.parse_args()

    dump_dir = Path(args.dump_dir)
    out_path = Path(args.out) if args.out else dump_dir / "coverage.csv"

    rows = []   # per-(run,level)
    by_level = {}  # level -> list of cov_pct (across runs)
    files_by_level = {}  # level -> list of file count

    for cov in sorted(dump_dir.glob("*.cov")):
        run = cov.stem
        level_rows, _l1 = parse_file(cov)
        if not level_rows:
            print(f"WARN: no parse for {cov.name}")
            continue
        for r in level_rows:
            rows.append({"run": run, **r})
            by_level.setdefault(r["level"], []).append(r["cov_pct"])
            files_by_level.setdefault(r["level"], []).append(r["files"])

    if not rows:
        print("no rows")
        return

    fields = ["run", "level", "files", "size_mb", "key_min", "key_max",
              "union_span", "files_span", "cov_pct"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"wrote {out_path} ({len(rows)} rows)")
    print()
    print("=== coverage % per level ===")
    for lvl in sorted(by_level):
        summarize(by_level[lvl], f"L{lvl} cov_pct")
    print()
    print("=== file count per level ===")
    for lvl in sorted(files_by_level):
        summarize([float(x) for x in files_by_level[lvl]], f"L{lvl} files")


if __name__ == "__main__":
    main()
