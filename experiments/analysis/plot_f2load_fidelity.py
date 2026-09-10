#!/usr/bin/env python3
"""F2Load fidelity against its baseline, before and after the dedup-ratio fix.

Every panel is one fidelity dimension, expressed as the F2Load value divided by
the baseline value measured in the same campaign. Perfect fidelity is 1.0, drawn
as the horizontal reference line, so the question each panel answers is how far
the reconstructed state sits from the naturally accumulated one - and the
connector shows which way the fix moved it.

Counters are per operation. The campaigns run for a fixed wall time, so a faster
arm performs more operations and every absolute counter scales with it; only
per-operation values are comparable.
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from compare_f2load_fidelity import cells, metrics

EXP = Path(__file__).resolve().parents[1]
LETTERS = ['a', 'b', 'c', 'd', 'e', 'f']

SURFACE = '#fcfcfb'
INK = '#0b0b0b'
INK_2 = '#52514e'
GRID = '#d8d7d2'
# Reference palette, categorical slots 1 and 2 in fixed order (palette.md).
BEFORE, AFTER = '#2a78d6', '#eb6834'
IDEAL = '#e34948'

# The dimensions the fidelity argument rests on: how fast the state serves the
# workload, how much filter work it takes, how many lookups land, how much
# compaction it still owes, and how much device work each operation costs.
PANELS = [
    ('throughput (ops/s)', 'Throughput'),
    ('avg latency (us)', 'Average latency'),
    ('filter checks / op', 'Filter checks / op'),
    ('filter positive / op', 'Filter positives / op'),
    ('found fraction', 'Found fraction'),
    ('compaction write B / op', 'Compaction write B / op'),
    ('data cache miss / op', 'Data cache miss / op'),
    # Index-cache misses were dropped from this figure: they run at 2e-5 to
    # 5e-4 per operation in every workload, so their ratio is noise. The
    # device's per-read service time is the residual that actually remains.
    ('device read p50 (us)', 'Device read latency (p50)'),
]

# Larger than the analysis-script default throughout, at the user's request.
FS_TITLE, FS_LABEL, FS_TICK, FS_LEGEND = 17, 15, 14, 16


def ratios(after_run, before_run):
    """{metric: {letter: (before, after)}} as F2Load / baseline."""
    base = cells(after_run, 'baseline')
    new = cells(after_run, 'f2load')
    old = cells(before_run, 'f2load')
    table = {}
    for key, _ in PANELS:
        row = {}
        for letter in LETTERS:
            if letter not in base or letter not in new or letter not in old:
                continue
            bv = metrics(base[letter]).get(key)
            nv = metrics(new[letter]).get(key)
            ov = metrics(old[letter]).get(key)
            if not bv or nv is None or ov is None:
                continue
            row[letter] = (ov / bv, nv / bv)
        table[key] = row
    return table


def style(axis, ylabel):
    axis.set_facecolor(SURFACE)
    axis.set_ylabel(ylabel, color=INK_2, fontsize=FS_LABEL)
    axis.yaxis.grid(True, color=GRID, linewidth=0.5)
    axis.set_axisbelow(True)
    for edge in ('top', 'right', 'left'):
        axis.spines[edge].set_visible(False)
    axis.spines['bottom'].set_color(GRID)
    axis.spines['bottom'].set_linewidth(0.6)
    axis.tick_params(colors=INK_2, labelsize=FS_TICK, length=0)
    axis.set_xticks(range(len(LETTERS)))
    axis.set_xticklabels([w.upper() for w in LETTERS])


def panel(axis, row, title, tag):
    axis.axhline(1.0, color=IDEAL, linewidth=1.4, zorder=1)
    blank = []
    for position, letter in enumerate(LETTERS):
        if letter not in row:
            # A dimension a workload does not exercise: C never compacts, E
            # issues scans rather than point lookups. Parked at the floor so it
            # never sits under the reference line.
            blank.append(position)
            continue
        before, after = row[letter]
        axis.plot([position, position], [before, after], color=INK_2,
                  linewidth=1.4, alpha=0.55, zorder=2, solid_capstyle='round')
        for value, color in ((before, BEFORE), (after, AFTER)):
            axis.plot([position], [value], marker='o', markersize=11,
                      color=color, markeredgecolor=SURFACE,
                      markeredgewidth=2.0, zorder=3)
    # Limits follow where the data actually lands and always contain 1.0. A
    # forced symmetry would spend half of a one-sided panel on empty space.
    values = [v for pair in row.values() for v in pair] + [1.0]
    pad = max(max(values) - min(values), 0.02) * 0.16
    low, high = min(values) - pad, max(values) + pad
    axis.set_ylim(low, high)
    for position in blank:
        axis.text(position, low + (high - low) * 0.06, 'n/a', color=INK_2,
                  fontsize=FS_TICK - 2, ha='center', va='center', alpha=0.7,
                  zorder=2, style='italic')
    style(axis, 'F2Load / baseline')
    axis.set_title('(%s) %s' % (tag, title), color=INK, fontsize=FS_TITLE,
                   loc='left', pad=10)


def summary(axis, table):
    """Mean absolute deviation from 1.0 over every panel, per workload."""
    span, gap = 0.72, 0.03
    width = span / 2 - gap
    means = {}
    for index, slot in enumerate((0, 1)):
        xs, ys = [], []
        for position, letter in enumerate(LETTERS):
            drift = [abs(row[letter][slot] - 1.0)
                     for row in table.values() if letter in row]
            if not drift:
                continue
            xs.append(position - span / 2 + index * (width + gap) + width / 2)
            ys.append(sum(drift) / len(drift) * 100)
            means.setdefault(letter, []).append(ys[-1])
        axis.bar(xs, ys, width=width, linewidth=0,
                 color=(BEFORE, AFTER)[index],
                 label=('Before the fix', 'After the fix')[index])
    style(axis, 'Mean |deviation| (%)')
    axis.set_ylim(0)
    axis.set_title('(i) Overall drift from the baseline', color=INK,
                   fontsize=FS_TITLE, loc='left', pad=10)
    return means


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', default='ycsb_50g_f2ratio_260909')
    parser.add_argument('--compare-run-id',
                        default='ycsb_50g_f2load_baseline_260909')
    parser.add_argument('--out')
    args = parser.parse_args()

    table = ratios(args.run_id, args.compare_run_id)

    figure, axes = plt.subplots(3, 3, figsize=(17.4, 12.6), facecolor=SURFACE)
    figure.subplots_adjust(wspace=0.26, hspace=0.34, bottom=0.10, top=0.92)
    flat = axes.ravel()
    for index, (key, title) in enumerate(PANELS):
        panel(flat[index], table[key], title, 'abcdefgh'[index])
        flat[index].set_xlabel('YCSB workload', color=INK_2, fontsize=FS_LABEL)
    means = summary(flat[8], table)
    flat[8].set_xlabel('YCSB workload', color=INK_2, fontsize=FS_LABEL)

    handles, texts = flat[8].get_legend_handles_labels()
    handles.append(plt.Line2D([], [], color=IDEAL, linewidth=1.4,
                              label='Baseline (perfect fidelity)'))
    texts.append('Baseline (perfect fidelity)')
    legend = figure.legend(handles, texts, frameon=False, fontsize=FS_LEGEND,
                           ncol=3, loc='lower center',
                           bbox_to_anchor=(0.5, 0.005), handlelength=1.4,
                           columnspacing=2.4)
    for text in legend.get_texts():
        text.set_color(INK_2)
    figure.suptitle('F2Load state fidelity against a naturally accumulated '
                    'baseline, before and after the dedup-ratio fix',
                    color=INK, fontsize=FS_TITLE + 3, x=0.5, y=0.975)

    out = Path(args.out) if args.out else (
        EXP / 'results/f2load_fidelity_260909.png')
    figure.savefig(out, dpi=170, facecolor=SURFACE, bbox_inches='tight')
    print('wrote', out)

    print('\n%-26s%s' % ('metric', ''.join('%16s' % w.upper() for w in LETTERS)))
    for key, _ in PANELS:
        row = table[key]
        line = '%-26s' % key
        for letter in LETTERS:
            if letter not in row:
                line += '%16s' % '-'
            else:
                line += '%16s' % ('%.3f>%.3f' % row[letter])
        print(line)
    print('\n%-26s' % 'mean |deviation| %'
          + ''.join('%16s' % ('%.2f>%.2f' % tuple(means[w]))
                    for w in LETTERS if w in means))


if __name__ == '__main__':
    main()
