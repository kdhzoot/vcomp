#!/usr/bin/env python3
"""Derive the configuration-sweep comparison from the raw per-cell measurements.

Every configuration loads the same logical dataset and is then measured with the
same YCSB workloads, so each cell can be read against the reference
configuration P0. The ratio is only meaningful where P0 itself has something to
measure: workload E issues no point lookups, and C and E provoke no compaction,
so those cells are left blank rather than divided. Workload D is blank for
compaction as well; P0 writes 0.12 GB there, and dividing by it turns a rounding
difference into a several-hundred-percent change.
"""
import csv
from pathlib import Path

EXP = Path(__file__).resolve().parents[1]
RAW = EXP / 'results/paper_ch3_config_raw.tsv'
OUT = EXP / 'results/paper_ch3_config_effect.tsv'
REFERENCE = 'P0-r1'
# metric column -> workloads where P0 has a value worth dividing by
METRICS = [('ops_per_sec', 'Throughput (ops/s)', set('ABCDEF')),
           ('filter_checks_per_read', 'Filter checks per lookup', set('ABCDF')),
           ('positive_lookup_pct', 'Positive lookups (%)', set('ABCDF')),
           ('compaction_write_gb', 'Compaction write (GB)', set('ABF'))]


def main():
    rows = list(csv.DictReader(open(RAW), delimiter='\t'))
    cell = {(r['config'], r['workload']): r for r in rows}
    configs = sorted({r['config'] for r in rows}, key=lambda c: (c != REFERENCE, c))
    out = []
    for col, label, workloads in METRICS:
        for w in sorted(workloads):
            base = cell[(REFERENCE, w)][col]
            if not base or float(base) == 0:
                continue
            base = float(base)
            for c in configs:
                v = cell[(c, w)][col]
                if not v:
                    continue
                v = float(v)
                out.append(dict(metric=label, workload=w, config=c,
                                value=round(v, 4), p0_value=round(base, 4),
                                ratio_to_p0=round(v / base, 4),
                                pct_change=round(100 * (v / base - 1), 2)))
    fields = ['metric', 'workload', 'config', 'value', 'p0_value', 'ratio_to_p0', 'pct_change']
    with open(OUT, 'w', newline='') as f:
        w = csv.DictWriter(f, fields, delimiter='\t')
        w.writeheader(); w.writerows(out)
    print('wrote %s (%d rows)' % (OUT, len(out)))

    print('\n설정 변경 시 이동 폭 (P0 대비, %p)')
    print('%-26s%s' % ('metric', ''.join('%8s' % w for w in 'ABCDF')))
    for col, label, workloads in METRICS:
        line = '%-26s' % label
        for w in 'ABCDF':
            v = [r['pct_change'] for r in out
                 if r['metric'] == label and r['workload'] == w and r['config'] != REFERENCE]
            line += '%8s' % ('%.0f' % (max(v) - min(v)) if v else '-')
        print(line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
