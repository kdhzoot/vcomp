# SST survival in the preserved 8 TB, 91 B loading

The completed first loading wrote 2,960,290 SSTs: 154,771 flush outputs and
2,805,519 compaction outputs. Final levelstats contains 106,160 SSTs.
The retained fraction is **3.586135142%, rounded to 3.59%**.
The logical input was 8,000 GiB, with 48-byte keys and 43-byte values.
This analysis reuses existing logs; no database or benchmark was run.

The numerator is the sum of final levelstats file counts. The denominator is
the sum of COUNT in `rocksdb.table.sync.micros` and
`rocksdb.compaction.outfile.sync.micros`. In the normal successful build paths,
these record one sync per completed flush or compaction output SST, respectively.
The implementation sites inspected were `rocksdb/db/builder.cc` (BuildTable)
and `rocksdb/db/compaction/compaction_outputs.cc` (WriterSyncClose).
The total also matches `rocksdb.no.file.opens` for this run; file-open counts
are only a consistency check, not a general SST-creation metric.

As an independent control, the same method returns 206,050 created SSTs and
13,461 final SSTs for the common 1 TB, 1 KB baseline, matching its published
`table_file_creation` event count and 6.532880369% retained fraction exactly.
Both logs have one completed loading, zero reported file errors, successful
waitforcompaction, and zero final estimated pending compaction bytes.

The 8 TB log SHA-256 matches the first-run preservation record:
`588e60488f7ece3d8f9c7e868d6b3dec1808fce567a618403d155b1bfa41e684`.
The later unintended repeat failed and overwrote the outer summary; it is
excluded. The first run had documented brief concurrent diagnostic activity,
and its device-write counters are not usable. The reported SST counts do not
estimate per-file lifetime or the share of loading wall time spent producing
intermediate SSTs.

`summary.tsv` contains the target and control measurements. `provenance.json`
contains source paths and hashes, the parser hash, the run's recorded build and
command configuration, and the original preservation notes.

Reproduce from the vcomp repository with a new output directory:

```bash
python3 experiments/analysis/summarize_sst_survival.py \
  --output-dir experiments/artifacts/analysis/sst_survival_recheck
```
