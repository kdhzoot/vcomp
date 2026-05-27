#!/usr/bin/env python3
"""Sample keys from a .vcomptrace file and analyze front-8-byte uniqueness.

Used to validate Option D applicability (vcomp/README.md §7.8) before
adding a new Twitter cluster. Threshold: prefix8-collision buckets must
be ~0% of unique full keys. If >1%, Option D will lose PLR resolution.

Usage:
  python3 analyze_trace_prefix.py <trace.vcomptrace> [sample_n]
"""

import struct
import sys
from collections import Counter

MAGIC = b"VCMPTRC1"
HEADER_LEN = 40


def parse_records(path, max_records=None):
    with open(path, "rb") as f:
        magic = f.read(8)
        assert magic == MAGIC, f"Bad magic: {magic!r}"
        version, reserved = struct.unpack("<II", f.read(8))
        assert version == 1, f"Bad version: {version}"
        f.seek(HEADER_LEN)

        n = 0
        while True:
            hdr = f.read(5)
            if len(hdr) < 5:
                break
            op, key_len = struct.unpack("<BI", hdr)
            key = f.read(key_len)
            if len(key) < key_len:
                break
            vsz_bytes = f.read(4)
            if len(vsz_bytes) < 4:
                break
            (value_size,) = struct.unpack("<I", vsz_bytes)
            yield op, key, value_size
            n += 1
            if max_records and n >= max_records:
                break


def main():
    path = sys.argv[1]
    max_records = int(sys.argv[2]) if len(sys.argv) > 2 else 1_000_000

    n_total = 0
    n_put = 0
    full_keys = set()
    prefix8_counter = Counter()
    sample_keys = []
    key_lens = Counter()

    for op, key, value_size in parse_records(path, max_records):
        n_total += 1
        if op == 1:
            n_put += 1
        full_keys.add(key)
        prefix = key[:8]
        prefix8_counter[prefix] += 1
        key_lens[len(key)] += 1
        if len(sample_keys) < 20:
            sample_keys.append(key)

    n_unique_full = len(full_keys)
    n_unique_prefix8 = len(prefix8_counter)

    print(f"Records scanned: {n_total:,}")
    print(f"Put records:     {n_put:,}")
    print(f"Unique full keys:        {n_unique_full:,} ({100*n_unique_full/n_total:.2f}%)")
    print(f"Unique prefix8 (first 8B): {n_unique_prefix8:,} ({100*n_unique_prefix8/n_total:.2f}%)")
    print(f"Collision rate of prefix8 (= same prefix8, different full key):")
    # How many full keys share their prefix8 with another full key?
    prefix_to_full = {}
    for k in full_keys:
        prefix_to_full.setdefault(k[:8], set()).add(k)
    distinct_prefix8 = sum(1 for v in prefix_to_full.values() if len(v) > 1)
    keys_in_colliding_buckets = sum(len(v) for v in prefix_to_full.values() if len(v) > 1)
    print(f"  prefix8 buckets w/ >1 full key:  {distinct_prefix8:,} / {len(prefix_to_full):,}")
    print(f"  full keys in those buckets:      {keys_in_colliding_buckets:,} / {n_unique_full:,} "
          f"({100*keys_in_colliding_buckets/n_unique_full:.2f}%)")

    print(f"\nKey length distribution:")
    for kl, c in sorted(key_lens.items()):
        print(f"  {kl} bytes: {c:,}")

    print(f"\nTop 10 most frequent prefix8 values:")
    for prefix, count in prefix8_counter.most_common(10):
        print(f"  {prefix.hex()} ({prefix!r}): {count:,}")

    print(f"\nSample keys (first 20):")
    for k in sample_keys[:20]:
        printable = "".join(c if 32 <= ord(c) < 127 else "." for c in k.decode("latin-1"))
        print(f"  prefix8={k[:8].hex()}  full({len(k)}B): {printable}")


if __name__ == "__main__":
    main()
