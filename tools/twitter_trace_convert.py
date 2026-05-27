#!/usr/bin/env python3
"""Twitter cache-trace CSV -> vcomp binary trace.

Format spec lives in vcomp/README.md §7 (Twitter trace replay).
Keep in sync.
"""

from __future__ import annotations

import argparse
import statistics
import struct
import sys
from pathlib import Path


MAGIC = b"VCMPTRC1"
VERSION = 1
HEADER_SIZE = 40  # see write_header layout below

OP_PUT = 1
OP_GET = 2
OP_DELETE = 3

WRITE_OPS = {"set", "add", "replace", "cas"}
READ_OPS = {"get", "gets"}
DELETE_OPS = {"delete"}


def write_header(out, total_puts: int, total_kv_bytes: int,
                 key_len_fixed: int) -> None:
    """Write the 40-byte header. Layout:

      0   8  magic           "VCMPTRC1"
      8   4  version         uint32 = 1
     12   4  reserved        uint32 = 0
     16   8  total_puts      uint64
     24   8  total_kv_bytes  uint64 (sum of key_len + value_size over Puts)
     32   4  key_len_fixed   uint32 (0 = variable; else common Put key length)
     36   4  padding         uint32 = 0
    """
    out.write(MAGIC)
    out.write(struct.pack("<II", VERSION, 0))
    out.write(struct.pack("<QQII",
                          total_puts, total_kv_bytes, key_len_fixed, 0))


def write_record(out, op: int, key: bytes, value_size: int) -> None:
    out.write(struct.pack("<BI", op, len(key)))
    out.write(key)
    out.write(struct.pack("<I", value_size))


def scale_and_clip(value_size: int, scale: float, clip: int) -> int:
    v = int(round(value_size * scale)) if scale != 1.0 else value_size
    if clip > 0 and v > clip:
        v = clip
    return max(v, 0)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Convert Twitter cache-trace CSV to vcomp binary trace")
    p.add_argument("input", type=str,
                   help="Twitter CSV (decompressed). Use '-' for stdin.")
    p.add_argument("output", type=Path, help="Output binary trace")
    p.add_argument("--max-ops", type=int, default=0,
                   help="Stop after N emitted records (0 = unlimited)")
    p.add_argument("--sample", type=int, default=0,
                   help="Keep 1 of every N qualifying ops (0 = off)")
    p.add_argument("--include-reads", action="store_true",
                   help="Also emit Get records for get/gets")
    p.add_argument("--include-deletes", action="store_true",
                   help="Also emit Delete for delete")
    p.add_argument("--value-size-scale", type=float, default=1.0,
                   help="Multiply every value_size by this factor")
    p.add_argument("--value-size-clip", type=int, default=0,
                   help="Cap value_size at N bytes (0 = off)")
    p.add_argument("--min-key-len", type=int, default=0,
                   help="Drop records whose key is shorter than N")
    p.add_argument("--progress", action="store_true",
                   help="Print progress every 1M input lines to stderr")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    emitted = 0
    malformed = 0
    lines_read = 0
    op_counts = {OP_PUT: 0, OP_GET: 0, OP_DELETE: 0}
    key_sizes: list[int] = []
    value_sizes: list[int] = []

    if args.input == "-":
        inf_cm = open(sys.stdin.fileno(), "r",
                      encoding="utf-8", errors="replace", closefd=False)
    else:
        inf_cm = Path(args.input).open(
            "r", encoding="utf-8", errors="replace")

    # Stats for the header. Filled during the streaming pass; final header
    # is patched in via seek(0) after the loop.
    total_puts = 0
    total_kv_bytes = 0  # sum of (key_len + value_size) over Puts only
    key_len_fixed = 0  # first observed Put key_len; reset to 0 on mismatch
    key_len_fixed_known = False

    with inf_cm as inf, args.output.open("wb") as outf:
        # Reserve space for the 40-byte header; we'll seek back and patch it
        # in at the end once total_puts / total_kv_bytes are known.
        outf.write(b"\x00" * HEADER_SIZE)

        for line in inf:
            lines_read += 1
            if args.progress and lines_read % 1_000_000 == 0:
                print(f"  read {lines_read:,} lines, emitted {emitted:,}",
                      file=sys.stderr)

            parts = line.rstrip("\n").split(",")
            if len(parts) < 7:
                malformed += 1
                continue
            try:
                key_str = parts[1]
                declared_key_size = int(parts[2])
                declared_value_size = int(parts[3])
                twitter_op = parts[5].lower()
            except ValueError:
                malformed += 1
                continue

            if twitter_op in WRITE_OPS:
                op = OP_PUT
            elif twitter_op in READ_OPS:
                if not args.include_reads:
                    continue
                op = OP_GET
            elif twitter_op in DELETE_OPS:
                if not args.include_deletes:
                    continue
                op = OP_DELETE
            else:
                continue

            key_bytes = key_str.encode("utf-8")
            if declared_key_size > 0 and len(key_bytes) != declared_key_size:
                if len(key_bytes) > declared_key_size:
                    key_bytes = key_bytes[:declared_key_size]
                else:
                    key_bytes = key_bytes + b"\x00" * (
                        declared_key_size - len(key_bytes))
            if len(key_bytes) < args.min_key_len:
                continue

            value_size = scale_and_clip(
                declared_value_size, args.value_size_scale,
                args.value_size_clip) if op == OP_PUT else 0

            if args.sample > 0 and (emitted % args.sample) != 0:
                emitted += 1
                continue

            write_record(outf, op, key_bytes, value_size)
            op_counts[op] += 1
            key_sizes.append(len(key_bytes))
            if op == OP_PUT:
                value_sizes.append(value_size)
                total_puts += 1
                total_kv_bytes += len(key_bytes) + value_size
                if not key_len_fixed_known:
                    key_len_fixed = len(key_bytes)
                    key_len_fixed_known = True
                elif key_len_fixed != 0 and len(key_bytes) != key_len_fixed:
                    key_len_fixed = 0  # variable
            emitted += 1

            if args.max_ops > 0 and emitted >= args.max_ops:
                break

        # Patch the 40-byte header now that totals are known.
        outf.seek(0)
        write_header(outf, total_puts, total_kv_bytes, key_len_fixed)

    def stat_line(label: str, xs: list[int]) -> str:
        if not xs:
            return f"{label}: n/a"
        return (f"{label}: min={min(xs)} median={int(statistics.median(xs))} "
                f"mean={statistics.mean(xs):.1f} max={max(xs)}")

    print(f"lines_read={lines_read:,} emitted={emitted:,} "
          f"malformed={malformed:,}", file=sys.stderr)
    print(f"ops: put={op_counts[OP_PUT]:,} get={op_counts[OP_GET]:,} "
          f"delete={op_counts[OP_DELETE]:,}", file=sys.stderr)
    print(stat_line("key_size", key_sizes), file=sys.stderr)
    print(stat_line("value_size", value_sizes), file=sys.stderr)
    print(f"header: total_puts={total_puts:,} total_kv_bytes={total_kv_bytes:,}"
          f" key_len_fixed={key_len_fixed}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
