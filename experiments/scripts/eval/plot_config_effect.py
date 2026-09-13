#!/usr/bin/env python3
"""P0 대비 축별 변화량. 축을 하나씩 바꿨을 때 세 지표가 얼마나 달라지는지 그린다."""
import json, re, sys
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

ROOT = Path('experiments/artifacts/eval_20260912_ycsb_af')
REF = 'P0-r1'
DBS = ['E2-4T', 'E3-u25', 'E4-64m4', 'E5-256', 'E7-lz4']
WL = list('ABCDEF')
SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4']
SURFACE, INK, INK2, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#e3e2df'

# 셀별 result.json 을 직접 모은다 (results.json 은 호출 단위로 덮어써짐)
rows = [json.loads(f.read_text()) for f in sorted(ROOT.glob('*/result.json'))]
cell = {}
for r in rows:
    name = Path(r['source']).name.replace('_baseline', '')
    letter = r['workload'][-1].upper()
    log = ROOT / f"{Path(r['source']).name}__{r['workload']}" / 'stdout_stderr.log'
    m = re.search(r'reads (\d+) in (\d+) found', log.read_text(errors='replace')) \
        if log.is_file() else None
    r['found'], r['queries'] = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    cell[(name, letter)] = r

METRICS = [
    ('Compaction write', 'A', 'GB written by compaction',
     lambda r: r['compact_write_bytes'] / 1e9),
    ('Filter checks per lookup', 'C', 'filter checks / read',
     lambda r: r.get('filter_probes_per_read')),
    ('Positive lookups', 'C', '% of queries that found a key',
     lambda r: 100 * r['found'] / r['queries'] if r.get('queries') else None),
]

def get(db, wl, fn):
    r = cell.get((db, wl))
    if not r: return None
    try: return fn(r)
    except Exception: return None

missing = [d for d in [REF] + DBS for w in WL if (d, w) not in cell]
if missing:
    sys.exit(f'아직 없는 셀: {sorted(set(missing))}')

fig, axes = plt.subplots(3, 1, figsize=(10.4, 9.2), sharex=True)
fig.patch.set_facecolor(SURFACE)
x = np.arange(len(WL)); w = 0.155
for ax, (title, _pref, unit, fn) in zip(axes, METRICS):
    ax.set_facecolor(SURFACE)
    ax.axhline(1.0, color=INK2, linewidth=1.4, zorder=4)
    for i, (db, color) in enumerate(zip(DBS, SERIES)):
        ratios = []
        for wl in WL:
            base, v = get(REF, wl, fn), get(db, wl, fn)
            # P0 기준값이 사실상 0 이면 비율이 의미 없다 (읽기 전용 워크로드의 compaction)
            floor = 1.0 if title.startswith('Compaction') else 1e-9
            ratios.append(v / base if (base and v is not None and base > floor) else np.nan)
        ax.bar(x + (i - 2) * w, ratios, w * 0.86, label=db, color=color,
               edgecolor=SURFACE, linewidth=1.2, zorder=3)
    base_txt = ', '.join(
        f'{wl}={get(REF, wl, fn):,.1f}' for wl in WL
        if get(REF, wl, fn) is not None and
        (get(REF, wl, fn) > (1.0 if title.startswith('Compaction') else 0)))
    ax.set_title(f'{title}   ·   P0 = {base_txt}', loc='left', fontsize=10.5,
                 color=INK, pad=8)
    ax.set_ylabel('relative to P0', fontsize=9, color=INK2)
    ax.grid(axis='y', color=GRID, linewidth=0.8, zorder=0); ax.set_axisbelow(True)
    for s2 in ('top', 'right'): ax.spines[s2].set_visible(False)
    for s2 in ('left', 'bottom'): ax.spines[s2].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9, length=0)

axes[-1].set_xticks(x)
axes[-1].set_xticklabels([f'YCSB {c}' for c in WL], fontsize=10.5, color=INK)
axes[0].legend(ncol=5, frameon=False, fontsize=9.5, loc='upper center',
               bbox_to_anchor=(0.5, 1.34), labelcolor=INK2)
fig.suptitle('One axis changed from P0: how far the state moves',
             x=0.012, ha='left', fontsize=13.5, color=INK, y=0.995)
fig.text(0.012, 0.955, 'baseline RocksDB · 1 TB logical input · YCSB A-F, 5 min per cell · '
         '50 GiB cache · horizontal line = P0 · blank = metric not exercised',
         fontsize=9.3, color=INK2, ha='left')
fig.tight_layout(rect=[0, 0, 1, 0.915])
out = ROOT / 'config_effect_vs_p0.png'
fig.savefig(out, dpi=170, facecolor=SURFACE)
print('saved', out)

tsv = ROOT / 'config_effect_vs_p0.tsv'
with open(tsv, 'w') as f:
    f.write('metric\tworkload\tconfig\tvalue\tp0_value\tratio_to_p0\tpct_change\n')
    for title, _, unit, fn in METRICS:
        for wl in WL:
            base = get(REF, wl, fn)
            for d in [REF] + DBS:
                v = get(d, wl, fn)
                if v is None or not base: continue   # P0 기준값 0 이면 비율 무의미
                f.write(f'{title}\t{wl}\t{d}\t{v:.4f}\t{base:.4f}\t'
                        f'{v/base:.4f}\t{(v/base-1)*100:+.2f}\n')
print('saved', tsv)
for title, wl, unit, fn in METRICS:
    base = get(REF, wl, fn)
    print(f'\n{title} (YCSB {wl}, P0={base:,.2f} {unit})')
    for d in DBS:
        v = get(d, wl, fn)
        print(f'  {d:10} {v:>12,.2f}  {(v/base-1)*100:+7.1f}%')
