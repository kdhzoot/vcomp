#!/usr/bin/env python3
"""Baseline, F2Load without the bitmap, and F2Load with the bitmap, on YCSB A-F
and MixGraph.

YCSB rows come from the band campaigns already exported in
`paper_ch3_band_em_raw.tsv`; MixGraph rows come from the 2026-09-14 pairs chain
(baseline and bitmap-free arms) and the 2026-09-15 bitmap chain. Every cell is a
single 300 s measurement on one loaded database. MixGraph cells additionally ran
waitforcompaction, so their compaction bytes are post-drain; the YCSB cells are
not drained, so compare compaction write only within a workload.
"""
import csv
import statistics as st
from pathlib import Path

EXP = Path(__file__).resolve().parents[2]
OUT = EXP / 'results'
YCSB = OUT / 'paper_ch3_band_em_raw.tsv'
MIX = OUT / 'mixgraph_three_way_260915.tsv'
BASE, NOBM, BM = 'baseline', 'F2Load PLR', 'F2Load exact membership'
SYSTEMS = (BASE, NOBM, BM)
MIX_LABEL = {'baseline': BASE, 'f2load_nobitmap': NOBM, 'f2load_bitmap': BM}
WORKLOADS = ('A', 'B', 'C', 'D', 'E', 'F', 'mixgraph')
METRICS = ('throughput_ops_sec', 'filter_checks_per_lookup', 'positive_lookup_pct',
           'compaction_write_per_op')
FIELDS = ('system', 'bitmap', 'workload', 'arm', 'operations') + METRICS


def num(x):
    return float(x) if x not in (None, '', 'NA') else None


def rows():
    out = []
    for r in csv.DictReader(YCSB.open(), delimiter='\t'):
        out.append(dict(system=r['system'], workload=r['workload'], arm=r['arm'],
                        operations=num(r['operations']),
                        throughput_ops_sec=num(r['throughput_ops_sec']),
                        filter_checks_per_lookup=num(r['filter_checks_per_lookup']),
                        positive_lookup_pct=num(r['positive_lookup_pct']),
                        compaction_write_per_op=num(r['compaction_write_per_op'])))
    for r in csv.DictReader(MIX.open(), delimiter='\t'):
        ops = num(r['operations'])
        out.append(dict(system=MIX_LABEL[r['system']], workload='mixgraph', arm=r['arm'],
                        operations=ops,
                        throughput_ops_sec=num(r['throughput_ops_sec']),
                        filter_checks_per_lookup=num(r['filter_checks_per_lookup']),
                        positive_lookup_pct=num(r['positive_lookup_pct']),
                        compaction_write_per_op=round(
                            num(r['compaction_write_bytes']) / ops, 4)))
    for r in out:
        r['bitmap'] = {BASE: 'n/a', NOBM: 'off', BM: 'on'}[r['system']]
    order = {s: i for i, s in enumerate(SYSTEMS)}
    wl = {w: i for i, w in enumerate(WORKLOADS)}
    return sorted(out, key=lambda r: (wl[r['workload']], order[r['system']], r['arm']))


def summary(data):
    out = []
    for workload in WORKLOADS:
        for metric in METRICS:
            row = dict(workload=workload, metric=metric)
            means = {}
            for system in SYSTEMS:
                v = [r[metric] for r in data
                     if r['workload'] == workload and r['system'] == system
                     and r[metric] is not None]
                key = {BASE: 'baseline', NOBM: 'nobitmap', BM: 'bitmap'}[system]
                row[key + '_n'] = len(v)
                means[key] = st.mean(v) if v else None
                row[key + '_mean'] = round(st.mean(v), 4) if v else None
                row[key + '_sd'] = round(st.stdev(v), 4) if len(v) > 1 else None
            for key in ('nobitmap', 'bitmap'):
                if means.get(key) is not None and means.get('baseline'):
                    row[key + '_vs_baseline_pct'] = round(
                        100 * (means[key] - means['baseline']) / means['baseline'], 3)
                else:
                    row[key + '_vs_baseline_pct'] = None
            out.append(row)
    return out


def write(path, fieldnames, data):
    with path.open('w', newline='') as h:
        w = csv.DictWriter(h, fieldnames=fieldnames, delimiter='\t', extrasaction='ignore')
        w.writeheader()
        w.writerows(data)
    print('wrote {} ({} rows)'.format(path, len(data)))


def main():
    data = rows()
    write(OUT / 'workload_three_way_260915.tsv', FIELDS, data)
    head = (['workload', 'metric']
            + [k + s for k in ('baseline', 'nobitmap', 'bitmap') for s in ('_n', '_mean', '_sd')]
            + ['nobitmap_vs_baseline_pct', 'bitmap_vs_baseline_pct'])
    write(OUT / 'workload_three_way_260915_summary.tsv', head, summary(data))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
