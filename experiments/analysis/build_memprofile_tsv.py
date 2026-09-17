#!/usr/bin/env python3
"""Section 5.5 tables from the F2Load memory-profile campaign.

Writes three TSVs into experiments/results/:
  memprofile_peak.tsv       one row per arm: peak RSS over its own baseline
  memprofile_breakdown.tsv  what that peak is made of, in bytes and percent
  memprofile_series_<arm>.tsv  the 100 ms RSS trace, for the curve panel
"""
import csv
import json
from pathlib import Path
import sys

EXP = Path(__file__).resolve().parents[1]
RUN = EXP / 'artifacts/log_loads' / (sys.argv[1] if len(sys.argv) > 1
                                     else 'f2load_memprofile_260914')
OUT = EXP / 'results'
GIB = 1024 ** 3

PARTS = [('descriptor_total', 'descriptors'),
         ('block_cache', 'block cache'),
         ('memtables', 'memtables'),
         ('other_live', 'other live'),
         ('overhead', 'allocator+process')]
DESC = [('plr_segment_bytes', 'PLR segments'),
        ('kmv_sample_bytes', 'KMV samples'),
        ('kmv_bucket_bytes', 'KMV bucket headers'),
        ('descriptor_object_bytes', 'descriptor objects'),
        ('registry_index_bytes', 'registry index')]


def write(name, header, rows):
    path = OUT / name
    with open(path, 'w', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        w.writerow(header)
        w.writerows(rows)
    print('  %-34s %d rows' % (name, len(rows)))


def main():
    records = json.loads((RUN / 'results.json').read_text())
    peak, breakdown = [], []
    for r in records:
        base = r['baseline_rss']
        over = r['overhead_hwm']
        peak.append([r['arm'], r['dataset_gib'], r['kv_bytes'], r['records'],
                     r['peak_files'], r['elapsed_sec'],
                     round(base / GIB, 4), round(r['peak_rss_hwm'] / GIB, 4),
                     round(r['peak_rss_sampled'] / GIB, 4), round(over / GIB, 4),
                     round(r['sampling_miss'] / GIB, 4),
                     round(over / r['records'], 4), r['peak_phase']])
        # The sampled peak is where the composition is known; state both so the
        # gap to VmHWM is visible rather than hidden.
        total = r['peak_rss_sampled'] - base
        for key, label in PARTS:
            v = r['peak_' + key] if key != 'descriptor_total' else r['peak_descriptor_total']
            breakdown.append([r['arm'], r['dataset_gib'], r['kv_bytes'], 'rss', label,
                              v, round(v / GIB, 4),
                              round(100.0 * v / max(total, 1), 2)])
        for key, label in DESC:
            v = r['peak_' + key]
            breakdown.append([r['arm'], r['dataset_gib'], r['kv_bytes'], 'descriptor',
                              label, v, round(v / GIB, 4),
                              round(100.0 * v / max(r['peak_descriptor_total'], 1), 2)])

    OUT.mkdir(parents=True, exist_ok=True)
    write('memprofile_peak.tsv',
          ['arm', 'dataset_gib', 'kv_bytes', 'records', 'peak_files', 'elapsed_sec',
           'baseline_rss_gib', 'peak_rss_hwm_gib', 'peak_rss_sampled_gib',
           'overhead_gib', 'sampling_miss_gib', 'overhead_bytes_per_record',
           'peak_phase'], peak)
    write('memprofile_breakdown.tsv',
          ['arm', 'dataset_gib', 'kv_bytes', 'scope', 'component', 'bytes', 'gib',
           'percent'], breakdown)

    for r in records:
        rows = []
        for line in Path(r['profile']).read_text().splitlines():
            if not line:
                continue
            s = json.loads(line)
            rows.append([round(s['t_ms'] / 1000.0, 2), s['phase'], s['tag'],
                         round((s['rss'] - s['base_rss']) / GIB, 4),
                         round(s['descriptor_total'] / GIB, 4),
                         round((s['block_cache'] + s['memtables']) / GIB, 4),
                         round(s['other_live'] / GIB, 4),
                         round(s['overhead'] / GIB, 4), s['files']])
        write('memprofile_series_%s.tsv' % r['arm'],
              ['t_sec', 'phase', 'tag', 'rss_over_baseline_gib', 'descriptors_gib',
               'cache_memtables_gib', 'other_live_gib', 'overhead_gib', 'files'], rows)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
