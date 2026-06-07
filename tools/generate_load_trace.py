#!/usr/bin/env python3
"""Generate deterministic binary load traces for baseline/vcomp experiments.

The format is documented in ../LOAD_TRACE_FORMAT.md.  The trace stores only
key ids; loaders should materialize RocksDB keys with the same key formatting
path used by fillrandom and generate values locally.
"""

import argparse
import json
import math
import os
import random
import struct
import sys
import time
from pathlib import Path


MAGIC = b"VLOADTR1"
VERSION = 1
HEADER = struct.Struct("<8sIIQQQIIddQQQ")
RECORD = struct.Struct("<Q")

FLAG_EXACT_UNIQUE_COUNT = 1 << 0
FLAG_AFFINE_KEY_MAPPING = 1 << 1


def parse_nonnegative_int(text):
    s = text.strip().replace("_", "")
    multipliers = {
        "k": 1_000,
        "m": 1_000_000,
        "g": 1_000_000_000,
        "t": 1_000_000_000_000,
    }
    if s and s[-1].lower() in multipliers:
        return int(float(s[:-1]) * multipliers[s[-1].lower()])
    return int(s)


def choose_coprime_multiplier(modulus, rng):
    if modulus <= 1:
        return 0

    candidate = rng.randrange(1, modulus)
    if candidate % 2 == 0:
        candidate += 1
    while math.gcd(candidate, modulus) != 1:
        candidate += 2
        if candidate >= modulus:
            candidate = 1
    return candidate


def sample_existing_rank(existing, alpha, rng):
    if existing <= 1:
        return 0
    if alpha <= 0:
        return rng.randrange(existing)

    # Continuous inverse-CDF approximation for p(rank) proportional to
    # rank^-alpha over ranks [1, existing].  This is dependency-free and stable
    # for large traces; exact discrete Zipf is unnecessary for the loader knob.
    u = rng.random()
    if abs(alpha - 1.0) < 1e-9:
        rank = int(math.exp(u * math.log(existing)))
    else:
        one_minus_alpha = 1.0 - alpha
        high = existing ** one_minus_alpha
        rank = int((u * (high - 1.0) + 1.0) ** (1.0 / one_minus_alpha))
    return max(0, min(existing - 1, rank - 1))


def compute_num_records(args):
    if args.num is not None:
        return args.num
    if args.target_db_gb is None:
        raise ValueError("either --num or --target-db-gb is required")
    kv_size = args.key_size + args.value_size
    if kv_size <= 0:
        raise ValueError("key_size + value_size must be positive")
    return int(args.target_db_gb * (1024 ** 3) // kv_size)


def compute_unique_count(num_records, key_domain, args):
    if num_records == 0:
        return 0
    if args.unique_count is not None:
        unique_count = args.unique_count
    else:
        unique_count = int(round(num_records * args.unique_ratio))
    unique_count = max(1, unique_count)
    unique_count = min(unique_count, num_records, key_domain)
    return unique_count


def write_trace(args):
    num_records = compute_num_records(args)
    key_domain = args.key_domain if args.key_domain is not None else num_records
    if key_domain < 0:
        raise ValueError("--key-domain must be non-negative")
    if num_records > 0 and key_domain == 0:
        raise ValueError("--key-domain must be positive for non-empty traces")

    unique_count = compute_unique_count(num_records, key_domain, args)
    effective_unique_ratio = (
        float(unique_count) / float(num_records) if num_records else 0.0
    )

    output = Path(args.output)
    if output.exists() and not args.force:
        raise FileExistsError(f"{output} already exists; pass --force to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)
    mapper_rng = random.Random(args.seed ^ 0x9E3779B97F4A7C15)
    multiplier = choose_coprime_multiplier(key_domain, mapper_rng)
    offset = mapper_rng.randrange(key_domain) if key_domain > 0 else 0

    def dense_to_key_id(dense_rank):
        if key_domain <= 1:
            return 0
        return (multiplier * dense_rank + offset) % key_domain

    header = HEADER.pack(
        MAGIC,
        VERSION,
        HEADER.size,
        num_records,
        key_domain,
        unique_count,
        args.key_size,
        args.value_size,
        effective_unique_ratio,
        args.zipf_alpha,
        args.seed,
        FLAG_EXACT_UNIQUE_COUNT | FLAG_AFFINE_KEY_MAPPING,
        0,
    )

    started = time.time()
    introduced = 0
    chunk = bytearray()
    chunk_records = max(1, args.chunk_records)
    progress_every = args.progress_every

    with output.open("wb") as f:
        f.write(header)
        for i in range(num_records):
            remaining_ops = num_records - i
            remaining_new = unique_count - introduced
            if remaining_new <= 0:
                dense_rank = sample_existing_rank(introduced, args.zipf_alpha, rng)
            elif remaining_new >= remaining_ops:
                dense_rank = introduced
                introduced += 1
            elif introduced == 0:
                dense_rank = introduced
                introduced += 1
            elif rng.random() < (float(remaining_new) / float(remaining_ops)):
                dense_rank = introduced
                introduced += 1
            else:
                dense_rank = sample_existing_rank(introduced, args.zipf_alpha, rng)

            chunk += RECORD.pack(dense_to_key_id(dense_rank))
            if (i + 1) % chunk_records == 0:
                f.write(chunk)
                chunk.clear()

            if progress_every and (i + 1) % progress_every == 0:
                elapsed = max(1e-9, time.time() - started)
                rate = (i + 1) / elapsed
                print(
                    f"[progress] records={i + 1}/{num_records} "
                    f"rate={rate:,.0f} rec/s",
                    file=sys.stderr,
                )
        if chunk:
            f.write(chunk)

    meta = {
        "format": "VLOADTR1",
        "version": VERSION,
        "header_size": HEADER.size,
        "num_records": num_records,
        "key_domain": key_domain,
        "unique_count": unique_count,
        "unique_ratio": effective_unique_ratio,
        "requested_unique_ratio": args.unique_ratio,
        "zipf_alpha": args.zipf_alpha,
        "seed": args.seed,
        "key_size": args.key_size,
        "value_size": args.value_size,
        "record_bytes": RECORD.size,
        "data_bytes": num_records * RECORD.size,
        "file_bytes": HEADER.size + num_records * RECORD.size,
        "key_mapping": {
            "type": "affine_mod",
            "multiplier": multiplier,
            "offset": offset,
            "modulus": key_domain,
        },
        "elapsed_sec": time.time() - started,
    }
    if not args.no_sidecar:
        sidecar = output.with_suffix(output.suffix + ".meta.json")
        sidecar.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")

    print(json.dumps(meta, indent=2, sort_keys=True))


def build_parser():
    parser = argparse.ArgumentParser(
        description="Generate a deterministic binary key-id load trace."
    )
    parser.add_argument("output", help="Output trace path")
    parser.add_argument(
        "--num",
        type=parse_nonnegative_int,
        help="Number of records. Supports k/m/g/t suffixes.",
    )
    parser.add_argument(
        "--target-db-gb",
        type=float,
        help="Compute records as GiB / (key_size + value_size), matching load.sh.",
    )
    parser.add_argument("--key-size", type=int, default=24)
    parser.add_argument("--value-size", type=int, default=1000)
    parser.add_argument(
        "--key-domain",
        type=parse_nonnegative_int,
        help="Key-id domain size. Defaults to num_records, matching fillrandom.",
    )
    parser.add_argument(
        "--unique-ratio",
        type=float,
        default=0.6321205588285577,
        help=(
            "Target unique-key ratio in [0,1]. Default is 1-exp(-1), "
            "the expected fillrandom unique ratio when key_domain=num."
        ),
    )
    parser.add_argument(
        "--unique-count",
        type=parse_nonnegative_int,
        help="Exact unique-key count. Overrides --unique-ratio.",
    )
    parser.add_argument(
        "--zipf-alpha",
        type=float,
        default=0.0,
        help="Repeat-key skew. 0 means uniform; larger values are more skewed.",
    )
    parser.add_argument("--seed", type=int, default=12345678)
    parser.add_argument("--chunk-records", type=parse_nonnegative_int, default=1_000_000)
    parser.add_argument(
        "--progress-every",
        type=parse_nonnegative_int,
        default=0,
        help="Print progress every N records to stderr. 0 disables progress.",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-sidecar", action="store_true")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.num is None and args.target_db_gb is None:
        parser.error("one of --num or --target-db-gb is required")
    if not (0.0 <= args.unique_ratio <= 1.0):
        parser.error("--unique-ratio must be in [0,1]")
    if args.unique_count is not None and args.unique_count < 0:
        parser.error("--unique-count must be non-negative")
    if args.zipf_alpha < 0:
        parser.error("--zipf-alpha must be non-negative")
    if args.key_size < 0 or args.value_size < 0:
        parser.error("--key-size and --value-size must be non-negative")

    try:
        write_trace(args)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
