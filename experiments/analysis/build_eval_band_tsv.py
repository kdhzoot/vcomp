#!/usr/bin/env python3
"""Section 5.2 data: ten baseline loadings against ten exact-membership loadings.

Three files. `paper_eval_band_loads.tsv` is one row per loading (time, SST
count, bytes, and for F2Load the key-id coverage). `paper_eval_band_raw.tsv` is
one row per loading and workload with the four behaviour metrics. 
`paper_eval_band_summary.tsv` is one row per workload and metric with each
system's mean, min, max and spread, the mean difference, and whether the
F2Load mean and range fall inside the baseline range.
"""
import csv
import glob
import json
from pathlib import Path
import re
import statistics as st

EXP = Path(__file__).resolve().parents[1]
RESULTS = EXP / 'results'
LOADS = EXP / 'artifacts/log_loads'
LETTERS = list('ABCDEF')
NO_LOOKUP = {'E'}
NO_COMPACTION = {'C', 'D', 'E'}
METRICS = ['throughput_ops_sec', 'filter_checks_per_lookup',
           'positive_lookup_pct', 'compaction_write_per_op_bytes']
# Baseline arms are keyed by final SST count in the physical-copy record.
BASE_ARMS = {13483: 'n01', 13480: 'n02', 13549: 'n03', 13509: 'n04', 13497: 'r01',
             13522: 'r02', 13461: 'run3', 13460: 'b01', 13496: 'b02', 13520: 'b03'}


def loads():
    out = {}
    for p in glob.glob(str(LOADS / '**/validated.json'), recursive=True):
        d = json.load(open(p))
        if d.get('dataset_gib') != 1000 or d.get('key_bytes') != 24:
            continue
        if d.get('system') == 'baseline' and d['final_sst_count'] in BASE_ARMS:
            arm = BASE_ARMS[d['final_sst_count']]
            out[('baseline', arm)] = dict(system='baseline', arm=arm, coverage_pct='',
                load_sec=round(d['elapsed_sec'], 1), ssts=d['final_sst_count'],
                sst_bytes=d['final_sst_bytes'])
        elif d.get('system') == 'f2load' and str(d.get('arm', '')).startswith('e'):
            arm = d['arm']
            bench = Path(d['log_dir']) / 'bench.out'
            if not bench.is_absolute():
                bench = EXP / bench
            m = re.search(r'covered \d+ \(([\d.]+)%\)', bench.read_text())
            out[('f2load', arm)] = dict(system='f2load', arm=arm,
                coverage_pct=float(m.group(1)) if m else '',
                load_sec=round(d['elapsed_sec'], 1), ssts=d['final_sst_count'],
                sst_bytes=d['final_sst_bytes'])
    return out


def cells():
    out = []
    for pattern, system in (('ycsb_band_*', 'baseline'), ('ycsb_f2band_e*', 'f2load')):
        for d in sorted(glob.glob(str(RESULTS / pattern))):
            p = Path(d) / 'results.json'
            arm = Path(d).name.split('_')[-2]
            if not p.exists() or (system == 'baseline' and arm == 'f2'):
                continue
            for r in json.load(open(p)):
                if r['phase'] != 'full' or r.get('status') != 'ok' or r['system'] != system:
                    continue
                w, ops = r['workload'][-1].upper(), r['operations']
                out.append(dict(
                    system=system, arm=arm, workload=w, operations=ops,
                    throughput_ops_sec=round(r['throughput_ops_sec'], 1),
                    filter_checks_per_lookup=(None if w in NO_LOOKUP else
                                              round(r['filter_cache_accesses'] / ops, 4)),
                    positive_lookup_pct=(None if w in NO_LOOKUP else
                                         round(100 * r['successful_gets'] / r['engine_keys_read'], 3)),
                    compaction_write_per_op_bytes=(None if w in NO_COMPACTION else
                                                   round(r['compaction_write_bytes'] / ops, 1))))
    return out


def main():
    ld, data = loads(), cells()
    with open(RESULTS / 'paper_eval_band_loads.tsv', 'w', newline='') as f:
        w = csv.DictWriter(f, ['system', 'arm', 'load_sec', 'ssts', 'sst_bytes', 'coverage_pct'],
                           delimiter='\t')
        w.writeheader()
        w.writerows(sorted(ld.values(), key=lambda r: (r['system'], r['arm'])))

    with open(RESULTS / 'paper_eval_band_raw.tsv', 'w', newline='') as f:
        w = csv.DictWriter(f, ['system', 'arm', 'workload', 'operations'] + METRICS, delimiter='\t')
        w.writeheader()
        w.writerows(sorted(data, key=lambda r: (r['workload'], r['system'], r['arm'])))

    with open(RESULTS / 'paper_eval_band_summary.tsv', 'w', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        w.writerow(['workload', 'metric',
                    'baseline_n', 'baseline_mean', 'baseline_min', 'baseline_max', 'baseline_spread_pct',
                    'f2load_n', 'f2load_mean', 'f2load_min', 'f2load_max', 'f2load_spread_pct',
                    'mean_diff_pct', 'f2load_mean_in_baseline_range', 'f2load_range_in_baseline_range'])
        for letter in LETTERS:
            for m in METRICS:
                b = [r[m] for r in data if r['workload'] == letter and r['system'] == 'baseline' and r[m] is not None]
                e = [r[m] for r in data if r['workload'] == letter and r['system'] == 'f2load' and r[m] is not None]
                if not b or not e:
                    continue
                bm, em = st.mean(b), st.mean(e)
                w.writerow([letter, m,
                            len(b), round(bm, 4), min(b), max(b), round(100 * (max(b) - min(b)) / bm, 2),
                            len(e), round(em, 4), min(e), max(e), round(100 * (max(e) - min(e)) / em, 2),
                            round(100 * (em - bm) / bm, 3),
                            'yes' if min(b) <= em <= max(b) else 'no',
                            'yes' if min(b) <= min(e) and max(e) <= max(b) else 'no'])
    n = {s: len({r['arm'] for r in data if r['system'] == s}) for s in ('baseline', 'f2load')}
    print('arms', n, '| loads recorded', {s: sum(1 for k in ld if k[0] == s) for s in ('baseline', 'f2load')})
    for name in ('paper_eval_band_loads.tsv', 'paper_eval_band_raw.tsv', 'paper_eval_band_summary.tsv'):
        print('wrote', RESULTS / name)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
