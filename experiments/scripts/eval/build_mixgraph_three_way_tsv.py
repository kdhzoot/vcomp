#!/usr/bin/env python3
"""Three-way mixgraph comparison: baseline, F2Load without and with the bitmap.

The 2026-09-14 chain measured ten baseline deep copies and ten bitmap-free
F2Load loadings; the 2026-09-15 chain added ten F2Load loadings that use the
exact-membership bitmap. Every cell is mixgraph for 300 s followed by
waitforcompaction, so the compaction bytes are read after the drain. The
baseline cells are shared by both comparisons and are not re-measured.
"""
import csv
import glob
import json
from pathlib import Path
import statistics as st
import sys

EXP = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(EXP / 'scripts/eval')]
import build_mixgraph_pairs_tsv as pairs  # noqa: E402

CHAINS = (
    (EXP / 'artifacts/log_loads/mixgraph_pairs_260914/chain_results.json', None),
    (EXP / 'artifacts/log_loads/mixgraph_bitmap_260915/chain_results.json', 'f2load_bitmap'),
)
SYSTEMS = ('baseline', 'f2load_nobitmap', 'f2load_bitmap')
OUT = EXP / 'results'


def cell_row(arm, system, entry, ssts):
    path = glob.glob(str(EXP / 'artifacts/log_runs' / entry['run_id']
                         / 'full/mixgraph/*/validated.json'))[0]
    cell = json.loads(Path(path).read_text())
    ops = cell['workload_operation_counts']
    ticker = cell['tickers']
    useful = ticker['rocksdb.bloom.filter.useful']
    positive = ticker['rocksdb.bloom.filter.full.positive']
    true_positive = ticker['rocksdb.bloom.filter.full.true.positive']
    checks = useful + positive
    negatives = useful + (positive - true_positive)
    prepare = entry.get('prepare_sec', entry.get('load_sec'))
    return dict(
        arm=arm, system=system, run_id=entry['run_id'],
        prepare_kind='deep_copy' if system == 'baseline' else 'fresh_load',
        prepare_sec=round(prepare, 1),
        source_sst_count=entry.get('final_sst_count') or ssts.get(arm),
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
        log_dir=cell['log_dir'])


def rows():
    ssts = pairs.load_sst_counts()
    out = []
    for path, forced in CHAINS:
        for entry in json.loads(path.read_text()):
            system = forced or ('baseline' if entry['system'] == 'baseline'
                                else 'f2load_nobitmap')
            out.append(cell_row(entry['arm'], system, entry, ssts))
    order = {s: i for i, s in enumerate(SYSTEMS)}
    return sorted(out, key=lambda r: (order[r['system']], r['arm']))


def summary(data):
    out = []
    for metric in pairs.SUMMARY:
        row, means = dict(metric=metric), {}
        for system in SYSTEMS:
            values = [r[metric] for r in data if r['system'] == system]
            means[system] = st.mean(values)
            row[system + '_n'] = len(values)
            row[system + '_mean'] = round(st.mean(values), 6)
            row[system + '_sd'] = round(st.stdev(values), 6)
        for system in ('f2load_nobitmap', 'f2load_bitmap'):
            row[system + '_vs_baseline_pct'] = round(
                100 * (means[system] - means['baseline']) / means['baseline'], 3)
        out.append(row)
    return out


def main():
    OUT.mkdir(exist_ok=True)
    data = rows()
    pairs.write(OUT / 'mixgraph_three_way_260915.tsv', pairs.FIELDS, data)
    head = (['metric']
            + [s + f for s in SYSTEMS for f in ('_n', '_mean', '_sd')]
            + [s + '_vs_baseline_pct' for s in ('f2load_nobitmap', 'f2load_bitmap')])
    pairs.write(OUT / 'mixgraph_three_way_260915_summary.tsv', head, summary(data))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
