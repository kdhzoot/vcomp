#!/usr/bin/env python3
"""F2Load state fidelity under the fair read protocol.

Every panel is one fidelity dimension as F2Load / baseline, measured within a
single interleaved campaign. Two conditions are drawn: reading each DB as its
loader left it on flash, and reading a fresh single-stream byte copy of both.
The copy removes a placement artefact of conventional loading that the
as-loaded protocol charges to the tree, so the second condition is what the
comparison actually measures.

Counters are per operation: the cells run for fixed wall time, so a faster arm
performs more operations and every absolute counter scales with it.
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from compare_f2load_fidelity import cells, metrics

EXP = Path(__file__).resolve().parents[1]
LETTERS = list('abcdef')

SURFACE, INK, INK_2, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#d8d7d2'
AS_LOADED, FRESH_COPY, IDEAL = '#2a78d6', '#eb6834', '#e34948'
FS_TITLE, FS_LABEL, FS_TICK, FS_LEGEND = 17, 15, 14, 16

# Each run holds its own baseline and F2Load arm, interleaved, so the ratio is
# always within-session.
RUNS = [('as loaded (hardlink clone)', 'ycsb_50g_f2ratio_260909', AS_LOADED),
        ('fresh copy of both DBs', 'ycsb_50g_physcopy_C_full_260910', FRESH_COPY)]

PANELS = [
    ('throughput (ops/s)', 'Throughput'),
    ('avg latency (us)', 'Average latency'),
    ('filter checks / op', 'Filter checks / op'),
    ('filter positive / op', 'Filter positives / op'),
    ('found fraction', 'Found fraction'),
    ('compaction write B / op', 'Compaction write B / op'),
    ('data cache miss / op', 'Data cache miss / op'),
    ('device read p50 (us)', 'Device read latency (p50)'),
]


def ratios():
    """{metric: {letter: [ratio per run]}} as F2Load / baseline within a run."""
    per_run = [(label, cells(run, 'baseline'), cells(run, 'f2load'), colour)
               for label, run, colour in RUNS]
    table = {}
    for key, _ in PANELS:
        row = {}
        for letter in LETTERS:
            values = []
            for _, base, f2 in ((a, b, c) for a, b, c, _ in per_run):
                if letter not in base or letter not in f2:
                    values.append(None); continue
                bv, fv = metrics(base[letter]).get(key), metrics(f2[letter]).get(key)
                values.append(fv / bv if bv and fv is not None else None)
            if any(v is not None for v in values):
                row[letter] = values
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
    axis.tick_params(colors=INK_2, labelsize=FS_TICK, length=0)
    axis.set_xticks(range(len(LETTERS)))
    axis.set_xticklabels([w.upper() for w in LETTERS])


def panel(axis, row, title, tag):
    axis.axhline(1.0, color=IDEAL, linewidth=1.4, zorder=1)
    blank = []
    for position, letter in enumerate(LETTERS):
        values = row.get(letter)
        if not values or all(v is None for v in values):
            blank.append(position); continue
        live = [v for v in values if v is not None]
        if len(live) > 1:
            axis.plot([position, position], [min(live), max(live)], color=INK_2,
                      linewidth=1.4, alpha=0.55, zorder=2, solid_capstyle='round')
        for value, (_, _, colour) in zip(values, RUNS):
            if value is None:
                continue
            axis.plot([position], [value], marker='o', markersize=11, color=colour,
                      markeredgecolor=SURFACE, markeredgewidth=2.0, zorder=3)
    seen = [v for values in row.values() for v in values if v is not None] + [1.0]
    pad = max(max(seen) - min(seen), 0.02) * 0.16
    low, high = min(seen) - pad, max(seen) + pad
    axis.set_ylim(low, high)
    for position in blank:
        axis.text(position, low + (high - low) * 0.06, 'n/a', color=INK_2,
                  fontsize=FS_TICK - 2, ha='center', va='center', alpha=0.7,
                  zorder=2, style='italic')
    style(axis, 'F2Load / baseline')
    axis.set_title('(%s) %s' % (tag, title), color=INK, fontsize=FS_TITLE,
                   loc='left', pad=10)


def summary(axis, table):
    span, gap = 0.72, 0.03
    width = span / len(RUNS) - gap
    means = {}
    for index, (_, _, colour) in enumerate(RUNS):
        xs, ys = [], []
        for position, letter in enumerate(LETTERS):
            drift = [abs(row[letter][index] - 1.0) for row in table.values()
                     if letter in row and row[letter][index] is not None]
            if not drift:
                continue
            xs.append(position - span / 2 + index * (width + gap) + width / 2)
            ys.append(sum(drift) / len(drift) * 100)
            means.setdefault(letter, []).append(ys[-1])
        axis.bar(xs, ys, width=width, linewidth=0, color=colour)
    style(axis, 'Mean |deviation| (%)')
    axis.set_ylim(0)
    axis.set_title('(i) Overall drift from the baseline', color=INK,
                   fontsize=FS_TITLE, loc='left', pad=10)
    return means


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out')
    args = parser.parse_args()
    table = ratios()

    figure, axes = plt.subplots(3, 3, figsize=(17.4, 12.6), facecolor=SURFACE)
    figure.subplots_adjust(wspace=0.26, hspace=0.34, bottom=0.10, top=0.92)
    flat = axes.ravel()
    for index, (key, title) in enumerate(PANELS):
        panel(flat[index], table[key], title, 'abcdefgh'[index])
        flat[index].set_xlabel('YCSB workload', color=INK_2, fontsize=FS_LABEL)
    means = summary(flat[8], table)
    flat[8].set_xlabel('YCSB workload', color=INK_2, fontsize=FS_LABEL)

    handles = [plt.Line2D([], [], marker='o', markersize=11, linestyle='none',
                          color=colour, markeredgecolor=SURFACE, markeredgewidth=2.0,
                          label=label) for label, _, colour in RUNS]
    handles.append(plt.Line2D([], [], color=IDEAL, linewidth=1.4,
                              label='Baseline (perfect fidelity)'))
    legend = figure.legend(handles=handles, frameon=False, fontsize=FS_LEGEND,
                           ncol=3, loc='lower center', bbox_to_anchor=(0.5, 0.005),
                           handlelength=1.6, columnspacing=2.4)
    for text in legend.get_texts():
        text.set_color(INK_2)
    figure.suptitle('F2Load state fidelity against a naturally accumulated baseline, '
                    'read as loaded and from fresh copies',
                    color=INK, fontsize=FS_TITLE + 3, x=0.5, y=0.975)

    out = Path(args.out) if args.out else (EXP / 'results/f2load_fidelity_fair_260910.png')
    figure.savefig(out, dpi=170, facecolor=SURFACE, bbox_inches='tight')
    print('wrote', out)

    print('\n%-26s%s' % ('metric', ''.join('%18s' % w.upper() for w in LETTERS)))
    for key, _ in PANELS:
        line = '%-26s' % key
        for letter in LETTERS:
            v = table[key].get(letter)
            line += '%18s' % ('-' if not v or v[0] is None or v[1] is None
                              else '%.3f>%.3f' % (v[0], v[1]))
        print(line)
    print('\n%-26s' % 'mean |deviation| %'
          + ''.join('%18s' % ('%.2f>%.2f' % tuple(means[w])) for w in LETTERS if w in means))


if __name__ == '__main__':
    main()
