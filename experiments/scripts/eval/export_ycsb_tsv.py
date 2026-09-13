#!/usr/bin/env python3
"""YCSB 매트릭스를 셀 단위 TSV 와 설정 단위 요약 TSV 로 내보낸다."""
import json, re, glob
from pathlib import Path

R = Path('experiments/artifacts/eval_20260912_ycsb_af')
ORDER = ['P0-r1', 'E2-4T', 'E3-u25', 'E4-64m4', 'E5-256', 'E7-lz4']

def levels(p):
    t = Path(p).read_text(errors='replace')
    b = re.findall(r'Level Files Size\(MB\)\n-+\n((?:\s+\d+\s+\d+\s+\d+\n)+)', t)
    return [tuple(int(x) for x in ln.split()) for ln in b[-1].strip().split('\n')] if b else []

cells = {}
for f in sorted(R.glob('*/result.json')):
    r = json.loads(f.read_text())
    name = Path(r['source']).name.replace('_baseline', '')
    wl = r['workload'][-1].upper()
    text = (f.parent / 'stdout_stderr.log').read_text(errors='replace')
    m = re.search(r'reads (\d+) in (\d+) found', text)
    r['found'], r['queries'] = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    r['positive_lookup_pct'] = (100 * r['found'] / r['queries']
                                if r['queries'] else None)
    cells[(name, wl)] = r

COLS = [('ops_per_sec', lambda r: r['ops_per_sec']),
        ('micros_per_op', lambda r: r['micros_per_op']),
        ('p50_us', lambda r: r['p50_us']), ('p99_us', lambda r: r['p99_us']),
        ('p999_us', lambda r: r['p999_us']),
        ('filter_checks_per_read', lambda r: r.get('filter_probes_per_read')),
        ('positive_lookup_pct', lambda r: r['positive_lookup_pct']),
        ('queries', lambda r: r['queries']), ('found', lambda r: r['found']),
        ('keys_written', lambda r: r['keys_written']),
        ('compaction_write_gb', lambda r: r['compact_write_bytes'] / 1e9),
        ('compaction_read_gb', lambda r: r['compact_read_bytes'] / 1e9),
        ('flush_write_gb', lambda r: r['flush_write_bytes'] / 1e9),
        ('user_bytes_read_gb', lambda r: r['bytes_read'] / 1e9),
        ('sst_block_reads', lambda r: r['sst_read_count'])]

fmt = lambda v: '' if v is None else (f'{v:.4f}' if isinstance(v, float) else str(v))
with open(R / 'ycsb_cells.tsv', 'w') as f:
    f.write('config\tworkload\t' + '\t'.join(c for c, _ in COLS) + '\n')
    for name in ORDER:
        for wl in 'ABCDEF':
            r = cells.get((name, wl))
            if not r: continue
            f.write(f'{name}\t{wl}\t' + '\t'.join(fmt(fn(r)) for _, fn in COLS) + '\n')
print('saved', R / 'ycsb_cells.tsv', f'({sum(1 for k in cells)} cells)')

with open(R / 'config_summary.tsv', 'w') as f:
    head = ('config\tdb_tb\tsst_count\tavg_sst_mb\tlevels\tbottom_level\t'
            'filter_checks_C\tpositive_lookup_C\tcompaction_write_A_gb\t'
            'ops_per_sec_A\tops_per_sec_C\tp99_us_A\tp99_us_C\n')
    f.write(head)
    for name in ORDER:
        hits = [h for h in glob.glob(
            f'experiments/artifacts/eval_*/{name}_baseline/stdout_stderr.log')
            if 'ycsb' not in h]
        L = [x for x in (levels(hits[0]) if hits else []) if x[1]]
        files = sum(x[1] for x in L); mb = sum(x[2] for x in L)
        c, a = cells.get((name, 'C')), cells.get((name, 'A'))
        f.write('\t'.join([
            name, f'{mb/1048576:.3f}', str(files), f'{mb/files:.1f}', str(len(L)),
            f'L{L[-1][0]}:{L[-1][1]}f/{L[-1][2]//1024}GB',
            fmt(c.get('filter_probes_per_read')), fmt(c['positive_lookup_pct']),
            f"{a['compact_write_bytes']/1e9:.1f}",
            str(a['ops_per_sec']), str(c['ops_per_sec']),
            fmt(a['p99_us']), fmt(c['p99_us'])]) + '\n')
print('saved', R / 'config_summary.tsv')
