#!/usr/bin/env python3
"""Simple absolute-value comparison: baseline (n=10) vs PLR-era F2Load (n=15,
no exact-membership bitmap), per workload, per metric. One point per loading."""
import csv
from pathlib import Path
import statistics as st

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

EXP = Path(__file__).resolve().parents[1]
RESULTS = EXP / 'results'
FIGS = EXP.parents[1] / 'paper/figs'
LETTERS = list('ABCDEF')
NO_LOOKUP = {'E'}
NO_COMPACTION = {'C', 'D', 'E'}
BASE, F2 = '#1f5fa9', '#d1662a'
GRID, INK2 = '#cfcfcf', '#52514e'
METRICS = [('throughput_ops_sec', 'Throughput', 'ops/s', None),
           ('filter_checks_per_lookup', 'Filter checks', 'checks/lookup', NO_LOOKUP),
           ('positive_lookup_pct', 'Positive lookup', '%', NO_LOOKUP),
           ('compaction_write_per_op', 'Compaction write', 'bytes/op', NO_COMPACTION)]


def swarm(values, step):
    offs = [0.0] * len(values)
    for k, i in enumerate(sorted(range(len(values)), key=lambda j: values[j])):
        offs[i] = step * ((k + 1) // 2) * (1 if k % 2 else -1)
    return offs


def draw(ax, x, values, colour):
    if not values:
        return 0
    ax.plot([x, x], [min(values), max(values)], color=colour, lw=1.0, alpha=0.6, zorder=3)
    ax.scatter([x + o for o in swarm(values, 0.02)], values, s=16, color=colour, zorder=4, linewidths=0)
    return max(values)


def main():
    rows = list(csv.DictReader(open(RESULTS / 'paper_ch3_band25_raw.tsv'), delimiter='\t'))
    f = lambda v: float(v) if v not in ('', 'None', None) else None
    fig, axes = plt.subplots(2, 2, figsize=(11, 6.6))
    for ax, (key, title, unit, skip) in zip(axes.ravel(), METRICS):
        top = 0
        for j, letter in enumerate(LETTERS):
            if skip and letter in skip:
                continue
            b = [f(r[key]) for r in rows if r['workload'] == letter and r['system'] == 'baseline' and f(r[key]) is not None]
            e = [f(r[key]) for r in rows if r['workload'] == letter and r['system'] == 'f2load' and f(r[key]) is not None]
            top = max(top, draw(ax, j - 0.15, b, BASE), draw(ax, j + 0.15, e, F2))
        ax.set_xticks(range(len(LETTERS)))
        ax.set_xticklabels(LETTERS)
        ax.set_xlim(-0.6, len(LETTERS) - 0.4)
        ax.set_ylim(0, top * 1.15)
        ax.set_title(title, fontsize=11, loc='left')
        ax.set_ylabel(unit)
        ax.set_xlabel('Workload')
        for s in ('top', 'right'):
            ax.spines[s].set_visible(False)
        ax.grid(True, axis='y', color=GRID, lw=0.5, zorder=0)
        ax.set_axisbelow(True)
    handles = [plt.Line2D([], [], color=BASE, marker='o', ls='', label='baseline (n=10)'),
               plt.Line2D([], [], color=F2, marker='o', ls='', label='F2Load, no exact-membership (n=15)')]
    fig.legend(handles=handles, loc='upper center', ncol=2, frameon=False, bbox_to_anchor=(0.5, 1.0))
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    for ext in ('pdf', 'png'):
        fig.savefig(FIGS / ('plr_vs_baseline_simple.' + ext), dpi=170)
    print('wrote', FIGS / 'plr_vs_baseline_simple.png')


if __name__ == '__main__':
    main()
