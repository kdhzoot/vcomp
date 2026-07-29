# Generated Load Trace Matrix

Trace root:

```text
/work/vcomp/load_traces
```

This matrix uses 12 traces, not 16. When `unique_ratio=100%`, every key appears
once, so `uniform` and `zipfian` are equivalent for put-only loading. The
100%-unique case is therefore represented once as `unique100`.

## Factors

| Factor | Values |
| --- | --- |
| DB size | 500GB, 1TB |
| KV size | 91B (`key_size=48,value_size=43`), 1024B (`key_size=24,value_size=1000`) |
| Distribution | `unique100`, `uniform50`, `zipf99_50` |
| Seed | 12345678 |

`zipf99_50` means `unique_ratio=0.5` and `zipf_alpha=0.99`.

## Files

The generated manifest is:

```text
/work/vcomp/load_traces/trace_matrix_manifest.tsv
```

Each trace uses the `VLOADTR1` format documented in:

```text
../../LOAD_TRACE_FORMAT.md
```

## Baseline Loading

After traces exist, run baseline DB creation with:

```bash
cd /home/smrc/virtual_compaction/vcomp/experiments
scripts/trace/run_trace_baseline_matrix.sh
```

The script uses `$VCOMP_DB_BENCH --benchmarks=baseload,...` with
`--use_virtual_compaction=false`, so it exercises the real RocksDB write and
compaction path while reading the generated trace.

## Known Follow-Up: Affine Mapping Sort Artifact

The current generated trace uses affine key mapping for exact unique-key
preservation:

```text
key_i = (a*i+b) mod key_domain
```

This is a permutation, but it creates a low-byte regularity that slows vcomp's
radix-sort scatter path. On 2026-06-07, `500GB/1024B/unique100` trace replay
showed sort `10.349s` vs `4.995s` for the synthetic RNG path with the same
record count. The difference came from radix scatter (`8.648s` vs `3.191s`),
not trace file I/O.

Next step when revisiting trace-based vcomp timing:

- Add a generator mode that uses a hash/permutation mapping with better low-byte
  randomness while preserving exact uniqueness.
- Re-run `500GB/1024B/unique100` synthetic vs trace and compare
  `Phase 1 sort time detail`.
- Keep baseline trace semantics unchanged unless the new mapping is documented
  in `../../LOAD_TRACE_FORMAT.md`.

Reference data:

```text
vcomp/experiments/artifacts/log_loads/sort_bottleneck_analysis.tsv
```
