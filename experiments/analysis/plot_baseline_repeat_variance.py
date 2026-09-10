#!/usr/bin/env python3
"""Variance across identically configured baseline loads and their YCSB runs.

Three panels, one measure each - never two y-scales on one plot:
  (a) loading time per load
  (b) L1 range coverage per load
  (c) YCSB throughput per workload, one series per overnight load

Loads appear in chronological order so that the environment drift across the
overnight campaign stays visible.
"""
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

EXP = Path(__file__).resolve().parents[1]
LOADS = EXP / 'artifacts/log_loads'
RUNS = EXP / 'artifacts/log_runs'

# Reference palette, fixed order, light mode (references/palette.md).
SURFACE = '#fcfcfb'
INK = '#0b0b0b'
INK_2 = '#52514e'
GRID = '#d8d7d2'
SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100']
WORKLOADS = ['a', 'b', 'c', 'd', 'e', 'f']


def load_records():
    """(label, elapsed_sec, waf, l1_coverage_pct) in chronological order."""
    out = []
    validated = json.loads((LOADS / 'paper_ch23_common_260905_approved_run3'
                            / 'full/baseline_1kb/validated.json').read_text())
    coverage = json.loads((LOADS / 'paper_ch23_common_260905_approved_run3'
                           / 'full/baseline_1kb/coverage_260909/coverage.json').read_text())
    out.append(('run3', validated['elapsed_sec'], validated['waf'],
                coverage['l1_global_coverage_pct']))
    for entry in json.loads((LOADS / 'baseline_coverage_260908_repeat1'
                             / 'results.json').read_text()):
        if entry['phase'] == 'full':
            out.append((entry['name'].replace('repeat_', 'rpt'),
                        entry['loading']['elapsed_sec'], entry['loading']['waf'],
                        entry['coverage']['l1_global_coverage_pct']))
    for index in range(1, 5):
        directory = LOADS / 'baseline_coverage_260908_night_n{:02}'.format(index)
        for entry in json.loads((directory / 'results.json').read_text()):
            if entry['phase'] == 'full':
                out.append(('n{:02}'.format(index), entry['loading']['elapsed_sec'],
                            entry['loading']['waf'],
                            entry['coverage']['l1_global_coverage_pct']))
    return out


def ycsb_records():
    """{workload letter: {run: ops/s}} for the overnight campaign."""
    table = {letter: {} for letter in WORKLOADS}
    for index in range(1, 5):
        run = 'n{:02}'.format(index)
        path = RUNS / ('baseline_repeat_ycsb_all_260908_night_' + run) / 'results.json'
        for row in json.loads(path.read_text()):
            if row.get('phase') == 'full':
                table[row['workload'][-1]][run] = row['throughput_ops_sec']
    return table


def style(axis, ylabel):
    axis.set_facecolor(SURFACE)
    axis.set_ylabel(ylabel, color=INK_2, fontsize=9)
    axis.yaxis.grid(True, color=GRID, linewidth=0.4)
    axis.set_axisbelow(True)
    for edge in ('top', 'right', 'left'):
        axis.spines[edge].set_visible(False)
    axis.spines['bottom'].set_color(GRID)
    axis.spines['bottom'].set_linewidth(0.5)
    axis.tick_params(colors=INK_2, labelsize=8, length=0)


def main():
    loads = load_records()
    ycsb = ycsb_records()
    labels = [row[0] for row in loads]

    figure, axes = plt.subplots(1, 3, figsize=(13.0, 3.5), facecolor=SURFACE)
    figure.subplots_adjust(wspace=0.28, bottom=0.22, top=0.86)

    # (a) loading time - one measure, one hue; identity is on the x axis.
    axis = axes[0]
    values = [row[1] / 60 for row in loads]
    axis.bar(labels, values, width=0.62, color=SERIES[0], linewidth=0)
    for x, value in enumerate(values):
        axis.text(x, value + 0.9, '{:.1f}'.format(value), ha='center',
                  color=INK_2, fontsize=7.5)
    axis.set_ylim(0, max(values) * 1.18)
    style(axis, 'Loading time (min)')
    axis.set_title('(a) Loading time', color=INK, fontsize=10, loc='left')

    # (b) L1 coverage - same entities, second measure, its own panel.
    axis = axes[1]
    values = [row[3] for row in loads]
    axis.bar(labels, values, width=0.62, color=SERIES[0], linewidth=0)
    for x, value in enumerate(values):
        axis.text(x, value + 2.4, '{:.1f}'.format(value), ha='center',
                  color=INK_2, fontsize=7.5)
    axis.set_ylim(0, 100)
    style(axis, 'L1 range coverage (%)')
    axis.set_title('(b) L1 range coverage', color=INK, fontsize=10, loc='left')

    # (c) YCSB throughput - four loads as series, grouped by workload.
    axis = axes[2]
    runs = ['n01', 'n02', 'n03', 'n04']
    span, gap = 0.78, 0.02
    width = span / len(runs) - gap
    for index, run in enumerate(runs):
        offsets = [x - span / 2 + index * (width + gap) + width / 2
                   for x in range(len(WORKLOADS))]
        axis.bar(offsets, [ycsb[w][run] / 1e3 for w in WORKLOADS], width=width,
                 color=SERIES[index], linewidth=0, label=run)
    axis.set_xticks(range(len(WORKLOADS)))
    axis.set_xticklabels([w.upper() for w in WORKLOADS])
    # Headroom so the legend never sits on top of the tallest group.
    axis.set_ylim(0, max(ycsb['d'].values()) / 1e3 * 1.22)
    style(axis, 'Throughput (K ops/s)')
    axis.set_title('(c) YCSB throughput, 50 GiB cache', color=INK, fontsize=10,
                   loc='left')
    legend = axis.legend(frameon=False, fontsize=8, ncol=4, loc='upper center',
                         bbox_to_anchor=(0.5, 1.02), handlelength=0.9,
                         columnspacing=1.0)
    for text in legend.get_texts():
        text.set_color(INK_2)

    out = EXP / 'results/baseline_repeat_variance_260909.png'
    figure.savefig(out, dpi=200, facecolor=SURFACE, bbox_inches='tight')
    print('wrote', out)

    print('\n%-6s %10s %8s %12s' % ('load', 'min', 'WAF', 'L1 cov %'))
    for label, elapsed, waf, cov in loads:
        print('%-6s %10.1f %8.3f %12.2f' % (label, elapsed / 60, waf, cov))
    print('\n%-4s' % 'wl' + ''.join('%10s' % r for r in runs) + '%10s' % 'spread')
    for w in WORKLOADS:
        xs = [ycsb[w][r] for r in runs]
        print('%-4s' % w.upper() + ''.join('%10.0f' % v for v in xs)
              + '%9.1f%%' % ((max(xs) / min(xs) - 1) * 100))


if __name__ == '__main__':
    main()
