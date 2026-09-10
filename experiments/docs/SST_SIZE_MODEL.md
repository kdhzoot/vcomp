# Physical SST size model

Scope: user-requested correction after updating to `9445c66c5b` on 2026-09-08.
No change to merge/dedup cardinality, input keys, batching, or level limits.
No changes to the manuscript or existing DBs/results/binaries.

## Problem and implementation

The old virtual file size was `entries * (key_size + value_size)`. Materialized
SSTs additionally contain internal-key encoding, block framing/restarts,
filters, indexes, properties, and a footer; compression and prefix sharing
also change the physical size. The previous 1TB run's materialized file bytes
were 1.59% above its written logical KV bytes. This observation is not a
universal correction factor, especially for 91-byte records or compression.

`--vcomp_sst_size_model=calibrated` is the new fillvirtual default. Before
registering any virtual files it writes bounded calibration SSTs to a private
in-memory filesystem, using the same normalized Options and value generator
as actual materialization. It does not consume the input RNG or write sample
files to the device. Calibration isolates statistics, listeners, logging,
rate limiting, and file management from the measured DB. Direct I/O is off
only for the in-memory files; actual materialization retains the user's I/O
options.

Two sample sizes (approximately 4/8 MiB of logical input, capped by entry count
and key domain) are measured for dense and representative sparse key spacing.
Each pair fits a rational affine model `ceil(n * slope) + fixed_file_bytes`;
the model takes the larger of the two predictions. Slopes remain fractional,
avoiding a whole-byte-per-entry rounding bias on small records. Positive
intercepts are rounded up, negative intercepts are clamped to zero. The empty
descriptor costs zero. Arithmetic saturates instead of wrapping.

The physical model is shared by initial L0 descriptors, compaction output
descriptor sizes, size-based split capacity, and grandparent byte thresholds.
Logical `key_size + value_size` remains the denominator for memtable batch
capacity. Each materialization logs per-level descriptor bytes, predicted bytes
for the actually emitted entry count, and actual file bytes, separating count
changes from byte-model error. Calibration samples and time are logged as well;
both the reported Phase 1/Total and enclosing db_bench/process wall time include
calibration. `fillvirtual` explicitly requires one outer benchmark coordinator
(`--threads=1`); phase-1 shards and materialization workers remain parallel.

`--vcomp_sst_size_model=logical` preserves the old byte estimate for controlled
comparisons with this same source version. It does not revert other incoming
changes (notably the default-on discrete-CDF path). A rebuilt binary is not the
historical binary, regardless of this flag.

## Limits

This remains an estimate, not an exact SST-size oracle or guaranteed upper
bound. Real key density, block rounding, compression, and per-file collectors
can differ from the probes. The sparse probe approximates gaps in a full-size
SST; it does not spread a tiny probe over the entire DB domain. Level-dependent
key distributions can still cause residual compaction. Keep final automatic
compaction/drain and zero-pending validation; do not suppress real compaction
to make the result appear settled. The latest source already installs the new
SuperVersion and queues compaction after materialization.

## Validation plan

- Pure standalone arithmetic tests: fitting, envelope, inverse capacity,
  zero/invalid input, overflow, and saturation, including sanitizer execution.
- Split tests: count/materialization preservation, L0 single-file rule,
  physical registration bytes, grandparent thresholds, unchanged logical batch
  size, and the legacy fallback.
- Memory-only builder tests: held-out real SST sizes for 91B/1KB records,
  Bloom on/off, block/restart options, and available compression; failures
  should report rather than hide unsupported configurations.
- After the running baseline repeat campaign completes, build a private source
  snapshot via `./make.sh`, leaving the historical workspace `db_bench` intact.
- Small serial smoke loads (1 GiB, 91B and 1KB) with logical and calibrated
  models on fresh disposable DBs: compare recorded model errors, input batch
  count, installation, final pending compaction, and read-only reopen. These
  are qualification runs, not new paper performance results or 1TB reruns.

## Completed qualification (2026-09-08)

Built after the baseline repeat campaign completed, using a private snapshot
of `9445c66c5bc04640f3b9c50dc22252eb6dc72645` plus this patch. The build command
was `DEBUG_LEVEL=0 OPTIMIZE_LEVEL=-O3 ./make.sh` (GCC/G++ 11, `-O3 -DNDEBUG`,
`-fno-rtti`). The historical workspace executable was not rebuilt or replaced.

Candidate executable:
`experiments/artifacts/builds/sst_size_model_260908/db_bench`.
SHA-256: `9f269e3d94177e95f5eb7c86e526d1ab33f936bfb4f51bf47124d1def10b6a06`.
The original private build path was `/tmp/vcomp-sst-size-build.naPtjb`.

The arithmetic suite passed, the memory-only calibration suite passed 517
checks with 34 held-out SST cases, and all 11 focused virtual-SST tests passed.
Four serial 1 GiB smoke loads also passed:

| Logical KV size | Logical model error | Calibrated model error | Input batches, each model |
| --- | ---: | ---: | ---: |
| 1KB (24 + 1000 B) | +1.589318% | +0.008542% | 16 |
| 91B (48 + 43 B) | +7.431758% | -0.013601% | 16 |

Error is `100 * (actual SST bytes / predicted bytes for emitted entries - 1)`;
positive means underprediction. Every case finished with zero pending
compaction bytes and passed a clean-binary read-only reopen. All four also had
zero physical compaction write bytes: this pilot demonstrates byte-estimation
accuracy, not that the model removed an observed residual-compaction workload.
It does not validate 1TB behavior or repair the independent key-distribution
and cardinality limitations of the current discrete-CDF implementation.

See the [qualification bundle](../results/sst_size_model_260908_smoke1/README.md)
for exact commands, configuration, source/build provenance, and logs. Existing
paper measurements remain unchanged.
