# Plan: Phase 1 consumes the baseline key stream

Status: planned, not implemented. Written 2026-09-13.

## Why

`--vcomp_exact_membership` now materializes from a bitmap of the key ids the
loading specification actually produces, and that closed the key-set gap: at
1 TB the overlap with a baseline DB went from 63.18 % (statistically
independent of it) to 99.52 %, with no key invented that baseline never wrote.
YCSB C positive lookup moved from 63.21 % (61.06-66.94 across 15 loadings) to
60.014 %, against baseline's 60.292 %.

Two costs remain, and both come from the same place: the bitmap is filled by a
separate thread that replays the baseline stream, while Phase 1 keeps drawing
its own keys from per-batch streams.

1. **Loading is slower.** `fillvirtual` went 55.9 s -> 72.8 s at 1 TB. Phase 1
   itself is unchanged (9.21 s -> 9.23 s) and materialization costs 0.8 s more;
   the other 20.9 s is waiting for the replay to finish. The replay itself runs
   about 50 s, of which only a few seconds are the RNG - the rest is random
   writes into a 131 MB bitmap.
2. **Coverage stops at ~99.5 % and moves between runs** (99.5210 %, then
   99.4969 %). The virtual tree is built from Phase 1's own key realization
   while the keys come from baseline's, so a file's entry budget and the keys
   available in its range are drawn from two different samples and disagree at
   the range level.

Folding the baseline stream into Phase 1 addresses both: the stream is drawn
once, marking rides along with work the shards already do, and the tree and the
key set share one realization.

## What baseline's stream is

Per write thread `t`, `Random64(*seed_base + t + 1)`. Per operation, two draws:
the first picks the key generator (`db_bench_tool.cc:8385`), the second is the
key, taken `% num`. Verified: replaying it reproduces a baseline DB's key set
exactly (overlap 100.0000 %, 6,626,871 distinct at 10 GB; 662,840,067 at 1 TB,
equal to the scanned baseline DB).

`my_seed` is `total_thread_count_` after increment, so the first benchmark's
thread `i` gets `*seed_base + i + 1`. Holds while the load benchmark is first in
`--benchmarks`, which it is for both baseline and `fillvirtual`.

## What Phase 1 does today

8 shards, 16,000 batches at 1 TB. Each batch draws its own stream from
`*seed_base ^ (0x9e3779b97f4a7c15 + batch_id * 0xbf58476d1ce4e5b9)`, then radix
sorts, dedups, fits the PLR model and registers the VSST. The per-batch seeds
exist to avoid a shared RNG; they preserve the distribution but not the
realization, which is what put the key set 37 % away from baseline's.

## The change

1. **Producer.** One thread runs the baseline stream and fills fixed-size
   chunks of key ids, one chunk per Phase 1 batch, handing them out in
   `batch_id` order through a bounded queue. Batch order stays deterministic,
   so the virtual tree stays deterministic.
2. **Shards consume chunks** instead of generating them. Everything after that
   is unchanged: sort, dedup, PLR fit, register.
3. **Marking moves into the shards.** Each shard marks its own chunk's keys,
   which are already in cache, so the 40 s of cache misses that dominate the
   replay are spread over 8 workers.
4. **Remove the replay thread and `MarkSerial`.**
5. **Gate on the flag.** With `--vcomp_exact_membership=false` the per-batch
   generator path stays exactly as it is; nothing about the default load moves.

Queue memory is depth x chunk x 8 B - 32 chunks of 65,536 keys is 16 MB.

## Risk

The producer is serial, so Phase 1 cannot finish faster than the stream. The
measured replay is ~50 s but is dominated by bitmap writes, not by the RNG, so
the producer alone should land near 5-10 s for 2.1e9 draws at 1 TB. If that
estimate is wrong, Phase 1 wall time floors at the producer's rate and the
loading time regresses instead of recovering.

**Measure the producer in isolation before touching Phase 1**: a standalone
loop of 2.1e9 `mt19937_64` draws with `% domain`, no marking. If it is above
~15 s at 1 TB, stop and take the fallback.

**Fallback**: keep the replay thread and shard only the marking by key range
(each marker owns a disjoint slice of the bitmap, so no atomics). That removes
the wait without touching Phase 1, but leaves the duplicated RNG work and the
two-realization mismatch behind.

## Measurements after

1 TB, frozen `load_options(1000, 1024, ..., 'f2load')` plus the flag, against
the recorded numbers above.

- `fillvirtual` total, Phase 1 wall, Phase 2a - target: back near 55.9 s
- `ingested key ids` (expect 662,840,067) and `covered` %
- `keyset_scan` against a baseline DB: overlap, missing, invented, Jaccard
- YCSB C: positive lookup against baseline's 60.292 %, filter checks against
  its 3.318-4.025 band

Repeat 3-5 times: baseline's positive lookup band has zero width because its
key set is deterministic, so F2Load's own spread is the only thing that can be
compared against it, and it is not yet known.

## Not doing

Caching the bitmap to a file and reusing it across loadings. It would make the
replay free, but the cost then sits outside the measured loading time, which
makes the loading-time comparison unfair.

## Open

`--threads > 1` baseline loads give each write thread its own stream and each
writes `num` keys. The union of the key sets does not depend on how the threads
interleave, but which keys land in which Phase 1 batch does, and that shapes the
tree. The frozen configuration uses `--threads=1`, so this is a limitation to
record rather than solve now.
