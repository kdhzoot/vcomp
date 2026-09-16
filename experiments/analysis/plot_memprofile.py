#!/usr/bin/env python3
"""Section 5.5 figure: how F2Load's peak memory scales, and what it is made of.

Left panel  - peak RSS above the process's own pre-loading RSS, against dataset
              size, one line per record size.
Right panel - the composition of that peak at the largest dataset, one stacked
              bar per record size.
"""
import csv
from pathlib import Path
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

EXP = Path(__file__).resolve().parents[1]
R = EXP / 'results'
FIGS = EXP.parents[1] / 'paper/figs'
KB, HB = '#1f5fa9', '#c2632a'
GRID, INK = '#cfcfcf', '#52514e'
# Descriptor parts first, then everything else, darkest to lightest.
ORDER = [('descriptors', '#1f5fa9'), ('other live', '#7fa8d4'),
         ('block cache', '#9c9c9c'), ('memtables', '#c4c4c4'),
         ('allocator+process', '#e0dcd4')]
LABEL = {1024: '1 KB records', 100: '100 B records'}


def tsv(name):
    return list(csv.DictReader(open(R / name), delimiter='\t'))


def style(ax, axis='y'):
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    ax.grid(True, axis=axis, color=GRID, linewidth=0.5, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK, labelsize=9)


def main():
    peak = tsv('memprofile_peak.tsv')
    bd = [r for r in tsv('memprofile_breakdown.tsv') if r['scope'] == 'rss']
    sizes = sorted({int(r['dataset_gib']) for r in peak})
    kvs = sorted({int(r['kv_bytes']) for r in peak}, reverse=True)
    biggest = max(sizes)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.4, 3.1),
                                   gridspec_kw=dict(width_ratios=[1.35, 1]))

    # (a) scaling
    style(ax1)
    for kv, color in zip(kvs, (KB, HB)):
        pts = sorted(((int(r['dataset_gib']), float(r['overhead_gib']))
                      for r in peak if int(r['kv_bytes']) == kv))
        ax1.plot([p[0] / 1024 for p in pts], [p[1] for p in pts], 'o-',
                 color=color, linewidth=1.8, markersize=5, label=LABEL[kv], zorder=3)
        for x, y in pts:
            ax1.annotate('%.2f' % y, (x / 1024, y), textcoords='offset points',
                         xytext=(0, 7), ha='center', fontsize=7.5, color=color)
    ax1.set_xlabel('dataset size (TB)', fontsize=9, color=INK)
    ax1.set_ylabel('peak RSS above baseline (GiB)', fontsize=9, color=INK)
    ax1.set_xticks([s / 1024 for s in sizes])
    ax1.set_xticklabels(['%g' % (s / 1024) for s in sizes])
    ax1.set_ylim(bottom=0)
    ax1.legend(frameon=False, fontsize=8.5, loc='upper left')
    ax1.set_title('(a) scaling', fontsize=9.5, color=INK, loc='left')

    # (b) composition at the largest dataset
    style(ax2)
    xs = range(len(kvs))
    for i, kv in enumerate(kvs):
        rows = {r['component']: float(r['gib']) for r in bd
                if int(r['kv_bytes']) == kv and int(r['dataset_gib']) == biggest}
        bottom = 0.0
        for name, color in ORDER:
            v = rows.get(name, 0.0)
            if v <= 0:
                continue
            ax2.bar(i, v, bottom=bottom, color=color, width=0.55,
                    edgecolor='white', linewidth=0.6, zorder=3,
                    label=name if i == 0 else None)
            if v > 0.06 * sum(rows.values()):
                ax2.text(i, bottom + v / 2, '%.2f' % v, ha='center', va='center',
                         fontsize=7.5, color='white' if color in (KB, '#7fa8d4') else INK)
            bottom += v
    ax2.set_xticks(list(xs))
    ax2.set_xticklabels([LABEL[k].replace(' records', '') for k in kvs], fontsize=9)
    ax2.set_ylabel('RSS at peak (GiB)', fontsize=9, color=INK)
    ax2.set_title('(b) composition at %g TB' % (biggest / 1024), fontsize=9.5,
                  color=INK, loc='left')
    ax2.legend(frameon=False, fontsize=8, loc='upper left', ncol=1,
               bbox_to_anchor=(1.02, 1.0))

    fig.tight_layout()
    FIGS.mkdir(parents=True, exist_ok=True)
    for ext in ('pdf', 'png'):
        out = FIGS / ('f2load_memory.' + ext)
        fig.savefig(out, dpi=200, bbox_inches='tight')
        print('  wrote ' + str(out))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
