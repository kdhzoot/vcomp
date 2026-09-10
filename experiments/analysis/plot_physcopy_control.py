#!/usr/bin/env python3
"""Workload E on byte-copied DBs against the originals.

Three cells per system: the original DB as measured in the fidelity campaign,
the same original re-measured in the copy campaign (same-session control),
and the byte copy. One measure per panel, zero-based bars; the system is the
colour and the copy is hatched so identity never rests on colour alone.
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

EXP = Path(__file__).resolve().parents[1]
RUNS = EXP / 'artifacts/log_runs'

SURFACE, INK, INK_2, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#d8d7d2'
COLOR = {'baseline': '#2a78d6', 'f2load': '#eb6834'}
NAME = {'baseline': 'Baseline', 'f2load': 'F2Load'}
FS_TITLE, FS_LABEL, FS_TICK, FS_LEGEND, FS_VALUE = 17, 15, 14, 15, 13

# (label, run id) per system; the copy campaign for each system is the one
# where that system's arm was the copy.
CELLS = {
    'baseline': [('orig.\nSep 9', 'ycsb_50g_f2ratio_260909'),
                 ('orig.\nsame\nsession', 'ycsb_50g_physcopy_B_e_260909'),
                 ('copy', 'ycsb_50g_physcopy_A_e_260909')],
    'f2load':   [('orig.\nSep 9', 'ycsb_50g_f2ratio_260909'),
                 ('orig.\nsame\nsession', 'ycsb_50g_physcopy_A_e_260909'),
                 ('copy', 'ycsb_50g_physcopy_B_e_260909')],
}
PANELS = [
    ('Throughput (K ops/s)', lambda r: r['throughput_ops_sec'] / 1e3, '{:.1f}'),
    ('Device read latency p50 (µs)', lambda r: r['engine_histograms']['rocksdb.sst.read.micros']['p50'], '{:.1f}'),
    ('Device read latency p99 (µs)', lambda r: r['engine_histograms']['rocksdb.sst.read.micros']['p99'], '{:.1f}'),
]


def cell(run_id, system):
    rows = json.loads((RUNS / run_id / 'results.json').read_text())
    return next(r for r in rows if r.get('phase') == 'full'
                and r['workload'].endswith('e') and r['system'] == system)


def main():
    figure, axes = plt.subplots(1, 3, figsize=(19, 7.0), facecolor=SURFACE)
    figure.subplots_adjust(wspace=0.30, bottom=0.32, top=0.85)
    width, gap = 0.30, 0.03
    step = 1.25  # group spacing
    for axis, (title, value, fmt) in zip(axes, PANELS):
        axis.set_facecolor(SURFACE)
        for g, system in enumerate(('baseline', 'f2load')):
            for i, (label, run) in enumerate(CELLS[system]):
                v = value(cell(run, system))
                x = g * step + (i - 1) * (width + gap)
                is_copy = 'copy' in label
                axis.bar(x, v, width=width, color=COLOR[system], linewidth=0,
                         hatch='///' if is_copy else None,
                         edgecolor=SURFACE if is_copy else 'none')
                axis.text(x, v, fmt.format(v), ha='center', va='bottom',
                          color=INK_2, fontsize=FS_VALUE, rotation=0)
                axis.text(x, -0.03, label, ha='center', va='top', color=INK_2,
                          fontsize=FS_TICK - 1, transform=axis.get_xaxis_transform(), linespacing=1.1)
        axis.set_xticks([0, step])
        axis.set_xticklabels([NAME['baseline'], NAME['f2load']], fontsize=FS_LABEL, color=INK)
        axis.tick_params(axis='x', pad=70, length=0)
        axis.tick_params(axis='y', colors=INK_2, labelsize=FS_TICK, length=0)
        axis.set_ylim(0, axis.get_ylim()[1] * 1.12)
        axis.set_xlim(-0.6, step + 0.6)
        axis.set_title(title, color=INK, fontsize=FS_TITLE, loc='left', pad=10)
        axis.yaxis.grid(True, color=GRID, linewidth=0.5)
        axis.set_axisbelow(True)
        for edge in ('top', 'right', 'left'):
            axis.spines[edge].set_visible(False)
        axis.spines['bottom'].set_color(GRID)
    handles = [plt.Rectangle((0, 0), 1, 1, color=COLOR['baseline'], label='Baseline'),
               plt.Rectangle((0, 0), 1, 1, color=COLOR['f2load'], label='F2Load'),
               plt.Rectangle((0, 0), 1, 1, facecolor=INK_2, hatch='///', edgecolor=SURFACE,
                             label='byte copy (cp -a, one stream)')]
    legend = figure.legend(handles=handles, frameon=False, fontsize=FS_LEGEND, ncol=3,
                           loc='lower center', bbox_to_anchor=(0.5, 0.0))
    for text in legend.get_texts():
        text.set_color(INK_2)
    figure.suptitle('YCSB E: the same DBs before and after a single-stream byte copy '
                    '(50 GiB cache, identical logical work per operation)',
                    color=INK, fontsize=FS_TITLE + 2, x=0.5, y=0.97)
    out = EXP / 'results/physcopy_control_e_260909.png'
    figure.savefig(out, dpi=170, facecolor=SURFACE, bbox_inches='tight')
    print('wrote', out)


if __name__ == '__main__':
    main()
