#!/usr/bin/env python3
"""Per-second YCSB throughput over the measured window, one line per workload.

db_bench writes report.rep at report_interval_seconds=1, so every cell of a
run_baseline_repeat_ycsb_all.py campaign carries a 300-point series. This plots
those series for one loaded DB. Throughput spans more than an order of
magnitude across workloads (D is ~2.6M ops/s, E is ~96K), so the single y axis
is logarithmic - one axis, never two scales.
"""
import argparse
import csv
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

EXP = Path(__file__).resolve().parents[1]
RUNS = EXP / 'artifacts/log_runs'

# Reference palette, fixed order, light mode (references/palette.md).
SURFACE = '#fcfcfb'
INK = '#0b0b0b'
INK_2 = '#52514e'
GRID = '#d8d7d2'
SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300']
LETTERS = ['a', 'b', 'c', 'd', 'e', 'f']


def series(run_id, letter):
    """(seconds, qps) for one workload cell, or None when absent."""
    base = RUNS / run_id / 'full' / ('workload' + letter)
    matches = sorted(base.glob('*/report.rep')) if base.is_dir() else []
    if not matches:
        return None
    seconds, qps = [], []
    with matches[0].open() as handle:
        for row in csv.DictReader(handle):
            seconds.append(int(row['secs_elapsed']))
            qps.append(int(row['interval_qps']))
    return seconds, qps


def plot(run_id, out_path, title, compare=None, labels=('', '')):
    figure, axis = plt.subplots(figsize=(7.6, 4.0), facecolor=SURFACE)
    axis.set_facecolor(SURFACE)
    drawn = 0
    handles = []
    for index, letter in enumerate(LETTERS):
        data = series(run_id, letter)
        if data is None:
            continue
        line, = axis.plot(data[0], data[1], color=SERIES[index], linewidth=2.0,
                          label=letter.upper(), solid_capstyle='round')
        handles.append(line)
        drawn += 1
        if compare is None:
            continue
        other = series(compare, letter)
        if other is not None:
            # Same hue keeps the workload's identity; the dash marks the run,
            # so the run dimension is never encoded by colour alone.
            axis.plot(other[0], other[1], color=SERIES[index], linewidth=1.6,
                      linestyle=(0, (4, 2)), alpha=0.85)
    if not drawn:
        raise SystemExit('no report.rep found under ' + str(RUNS / run_id))
    if compare is not None:
        style_key = [
            plt.Line2D([], [], color=INK_2, linewidth=2.0, label=labels[0]),
            plt.Line2D([], [], color=INK_2, linewidth=1.6,
                       linestyle=(0, (4, 2)), label=labels[1]),
        ]
        keyed = axis.legend(handles=style_key, frameon=False, fontsize=8,
                            loc='lower right', handlelength=2.0)
        for text in keyed.get_texts():
            text.set_color(INK_2)
        axis.add_artist(keyed)
    axis.set_yscale('log')
    axis.set_xlim(0, 300)
    axis.set_xlabel('Elapsed time (s)', color=INK_2, fontsize=9)
    axis.set_ylabel('Throughput (ops/s, log)', color=INK_2, fontsize=9)
    axis.grid(True, which='major', color=GRID, linewidth=0.4)
    axis.grid(True, which='minor', color=GRID, linewidth=0.2, alpha=0.6)
    axis.set_axisbelow(True)
    for edge in ('top', 'right', 'left'):
        axis.spines[edge].set_visible(False)
    axis.spines['bottom'].set_color(GRID)
    axis.spines['bottom'].set_linewidth(0.5)
    axis.tick_params(colors=INK_2, labelsize=8, length=0)
    axis.set_title(title, color=INK, fontsize=10, loc='left')
    legend = axis.legend(handles=handles, frameon=False, fontsize=8, ncol=drawn,
                         loc='lower left', bbox_to_anchor=(0, -0.30),
                         handlelength=1.2, columnspacing=1.4,
                         title='YCSB workload')
    legend.get_title().set_color(INK_2)
    legend.get_title().set_fontsize(8)
    for text in legend.get_texts():
        text.set_color(INK_2)
    figure.savefig(out_path, dpi=200, facecolor=SURFACE, bbox_inches='tight')
    print('wrote', out_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--title')
    parser.add_argument('--out')
    parser.add_argument('--compare', help='second run id, drawn dashed')
    parser.add_argument('--labels', default='solid,dashed',
                        help='comma-separated names for the two runs')
    args = parser.parse_args()
    out = Path(args.out) if args.out else (
        EXP / 'results' / ('ycsb_timeseries_' + args.run_id + '.png'))
    plot(args.run_id, out, args.title or args.run_id, args.compare,
         tuple(args.labels.split(',', 1)))


if __name__ == '__main__':
    main()
