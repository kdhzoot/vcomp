#!/usr/bin/env python3
"""15쌍 baseline vs F2Load (YCSB C uniform, 딥카피) — ch32 막대 스타일, 지표당 1장."""
import json, re, glob
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator, FuncFormatter

U = 'experiments/artifacts/fix_20260914_ycsbc_uniform_deep/'
OUT = 'experiments/results/paper_ch32/'
# (baseline cell, f2load cell, x label, 축 색)
C = dict(base='#0072B2', kv='#CC79A7', ds='#E69F00', uq='#56B4E9', lv='#009E73', cp='#F9F4B3', sst='#D55E00')
CFG = [('P0-r1', 'P0fix-r1', 'P0', C['base']),
       ('E1-100B', 'E1-100B', 'KV 100 B', C['kv']), ('E1-10KB', 'E1-10KB', 'KV 10 KB', C['kv']),
       ('E2-2T', 'E2-2T', '2 TB', C['ds']), ('E2-4T', 'E2-4T', '4 TB', C['ds']), ('E2-8T', 'E2-8T', '8 TB', C['ds']),
       ('E3-u25', 'E3-u25', 'Unique 25%', C['uq']), ('E3-u50', 'E3-u50', 'Unique 50%', C['uq']),
       ('E3-u75', 'E3-u75', 'Unique 75%', C['uq']), ('E3-u100', 'E3-u100', 'Unique 100%', C['uq']),
       ('E7-lz4', 'E7-lz4', 'LZ4', C['cp']),
       ('E4-256m4', 'E4-256m4', 'Level 256M x4', C['lv']), ('E4-1024m10', 'E4-1024m10', 'Level 1G x10', C['lv']),
       ('E5-sst16', 'E5-sst16', 'SST 16 MB', C['sst']), ('E5-sst256', 'E5-sst256', 'SST 256 MB', C['sst'])]
METRICS = [('fpr', 'Filter checks / lookup', 'dec', 'filter_checks'),
           ('ops', 'Throughput (ops/s)', 'big', 'throughput'),
           ('found', 'Positive lookup (%)', 'pct', 'positive_lookup'),
           ('ssts', 'SST count', 'big', 'sst_count')]

fam = 'Cambria' if any('cambria' in f.name.lower() for f in font_manager.fontManager.ttflist) else 'DejaVu Serif'
plt.rcParams.update({'font.family': fam})

def cell(name):
    r = json.load(open(U + name + '__workloadc/result.json'))
    return dict(ops=r['ops_per_sec'], fpr=r['filter_probes_per_read'], found=r['positive_lookup_pct'], ssts=r['source_identity_ssts'])
def fmt(kind):
    def big(v, _=None):
        if v >= 1e6: return f'{v/1e6:.2f}'.rstrip('0').rstrip('.') + 'M'
        if v >= 1e3: return f'{v/1e3:.0f}K'
        return f'{v:.0f}'
    return {'big': big, 'dec': lambda v, _=None: f'{v:.1f}', 'pct': lambda v, _=None: f'{v:.0f}'}[kind]

data = [(cell(b + '_baseline'), cell(f + '_f2load')) for b, f, _, _ in CFG]
for key, ylabel, kind, fname in METRICS:
    bv = [d[0][key] for d in data]; fv = [d[1][key] for d in data]
    fig = plt.figure(figsize=(11.0, 3.96), dpi=200)
    ax = fig.add_axes([0.14, 0.38, 0.84, 0.57])
    x = list(range(len(CFG))); w = 0.38
    ax.bar([i - w / 2 for i in x], bv, w, color=[c for *_, c in CFG], edgecolor='black', linewidth=1.0, zorder=3)
    ax.bar([i + w / 2 for i in x], fv, w, color=[c for *_, c in CFG], edgecolor='black', linewidth=1.0, hatch='////', zorder=3)
    ax.axhline(bv[0], color='#FF0000', linewidth=1.75, zorder=4)
    f = fmt(kind); top = max(max(bv), max(fv)); pad = top * 0.02
    for i in x:
        ax.text(i - w / 2, bv[i] + pad, f(bv[i]), ha='center', va='bottom', fontsize=9, color='black', zorder=5, rotation=90)
        ax.text(i + w / 2, fv[i] + pad, f(fv[i]), ha='center', va='bottom', fontsize=9, color='black', zorder=5, rotation=90)
    ax.set_xticks(x); ax.set_xticklabels([l for _, _, l, _ in CFG], fontsize=14, rotation=45, ha='right', rotation_mode='anchor')
    ax.set_ylabel(ylabel, fontsize=22); ax.set_xlabel('')
    ax.set_ylim(0, top * 1.32)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=(kind != 'dec')))
    ax.yaxis.set_major_formatter(FuncFormatter(f))
    ax.tick_params(axis='y', labelsize=18); ax.tick_params(axis='x', length=0)
    ax.set_axisbelow(True); ax.grid(axis='y', color='#D9D9D9', linewidth=0.75); ax.grid(axis='x', visible=False)
    for s in ('top', 'right'): ax.spines[s].set_visible(False)
    for s in ('left', 'bottom'): ax.spines[s].set_color('black'); ax.spines[s].set_linewidth(1.0)
    ax.legend(handles=[Patch(facecolor='white', edgecolor='black', label='Baseline'),
                       Patch(facecolor='white', edgecolor='black', hatch='////', label='F2Load')],
              fontsize=12, frameon=False, loc='upper left', ncol=2)
    extra = [ax.yaxis.label] + ax.get_xticklabels()
    for ext in ('png', 'pdf'):
        fig.savefig(OUT + f'ch32_pairs_{fname}_uniform.{ext}', dpi=200, transparent=True, bbox_inches='tight', bbox_extra_artists=extra, pad_inches=0.05)
    plt.close(fig); print('saved', fname)
print('font:', fam)
