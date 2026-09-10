#!/usr/bin/env python3
"""YCSB throughput across identically configured baseline loads.

Left: the measured values, grouped by workload. Throughput spans more than an
order of magnitude across workloads, so that panel uses a logarithmic axis.
Right: the same numbers indexed to the mean of the loads measured at the same
disk fill, which is what makes the between-load difference legible - one axis
per panel, never two scales on one plot.
"""
import json
from pathlib import Path
import statistics as st

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

EXP = Path(__file__).resolve().parents[1]
RUNS = EXP / 'artifacts/log_runs'

SURFACE = '#fcfcfb'
INK = '#0b0b0b'
INK_2 = '#52514e'
GRID = '#d8d7d2'
SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#4a3aa7', '#008300']
LETTERS = ['a', 'b', 'c', 'd', 'e', 'f']

# (label, run id) in measurement order. The final entry re-measured the same
# n04 database after the campaign freed 36 TB, so it isolates the disk effect.
LOADS = [
    ('n01', 'baseline_repeat_ycsb_all_260908_night_n01'),
    ('n02', 'baseline_repeat_ycsb_all_260908_night_n02'),
    ('n03', 'baseline_repeat_ycsb_all_260908_night_n03'),
    ('n04', 'baseline_repeat_ycsb_all_260908_night_n04'),
    # F2Load before and after the dedup-ratio fix. Each session measured its
    # own baseline arm on run3's DB with the workloads interleaved between the
    # two systems, so the two F2Load columns are directly comparable.
    ('F2 before', 'ycsb_50g_f2load_baseline_260909'),
    ('F2 after', 'ycsb_50g_f2ratio_260909'),
]
# Those sessions hold two systems in one results file, so each label says which.
SYSTEM_OF = {'F2 before': 'f2load', 'F2 after': 'f2load'}
# Workload A responds to disk fill even at 92%, and F collapses at 94%, so those
# cells are taken from re-measurements made once the array was 58% full. B-E are
# insensitive (-4.5% to +3.1% on n01 across the two conditions) and stay as
# first measured. Every override used the same DB, binary and configuration.
OVERRIDE = {
    'n01': ('baseline_repeat_ycsb_all_260909_n01_all', 'abcdef'),
    'n02': ('baseline_repeat_ycsb_all_260909_n02_a', 'a'),
    'n03': ('baseline_repeat_ycsb_all_260909_n03_a', 'a'),
    'n04': ('baseline_repeat_ycsb_all_260909_n04_af', 'af'),
}
BASE = ('n01', 'n02', 'n03', 'n04')


def cells(run_id, keep=None, system=None):
    path = RUNS / run_id / 'results.json'
    if not path.is_file():
        return {}
    out = {}
    for row in json.loads(path.read_text()):
        letter = row['workload'][-1]
        if row.get('phase') != 'full':
            continue
        if system is not None and row.get('system') != system:
            continue
        if keep is None or letter in keep:
            out[letter] = row['throughput_ops_sec']
    return out


def throughput():
    table = {}
    for label, run_id in LOADS:
        merged = cells(run_id, system=SYSTEM_OF.get(label))
        if label in OVERRIDE:
            run, keep = OVERRIDE[label]
            merged.update(cells(run, keep))
        if merged:
            table[label] = merged
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


def grouped(axis, table, labels, transform):
    span, gap = 0.80, 0.02
    width = span / len(labels) - gap
    for index, label in enumerate(labels):
        xs, ys = [], []
        for position, letter in enumerate(LETTERS):
            if letter not in table.get(label, {}):
                continue
            xs.append(position - span / 2 + index * (width + gap) + width / 2)
            ys.append(transform(letter, table[label][letter]))
        axis.bar(xs, ys, width=width, color=SERIES[index], linewidth=0, label=label)
    axis.set_xticks(range(len(LETTERS)))
    axis.set_xticklabels([w.upper() for w in LETTERS])


def main():
    table = throughput()
    labels = [label for label, _ in LOADS if label in table]
    base = {letter: st.mean([table[b][letter] for b in BASE]) for letter in LETTERS}

    figure, axes = plt.subplots(1, 2, figsize=(12.4, 3.9), facecolor=SURFACE)
    figure.subplots_adjust(wspace=0.22, bottom=0.26, top=0.84)

    axis = axes[0]
    grouped(axis, table, labels, lambda letter, value: value)
    axis.set_yscale('log')
    # E runs at ~96K ops/s, so the lower bound must sit below 1e5.
    axis.set_ylim(5e4, 5e6)
    style(axis, 'Throughput (ops/s, log)')
    axis.set_title('(a) Measured throughput', color=INK, fontsize=10, loc='left')

    axis = axes[1]
    # A band showing where the four baseline repeats themselves land, so the
    # F2Load columns can be read against real run-to-run spread.
    lo = min(min(table[b][w] for b in BASE) / base[w] for w in LETTERS)
    hi = max(max(table[b][w] for b in BASE) / base[w] for w in LETTERS)
    axis.axhspan(lo, hi, color='#d8d7d2', alpha=0.55, linewidth=0,
                 label='baseline spread')
    grouped(axis, table, labels, lambda letter, value: value / base[letter])
    axis.axhline(1.0, color='#e34948', linewidth=1.0)
    axis.set_ylim(0, 1.35)
    style(axis, 'Relative to the baseline mean')
    axis.set_title('(b) Indexed to the baseline repeat mean', color=INK, fontsize=10,
                   loc='left')

    handles, texts = axes[0].get_legend_handles_labels()
    legend = figure.legend(handles, texts, frameon=False, fontsize=8,
                           ncol=len(labels), loc='lower center',
                           bbox_to_anchor=(0.5, -0.02), handlelength=1.0,
                           columnspacing=1.6)
    for text in legend.get_texts():
        text.set_color(INK_2)

    out = EXP / 'results/ycsb_across_loads_260909.png'
    figure.savefig(out, dpi=200, facecolor=SURFACE, bbox_inches='tight')
    print('wrote', out)

    header = '%-5s' % 'wl' + ''.join('%12s' % l for l in labels) + '%12s' % 'spread*'
    print('\n' + header)
    for letter in LETTERS:
        row = '%-5s' % letter.upper()
        for label in labels:
            value = table[label].get(letter)
            row += '%12s' % ('-' if value is None else '{:,.0f}'.format(value))
        clean = [table[b][letter] for b in BASE]
        row += '%11.1f%%' % ((max(clean) / min(clean) - 1) * 100)
        print(row)
    print('* max/min - 1 over the four loads. A (all four) and F (n04) come '
          'from the 58%-fill re-measurements.')


if __name__ == '__main__':
    main()
