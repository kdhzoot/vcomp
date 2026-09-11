#!/usr/bin/env python3
"""Measured YCSB A-F results for one campaign, as run.

Absolute values, not ratios. Throughput spans 25x across workloads, so each
workload is its own small multiple with its own zero-based axis rather than
one shared log axis - a bar has to start at zero to be read by length.
"""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

EXP = Path(__file__).resolve().parents[1]
RUNS = EXP / 'artifacts/log_runs'
LETTERS = list('abcdef')

SURFACE, INK, INK_2, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#d8d7d2'
COLOR = {'baseline': '#2a78d6', 'f2load': '#eb6834'}
NAME = {'baseline': 'Baseline\n(conventional)', 'f2load': 'F2Load'}
FS_TITLE, FS_LABEL, FS_TICK, FS_LEGEND, FS_VALUE = 16, 14, 13, 16, 14

ROWS = [('throughput_ops_sec', 'Throughput (K ops/s)', 1e3, '{:,.1f}'),
        ('avg_latency_us', 'Average latency (µs)', 1.0, '{:,.1f}')]


def cells(run):
    out = {}
    for r in json.loads((RUNS / run / 'results.json').read_text()):
        if r.get('phase') == 'full' and r.get('status') == 'ok':
            out[(r['system'], r['workload'][-1])] = r
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', default='ycsb_50g_physcopy_C_full_260910')
    parser.add_argument('--title', default='Measured YCSB A-F: 1 TB dataset, 50 GiB block '
                                           'cache, 48 threads, 300 s per cell, fresh copy of both DBs')
    parser.add_argument('--out')
    args = parser.parse_args()
    data = cells(args.run_id)

    figure, axes = plt.subplots(len(ROWS), len(LETTERS),
                                figsize=(19, 8.4), facecolor=SURFACE)
    figure.subplots_adjust(wspace=0.42, hspace=0.42, bottom=0.14, top=0.86)

    for row, (key, ylabel, scale, fmt) in enumerate(ROWS):
        for col, letter in enumerate(LETTERS):
            axis = axes[row][col]
            axis.set_facecolor(SURFACE)
            values = []
            for i, system in enumerate(('baseline', 'f2load')):
                v = data[(system, letter)][key] / scale
                values.append(v)
                axis.bar([i], [v], width=0.62, color=COLOR[system], linewidth=0)
                axis.text(i, v, fmt.format(v), ha='center', va='bottom',
                          color=INK_2, fontsize=FS_VALUE)
            axis.set_ylim(0, max(values) * 1.24)
            axis.set_xlim(-0.7, 1.7)
            axis.set_xticks([])
            axis.yaxis.grid(True, color=GRID, linewidth=0.5)
            axis.set_axisbelow(True)
            for edge in ('top', 'right', 'left'):
                axis.spines[edge].set_visible(False)
            axis.spines['bottom'].set_color(GRID)
            axis.tick_params(colors=INK_2, labelsize=FS_TICK, length=0)
            if col == 0:
                axis.set_ylabel(ylabel, color=INK_2, fontsize=FS_LABEL)
            if row == 0:
                axis.set_title('Workload ' + letter.upper(), color=INK,
                               fontsize=FS_TITLE, pad=10)
            # F2Load / baseline, stated as the number rather than left to the eye.
            axis.set_xlabel('%.3f×' % (values[1] / values[0]),
                            color=INK_2, fontsize=FS_LABEL)

    handles = [plt.Rectangle((0, 0), 1, 1, color=COLOR[s],
                             label=NAME[s].replace('\n', ' '))
               for s in ('baseline', 'f2load')]
    legend = figure.legend(handles=handles, frameon=False, fontsize=FS_LEGEND,
                           ncol=2, loc='lower center', bbox_to_anchor=(0.5, 0.005))
    for text in legend.get_texts():
        text.set_color(INK_2)
    figure.suptitle(args.title, color=INK, fontsize=FS_TITLE + 3, x=0.5, y=0.965)
    figure.text(0.5, 0.075, 'the number under each pair is F2Load / baseline',
                ha='center', color=INK_2, fontsize=FS_LABEL)

    out = Path(args.out) if args.out else (EXP / 'results' / ('ycsb_measured_' + args.run_id + '.png'))
    figure.savefig(out, dpi=170, facecolor=SURFACE, bbox_inches='tight')
    print('wrote', out)

    print('\n%-3s %14s %14s %8s | %12s %12s' % ('wl', 'baseline ops/s', 'f2load ops/s',
                                                 'ratio', 'base lat us', 'f2 lat us'))
    for letter in LETTERS:
        b, f = data[('baseline', letter)], data[('f2load', letter)]
        print('%-3s %14,.0f %14,.0f %8.3f | %12.1f %12.1f'.replace(',', '')
              % (letter.upper(), b['throughput_ops_sec'], f['throughput_ops_sec'],
                 f['throughput_ops_sec'] / b['throughput_ops_sec'],
                 b['avg_latency_us'], f['avg_latency_us']))


if __name__ == '__main__':
    main()
