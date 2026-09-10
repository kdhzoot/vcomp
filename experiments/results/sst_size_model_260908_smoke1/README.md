# SST-size model qualification, 2026-09-08

Scope: four fresh, serial 1 GiB F2Load loads, one run per KV-size/model pair.
These are implementation qualification results, not paper performance results
or a 1TB rerun. No historical DB, executable, or result was replaced.

## Result

| KV bytes | Model | Actual SST bytes | Predicted bytes for emitted entries | Error (%) |
| ---: | --- | ---: | ---: | ---: |
| 1024 | logical | 716347663 | 705140736 | +1.589318 |
| 1024 | calibrated | 716355965 | 716294780 | +0.008542 |
| 91 | logical | 733214318 | 682493084 | +7.431758 |
| 91 | calibrated | 733181737 | 733281474 | -0.013601 |

Error = `100 * (actual / predicted - 1)`. All four cases used 16 input batches,
finished with zero pending compaction bytes, and passed read-only reopening
with the qualified clean RocksDB binary. Of 1000 random lookups, both 1KB
models found 671 keys and both 91B models found 640. This is a reopening smoke
check, not full key-membership equivalence or cardinality validation.

Physical compaction write bytes were zero in all four cases, including the
logical controls. Therefore this pilot does not demonstrate elimination of a
residual-compaction workload; 1TB behavior remains untested. Cardinality and
distribution limitations of the incoming discrete-CDF implementation remain
separate from this byte-model correction. The `benchmark` field in
`results.json` is the original db_bench summary, not end-to-end loading time;
use the execution records for process wall time, and do not use these tiny
qualification runs to make performance claims.

Additional validation passed: standalone arithmetic tests; 517 memory-builder
checks with 34 held-out SST cases; all 11 focused virtual-SST tests.

## Configuration and reproduction

The source base was `9445c66c5bc04640f3b9c50dc22252eb6dc72645` plus the size-model
patch preserved under `provenance/`. `logical` only selects the old byte model;
it does not revert other changes from the source base or reproduce the old
historical executable. Discrete-CDF and KMV use this source's enabled defaults;
global-unique materialization is disabled.

Each dataset has 1 GiB logical input: 1,048,576 records of 24+1000 bytes or
11,799,360 records of 48+43 bytes (the latter rounded down to whole records).
Shared options: seed 12345678; 64 MiB logical batches and target SST size;
256 MiB level base; level multiplier 10; 16 maximum write buffers; one outer
coordinator, eight phase-1 shards and 48 materialization workers; Bloom 10;
format version 6; no compression; direct I/O; automatic compaction enabled.
The four load and four verification `command.json` files retain exact argv.

Built in `/tmp/vcomp-sst-size-build.naPtjb` with:

```bash
DEBUG_LEVEL=0 OPTIMIZE_LEVEL=-O3 ./make.sh
```

GCC/G++ 11, C++20, `-O3 -DNDEBUG -fno-rtti`. The validated binary is archived at
`experiments/artifacts/builds/sst_size_model_260908/db_bench`, SHA-256
`9f269e3d94177e95f5eb7c86e526d1ab33f936bfb4f51bf47124d1def10b6a06`.
Executables are not included in Git. The original run command was:

```bash
python3 -u experiments/scripts/load/smoke_sst_size_model.py \
  --binary /tmp/vcomp-sst-size-build.naPtjb/db_bench \
  --run-id sst_size_model_260908_smoke1
```

Use a new run ID for a rerun; the runner rejects existing output/DB roots.
The original DB root is `/work/vcomp/exp/sst_size_model_260908_smoke1` and raw
outputs are in `experiments/artifacts/validation/sst_size_model_260908_smoke1`.
All four new DBs remain there.

## Evidence

- `manifest.json`, `results.json`, `COMPLETED`: unchanged original run records.
- `evidence/`: original commands, execution records and small benchmark logs.
- `model_test.log`, `calibration_test.log`, `split_test.log`: focused test results.
- `provenance/`: build metadata, tracked implementation patch and newly added
  source files; the exact runner and its common helper are also archived.
- [Implementation notes](../../docs/SST_SIZE_MODEL.md): placement, timing,
  model semantics, and limitations.
