#!/usr/bin/env python3
"""Export completed sparse YCSB A/C and load metrics without replacing old data."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from plot_ycsb_raw_metrics import DEFINITIONS, get_metrics


def write_tsv(path, rows):
    with path.open('x', newline='') as stream:
        writer = csv.DictWriter(stream, list(rows[0]), delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--load-completion', type=Path, required=True)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    complete = json.loads((bundle / 'COMPLETED.json').read_text())
    load = json.loads(args.load_completion.read_text())
    assert complete['status'] == load['status'] == 'ok'
    assert complete['sources_preserved'] and load['membership']['status'] == 'ok'
    assert load['measurement_run_id'] == bundle.name
    full = [r for r in json.loads((bundle / 'results.json').read_text())
            if r['phase'] == 'full']
    assert len(full) == 2 and {r['workload'] for r in full} == {'workloada', 'workloadc'}
    sources = {bundle / 'results.json', args.load_completion.resolve()}
    rows = []
    for r in sorted(full, key=lambda r: r['workload']):
        assert r['status'] == 'ok' and r['system'] == 'fillseq_sparse'
        assert r['duration_sec'] == 300 and not r['timed_out']
        m = get_metrics(r, 'md0', sources)
        rows.append(dict(system=r['system'], workload=r['workload'][-1].upper(),
                         operations=r['operations'], **m,
                         flush_write_bytes=r['flush_write_bytes'],
                         filter_cache_accesses_per_op=r['filter_cache_accesses'] / r['operations'],
                         compaction_write_per_op_bytes=r['compaction_write_bytes'] / r['operations'],
                         measured_seconds=r['measured_seconds'],
                         source_run=bundle.name, source_db_dir=r['source_db_dir']))
    sparse = load['sparse']
    loading = {key: sparse[key] for key in (
        'system', 'loading_min', 'elapsed_sec', 'extraction_sec', 'preparation_sec',
        'unique_count', 'num_keys', 'final_sst_bytes', 'final_sst_count', 'db_dir')}
    write_tsv(bundle / 'plot_metrics.tsv', rows)
    write_tsv(bundle / 'loading.tsv', [loading])
    notes = dict(definitions=DEFINITIONS, load_completion=str(args.load_completion.resolve()),
                 scope='Only full 300-second A/C cells; pilot rows excluded.',
                 comparison='Online 300-second compaction counters; not fixed-write-volume plus compaction drain.',
                 legacy_filter='Some chapter-3 tables label filter_cache_accesses/operations as filter_checks_per_lookup. This export records that separately as filter_cache_accesses_per_op. filter_checks_per_get uses the actual filter tickers divided by Get requests, matching the fidelity plots.',
                 loading='Insertion plus flush/compaction drain excludes key extraction; preparation_sec includes the original reused extraction time. Interrupted attempts are excluded.',
                 sources={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources)})
    with (bundle / 'tsv_provenance.json').open('x') as stream:
        json.dump(notes, stream, indent=2)
    print(bundle / 'plot_metrics.tsv')
    print(bundle / 'loading.tsv')


if __name__ == '__main__':
    main()
