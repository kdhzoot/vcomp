# F2Load completion of the common Chapter 2/3 campaign

Authorized by the author on 2026-09-07: run the deferred F2Load experiment
and update its figure and prose results. The earlier seven loads and twenty
reads remain the shared controls. Size scaling and instrumentation breakdown
are still excluded.

## Problem and measurement boundary

The previous F2Load invocation exited normally and materialized real SSTs,
but reported 17,558,185,945 estimated pending compaction bytes. It did not
pass the zero-pending endpoint used by the conventional baseline. The source
updates the VersionSet directly after materialization and resumes background
work; `WaitForCompact` waits for scheduled/queued work, not for the estimated
pending-byte property itself to reach zero. The missing rescheduling after
materialization is a source-level explanation to be checked experimentally,
not an excuse to waive the completion gate.

Use the frozen F2Load executable for a **fresh** loading run. Preserve its
output DB by making a checkpoint (hard-linked immutable SSTs, copied mutable
metadata), then reopen that checkpoint with the frozen clean baseline binary,
automatic compaction enabled, and run `waitforcompaction,stats,levelstats`.
This is ordinary background settling, not a forced full-range compaction.
Require zero final pending bytes, no new key writes, successful clean read-only
reopening, and an unchanged pre-settle source DB. If the gate still fails,
preserve the evidence and diagnose it before any promotion.

The reported F2Load loading time is the **sum of both measured process wall
times**, including the clean reopen and any actual compaction. Both phases'
device I/O is counted. Record the initial materialization time, initial pending
bytes, settling wall time and compaction bytes separately. Checkpoint creation
and between-phase cache reset are instrumentation outside these timings, as
with the existing Last-comp checkpoint protocol. Do not present the first
phase alone as the settled loading result or describe physical settling as
metadata-only work. The exact binary files and source repositories remain
unchanged.

## Frozen conditions

All original options come from `experiments/lib/ch23_common.py`:

- Logical input: 1,000 GiB; 24-byte keys + 1,000-byte values;
  1,048,576,000 generated inputs; seed 12345678, uniform with replacement.
- F2Load SHA-256:
  `c5964e54fb0afc4779fd17b82f7366bdd8d19be9bd2acae33dd26c77e55019cd`.
  Source revision: `f9e281caa65943140385aa99fff1066da4117be8`;
  its recorded worktree status/diff and exact executable hash identify the build.
- Clean completion and reader SHA-256:
  `8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b`.
  Clean source revision: `f455ab7bd6a8c67f00d48075bb310f131d9fae5f`.
- 64 MiB write buffers, maximum 16; Vector; 64 MiB SST target; leveled
  compaction, static 256 MiB level base and multiplier 10; 48 background jobs,
  one subcompaction; no WAL/compression; direct I/O; statistics enabled.
- F2Load retains its qualified SST format 6 in both load phases; the existing
  conventional alternatives use format 7. This method-specific format
  exception is recorded, not described as an identical loader build.
- F2Load: PLR error 8, flush size 64 MiB, registration cap 256, visible-L0
  batch 0, phase-1 shards 8, materialization workers 48; detailed timers off.
- Four reads use the exact previous `readrandom` settings: uniform requests,
  seed 87654321, 48 threads, 300 seconds, direct/read-only, automatic
  compaction off, unused `put` merge operator to select the common generic
  reader, no warm-up, cache reset before every measured invocation.
- Read cells: metadata cached/pinned x LRU 1 byte/50 GiB, in A/B/C/D order.
  All four readers use the same final DB as the reported F2Load loading time.
- One full repetition. The delayed F2Load measurements occur after the
  controls rather than in the original interleaved order; report this limit.
- Membership is measured, not required to match baseline: the prototype
  reconstructs approximate key identities. Clean reopen validates usability;
  it is not proof of exact baseline state or key-set equivalence.

## Pilot, execution, and outputs

Runner: `scripts/paper/run_paper_ch23_f2_completion.py`.
Run ID: `paper_ch23_common_260907_f2_completion1`.

First run a 1-GiB pilot through both load phases, clean reopening, and all
four 30-second read cells. Require unchanged runner/library/binary hashes
before the full 1,000-GiB run. Then reuse the seven validated loads and twenty
reads from `paper_ch23_common_260905_approved_run3/full`, validating their
source identities. Run only the new F2Load load and four reads. Store raw
outputs and commands in the new run's pilot/full artifact directories, and
DBs under `/work/vcomp/exp/<run-id>/`. No old DB or measurement is deleted.

At preflight the host had no active db_bench and approximately 12 TiB free.
Require at least 4 TiB before the new full run and maintain the existing
2-TiB free-space safety margin. Serialize all storage measurements, check
swap and interference, and keep the previous failed F2Load evidence.

Only after all eight combined load states and twenty-four reads validate,
produce a new result bundle and update Figure 4, Figure 5 and dependent
numeric prose. Keep Figure 2's shared baseline values unchanged, retain
historical scaling/breakdown separately, preserve the author's current
manuscript edits and old prose as comments, compile, and visually inspect.

## Observed loading and pilot results (2026-09-07 UTC)

The 1-GiB pilot completed both load phases, clean reopening and four 30-second
reads at 02:52:17 UTC. The full load then completed and validated at 02:54:40 UTC:

| Component | Process wall time | Endpoint |
| --- | ---: | --- |
| Frozen F2Load loading/materialization | 96.957026 s | 17,255,557,663 estimated pending bytes |
| Clean automatic completion | 37.773671 s | Zero estimated pending bytes; no inserted keys |
| Reported total | 134.730697 s | Clean read-only reopening passed |

The completion phase performed 61 physical compaction jobs, reading
21.642739 GiB and writing 19.553249 GiB of SST data. Total device writes across
both phases were 850,250,850,304 bytes (0.791858 times logical input), versus
the existing conventional baseline's device WAF of 13.50. This is a 94.1353%
reduction in device writes and a 25.9883x loading-time ratio. SST ticker bytes
alone are not a valid F2Load total because materialization bypasses those tickers.

The preserved materialized DB is `/work/vcomp/exp/paper_ch23_common_260907_f2_completion1/full/f2load_1kb_materialized`.
The completed DB used by all four new reads is the sibling `f2load_1kb` directory.
All exact argv arrays, resource logs and storage counters are under
`artifacts/log_loads/paper_ch23_common_260907_f2_completion1/full/f2load_1kb/phase{1,2}/raw/`;
the corresponding read arrays are under
`artifacts/log_runs/paper_ch23_common_260907_f2_completion1/full/<config>/f2load/raw/`.

The executed controller command was:

```sh
python3 -u experiments/scripts/paper/run_paper_ch23_f2_completion.py \
  --run-id paper_ch23_common_260907_f2_completion1 --phase all
```

## Final completion and paper promotion

All four full 300-second reads passed at 03:14:57 UTC. The combined eight-load/24-read matrix passed raw-counter, source-DB, command and binary validation. The promotion command, from the vcomp repository, was:

```sh
python3 experiments/analysis/promote_paper_ch23_common.py --run-root experiments/artifacts/log_loads/paper_ch23_common_260907_f2_completion1/full --f2-completion-update
```

F2Load throughput was 77,550 / 829,354 / 711,376 / 901,866 ops/s in A/B/C/D order, or 1.695 / 1.095 / 1.038 / 1.091 times the same-configuration baseline. Pinned-metadata/50-GiB successful lookups were 61.8088%, versus 63.2119% for baseline, a -1.4031 percentage-point difference. The manuscript reports this key-membership limitation.

Figure 2 source data remained unchanged. Figures 4/5 and dependent numerical prose were replaced, prior prose was preserved as comments, and the latest author edits and review markers were retained. The PDF compiled without unresolved references or errors; pages 1, 4, 6, 7 and 8 were visually inspected. A subsequent automatic rebuild produced identical rendered text from unchanged sources. See the result bundle and `FINAL_AUDIT.json`. Existing bibliography/template warnings remain. Scaling and instrumentation follow-ups remain deferred.
