#!/usr/bin/env python3
"""The data behind the run-to-run band figure, as TSV.

One row per loading and workload holds the measured values; a second file gives
the per-workload band. Both apply the figure's exclusions: workload E issues no
point lookups, so its filter and found-key columns are blank, and workloads D
and E trigger almost no compaction inside a 300 s window, so their per-operation
compaction is a ratio of near-zero quantities and is left blank as well.
"""
import csv
import glob
import json
from pathlib import Path
import statistics as st

EXP = Path(__file__).resolve().parents[1]
RESULTS = EXP / 'results'
RAW = RESULTS / 'paper_ch3_band_raw.tsv'
SUMMARY = RESULTS / 'paper_ch3_band_summary.tsv'
LETTERS = list('ABCDEF')
NO_LOOKUP = {'E'}
NO_COMPACTION = {'C', 'D', 'E'}
METRICS = ['throughput_ops_sec', 'filter_checks_per_lookup',
           'found_keys_per_lookup', 'compaction_read_per_op_bytes',
           'compaction_write_per_op_bytes']


def rows():
    out = []
    for d in sorted(glob.glob(str(RESULTS / 'ycsb_band_*'))):
        arm = Path(d).name.replace('ycsb_band_', '').replace('_260910', '')
        p = Path(d) / 'results.json'
        if not p.exists():
            continue
        for r in json.load(open(p)):
            if (r['phase'] != 'full' or r.get('status') != 'ok'
                    or r['system'] != 'baseline'):
                continue
            w, ops, t = r['workload'][-1].upper(), r['operations'], r['tickers']
            out.append(dict(
                arm=arm, workload=w, operations=ops,
                throughput_ops_sec=round(r['throughput_ops_sec'], 1),
                filter_checks_per_lookup=(None if w in NO_LOOKUP else
                                          round(r['filter_cache_accesses'] / ops, 4)),
                found_keys_per_lookup=(None if w in NO_LOOKUP else
                                       round(r['successful_gets'] / r['engine_keys_read'], 5)),
                compaction_read_per_op_bytes=(None if w in NO_COMPACTION else
                                              round(t['rocksdb.compact.read.bytes'] / ops, 1)),
                compaction_write_per_op_bytes=(None if w in NO_COMPACTION else
                                               round(t['rocksdb.compact.write.bytes'] / ops, 1))))
    return out


def main():
    data = rows()
    fields = ['arm', 'workload', 'operations'] + METRICS
    with open(RAW, 'w', newline='') as f:
        w = csv.DictWriter(f, fields, delimiter='\t')
        w.writeheader()
        w.writerows(sorted(data, key=lambda r: (r['workload'], r['arm'])))

    with open(SUMMARY, 'w', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        w.writerow(['workload', 'metric', 'n', 'mean', 'min', 'max', 'spread_pct'])
        for letter in LETTERS:
            for m in METRICS:
                v = [r[m] for r in data if r['workload'] == letter and r[m] is not None]
                if not v:
                    continue
                mean = st.mean(v)
                w.writerow([letter, m, len(v), round(mean, 4), round(min(v), 4),
                            round(max(v), 4), round(100 * (max(v) - min(v)) / mean, 2)])
    print('wrote {}\nwrote {}'.format(RAW, SUMMARY))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
