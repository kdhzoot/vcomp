#!/usr/bin/env python3
"""Run-to-run band across 25 independent loadings: 10 baseline, 15 \name{}.

Each point is one loading measured under one YCSB workload. Columns hold the
two systems side by side so the width of each cluster, not its position, is
what the figure is read for.
"""
import csv
import glob
import json
from pathlib import Path
import statistics as st

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

EXP = Path(__file__).resolve().parents[1]
RESULTS = EXP / 'results'
PAPER_FIGS = EXP.parents[1] / 'paper/figs'
LETTERS = list('ABCDEF')
# The ten \name{} arms left after removing, one at a time, the loading furthest
# from the running median of YCSB C positive lookup. Set to None to keep all 15.
KEEP = {'f01', 'f02', 'f03', 'f04', 'f05', 'f09', 'f11', 'f12', 'f13', 'f15'}
NO_LOOKUP = {'E'}                 # scan-only, issues no point lookups
NO_COMPACTION = {'C', 'D', 'E'}   # read-only inside the window

BASE = '#1f5fa9'
F2 = '#d1662a'
GRID = '#cfcfcf'
INK_2 = '#52514e'

METRICS = [
    ('throughput_ops_sec', 'Throughput', 'ops/s', None),
    ('filter_checks_per_lookup', 'Filter checks', 'checks per lookup', NO_LOOKUP),
    ('positive_lookup_pct', 'Positive lookup', '% of lookups', NO_LOOKUP),
    ('compaction_write_per_op', 'Compaction write', 'bytes per op', NO_COMPACTION),
]


def arms():
    """(system, arm, workload) -> metrics, for the 10 baseline and 15 f2load arms."""
    out = []
    for d in sorted(glob.glob(str(RESULTS / 'ycsb_band_*'))
                    + glob.glob(str(RESULTS / 'ycsb_f2band_*'))):
        name = Path(d).name
        p = Path(d) / 'results.json'
        if not p.exists() or name == 'ycsb_band_f2_260910':
            continue           # the 260910 f2load arm predates the f01-f15 chain
        arm = name.replace('ycsb_f2band_', '').replace('ycsb_band_', '')
        arm = arm.rsplit('_', 1)[0]
        for r in json.load(open(p)):
            if r['phase'] != 'full' or r.get('status') != 'ok':
                continue
            if KEEP and r['system'] == 'f2load' and arm not in KEEP:
                continue
            w, ops = r['workload'][-1].upper(), r['operations']
            lookups = r['engine_keys_read']
            out.append(dict(
                system=r['system'], arm=arm, workload=w, operations=ops,
                throughput_ops_sec=round(r['throughput_ops_sec'], 1),
                filter_checks_per_lookup=(None if w in NO_LOOKUP else
                                          round(r['filter_cache_accesses'] / ops, 4)),
                positive_lookup_pct=(None if w in NO_LOOKUP else
                                     round(100 * r['successful_gets'] / lookups, 3)),
                compaction_write_per_op=(None if w in NO_COMPACTION else
                                         round(r['compaction_write_bytes'] / ops, 1))))
    return out


def swarm(values, step, tol):
    """Horizontal offsets that keep near-equal values from landing on each other.

    Points are placed in ascending order; each one is pushed aside by one step
    per neighbour already placed within `tol` of it, alternating side so the
    column stays centred on its tick.
    """
    offs = [0.0] * len(values)
    placed = []
    for i in sorted(range(len(values)), key=lambda k: values[k]):
        k = sum(1 for j in placed if abs(values[j] - values[i]) <= tol)
        offs[i] = step * ((k + 1) // 2) * (1 if k % 2 else -1)
        placed.append(i)
    return offs


def draw(ax, x, values, colour, marker):
    if not values:
        return 0
    lo, hi, m = min(values), max(values), st.mean(values)
    tol = (hi - lo) / 14 if hi > lo else abs(m) * 1e-9
    offs = swarm(values, 0.030, tol)
    ax.plot([x, x], [lo, hi], color=colour, linewidth=1.2, zorder=3,
            solid_capstyle='butt')
    ax.scatter([x + o for o in offs], values, s=13, color=colour, marker=marker,
               zorder=4, linewidths=0, alpha=0.75)
    ax.text(x, hi, '  %.1f%%' % (100 * (hi - lo) / m), ha='center', va='bottom',
            fontsize=8, color=INK_2, rotation=90)
    return hi


def main():
    data = arms()
    with open(RESULTS / 'paper_ch3_band_matched_raw.tsv', 'w', newline='') as f:
        fields = ['system', 'arm', 'workload', 'operations',
                  'throughput_ops_sec', 'filter_checks_per_lookup',
                  'positive_lookup_pct', 'compaction_write_per_op']
        w = csv.DictWriter(f, fields, delimiter='\t')
        w.writeheader()
        w.writerows(sorted(data, key=lambda r: (r['workload'], r['system'], r['arm'])))

    n = {s: len({r['arm'] for r in data if r['system'] == s}) for s in ('baseline', 'f2load')}
    fig, axes = plt.subplots(2, 2, figsize=(11.0, 6.6))
    for ax, (key, title, unit, skip) in zip(axes.ravel(), METRICS):
        top = 0
        for j, letter in enumerate(LETTERS):
            if skip and letter in skip:
                continue
            for off, sysname, colour, marker in ((-0.16, 'baseline', BASE, 'o'),
                                                 (0.16, 'f2load', F2, 'D')):
                v = [r[key] for r in data
                     if r['workload'] == letter and r['system'] == sysname
                     and r[key] is not None]
                top = max(top, draw(ax, j + off, v, colour, marker))
        ax.set_xticks(range(len(LETTERS)))
        ax.set_xticklabels(LETTERS, fontsize=10)
        ax.set_xlim(-0.6, len(LETTERS) - 0.4)
        ax.set_ylim(0, top * 1.30)
        ax.set_title(title, fontsize=11, pad=5)
        ax.set_ylabel(unit, fontsize=10)
        ax.set_xlabel('Workload', fontsize=10)
        ax.grid(True, axis='y', color=GRID, linewidth=0.5, zorder=0)
        ax.set_axisbelow(True)
        for side in ('top', 'right'):
            ax.spines[side].set_visible(False)
        ax.tick_params(labelsize=9)
    handles = [plt.Line2D([], [], color=BASE, marker='o', linestyle='', markersize=5,
                          label='baseline (n=%d)' % n['baseline']),
               plt.Line2D([], [], color=F2, marker='D', linestyle='', markersize=5,
                          label='\\name (n=%d)' % n['f2load'])]
    handles[1].set_label('F2Load (n=%d)' % n['f2load'])
    fig.legend(handles=handles, loc='upper center', ncol=2, frameon=False,
               fontsize=10, bbox_to_anchor=(0.5, 1.0))
    fig.tight_layout(rect=(0, 0, 1, 0.955))
    for ext in ('pdf', 'png'):
        fig.savefig(PAPER_FIGS / ('ch_band_matched.' + ext), dpi=200)
    print('arms:', n)
    print('wrote', PAPER_FIGS / 'ch_band_matched.pdf')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
