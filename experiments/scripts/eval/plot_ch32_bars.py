#!/usr/bin/env python3
"""§3.2 막대그래프 (지표당 1장): Baseline / Record count / Key uniqueness / Level multiplier / Compression."""
import csv
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import MaxNLocator, FuncFormatter

TSV = 'experiments/results/paper_ch3_config_uniform_260914.tsv'
OUT = 'experiments/results/paper_ch32/'
ORDER = [('P0-r1', 'Baseline', '#0072B2'), ('E2-4T', 'Record count', '#E69F00'),
         ('E3-u25', 'Key uniqueness', '#56B4E9'), ('E4-256m4', 'Level multiplier', '#009E73'),
         ('E7-lz4', 'Compression', '#F9F4B3')]
METRICS = [('filter_checks_C', 'Filter checks / lookup', 'dec', 'filter_checks'),
           ('ops_per_sec_C', 'Throughput (ops/s)', 'big', 'throughput'),
           ('positive_lookup_C', 'Positive lookup (%)', 'pct', 'positive_lookup')]

fam = 'Cambria' if any('cambria' in f.name.lower() for f in font_manager.fontManager.ttflist) else 'DejaVu Serif'
plt.rcParams.update({'font.family': fam, 'mathtext.fontset': 'dejavuserif'})

def fmt(kind):
    def big2(v, _=None):
        if v >= 1e6:
            s = f'{v/1e6:.2f}'.rstrip('0').rstrip('.'); return s + 'M'
        if v >= 1e3: return f'{v/1e3:.0f}K'
        return f'{v:.0f}'
    return {'big': big2, 'dec': lambda v, _=None: f'{v:.1f}', 'pct': lambda v, _=None: f'{v:.0f}'}[kind]

rows = {r['config']: r for r in csv.DictReader(open(TSV), delimiter='\t')}
for col, ylabel, kind, fname in METRICS:
    vals = [float(rows[c][col]) for c, _, _ in ORDER]
    fig = plt.figure(figsize=(4.17, 3.96), dpi=200)
    ax = fig.add_axes([0.36, 0.38, 0.59, 0.57])   # 왼쪽 36%, 폭 59%, 위 5%, 높이 57%
    x = range(len(vals))
    ax.bar(x, vals, width=0.53, color=[c for _, _, c in ORDER], edgecolor='black', linewidth=1.0, zorder=3)
    ax.axhline(vals[0], color='#FF0000', linewidth=1.75, zorder=4)
    f = fmt(kind)
    pad = max(vals) * 0.02
    for xi, v in zip(x, vals):
        ax.text(xi, v + pad, f(v), ha='center', va='bottom', fontsize=13, color='black', zorder=5)
    ax.set_xticks(list(x)); ax.set_xticklabels([l for _, l, _ in ORDER], fontsize=14, rotation=45, ha='right', rotation_mode='anchor')
    ax.set_xlabel('')
    ax.set_ylabel(ylabel, fontsize=22)
    ax.set_ylim(0, max(vals) * 1.15)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=(kind != 'dec')))
    ax.yaxis.set_major_formatter(FuncFormatter(f))
    ax.tick_params(axis='y', labelsize=18); ax.tick_params(axis='x', length=0)
    ax.set_axisbelow(True); ax.grid(axis='y', color='#D9D9D9', linewidth=0.75); ax.grid(axis='x', visible=False)
    for s in ('top', 'right'): ax.spines[s].set_visible(False)
    for s in ('left', 'bottom'): ax.spines[s].set_color('black'); ax.spines[s].set_linewidth(1.0)
    extra = [ax.yaxis.label] + ax.get_xticklabels()
    fig.savefig(OUT + f'ch32_{fname}_uniform.png', dpi=200, transparent=True, bbox_inches='tight', bbox_extra_artists=extra, pad_inches=0.05)
    fig.savefig(OUT + f'ch32_{fname}_uniform.pdf', transparent=True, bbox_inches='tight', bbox_extra_artists=extra, pad_inches=0.05)
    plt.close(fig)
    print('saved', OUT + f'ch32_{fname}_uniform.png', '|', ', '.join(f(v) for v in vals))
print('font:', fam)
