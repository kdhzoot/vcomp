#!/usr/bin/env python3
"""Run-to-run band, three ways of loading the same 1 TB configuration.

baseline is incremental construction, ten independent loadings. PLR is F2Load
as it materialized before 2026-09-13, fifteen loadings whose key set was
statistically independent of baseline's. exact membership is F2Load with
--vcomp_exact_membership, which materializes the key ids the write path
actually emits. Each point is one loading under one YCSB workload; the width
of a cluster is what the figure is read for, and the distance between the
F2Load clusters and baseline's is what the mode is judged on.
"""
import argparse
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
NO_LOOKUP = {'E'}
NO_COMPACTION = {'C', 'D', 'E'}
GRID, INK_2 = '#cfcfcf', '#52514e'
STEP = 0.018  # x distance between neighbouring points of one cluster

# (label, glob, arm filter, colour, marker, x offset)
SYSTEMS = [
    ('baseline (n=%d)', 'ycsb_band_*', lambda a: a != 'f2', '#1f5fa9', 'o', -0.22),
    ('F2Load PLR (n=%d)', 'ycsb_f2band_f*', lambda a: True, '#d1662a', 'D', 0.0),
    ('F2Load exact membership (n=%d)', 'ycsb_f2band_e*', lambda a: True, '#2a8a5c', 's', 0.22),
]
METRICS = [
    ('throughput_ops_sec', 'Throughput', 'ops/s', None),
    ('filter_checks_per_lookup', 'Filter checks', 'checks per lookup', NO_LOOKUP),
    ('positive_lookup_pct', 'Positive lookup', '% of lookups', NO_LOOKUP),
    ('compaction_write_per_op', 'Compaction write', 'bytes per op', NO_COMPACTION),
]


def rows():
    out = []
    for si, (_, pattern, keep, *_rest) in enumerate(SYSTEMS):
        for d in sorted(glob.glob(str(RESULTS / pattern))):
            p = Path(d) / 'results.json'
            arm = Path(d).name.split('_')[-2]
            if not p.exists() or not keep(arm):
                continue
            for r in json.load(open(p)):
                if r['phase'] != 'full' or r.get('status') != 'ok':
                    continue
                w, ops = r['workload'][-1].upper(), r['operations']
                out.append(dict(
                    sys=si, arm=arm, workload=w, operations=ops,
                    throughput_ops_sec=round(r['throughput_ops_sec'], 1),
                    filter_checks_per_lookup=(None if w in NO_LOOKUP else
                                              round(r['filter_cache_accesses'] / ops, 4)),
                    positive_lookup_pct=(None if w in NO_LOOKUP else
                                         round(100 * r['successful_gets'] / r['engine_keys_read'], 3)),
                    compaction_write_per_op=(None if w in NO_COMPACTION else
                                             round(r['compaction_write_bytes'] / ops, 1))))
    return out


def swarm(values, step):
    """One x position per point, so no two points of a cluster share an axis.

    Points are placed in ascending order of value and fanned out alternately to
    the right and left of the column centre, one step further each time. The
    fan is symmetric, so the cluster stays centred on its tick, and the min-max
    line drawn at the centre still reads as the cluster's spine.
    """
    offs = [0.0] * len(values)
    for k, i in enumerate(sorted(range(len(values)), key=lambda j: values[j])):
        offs[i] = step * ((k + 1) // 2) * (1 if k % 2 else -1)
    return offs


def draw(ax, x, values, colour, marker):
    if not values:
        return 0
    lo, hi, m = min(values), max(values), st.mean(values)
    ax.plot([x, x], [lo, hi], color=colour, linewidth=1.0, zorder=3, solid_capstyle='butt', alpha=0.6)
    ax.scatter([x + o for o in swarm(values, STEP)], values, s=14, color=colour,
               marker=marker, zorder=4, linewidths=0, alpha=0.8)
    ax.text(x, hi, '  %.1f%%' % (100 * (hi - lo) / m), ha='center', va='bottom',
            fontsize=7.5, color=INK_2, rotation=90)
    return hi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--without-plr', action='store_true',
                    help='baseline against exact membership only')
    args = ap.parse_args()
    if args.without_plr:
        del SYSTEMS[1]
        SYSTEMS[1] = SYSTEMS[1][:5] + (0.25,)
        SYSTEMS[0] = SYSTEMS[0][:5] + (-0.25,)
        global STEP
        STEP = 0.036
    stem = 'ch_band_em_vs_baseline' if args.without_plr else 'ch_band_em'
    data = rows()
    with open(RESULTS / (stem.replace('ch_band', 'paper_ch3_band') + '_raw.tsv'), 'w', newline='') as f:
        fields = ['system', 'arm', 'workload', 'operations', 'throughput_ops_sec',
                  'filter_checks_per_lookup', 'positive_lookup_pct', 'compaction_write_per_op']
        w = csv.DictWriter(f, fields, delimiter='\t', extrasaction='ignore')
        w.writeheader()
        for r in sorted(data, key=lambda r: (r['workload'], r['sys'], r['arm'])):
            w.writerow(dict(r, system=SYSTEMS[r['sys']][0].split(' (')[0]))
    n = [len({r['arm'] for r in data if r['sys'] == i}) for i in range(len(SYSTEMS))]

    fig, axes = plt.subplots(2, 2, figsize=(11.0, 6.6))
    for ax, (key, title, unit, skip) in zip(axes.ravel(), METRICS):
        top = 0
        for j, letter in enumerate(LETTERS):
            if skip and letter in skip:
                continue
            for si, (_, _, _, colour, marker, off) in enumerate(SYSTEMS):
                v = [r[key] for r in data
                     if r['workload'] == letter and r['sys'] == si and r[key] is not None]
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
    handles = [plt.Line2D([], [], color=c, marker=mk, linestyle='', markersize=5, label=lab % n[i])
               for i, (lab, _, _, c, mk, _) in enumerate(SYSTEMS) if n[i]]
    fig.legend(handles=handles, loc='upper center', ncol=3, frameon=False, fontsize=10,
               bbox_to_anchor=(0.5, 1.0))
    fig.tight_layout(rect=(0, 0, 1, 0.955))
    for ext in ('pdf', 'png'):
        fig.savefig(PAPER_FIGS / (stem + '.' + ext), dpi=200)
    print('arms:', dict(zip([s[0].split(' (')[0] for s in SYSTEMS], n)))

    # The comparison the mode is judged on, workload C.
    for key, name in (('positive_lookup_pct', 'positive lookup'),
                      ('filter_checks_per_lookup', 'filter checks'),
                      ('throughput_ops_sec', 'throughput')):
        fmt = ',.0f' if key == 'throughput_ops_sec' else '.3f'
        cells = []
        for si, (lab, *_r) in enumerate(SYSTEMS):
            v = [r[key] for r in data if r['workload'] == 'C' and r['sys'] == si and r[key] is not None]
            if v:
                cells.append('%s %s [%s, %s]' % (lab.split(' (')[0], format(st.mean(v), fmt),
                                                 format(min(v), fmt), format(max(v), fmt)))
        print('C %-16s  ' % name + '   '.join(cells))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
