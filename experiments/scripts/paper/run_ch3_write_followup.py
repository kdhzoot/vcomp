#!/usr/bin/env python3
"""Measure the compaction a subsequent write workload triggers on each loaded state.

Section 3 argues that the final LSM-Tree state determines subsequent workload
behavior.  The read side is already covered by the uniform-read matrix; this
script covers the write side.  For every alternative state it stages a
hard-linked copy of the loaded DB, runs a fixed-duration overwrite against that
copy, and records the compaction work the writes provoke.

The source DBs are never opened by db_bench.  stage_db() hard-links the
immutable SSTs and copies the mutable metadata, so compaction inside the copy
allocates new files and drops only its own links.
"""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lib'))
from ch23_common import (BASE, CLEAN, GIB, active_benchmarks, bench_stats, command,
    db_identity, levels, measure, require, save_json, stage_db, ticker)

# last_comp is excluded: its state is one compacted level produced by a manual
# compaction, and Section 3 discusses it in a single sentence rather than as a
# measured alternative.
STATES = ('baseline', 'flush_only', 'fillseq', 'fillseq_ow')
KV = 1024
TICKERS = ('rocksdb.compact.read.bytes', 'rocksdb.compact.write.bytes',
           'rocksdb.flush.write.bytes', 'rocksdb.number.keys.written',
           'rocksdb.stall.micros', 'rocksdb.compaction.times.micros')


def write_options(db, log, num, duration):
    # BASE re-enables auto compaction, so flush_only and last_comp states face
    # the triggers a real workload would face rather than the ones used to load
    # them.  That is the point of the measurement.
    return dict(BASE, num=num, key_size=24, value_size=1000, db=str(db),
                report_file=str(log / 'report.rep'), use_existing_db=True,
                benchmarks='overwrite,stats,levelstats', writes=num,
                duration=duration, ops_between_duration_checks=1,
                seed=87654321, stats_level=3, open_files=-1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source-root', required=True,
                    help='directory holding the loaded DBs (read-only here)')
    ap.add_argument('--stage-root', required=True,
                    help='directory for the hard-linked copies that get written')
    ap.add_argument('--out', required=True, help='result directory')
    ap.add_argument('--gib', type=int, default=1000)
    ap.add_argument('--duration', type=int, default=300)
    ap.add_argument('--states', default=','.join(STATES))
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    source, stage, out = (Path(args.source_root), Path(args.stage_root), Path(args.out))
    require(source.is_dir(), 'missing source root: ' + str(source))
    require(not stage.exists() or not any(stage.iterdir()), 'stage root must be empty')
    require(stage.resolve() != source.resolve(), 'stage root must differ from source root')
    require(source.resolve() not in stage.resolve().parents,
            'stage root must not sit inside the source root')
    require(not active_benchmarks(), 'another storage benchmark is active')
    num = args.gib * GIB // KV
    states = [s.strip() for s in args.states.split(',') if s.strip()]

    plan = []
    for name in states:
        src = source / (name + '_1kb')
        require(src.is_dir(), 'missing source DB: ' + str(src))
        plan.append((name, src, stage / (name + '_1kb'), out / name))
    print('num keys per state: {}'.format(num))
    for name, src, dst, log in plan:
        print('  {:<11} {} -> {}'.format(name, src, dst))
    if args.dry_run:
        print('dry run: nothing staged or executed')
        return 0

    out.mkdir(parents=True, exist_ok=True)
    results = []
    for name, src, dst, log in plan:
        print('=== {} ==='.format(name), flush=True)
        identity = db_identity(src)
        staged = None
        try:
            staged = time.time()
            before = stage_db(src, dst)
            require(before == identity, 'staged copy does not match the source')
            opts = write_options(dst, log, num, args.duration)
            require(Path(opts['db']).resolve() != src.resolve(), 'refusing to write the source DB')
            phase, text = measure(command(CLEAN, opts), log, lambda *a: None)
            stats = bench_stats(text, 'overwrite')
            row = dict(state=name, gib=args.gib, duration_sec=args.duration,
                       staged_sec=round(time.time() - staged, 1),
                       levels=levels(text), **stats)
            for t in TICKERS:
                try:
                    row[t.replace('rocksdb.', '').replace('.', '_')] = ticker(text, t)
                except Exception:
                    row[t.replace('rocksdb.', '').replace('.', '_')] = None
            written = row['number_keys_written'] or stats['operations']
            cw = row['compact_write_bytes'] or 0
            row['logical_write_bytes'] = written * KV
            row['followup_waf'] = round(cw / (written * KV), 3) if written else None
            results.append(row)
            save_json(out / 'write_followup.json', results)
            print(json.dumps(row, indent=2, sort_keys=True), flush=True)
        finally:
            # The source must be untouched whether or not the run succeeded.
            require(db_identity(src) == identity, 'SOURCE DB CHANGED: ' + str(src))
    save_json(out / 'write_followup.json', results)
    print('wrote ' + str(out / 'write_followup.json'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
