# Same-input baseline loading repeats for L1 coverage

User request (2026-09-08): repeat the existing baseline `fillrandom` load twice
under identical conditions to check whether its high L1 coverage is an unusual
final layout. Do not modify any existing DB or paper content.

## Frozen configuration and provenance

- Reference: `results/paper_ch23_common_260905_approved_run3/loads.json`,
  `baseline_1kb`, including its archived exact phase1 command.
- Logical input 1000 GiB (the existing "1TB" convention); 1,048,576,000 writes;
  24-byte key plus 1000-byte value. `fillrandom` samples with replacement.
- Clean Release `rocksdb-f455-release/db_bench`, source commit
  `f455ab7bd6a8c67f00d48075bb310f131d9fae5f`, SHA-256
  `8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b`.
  Reuse the qualified binary on the same host; no rebuilding.
- Same seed 12345678, foreground threads 1, vector memtable 64 MiB, max buffers
  **16**, batch 1, background jobs 48, subcompactions 1. The older unrelated
  max-buffer-2 experiment is not the source of this comparison.
- Bloom 10, no compression, SST format 7, target SST 64 MiB, base level 256 MiB,
  seven levels, static level sizes, multiplier 10, compaction priority 3,
  L0 thresholds 4/20/36, pending-compaction limits 64/128 GiB, WAL off,
  direct reads and direct flush/compaction I/O, auto compaction enabled.
- Exact existing endpoint:
  `fillrandom,flush,compact0,waitforcompaction,stats,levelstats`.
  No additional full/manual compaction and no online YCSB experiment.
- Reset page cache using the existing helper before each measurement. One
  storage benchmark at a time. Identical seeds test run/scheduling variability,
  not sensitivity across independent input datasets.

## Execution, validation, and retention

Runner: `scripts/load/run_baseline_coverage_repeats.py` reuses the original
`Campaign.load_case` and shared measurement/validation helpers. Compare every
full load argument to the archived command, allowing only DB/report paths to
change. The pilot additionally changes only dataset count.

First dry-run command parity checks, then one 1-GiB pipeline pilot, then two
1000-GiB loads sequentially. Each load must complete exactly N writes, drain
compactions with zero estimated pending bytes, match persisted OPTIONS, pass
Release/error/swap/interference checks, and reopen successfully for known-key
reads (1000 pilot / 10000 full). Reopen validation uses a disposable hardlink
clone, never the preserved completed DB itself.

After each load, dump coverage on another read-only clone with auto compaction
disabled using the already qualified profiler binary (SHA-256
`20d67c38612cee9a34d9ef7114c81bc8366b2d4cf99c598b642e63f21ab74266`).
That binary is used only for metadata inspection, **not loading**. Acquire the
new DB's POSIX LOCK while staging and inspecting; verify original and clone
identities afterward. Remove only validated disposable verification/coverage
clones. Retain the pilot and both full loaded DBs for later inspection.

Primary metrics: elapsed loading time, L1 file count/size/ranges, and L1 range
union divided by the shared complete integer key domain. Distinguish this from
the old level-local-envelope coverage denominator. Also retain all final level
counts/sizes, WAF, phase counters, exact commands, source/binary hashes, persisted
OPTIONS, identities, and resource logs. No figure or manuscript change yet.

Initial free-space requirement 5 TiB; stop below the inherited 2-TiB safety
margin. Expected retained full DB size approximately 1.5 TiB total plus
temporary compaction space. Current host had approximately 11 TiB free.
Any validation failure stops the queue and preserves failure evidence; do not
overwrite or automatically retry an existing run directory.

## Time estimate and paths

Reference elapsed time was 3501.417 seconds (58m21s), so two loads project to
7002.834 seconds (1h56m43s). Allow roughly 2h-2h15m including pilot, validation,
and metadata inspection; storage and compaction variability may extend this.

```bash
python3 experiments/scripts/load/run_baseline_coverage_repeats.py --run-id baseline_coverage_260908_repeat1 --dry-run
python3 experiments/scripts/load/run_baseline_coverage_repeats.py --run-id baseline_coverage_260908_repeat1
```

DBs: `/work/vcomp/exp/baseline_coverage_260908_repeat1/{pilot,full}/`;
full repetitions live under `repeat_01/baseline_1kb` and
`repeat_02/baseline_1kb`. Existing baseline/F2Load DBs are not opened or changed.
Logs/status/manifest/results:
`artifacts/log_loads/baseline_coverage_260908_repeat1/`.
Verification evidence: `artifacts/log_runs/baseline_coverage_260908_repeat1/`.
Promote validated summaries and selected provenance to `results/` after review;
raw data, DBs, and executables stay outside Git. No commit/push in this task.

## Launch record

On 2026-09-08, syntax checking and archived-command parity dry run passed.
The detached runner started at 09:45:53 UTC (PID 499698). The 1-GiB pilot
completed loading in 11.235 seconds, passed its 1000/1000 known-key read-only
reopen check, and successfully extracted coverage with unchanged DB identities.
Full `repeat_01` began at 09:46:07 UTC; `repeat_02` is queued after validation
and coverage of the first repeat. This is a launch record, not a completion
claim; consult the run's `status.json`, `results.json`, and console log for
current progress.
