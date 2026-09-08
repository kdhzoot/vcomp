# Paper Artifact-Baseline Comparison

## Purpose and status

**Status:** 1 GB smoke gate passed for ADOC/ADOC-off and DiffKV/Titan on
2026-08-24. These are functional checks only; performance pilots and
subcompaction propagation checks remain pending.

This experiment supports the paragraph in
`paper/tex/02_Background.tex` that distinguishes systems evaluated directly
from systems cited only as related work. It must answer two separate questions:

1. Which public artifacts can be reproduced faithfully on the current
   single-node commodity-SSD testbed?
2. Among the artifacts that pass that gate, how much does each technique reduce
   1 TB loading time relative to a version-matched baseline?

Artifact availability alone is not sufficient for inclusion. A system enters
the paper figure only after its build, workload, mechanism, completion boundary,
and output state have been validated. No result may be inserted in the paper
while the corresponding `\TBC{}` remains unresolved.

## Candidate register

The table below is the source of truth for inclusion and exclusion decisions.
`Can reproduce?` means faithful reproduction of the core mechanism on the
current machine, not merely successful compilation.

| Paper | Venue / year | Optimization target | Implementation base | Artifact URL | Artifact availability | Required hardware | Required deployment | Can reproduce? | Can use as baseline? | Reason for inclusion or exclusion |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ADOC | FAST '23 | data-flow tuning and write-stall reduction | RocksDB; author artifact exposes ADOC through `db_bench_tool` | <https://github.com/supermt/FEAT_7.11> | Public | Ordinary Linux storage; no mandatory accelerator or PM | Single node | **1 GB smoke passed; pilot required** | **Primary candidate** | ADOC-on and ADOC-off both loaded and reopened; the on log proves both FEA and TEA activation. The current binary is a debug build, so its times are not performance evidence. |
| DiffKV | ATC '21 | differentiated ordering and KV separation | Titan/RocksDB | <https://github.com/ustcadsl/diffkv> | Public | Ordinary Linux server and SSD | Single node | **1 GB smoke passed with wrapper-only lifecycle fix; pilot required** | **Primary candidate** | Titan and DiffKV each persisted about 1.1 GB and returned sampled values after reopen. The artifact YCSB load must keep assertions enabled, and the small smoke wrapper explicitly flushes before close; these adaptations must remain disclosed. |
| SpanDB | FAST '21 | hybrid-device placement, SPDK WAL, asynchronous execution | RocksDB-derived | <https://github.com/SpanDB/SpanDB> | Public | Separate speed and capacity devices; raw NVMe binding, SPDK, and hugepages | Single node with heterogeneous devices | **No on the current testbed** | Conditional only | All 31 experiment NVMe devices are members of `/dev/md0`; the remaining NVMe hosts the OS. Unbinding either target would disrupt or damage active storage. Reconsider only when dedicated spare devices are provided. |
| MatrixKV | ATC '20 | NVM L0 matrix and fine-grained compaction | RocksDB 5.18.3 | <https://github.com/PDS-Lab/MatrixKV> | Public | Intel Optane DC PMM, PMDK, and SSD | Single node with DRAM/NVM/SSD hierarchy | No | Citation only | The NVM-resident matrix container is a core mechanism and cannot be reproduced faithfully on SSD alone. |
| ListDB | OSDI '22 | persistent SkipLists and in-place Zipper Compaction | Standalone PM KV store | <https://github.com/DICL/listdb> | Public | Byte-addressable NVMM; paper uses Optane DCPMM in AppDirect mode | Single NUMA server with PM | No | Citation only | In-place pointer updates and copy avoidance rely on persistent memory semantics. |
| Pacman | ATC '22 | PM-aware log compaction | PM-based log-structured KV stores | <https://github.com/thustorage/pacman> | Public | Optane DCPMM, PMDK, AppDirect, `fsdax`/DAX filesystem | Single node with PM | No | Citation only | The artifact explicitly requires PM configuration and DAX. |
| DecouKV | ATC '25 | decoupled sorting operations on hybrid storage | RocksDB-derived artifact containing RocksDB, MatrixKV, and ADOC comparisons | <https://github.com/QingyangZ/DecouKV> | Public | 128 GB Optane DCPMM plus SSD in the paper; artifact requests at least 50 GB PMEM and 200 GB SSD space | Single node with hybrid fast/slow devices | No | Citation only | The artifact is useful as code/reference, but its AOF placement and resource decoupling require PMEM unavailable in the testbed. |
| AegonKV | FAST '25 | KV separation with GC offload | Titan/RocksDB 6.29-derived | <https://github.com/CGCL-codes/AegonKV> | Public | Samsung SmartSSD, Vitis 2021.1, XRT 2021.1, SmartSSD target platform | Single node with computational storage | No | Citation only | SmartSSD GC offload is the core mechanism and cannot be emulated faithfully with ordinary NVMe SSDs. |
| HATS | FAST '26 | coordinated read/compaction scheduling | Cassandra 5.0 | <https://github.com/adslabcuhk/hats> | Public, tagged artifact | Commodity servers and SSDs, but many machines and 10 Gbps networking | Distributed Cassandra cluster | No | Citation only | The paper evaluates a 22-machine testbed (20 servers and 2 clients); the artifact appendix gives a 12-machine example (10 storage and 2 client nodes). Both are outside the single-node loading scope. |
| Calcspar | ATC '23 | IOPS-contract-aware cache and compaction-I/O allocation | RocksDB-derived | <https://github.com/yhzhou-pds/ATC23-Calcspar> | Public | Cloud block storage exposing an IOPS contract, evaluated primarily on AWS EBS | Cloud VM plus provisioned block volume | No | Citation only | Its mechanism targets contractual throttling and latency variation in cloud block storage, not local SSD loading. |

### Decision rule used in the paper

- **Direct evaluation:** the public artifact must run its core mechanism on the
  current single-node SSD testbed, expose a verifiable load path, and provide a
  same-version matched baseline.
- **Hardware-constrained citation:** source is available, but the mechanism
  requires persistent memory or computational storage that the testbed lacks.
- **Deployment-constrained citation:** source is available, but the mechanism
  assumes a distributed cluster or cloud storage contract outside the paper's
  single-node local-SSD scope.
- Do not say that a system was excluded because its artifact is unavailable
  when the artifact is public. Use: "the artifact is available, but faithful
  reproduction requires hardware or a deployment unavailable in our testbed."

## Completed 1 GB smoke gate (2026-08-24)

These runs test buildability, feature activation, persistence, and reopen
correctness. They do not qualify loading-time claims.

| Pair member | Artifact commit | Result | Functional evidence | Performance caveat |
| --- | --- | --- | --- | --- |
| ADOC-off | `5ed60f50d6cd8259b94e7f842ff06c6ab4df40a1` | Pass | 1 GB logical load; 10,000/10,000 reads found after reopen | Artifact `db_bench` was built with its debug default |
| ADOC-on | same | Pass | Same output size as ADOC-off; log contains `Using FEAT tuner`, `FEA is triggered`, and `TEA is triggered`; 10,000/10,000 reads found | Same debug-build caveat; the 1 GB run is too short for comparison |
| Titan | `f37ea7c94b6c22d082312b288c8a81c22acf100d` | Pass | 1,048,576 records loaded; 1,122,784,001 final bytes; 258 distributed keys returned 1,000-byte values after reopen | Single-thread smoke with wrapper-only explicit flush |
| DiffKV | same | Pass | 1,048,576 records loaded; 1,095,660,473 final bytes; 258 distributed keys returned 1,000-byte values after reopen | Same smoke wrapper; absolute time is not comparable to ADOC or F2Load |

Raw valid artifacts are under
`artifacts/log_loads/paper_artifact_baselines_260824_smoke2/adoc_smoke/` and
`artifacts/log_loads/paper_artifact_baselines_260824_smoke3/diffkv_smoke/`.
The executable hashes, source commits, configs, environment snapshot, command
lines, disk counters, and immutable runner snapshots are stored with each run.

Two DiffKV diagnostics are retained rather than silently discarded:

- `260824_smoke1` is invalid because a release build defined `NDEBUG`. The
  upstream YCSB-C places `DB::Insert()` inside `assert()`, so the compiler
  removed the writes while the client still printed the requested operation
  count.
- `260824_smoke2` executed writes but is invalid because the 1 GB process
  closed before foreground blob files were committed to the Titan manifest;
  reopen then reported missing blob files. The single-thread smoke adapter now
  performs a synchronous public-API `Flush()` before destruction.

### DiffKV/Titan benchmark-driver decision

The checked-out DiffKV artifact contains RocksDB-style `db_bench` sources and
builds them as `titandb_bench` when `WITH_TITAN_TOOLS=ON`. The current release
build has that target configured but has not built the executable yet.

The upstream `titandb_bench` cannot be used as an unmodified DiffKV/Titan
selector. Its `--use_titan=true` path opens Titan and its
`--use_titan=false` path opens the bundled plain RocksDB; the latter is not
DiffKV. The tool exposes Titan's minimum blob size and basic GC controls, but
does not expose DiffKV's `level_merge`, `range_merge`, `lazy_merge`,
`sep_before_flush`, `mid_blob_size`, or `max_sorted_runs` controls. In the
artifact's official YCSB-C path, both `-db titandb` and `-db diffkv` use the
same TitanDB adapter and are distinguished by `titandb_config.ini` versus
`diffkv_config.ini`.

For an exact match to the current Figure 3 load generator, use a narrowly
instrumented `titandb_bench` that only exposes those existing Titan options.
Keep the artifact source commit and linked RocksDB submodule fixed, save the
patch with every run, and use the same patched release binary for both members.
This preserves `db_bench`'s exact 24-byte random-key and 1,000-byte value
generation instead of substituting YCSB-C's variable-length `user<hash>` keys.
Cross-check the patched driver's 10 GiB output and active option dump against
the official YCSB-C configurations before promotion.

The matched settings are:

- Titan: `min_blob_size=0`, `mid_blob_size=0`, `level_merge=false`,
  `range_merge=false`, `lazy_merge=false`, `sep_before_flush=true`, and
  `max_sorted_runs=0`;
- DiffKV: `min_blob_size=128`, `mid_blob_size=8192`, `level_merge=true`,
  `range_merge=true`, `lazy_merge=true`, `sep_before_flush=true`, and
  `max_sorted_runs=10`; enabling level merge also sets the artifact's dynamic
  level options exactly as its YCSB-C adapter does;
- both: identical GC thresholds and threads, RocksDB background jobs,
  subcompaction cap, direct-I/O policy, disabled WAL, no compression, seed,
  write batch, timing boundary, and validation.

The Figure 3 workload is 1,048,576,000 records, 24-byte keys, 1,000-byte
values, and
`fillrandom,flush,compact0,waitforcompaction,stats,levelstats`. Use the default
skip-list memtable for the primary DiffKV/Titan pair: the artifact's own Titan
benchmark notes that Titan GC reads the memtable and that a vector memtable
causes poor performance. If vector is required as a sensitivity test, label it
separately and never compare Titan-skiplist with DiffKV-vector.

Promotion sequence: build and dry-run the patched target; pass a 10 GiB paired
pilot with reopen verification and zero pending work; pass a 100 GiB capacity,
GC, RSS, and option-propagation qualification; then run the 1,000 GiB pair only
while `/work` is otherwise idle. Alternate pair order across repetitions. A
single validated run may populate the provisional Figure 3 bar, but the final
paper result still requires three matched repetitions.

Before a performance pilot, rebuild ADOC in release mode, validate DiffKV's
completion boundary at 10 GB without relying on the small-workload workaround,
and trace `max_subcompactions` propagation independently for both artifact
families.

## Comparison methodology

### Why absolute times cannot be pooled naively

ADOC, DiffKV, and the current F2Load harness use different RocksDB/Titan
versions and may use different physical layouts. A single absolute-time chart
would conflate the research mechanism with engine-version, backend, and
workload differences. In particular, DiffKV's KV-separated output is not the
same final storage organization as natural RocksDB loading.

The primary comparison is therefore **normalized loading time relative to a
matched baseline from the same artifact**:

```text
normalized loading time = treatment completion time / matched-baseline completion time
```

Lower is better. The reciprocal may be reported as speedup, but the figure and
caption must use one convention consistently.

| Treatment shown on x-axis | Matched baseline | Required pairing |
| --- | --- | --- |
| ADOC | same artifact/build with ADOC disabled | identical workload, options, compiler, threads, selected subcompaction cap, and completion boundary |
| DiffKV | Titan backend from the same artifact | identical YCSB workload, config, threads, selected subcompaction cap, and GC/settling policy; also record artifact RocksDB as context |
| SpanDB, only if later feasible | RocksDB backend in the SpanDB artifact | same speed/capacity device allocation and client configuration |
| no-compaction | current RocksDB natural loading | same current binary and options except compaction policy; subcompaction is `N/A` for the treatment |
| last-compaction | current RocksDB natural loading | same current binary, selected subcompaction cap, and options except compaction policy |

The paper's Background figure should not mix these bars with F2Load's main
end-to-end result. It is a design-alternative figure, with natural loading as
the 1.0 reference and the x-axis ordered as `ADOC`, `DiffKV`, `no-compaction`,
and `last-compaction`; add `SpanDB` only if its hardware gate later passes.
Keep absolute hours, engine versions, and output-state caveats in an adjacent
table or appendix. The same labeled figure can be referenced from
`sec:alternatives-bypass-compaction`.

### Subcompaction policy

Subcompaction is a standard engine tuning option, not a separate paper
treatment. Never enable it only for one member of a compaction-bearing matched
pair. For each implementation family, the treatment and matched baseline use
the same selected cap:

- current RocksDB: natural baseline and last-compaction use the same cap;
- ADOC artifact: ADOC-on and ADOC-off use the same cap;
- DiffKV artifact: DiffKV and Titan use the same cap for the LSM compaction
  path. Record separately that Titan value-log GC is not controlled by this
  RocksDB option.

No-compaction performs no compaction, so its configured value is reported as
`N/A` and its actual scheduled count must be zero. F2Load's materialization
workers are also distinct from RocksDB subcompactions and must not be labeled
as subcompactions.

Do not assume that an old artifact exposes or correctly propagates this
option. During the 1 GB smoke test, trace the artifact flag/config field to the
RocksDB `max_subcompactions` or `CompactRangeOptions.max_subcompactions` field.
Use wrapper/configuration changes only. If faithful activation requires an
algorithm patch, retain the artifact's native setting, report it, and do not
claim a subcompaction-controlled comparison.

For current RocksDB last-compaction, perform an idle-machine 100 GB sweep at
caps `1, 4, 8, 16, 32`. The existing cap-48 result is diagnostic only: it
scheduled 35 subcompactions and used 113.1 GiB peak RSS while another load was
active. Select the smallest cap whose last-compaction time is within 5% of the
fastest safe point and whose matched natural-baseline time is within 5% of its
best safe point. This avoids selecting a memory-heavy cap for a marginal gain
or tuning only the treatment. The selected cap must also satisfy all of the
following:

- more than one subcompaction is actually scheduled;
- peak RSS remains below 25% of testbed DRAM (256 GiB), with no swap, OOM, or
  sustained memory-pressure event;
- the final compaction reaches zero pending bytes and the database reopens;
- sampled key/value checks pass and the expected last-compaction layout is
  recorded;
- improvement is repeatable rather than a single-run fluctuation;
- the same cap is used by the natural baseline and does not cause a material
  regression outside the qualification's run-to-run variation.

Before using the selected cap at 1 TB, run one 500 GB memory-safety
qualification. Abort or lower the cap if RSS approaches the 256 GiB gate. The
same selected current-RocksDB cap is then used for the natural baseline and
last-compaction paper pair. For ADOC and DiffKV, test `1` and this selected cap
at 10 GB, then confirm the supported cap independently at 100 GB; lower it for
that artifact family if its engine version or memory footprint requires it.
Within-family pairing is mandatory even when selected caps differ across
artifact families.

### Completion boundaries

- **Natural RocksDB:** start before the first insert; stop only after flush and
  all background compaction have settled.
- **ADOC:** use the same start and settled-state boundary as its matched
  ADOC-off build. Tuner initialization is included.
- **DiffKV/Titan:** include load plus any synchronous/background work required
  by the artifact to declare the database ready. Record whether value-log GC is
  enabled and whether pending GC remains.
- **no-compaction (`l0only`):** stop after inserts and final flush. It is an
  intentionally non-realistic state and must be visually marked as such.
  Confirm that zero subcompactions were scheduled.
- **last-compaction (`l0compact`):** stop after inserts, flush, and the final
  forced full compaction using the selected cap. Validate the actual scheduled
  count and report that its final layout can differ from natural leveled
  loading.

## Workload and scale

### Compatibility gate

The preferred common workload is a 1 TB logical dataset with 24 B keys and
1000 B values, no compression, deterministic keys, and no WAL. Use it only if
the unmodified artifact supports the same semantics. If an artifact requires
its native workload (for example, DiffKV's Pareto-distributed average 1 KB
values), run treatment and matched baseline with that native workload, document
the difference, and retain only the within-artifact normalized result. Do not
present cross-artifact absolute time as an apples-to-apples ranking.

For each candidate:

1. 1 GB smoke test: build, open, insert, close, and reopen.
2. 10 GB pilot: exercise the complete timing, settling, validation, and parsing
   pipeline.
3. 100 GB qualification: verify stability, space growth, pending work, and that
   the technique is actually active.
4. 1 TB paper run: three independent repetitions, alternating matched baseline
   and treatment order. Report median and min/max.

For no-compaction and last-compaction, the 100 GB qualification additionally
includes the subcompaction sweep above. Build or copy a clean L0-only source
outside the timed interval for final-compaction-only tuning, then rerun the
selected configuration end to end. Never reuse a database after a destructive
full compaction. At 1 TB, alternate natural-baseline and treatment order across
the three matched repetitions.

Do not launch a 1 TB artifact run until the current 8 TB true-91 B baseline run
has completed and `/dev/md0` is idle. Only one load process may use `/work` at a
time.

## Artifact acquisition and provenance

- Place checkouts and build outputs under
  `experiments/artifacts/external_baselines/<system>/`; they are provisional and
  must not be promoted as source code in this repository.
- Pin and record the exact commit before any patching.
- Archive `git status --short`, the full patch against that commit, compiler and
  dependency versions, build command, executable SHA-256, and artifact README.
- Wrapper-only changes belong under `experiments/scripts/artifact_baselines/`.
  Avoid changing the algorithm; any compatibility patch must be isolated,
  justified, and reported in the paper artifact notes.
- Use unique database directories under
  `/work/vcomp/exp/paper_artifact_baselines/<system>/<run_id>/`.
- Store raw logs under
  `experiments/artifacts/log_loads/paper_artifact_baselines_<run_id>/`.

## Metrics and validation

Collect for every treatment and matched baseline:

- end-to-end completion time and insert-only time;
- operations/s and submitted logical bytes;
- host-visible bytes written to `/dev/md0`;
- final on-device bytes;
- peak RSS and CPU time;
- configured subcompaction cap, actual scheduled count, setup time, and
  per-subcompaction duration/bytes when exposed;
- write-stall duration, compaction bytes/count, and pending background work when
  exposed by the artifact;
- key count after reopen and sampled point-lookups for correctness;
- final layout description, including whether values are separated or all SSTs
  remain in L0.

An artifact is excluded from the figure if any of the following holds:

- its core feature cannot be proven active;
- propagation of a configured subcompaction cap cannot be proven in a
  compaction-bearing implementation. A job that remains a single range due to
  insufficient input size is allowed only when that reason is recorded;
- peak RSS exceeds 256 GiB, swap/OOM occurs, or the memory monitor triggers;
- there is no same-version matched baseline;
- the 100 GB qualification fails to settle, reopen, or return correct values;
- the 1 TB configuration changes workload semantics between treatment and
  matched baseline;
- the artifact requires destructive device rebinding, PM, SmartSSD, cloud EBS,
  or a multi-node cluster unavailable in the testbed;
- measurement begins or ends at different lifecycle boundaries.

Build failure alone is reported as a reproduction failure, not retroactively as
"artifact unavailable."

## Expected outputs

- `experiments/results/paper_artifact_baseline_runs.tsv`: one row per run with
  provenance and validation status.
- `experiments/results/paper_artifact_baseline_summary.tsv`: matched pairs,
  normalized time, median, min/max, and exclusion reason.
- `experiments/results/paper_artifact_baseline_absolute.tsv`: absolute time and
  workload/version metadata for the appendix or audit trail.
- `experiments/analysis/summarize_paper_artifact_baselines.py`: validation and
  aggregation.
- `experiments/analysis/plot_paper_artifact_baselines.py`: publication plot.
- `paper/figs/bg_artifact_loading.pdf` and `.png`: promoted only after all
  included candidates pass validation.

The caption must state that prior-system bars are normalized to artifact-matched
baselines, that no-compaction leaves a non-realistic L0-only state, and that
last-compaction includes its final forced compaction.

## Execution order

1. Freeze this candidate register and capture official paper/artifact links.
2. Resolve the preserved 8 TB true-91 B RocksDB result: either accept the
   completed 46.2-hour run with its documented brief interference or schedule
   an isolated rerun. Device write amplification must come from a clean run.
   Confirm the machine and `/dev/md0` are idle before continuing.
3. Add a non-destructive preflight script that checks toolchains, free space,
   background writers, and artifact feature activation.
4. Run the idle current-RocksDB last-compaction subcompaction sweep; qualify
   no-compaction once, then perform the 500 GB memory gate for the selected
   last-compaction cap.
5. **Smoke complete.** Rebuild ADOC in release mode, then pilot ADOC and its
   matched ADOC-off baseline while proving that both builds receive the same
   subcompaction cap.
6. **Smoke complete with documented wrapper adaptations.** Add only the
   missing DiffKV/Titan option plumbing to `titandb_bench`, build it in release
   mode, and cross-check a 10 GiB pair against the official YCSB-C configs.
   Prove that both members receive the same LSM subcompaction cap and GC policy;
   separately verify the artifact RocksDB backend for context.
7. Promote only candidates that pass the 100 GB qualification to the 1 TB
   three-repetition series.
8. Aggregate matched pairs, generate the figure, and review the caption and
   exclusion wording before resolving the paper `\TBC{}`.
9. Leave SpanDB blocked unless dedicated non-RAID speed and capacity devices
   are provided; never unbind a `/dev/md0` member or the system disk.

## Source verification notes

- ADOC's official paper and repository establish its RocksDB/db_bench path, but
  the repository README also labels parts as work in progress; hence the pilot
  gate.
- DiffKV's paper and repository establish the Titan base, public YCSB-C load
  phase, and ordinary SSD evaluation.
- SpanDB's repository explicitly requires SPDK hugepages and raw NVMe device
  binding, while the paper uses separate speed and capacity devices.
- MatrixKV, ListDB, Pacman, DecouKV, and AegonKV document the PM or SmartSSD
  requirements used in the classification above.
- HATS is Cassandra-based. Its paper reports a 22-machine evaluation testbed;
  its artifact appendix gives a 12-machine example, both over 10 Gbps networking.
- Calcspar is evaluated on contract-governed cloud block storage, especially
  AWS EBS, rather than local SSDs.

## Figure 3 single-run measurements (2026-08-31)

At the author's request, no-compaction and last-compaction were measured once
at 1 TB and added to the current Figure 3 alongside the existing single-run
baseline and F2Load measurements. All four measurements use 24-byte keys,
1,000-byte values, no compression, disabled WAL, and one timed repetition.
The consolidated values and raw-source paths are recorded in
`experiments/results/paper_figure4_loading_time_1tb_single.tsv`.

- No-compaction completed the timed load in 1,286 s (21.4 min). The runner's
  original 10,000-read validation was stopped because point lookups over the
  intentionally overlapping L0 state were prohibitively slow; a separate
  reopen check found 100 of 100 sampled keys. The final state retains
  1,091,414,965,952 pending compaction bytes and is not a settled LSM tree.
- Last-compaction completed in 4,924 s (82.1 min), including 1,295.026 s of
  loading and 3,628.383 s for the final forced compaction. A reopen check found
  10,000 of 10,000 sampled keys, pending compaction bytes were zero, peak RSS
  was 40,570,564 KB, and no swap activity was observed.

DiffKV remains pending. These single-run absolute bars are provisional and do
not replace the matched-baseline, three-repetition methodology specified above
for a final cross-system artifact comparison.

## ADOC 1 TB single-run measurement (2026-09-01)

ADOC-on completed the figure workload in 3,685 s (61.4 min), including a
3,664.586 s `fillrandom` phase. The timed command exited successfully after its
final `waitforcompaction` reported `finished`. A separate reopen validation
then waited for 79.15 s while RocksDB performed additional compaction and found
10,000 of 10,000 sampled keys. The Figure 4 TSV and bar use the timed-command
boundary, consistently with the existing absolute bars; the extra reopen work
is retained as a boundary caveat rather than silently added to the result.

The version-matched ADOC-off control also completed successfully: 3,354 s
(55.9 min) total and 3,333.142 s for `fillrandom`. Its separate reopen
validation took 8.69 s, observed no residual compaction, and found 10,000 of
10,000 sampled keys. Thus the single-run ADOC-on/off normalized loading time is
1.0987 (3,685 / 3,354), or a 0.9102x speedup: ADOC-on was 9.9% slower in this
run. These numbers are not a repetition-based claim.

Both ADOC runs use the release-built author artifact (RocksDB 7.7.0, commit
`5ed60f50d6cd8259b94e7f842ff06c6ab4df40a1`, binary SHA-256
`68eb52fb2c6db555f47fa877c015015a98ee7ecc879fe3574004c9ec47f47a05`) with a
vector memtable. The historical 4,889 s plain-RocksDB bar uses RocksDB 11.1.0
and its default skip-list memtable. Consequently, the 61.4 min ADOC absolute
bar is suitable only as the requested provisional Figure 4 point; attribution
of an ADOC effect must use the version-matched ADOC-off control above.

Raw evidence is under
`experiments/artifacts/log_loads/paper_adoc_1tb_260901_run1/adoc_on/` and
`experiments/artifacts/log_loads/paper_adoc_1tb_260901_run1_off/adoc_off/`.
The paired machine-readable summary is
`experiments/results/paper_adoc_1tb_single.tsv`.

## Corrected ADOC core-48 rerun (2026-09-02)

The ADOC-on Figure 4 point above has been superseded by a corrected run that
sets both `--max_background_jobs=48` and ADOC's independent
`--core_num=48` limit. The earlier run omitted `--core_num`, leaving the
artifact default of 20 in effect; it remains preserved as historical evidence
under `paper_adoc_1tb_260901_run1` but is no longer the plotted ADOC point.

The corrected run completed in 3,840 s (64.0 min), including a 3,798.908 s
`fillrandom` phase. It reached the runner's settled boundary with zero pending
compaction bytes, and the reopen validation found 10,000 of 10,000 sampled
keys. The run used the vector memtable, direct reads, direct flush/compaction
I/O, one foreground write thread, one subcompaction per job, and enabled both
FEA and TEA. Against the 3,472 s vector-direct clean baseline, the measured
ADOC time is 1.106x, or 10.6% longer. Against the 3,354 s version-matched
ADOC-off control, it is 1.145x, or 14.5% longer. These are single-run results.

The promoted summaries and Figure 4 source now point to
`experiments/artifacts/log_loads/paper_adoc_core48_1000gib_260902_run1/adoc_on/`.

This corrected run is the fixed Figure 4 ADOC point. Its artifact defaults of
two write buffers and 64/128 GiB soft/hard pending-compaction limits are part
of the fixed configuration. Later thread, memtable, write-buffer, and pending-
limit diagnostics do not replace it; see
`PAPER_FIGURE4_FIXED_CONFIGURATION.md`.

## ADOC core-cap diagnostic (2026-09-02)

The corrected ADOC run spent 3,755 of 3,798 one-second tuning samples
(98.87%) at `max_background_jobs=48`, which is also its configured
`--core_num=48` ceiling. To test whether this ceiling suppresses ADOC's
thread-allocation policy, run one diagnostic 1,000 GiB ADOC-on load with the
same release binary, direct-I/O policy, vector memtable, workload, initial
`--max_background_jobs=48`, and all other options unchanged, but set
`--core_num=96`. The host still has only 48 online CPUs, so 96 denotes a
background-job ceiling rather than 96 available CPU cores.

Use `SYSTEM_ORDER=adoc_on` and preserve the database and raw evidence under
`paper_adoc_core96_1000gib_260902_smoke1`. During the run, compare the
one-second `report.rep` records against the core-48 run over matched elapsed
time prefixes. Record whether the tuner raises `max_background_jobs` above 48,
the time and maximum of any increase, and the cumulative and windowed
foreground throughput. Treat this as a diagnostic, not a promoted Figure 4
replacement: a resource-fair headline comparison would also require a clean
RocksDB control allowed the corresponding background-job ceiling.
