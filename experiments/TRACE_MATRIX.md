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
../vcomp/LOAD_TRACE_FORMAT.md
```

## Baseline Loading

After traces exist, run baseline DB creation with:

```bash
cd /home/smrc/virtual_compaction/eval-vcomp
./run_trace_baseline_matrix.sh
```

The script uses `../vcomp/db_bench --benchmarks=baseload,...` with
`--use_virtual_compaction=false`, so it exercises the real RocksDB write and
compaction path while reading the generated trace.
