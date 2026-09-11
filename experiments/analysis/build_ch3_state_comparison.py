#!/usr/bin/env python3
"""Collect the three numbers Section 3.1 compares across loading alternatives.

Loading time comes from each state's own loading record, throughput and filter
checks from the YCSB C cells measured with a 50 GiB block cache. C is the only
one of A to D that issues no writes, so it is the only workload flush-only can
serve: its A, B and D cells stop at DB open and end at the watchdog.

The baseline arm is not the one loaded alongside flush-only and fillseq. Its
YCSB cells come from n01, the independently loaded database whose A to D
throughput sits closest to the median of ten such loadings, within 0.42 percent.
Its loading record is read from that same loading, so the row stays internally
consistent.
"""
import csv
import json
from pathlib import Path

EXP = Path(__file__).resolve().parents[1]
LOADS = EXP / 'artifacts/log_loads'
RESULTS = EXP / 'results'
GiB = 1024 ** 3
OUT = RESULTS / 'paper_ch3_state_comparison.tsv'

# state -> (label, loading record, YCSB results file)
STATES = [
    ('baseline', 'Incremental construction',
     LOADS / 'baseline_coverage_260908_night_n01/full/repeat_01/baseline_1kb/validated.json',
     RESULTS / 'ycsb_band_n01_260910/results.json'),
    ('fillseq', 'Fillseq',
     LOADS / 'paper_ch23_common_260905_approved_run3/full/fillseq_1kb/validated.json',
     RESULTS / 'ch3_ycsb_cache50_260911_run2/results.json'),
    ('flush_only', 'Flush-only',
     LOADS / 'paper_ch23_common_260905_approved_run3/full/flush_only_1kb/validated.json',
     RESULTS / 'ch3_ycsb_cache50_260911_run2/results.json'),
]
FIELDS = ['state', 'label', 'loading_min', 'loading_speedup_vs_baseline',
          'ycsb_c_throughput_ops_sec', 'ycsb_c_throughput_vs_baseline',
          'filter_checks_per_lookup', 'filter_positives_per_lookup',
          'filter_true_positives_per_lookup', 'filter_false_positive_pct',
          'ycsb_c_avg_latency_us',
          'ycsb_c_successful_lookup_pct', 'ycsb_c_operations',
          'ycsb_abd_status', 'final_sst_count', 'final_sst_gib',
          'loading_waf', 'source_db_dir', 'ycsb_run']


def cell(path, system, workload):
    for r in json.load(open(path)):
        if r['phase'] == 'full' and r['system'] == system and r['workload'] == workload:
            return r
    raise SystemExit('missing cell: {} {} in {}'.format(system, workload, path))


def main():
    rows = []
    for state, label, load_path, ycsb_path in STATES:
        load = json.load(open(load_path))
        c = cell(ycsb_path, state, 'workloadc')
        abd = [cell(ycsb_path, state, 'workload' + w)['status'] for w in 'abd']
        rows.append(dict(
            state=state, label=label,
            loading_min=round(load['loading_min'], 2),
            ycsb_c_throughput_ops_sec=round(c['throughput_ops_sec']),
            filter_checks_per_lookup=round(
                c['filter_cache_accesses'] / c['operations'], 2),
            # Of the SSTs a lookup consults, how many report the key as present
            # and how many of those actually hold it. The gap is the wasted
            # data-block read a false positive costs.
            filter_positives_per_lookup=round(
                c['tickers']['rocksdb.bloom.filter.full.positive']
                / c['operations'], 2),
            filter_true_positives_per_lookup=round(
                c['tickers']['rocksdb.bloom.filter.full.true.positive']
                / c['operations'], 2),
            filter_false_positive_pct=round(100 * (
                1 - c['tickers']['rocksdb.bloom.filter.full.true.positive']
                / c['tickers']['rocksdb.bloom.filter.full.positive']), 2)
                if c['tickers']['rocksdb.bloom.filter.full.positive'] else None,
            ycsb_c_avg_latency_us=round(c['avg_latency_us'], 1),
            ycsb_c_successful_lookup_pct=round(
                100 * c['successful_gets'] / c['engine_keys_read'], 2),
            ycsb_c_operations=c['operations'],
            ycsb_abd_status=','.join(abd),
            final_sst_count=load['final_sst_count'],
            final_sst_gib=round(load['final_sst_bytes'] / GiB, 1),
            loading_waf=round(load['waf'], 2),
            source_db_dir=load['db_dir'],
            ycsb_run=ycsb_path.parent.name))
    base = rows[0]
    for r in rows:
        r['loading_speedup_vs_baseline'] = round(
            base['loading_min'] / r['loading_min'], 2)
        r['ycsb_c_throughput_vs_baseline'] = round(
            r['ycsb_c_throughput_ops_sec'] / base['ycsb_c_throughput_ops_sec'], 4)
    with open(OUT, 'w', newline='') as f:
        w = csv.DictWriter(f, FIELDS, delimiter='\t')
        w.writeheader()
        w.writerows(rows)
    print('wrote ' + str(OUT))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
