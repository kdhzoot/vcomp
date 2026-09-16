#!/usr/bin/env python3
"""Paired mixgraph comparison, ten baseline against ten no-bitmap F2Load loadings."""
import csv
from pathlib import Path
import statistics as st
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D

fam = 'Cambria' if any('cambria' in f.name.lower() for f in font_manager.fontManager.ttflist) else 'DejaVu Serif'
plt.rcParams.update({'font.family': fam, 'font.size': 11})

EXP = Path(__file__).resolve().parents[2]
TSV = EXP / 'results/mixgraph_pairs_260914.tsv'
OUT = EXP / 'results/mixgraph_pairs_260914'
BLUE, GREEN, GREY = '#0072B2', '#009E73', '#8C8C8C'
SERIES = [('baseline', 'Baseline', BLUE, 'o'),
          ('f2load', 'F2Load (no bitmap)', GREEN, 's')]
PANELS = [
    ('throughput_ops_sec', 1e3, '(a)  mixgraph throughput', 'thousand ops/s', '{:,.0f}k'),
    ('get_found_fraction', 1e-2, '(b)  Get hit rate in the key space', 'positive Gets (%)', '{:.1f}%'),
    ('data_cache_hit_fraction', 1e-2, '(c)  data block cache hit rate', 'cache hits (%)', '{:.1f}%'),
    ('compaction_write_bytes_per_put', 1, '(d)  compaction write per Put, after drain',
     'bytes written per Put', '{:,.0f} B'),
]

rows = list(csv.DictReader(TSV.open(), delimiter='\t'))
by = {s: [r for r in rows if r['system'] == s] for s, _, _, _ in SERIES}

fig, axes = plt.subplots(2, 2, figsize=(12.6, 8.4), dpi=200)
for ax, (field, scale, title, ylabel, fmt) in zip(axes.ravel(), PANELS):
    for key, label, colour, marker in SERIES:
        values = [float(r[field]) / scale for r in by[key]]
        xs = range(1, len(values) + 1)
        ax.plot(xs, values, linestyle='none', marker=marker, markersize=8, color=colour,
                markeredgecolor='white', markeredgewidth=1.4, zorder=4, label=label)
        mean = st.mean(values)
        ax.axhline(mean, color=colour, linewidth=1.3, linestyle='--', alpha=0.75, zorder=2)
        ax.annotate(fmt.format(mean), (10.5, mean), textcoords='offset points',
                    xytext=(6, 0), va='center', fontsize=10, color=colour,
                    bbox=dict(boxstyle='round,pad=0.18', facecolor='white',
                              edgecolor='none'))
    gap = 100 * (st.mean([float(r[field]) for r in by['f2load']])
                 / st.mean([float(r[field]) for r in by['baseline']]) - 1)
    ax.set_title('{}   (F2Load {:+.1f}%)'.format(title, gap), fontsize=12.5, pad=10)
    ax.set_ylabel(ylabel, fontsize=11.5, labelpad=7)
    ax.set_xlabel('loading (independent repeat)', fontsize=11, labelpad=6)
    ax.set_xticks(range(1, 11))
    ax.set_xlim(0.4, 12.4)
    ax.set_axisbelow(True)
    ax.grid(axis='y', color='#D9D9D9', linewidth=0.75)
    ax.grid(axis='x', visible=False)
    for sp in ('top', 'right'):
        ax.spines[sp].set_visible(False)
    for sp in ('left', 'bottom'):
        ax.spines[sp].set_color('#4A4A4A')

fig.legend(handles=[Line2D([], [], linestyle='none', marker=m, markersize=9, color=c,
                           markeredgecolor='white', markeredgewidth=1.4, label=l)
                    for _, l, c, m in SERIES]
                   + [Line2D([], [], color=GREY, linestyle='--', linewidth=1.3, label='mean of ten')],
           fontsize=11.5, frameon=False, loc='upper center', ncol=3, bbox_to_anchor=(0.5, 0.965))
fig.suptitle('mixgraph, 300 s + compaction drain, 1 TB / 1 KB, 50 GiB cache, 48 threads  '
             '(campaign mixgraph_pairs_260914)', fontsize=13, y=1.005)
fig.tight_layout(rect=(0, 0, 1, 0.925))
for ext in ('png', 'pdf'):
    fig.savefig('{}.{}'.format(OUT, ext), bbox_inches='tight', pad_inches=0.25, facecolor='white')
print('wrote {}.png and {}.pdf'.format(OUT, OUT))
