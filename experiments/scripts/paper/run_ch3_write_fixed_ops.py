#!/usr/bin/env python3
"""Separate compaction work a write workload does from work it defers.

The 300 s YCSB cells show fillseq paying 45% less compaction write per
operation than incremental construction on workload A, but they cannot say
whether that state needs less compaction or has merely not done it yet. Two
things confound the fixed-duration comparison: the faster state issues more
operations inside the window, and the window ends wherever it ends, leaving an
unequal backlog. Workload A finished with 1.62x the pending compaction bytes on
fillseq and workload B with 0.07x, so the mixture differs per workload.

This runner fixes both. Every state runs the same YCSB workload for the same
operation count instead of the same wall time, and the tree is drained with
waitforcompaction afterwards. Statistics are dumped on both sides of the drain,
so the result carries work done during the window and work owed at its end as
separate numbers; their sum is what the state actually costs for that write
volume.

Options otherwise match run_ycsb_alternatives.py exactly, so these cells sit
beside the campaign's.
"""
import argparse
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lib'))
from ch23_common import (BASE, GIB, HASHES, active_benchmarks, bench_stats,
    command, db_identity, levels, measure, require, save_json, sha, stage_db)

# The profiler build the YCSB campaign froze into its own directory. These cells
# have to run the same executable to sit beside that campaign's, so the hash is
# checked against the campaign's record and then registered for measure(), which
# only knows the two binaries the frozen table pins.
PROFILER_SHA = '20d67c38612cee9a34d9ef7114c81bc8366b2d4cf99c598b642e63f21ab74266'

PROFILER = Path(__file__).resolve().parents[2] / 'artifacts/log_runs'
SOURCE_BUNDLE = (Path(__file__).resolve().parents[2]
                 / 'results/paper_ch23_common_260907_f2_completion1')
# Flush-only is absent by construction: it stops writes at DB open, so it has
# no write workload to measure.
STATES = ('baseline', 'fillseq')
TICKERS = ('rocksdb.compact.read.bytes', 'rocksdb.compact.write.bytes',
           'rocksdb.flush.write.bytes', 'rocksdb.number.keys.written',
           'rocksdb.number.keys.read', 'rocksdb.stall.micros',
           'rocksdb.compaction.key.drop.obsolete')


def options(source, db, log, workload, reads_per_thread, cache_size,
            write_only=False):
    # duration=0 makes Duration terminate on the operation count instead of
    # wall time; reads_ is per thread, so the total is reads * threads.
    return dict(BASE, num=source['num_keys'], key_size=source['key_bytes'],
                value_size=source['value_bytes'], db=str(db),
                benchmarks=workload + ',stats,waitforcompaction,stats,levelstats',
                use_existing_db=True, readonly=False,
                disable_auto_compactions=False, memtablerep='skip_list',
                threads=48, duration=0, reads=reads_per_thread,
                seed=87654321, ops_between_duration_checks=1,
                cache_type='lru_cache', cache_size=cache_size,
                cache_index_and_filter_blocks=True,
                pin_l0_filter_and_index_blocks_in_cache=False,
                pin_top_level_index_and_filter=False,
                open_files=-1, stats_level=3, histogram=True, perf_level=3,
                report_interval_seconds=1, report_file=str(log / 'report.rep'),
                stats_interval_seconds=30, stats_per_interval=1,
                ycsb_requestdistribution='latest' if workload == 'workloadd'
                else 'zipfian', ycsb_minscanlength=1, ycsb_maxscanlength=100,
                ycsb_scanlengthdistribution='uniform',
                # A write-only point is the same workload with its read share
                # moved to updates, so the key generator and its zipfian
                # distribution stay exactly as in the mixed runs. db_bench's
                # own overwrite benchmark would draw uniformly instead, which
                # would change which keys are touched and so what compaction
                # has to merge.
                **(dict(ycsb_readproportion=0.0, ycsb_updateproportion=1.0,
                        ycsb_insertproportion=0.0, ycsb_scanproportion=0.0,
                        ycsb_readmodifywriteproportion=0.0)
                   if write_only else {}))


def counters(text):
    """Read the tickers from the run's statistics block.

    db_bench emits one STATISTICS block per process, after the last benchmark,
    whatever the position of `stats` in the benchmark list. The block therefore
    covers the workload and the waitforcompaction drain together, which is the
    number this comparison wants: what the write volume costs in total, not what
    happened to fit inside a window.
    """
    out = {}
    for name in TICKERS:
        values = re.findall(r'^' + re.escape(name) + r' COUNT\s*:\s*(\d+)',
                            text, re.M)
        require(values, 'statistics missing for ' + name)
        out[name.replace('rocksdb.', '').replace('.', '_')] = int(values[-1])
    return out


def pending_bytes(text):
    values = re.findall(r'Estimated pending compaction bytes:\s*(\d+)', text)
    return int(values[-1]) if values else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--binary', required=True,
                    help='profiler db_bench, the build the YCSB campaign used')
    ap.add_argument('--source-root', required=True)
    ap.add_argument('--stage-root', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--workload', default='workloada')
    ap.add_argument('--operations', type=int, default=210_000_000,
                    help='total operations, split evenly across 48 threads')
    ap.add_argument('--cache-size', type=int, default=50 * GIB)
    ap.add_argument('--states', default=','.join(STATES))
    ap.add_argument('--write-only', action='store_true',
                    help='move the read share of the workload to updates')
    ap.add_argument('--keep-stage', action='store_true',
                    help='leave the staged copy in place for inspection')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    binary = Path(args.binary)
    source, stage, out = Path(args.source_root), Path(args.stage_root), Path(args.out)
    require(binary.is_file(), 'missing binary: ' + str(binary))
    require(sha(binary) == PROFILER_SHA,
            'binary is not the profiler build the campaign used')
    HASHES[str(binary)] = PROFILER_SHA
    require(source.is_dir(), 'missing source root: ' + str(source))
    require(stage.resolve() != source.resolve(), 'stage root must differ from source root')
    require(source.resolve() not in stage.resolve().parents,
            'stage root must not sit inside the source root')
    require(not active_benchmarks(), 'another storage benchmark is active')
    per_thread = args.operations // 48
    loads = json.loads((SOURCE_BUNDLE / 'loads.json').read_text())
    states = [s.strip() for s in args.states.split(',') if s.strip()]

    print('{} x {} operations ({} per thread across 48)'.format(
        args.workload, format(per_thread * 48, ','), format(per_thread, ',')))
    plan = []
    for name in states:
        src = loads[name + '_1kb']
        plan.append((name, Path(src['db_dir']), stage / name, out / name, src))
        print('  {:<10} {} -> {}'.format(name, src['db_dir'], stage / name))
    if args.dry_run:
        print('dry run: nothing staged or executed')
        return 0

    out.mkdir(parents=True, exist_ok=True)
    results = []
    for name, src, dst, log, source_row in plan:
        print('=== {} ==='.format(name), flush=True)
        identity = db_identity(src)
        try:
            started = time.time()
            before = stage_db(src, dst)
            require(before == identity, 'staged copy does not match the source')
            opts = options(source_row, dst, log, args.workload, per_thread,
                           args.cache_size, args.write_only)
            require(Path(opts['db']).resolve() != src.resolve(),
                    'refusing to write the source DB')
            phase, text = measure(command(binary, opts), log, lambda *a: None)
            total = counters(text)
            row = dict(state=name, workload=args.workload,
                       write_only=args.write_only,
                       operations_requested=per_thread * 48,
                       wall_sec=round(time.time() - started, 1),
                       elapsed_sec=phase['elapsed_sec'],
                       levels=levels(text),
                       pending_bytes_end=pending_bytes(text),
                       total=total, **bench_stats(text, args.workload))
            written = total['number_keys_written']
            if written:
                row['compaction_write_per_key'] = round(
                    total['compact_write_bytes'] / written, 1)
                row['compaction_read_per_key'] = round(
                    total['compact_read_bytes'] / written, 1)
                row['write_amplification'] = round(
                    total['compact_write_bytes'] / total['flush_write_bytes'], 2)
            results.append(row)
            save_json(out / 'write_fixed_ops.json', results)
            print(json.dumps({k: v for k, v in row.items() if k != 'levels'},
                             indent=2, sort_keys=True), flush=True)
        finally:
            require(db_identity(src) == identity, 'SOURCE DB CHANGED: ' + str(src))
    save_json(out / 'write_fixed_ops.json', results)
    print('wrote ' + str(out / 'write_fixed_ops.json'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
