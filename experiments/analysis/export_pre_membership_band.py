#!/usr/bin/env python3
"""Export the historical initial and figure-selected PLR cohorts, unchanged."""
import csv
import hashlib
import json
from pathlib import Path
import statistics

EXP = Path(__file__).resolve().parents[1]
RESULTS = EXP / 'results'
OUT = RESULTS / '20260914-071449_pre_membership_band_export'
BASE = {'b01', 'b02', 'b03', 'n01', 'n02', 'n03', 'n04', 'r01', 'r02', 'run3'}
INITIAL = {f'f{i:02d}' for i in range(1, 11)}
MATCHED = {'f01', 'f02', 'f03', 'f04', 'f05', 'f09', 'f11', 'f12', 'f13', 'f15'}
METRICS = ['throughput_ops_sec', 'filter_checks_per_lookup',
           'positive_lookup_pct', 'compaction_write_per_op']


def write_tsv(path, rows):
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)


def main():
    OUT.mkdir(exist_ok=True)
    source = RESULTS / 'paper_ch3_band25_raw.tsv'
    all_rows = list(csv.DictReader(source.open(), delimiter='\t'))
    manifest = {'purpose': 'Historical re-export; no new measurements.',
                'engine': 'RocksDB', 'input_gib': 1000, 'key_bytes': 24,
                'value_bytes': 1000, 'threads': 48, 'cache_gib': 50,
                'workload_seconds': 300, 'sources': {}}

    def record(path):
        manifest['sources'][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()

    record(source)
    for cohort, keep in [('initial_10', INITIAL), ('figure_selected_10', MATCHED)]:
        rows = [r for r in all_rows if (r['system'] == 'baseline' and r['arm'] in BASE)
                or (r['system'] == 'f2load' and r['arm'] in keep)]
        assert len(rows) == 120
        assert len({(r['system'], r['arm'], r['workload']) for r in rows}) == 120
        for system, arms in [('baseline', BASE), ('f2load', keep)]:
            for arm in arms:
                prefix = 'ycsb_band_' if system == 'baseline' else 'ycsb_f2band_'
                paths = list(RESULTS.glob(prefix + arm + '_*/results.json'))
                assert len(paths) == 1, (arm, paths)
                original = [x for x in json.loads(paths[0].read_text())
                            if x['phase'] == 'full' and x['status'] == 'ok'
                            and x['system'] == system]
                assert len(original) == 6
                for x in original:
                    w = x['workload'][-1].upper()
                    row = next(r for r in rows if r['system'] == system
                               and r['arm'] == arm and r['workload'] == w)
                    assert float(row['throughput_ops_sec']) == round(x['throughput_ops_sec'], 1)
                    assert int(row['operations']) == x['operations']
                record(paths[0])
                for name in ['manifest.json', 'provenance/sources.json']:
                    p = paths[0].parent / name
                    if p.exists():
                        record(p)
        write_tsv(OUT / f'{cohort}_raw.tsv', rows)
        summary = []
        for w in 'ABCDEF':
            for metric in METRICS:
                values = {s: [float(r[metric]) for r in rows if r['system'] == s
                              and r['workload'] == w and r[metric]]
                          for s in ['baseline', 'f2load']}
                if not values['baseline']:
                    continue
                b, f = values['baseline'], values['f2load']
                assert len(b) == len(f) == 10
                summary.append(dict(workload=w, metric=metric,
                    baseline_n=10, baseline_mean=statistics.mean(b),
                    baseline_min=min(b), baseline_max=max(b),
                    f2load_n=10, f2load_mean=statistics.mean(f),
                    f2load_min=min(f), f2load_max=max(f),
                    mean_difference_pct=100*(statistics.mean(f)/statistics.mean(b)-1)))
        write_tsv(OUT / f'{cohort}_summary.tsv', summary)
        print(cohort)
        for r in summary:
            if r['metric'] == 'throughput_ops_sec':
                print(r['workload'], r['baseline_mean'], r['f2load_mean'],
                      round(r['mean_difference_pct'], 3))
        manifest[cohort] = {'baseline_arms': sorted(BASE), 'f2load_arms': sorted(keep),
                            'rows': len(rows), 'exclusions_within_selected_arms': []}
    (OUT / 'source_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
