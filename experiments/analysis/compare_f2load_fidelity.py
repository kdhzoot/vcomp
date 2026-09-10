#!/usr/bin/env python3
"""Every fidelity dimension of an F2Load-built state against its baseline.

Reads two run_ycsb_alternatives.py campaigns that each measured a baseline arm
and an F2Load arm with the workloads interleaved, and reports throughput,
latency, filter work, lookup membership, compaction volume and device I/O.

All counters are normalised per operation. The campaigns run for a fixed wall
time, so a faster arm performs more operations and every absolute counter
scales with it; only per-operation values are comparable.
"""
import argparse
import json
from pathlib import Path

EXP = Path(__file__).resolve().parents[1]
RUNS = EXP / 'artifacts/log_runs'
LETTERS = ['a', 'b', 'c', 'd', 'e', 'f']


def cells(run_id, system):
    path = RUNS / run_id / 'results.json'
    return {row['workload'][-1]: row
            for row in json.loads(path.read_text())
            if row.get('phase') == 'full' and row['system'] == system}


def ticker(row, name):
    return (row.get('tickers') or {}).get(name, 0)


def metrics(row):
    """Per-operation fidelity metrics for one cell."""
    ops = row['operations']
    useful = ticker(row, 'rocksdb.bloom.filter.useful')          # negative, filtered out
    positive = ticker(row, 'rocksdb.bloom.filter.full.positive')  # went on to read
    true_positive = ticker(row, 'rocksdb.bloom.filter.full.true.positive')
    checked = useful + positive
    out = {
        'throughput (ops/s)': row['throughput_ops_sec'],
        'avg latency (us)': row['avg_latency_us'],
        'filter checks / op': checked / ops,
        'filter positive / op': positive / ops,
        'filter positive share': (positive / checked) if checked else None,
        'bloom true positive share': (true_positive / positive) if positive else None,
        'found fraction': row.get('get_found_fraction'),
        'compaction read B / op': ticker(row, 'rocksdb.compact.read.bytes') / ops,
        'compaction write B / op': ticker(row, 'rocksdb.compact.write.bytes') / ops,
        'flush write B / op': row.get('flush_write_bytes', 0) / ops,
        'engine bytes read / op': row.get('engine_bytes_read', 0) / ops,
        'engine bytes written / op': row.get('engine_bytes_written', 0) / ops,
        'data cache miss / op': row['data_cache_miss'] / ops,
        'index cache miss / op': row['index_cache_miss'] / ops,
        'filter cache miss / op': row['filter_cache_miss'] / ops,
        # Latency of one direct pread as RocksDB saw it. Both arms issue the
        # same number of preads per operation, so this isolates the storage
        # device's service time from every logical property of the tree.
        'device read p50 (us)': ((row.get('engine_histograms') or {})
                                 .get('rocksdb.sst.read.micros') or {}).get('p50'),
    }
    return out


def show(title, base, f2):
    print('=== ' + title)
    print('   %-28s %14s %14s %9s' % ('metric', 'baseline', 'F2Load', 'F2/base'))
    for key in metrics(base):
        bv = metrics(base)[key]
        fv = metrics(f2)[key]
        if bv is None or fv is None:
            continue
        ratio = (fv / bv) if bv else float('nan')
        cell = (lambda v: '{:,.0f}'.format(v)) if abs(bv) >= 1000 \
            else (lambda v: '{:.4f}'.format(v))
        print('   %-28s %14s %14s %8.3fx'
              % (key, cell(bv), cell(fv), ratio))
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--compare-run-id',
                        help='a second campaign, reported as F2 before/after')
    parser.add_argument('--workloads', default='abcdef')
    args = parser.parse_args()

    base = cells(args.run_id, 'baseline')
    f2 = cells(args.run_id, 'f2load')
    old = cells(args.compare_run_id, 'f2load') if args.compare_run_id else None

    for letter in args.workloads:
        if letter not in base or letter not in f2:
            continue
        show('workload ' + letter.upper(), base[letter], f2[letter])

    if old is None:
        return
    print('=== F2Load / baseline ratio, before and after the dedup fix')
    print('   %-28s %s' % ('metric', ''.join('%12s' % w.upper()
                                             for w in args.workloads)))
    keys = [k for k in metrics(f2['a']) if metrics(base['a']).get(k) is not None]
    for key in keys:
        before, after = [], []
        for letter in args.workloads:
            if letter not in base or letter not in old:
                before.append(None), after.append(None)
                continue
            bv = metrics(base[letter])[key]
            before.append(metrics(old[letter])[key] / bv if bv else None)
            after.append(metrics(f2[letter])[key] / bv if bv else None)
        print('   %-28s' % (key + ' [before]')
              + ''.join('%12s' % ('-' if v is None else '%.3f' % v) for v in before))
        print('   %-28s' % (key + ' [after]')
              + ''.join('%12s' % ('-' if v is None else '%.3f' % v) for v in after))


if __name__ == '__main__':
    main()
