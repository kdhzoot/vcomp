#!/usr/bin/env python3
"""Per-arm and per-system TSVs for the mixgraph_pairs_260914 campaign.

Twenty single-cell campaigns, ten baseline loadings (deep-copied immediately
before measurement) and ten no-bitmap F2Load loadings, each measured with
mixgraph for 300 s followed by waitforcompaction so the compaction bytes are
post-drain.
"""
import csv
import json
import glob
from pathlib import Path
import statistics as st

EXP = Path(__file__).resolve().parents[2]
CHAIN = EXP / 'artifacts/log_loads/mixgraph_pairs_260914/chain_results.json'
OUT = EXP / 'results'
FIELDS = [
    'arm', 'system', 'run_id', 'prepare_kind', 'prepare_sec', 'source_sst_count',
    'status', 'measured_sec', 'drain_sec', 'process_elapsed_sec',
    'throughput_ops_sec', 'avg_latency_us', 'operations',
    'gets', 'puts', 'seeks', 'scan_entries_per_seek',
    'get_found_fraction', 'positive_lookup_pct',
    'filter_checks_per_lookup', 'bloom_filter_useful', 'bloom_filter_full_positive',
    'bloom_filter_full_true_positive', 'bloom_false_positive_rate',
    'bloom_filter_prefix_checked',
    'memtable_hits', 'l0_hits', 'l1_hits', 'l2_and_up_hits',
    'data_cache_hit_fraction', 'filter_cache_hit_fraction', 'index_cache_hit_fraction',
    'engine_keys_read', 'engine_keys_written', 'engine_iterator_bytes_read',
    'compaction_write_bytes', 'compaction_read_bytes', 'flush_write_bytes',
    'compaction_write_bytes_per_put', 'pending_bytes_end',
    'peak_rss_kb', 'stall_micros', 'source_db_dir', 'log_dir',
]


def load_sst_counts():
    """SST count of each arm's measured source: the copy for baseline arms, the
    fresh loading for F2Load arms."""
    counts = {}
    for path in (EXP / 'artifacts/log_loads/mixgraph_pairs_260914').glob('g*/validated.json'):
        record = json.loads(path.read_text())
        counts[record['arm']] = record['final_sst_count']
    base = {13483: 'n01', 13480: 'n02', 13549: 'n03', 13509: 'n04', 13497: 'r01',
            13522: 'r02', 13461: 'run3', 13460: 'b01', 13496: 'b02', 13520: 'b03'}
    for path in (EXP / 'artifacts/log_loads').rglob('validated.json'):
        record = json.loads(path.read_text())
        if (record.get('system') == 'baseline' and record.get('dataset_gib') == 1000
                and record.get('final_sst_count') in base):
            counts[base[record['final_sst_count']]] = record['final_sst_count']
    return counts


def rows():
    chain = {r['arm']: r for r in json.loads(CHAIN.read_text())}
    ssts = load_sst_counts()
    out = []
    for arm, entry in chain.items():
        path = glob.glob(str(EXP / 'artifacts/log_runs' / entry['run_id']
                             / 'full/mixgraph/*/validated.json'))[0]
        cell = json.loads(Path(path).read_text())
        ops = cell['workload_operation_counts']
        ticker = cell['tickers']
        # A lookup consults one filter per table it reaches: the filter either
        # rejects the key (useful) or lets the read through (full.positive).
        useful = ticker['rocksdb.bloom.filter.useful']
        positive = ticker['rocksdb.bloom.filter.full.positive']
        true_positive = ticker['rocksdb.bloom.filter.full.true.positive']
        checks = useful + positive
        negatives = useful + (positive - true_positive)
        out.append(dict(
            arm=arm, system=cell['system'], run_id=entry['run_id'],
            prepare_kind='deep_copy' if cell['system'] == 'baseline' else 'fresh_load',
            prepare_sec=round(entry['prepare_sec'], 1), source_sst_count=ssts.get(arm),
            status=cell['status'], measured_sec=cell['measured_seconds'],
            drain_sec=round(cell['process_elapsed_sec'] - cell['measured_seconds'], 2),
            process_elapsed_sec=round(cell['process_elapsed_sec'], 2),
            throughput_ops_sec=cell['throughput_ops_sec'],
            avg_latency_us=cell['avg_latency_us'], operations=cell['operations'],
            gets=ops['read'], puts=ops['update'], seeks=ops['scan'],
            scan_entries_per_seek=round(cell['engine_next_calls'] / cell['engine_seek_calls'], 3),
            get_found_fraction=round(cell['get_found_fraction'], 6),
            positive_lookup_pct=round(100 * cell['get_found_fraction'], 4),
            filter_checks_per_lookup=round(checks / cell['engine_keys_read'], 5),
            bloom_filter_useful=useful, bloom_filter_full_positive=positive,
            bloom_filter_full_true_positive=true_positive,
            bloom_false_positive_rate=round((positive - true_positive) / negatives, 8)
            if negatives else None,
            bloom_filter_prefix_checked=ticker['rocksdb.bloom.filter.prefix.checked'],
            memtable_hits=cell['memtable_hits'], l0_hits=cell['l0_hits'],
            l1_hits=cell['l1_hits'], l2_and_up_hits=cell['l2_and_up_hits'],
            index_cache_hit_fraction=round(cell['index_cache_hit_fraction'], 6),
            data_cache_hit_fraction=round(cell['data_cache_hit_fraction'], 6),
            filter_cache_hit_fraction=round(cell['filter_cache_hit_fraction'], 6),
            engine_keys_read=cell['engine_keys_read'],
            engine_keys_written=cell['engine_keys_written'],
            engine_iterator_bytes_read=cell['engine_iterator_bytes_read'],
            compaction_write_bytes=cell['compaction_write_bytes'],
            compaction_read_bytes=cell['compaction_read_bytes'],
            flush_write_bytes=cell['flush_write_bytes'],
            compaction_write_bytes_per_put=round(
                cell['compaction_write_bytes'] / cell['engine_keys_written'], 2),
            pending_bytes_end=cell['pending_bytes_end'], peak_rss_kb=cell['peak_rss_kb'],
            stall_micros=cell['stall_micros'], source_db_dir=cell['source_db_dir'],
            log_dir=cell['log_dir']))
    # Chain order, so the interleaving is visible in the file.
    order = {a: i for i, a in enumerate(chain)}
    return sorted(out, key=lambda r: order[r['arm']])


SUMMARY = ['throughput_ops_sec', 'avg_latency_us', 'get_found_fraction',
           'filter_checks_per_lookup', 'bloom_false_positive_rate',
           'bloom_filter_useful', 'bloom_filter_full_positive',
           'memtable_hits', 'l0_hits', 'l1_hits', 'l2_and_up_hits',
           'data_cache_hit_fraction', 'filter_cache_hit_fraction',
           'index_cache_hit_fraction', 'gets', 'puts', 'seeks',
           'engine_iterator_bytes_read', 'compaction_write_bytes',
           'compaction_read_bytes', 'flush_write_bytes',
           'compaction_write_bytes_per_put', 'drain_sec', 'prepare_sec']


def summary(data):
    out = []
    for metric in SUMMARY:
        row = dict(metric=metric)
        means = {}
        for system in ('baseline', 'f2load'):
            values = [r[metric] for r in data if r['system'] == system]
            means[system] = st.mean(values)
            row[system + '_n'] = len(values)
            row[system + '_mean'] = round(st.mean(values), 6)
            row[system + '_sd'] = round(st.stdev(values), 6)
            row[system + '_min'] = round(min(values), 6)
            row[system + '_max'] = round(max(values), 6)
        row['f2load_vs_baseline_pct'] = round(
            100 * (means['f2load'] - means['baseline']) / means['baseline'], 3)
        out.append(row)
    return out


def write(path, fieldnames, data):
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter='\t',
                                extrasaction='ignore')
        writer.writeheader()
        writer.writerows(data)
    print('wrote {} ({} rows)'.format(path, len(data)))


def main():
    OUT.mkdir(exist_ok=True)
    data = rows()
    write(OUT / 'mixgraph_pairs_260914.tsv', FIELDS, data)
    head = ['metric'] + [s + f for s in ('baseline_', 'f2load_')
                         for f in ('n', 'mean', 'sd', 'min', 'max')] \
           + ['f2load_vs_baseline_pct']
    write(OUT / 'mixgraph_pairs_260914_summary.tsv', head, summary(data))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
