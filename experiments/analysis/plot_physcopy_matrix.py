#!/usr/bin/env python3
"""The 2x2 byte-copy control: which arm's flash placement carries the gap.

Four YCSB A-F campaigns differ only in which DB each arm read from -- the DB
as the loader left it, or a byte-for-byte copy written as one sequential
stream. Every campaign interleaves its two arms in one session, and the runner
drops the page cache before each cell, so a column is a fair within-session
comparison. Both arms read with O_DIRECT throughout.
"""
import json
from pathlib import Path
import statistics as st

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

EXP = Path(__file__).resolve().parents[1]
RUNS = EXP / 'artifacts/log_runs'
LETTERS = list('abcdef')

SURFACE, INK, INK_2, GRID, IDEAL = '#fcfcfb', '#0b0b0b', '#52514e', '#d8d7d2', '#e34948'
SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100']
SYS = {'baseline': '#2a78d6', 'f2load': '#eb6834'}
FS_TITLE, FS_LABEL, FS_TICK, FS_LEGEND = 17, 15, 14, 15

# (label, run id, baseline arm copied?, f2load arm copied?)
SESSIONS = [
    ('neither (as loaded)', 'ycsb_50g_f2ratio_260909', False, False),
    ('baseline copied', 'ycsb_50g_physcopy_A_full_260910', True, False),
    ('F2Load copied', 'ycsb_50g_physcopy_B_full_260910', False, True),
    ('both copied', 'ycsb_50g_physcopy_C_full_260910', True, True),
]


def cells(run):
    out = {}
    for r in json.loads((RUNS / run / 'results.json').read_text()):
        if r.get('phase') == 'full' and r.get('status') == 'ok':
            hist = (r.get('engine_histograms') or {}).get('rocksdb.sst.read.micros') or {}
            out[(r['system'], r['workload'][-1])] = (r['throughput_ops_sec'], hist.get('p99'))
    return out


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
    axis.set_xticklabels([w.upper() for w in LETTERS], fontsize=FS_LABEL, color=INK)
    axis.set_xlabel('YCSB workload', color=INK_2, fontsize=FS_LABEL)


def main():
    data = {label: cells(run) for label, run, _, _ in SESSIONS}
    # Each (system, treatment) combination was measured in two sessions.
    pool = {}
    for system in ('baseline', 'f2load'):
        for copied in (False, True):
            runs = [lab for lab, _, b, f in SESSIONS
                    if (b if system == 'baseline' else f) == copied]
            for w in LETTERS:
                pool[(system, copied, w)] = st.mean(data[r][(system, w)][0] for r in runs)

    figure, axes = plt.subplots(1, 3, figsize=(21, 6.8), facecolor=SURFACE)
    figure.subplots_adjust(wspace=0.30, bottom=0.26, top=0.80)

    # (a) the ratio, per workload, in each of the four sessions
    axis = axes[0]
    axis.axhline(1.0, color=IDEAL, linewidth=1.4, zorder=1)
    span, gap = 0.82, 0.02
    width = span / len(SESSIONS) - gap
    for i, (label, _, _, _) in enumerate(SESSIONS):
        xs, ys = [], []
        for p, w in enumerate(LETTERS):
            cell = data[label]
            xs.append(p - span / 2 + i * (width + gap) + width / 2)
            ys.append(cell[('f2load', w)][0] / cell[('baseline', w)][0])
        axis.bar(xs, ys, width=width, color=SERIES[i], linewidth=0, label=label, zorder=2)
    axis.set_ylim(0.9, 1.2)
    style(axis, 'F2Load / baseline throughput')
    axis.set_title('(a) The measured gap',
                   color=INK, fontsize=FS_TITLE, loc='left', pad=10)
    legend = axis.legend(frameon=False, fontsize=FS_LEGEND - 2, ncol=2, loc='lower center',
                         bbox_to_anchor=(0.5, -0.40), title='byte-copied arm',
                         handlelength=1.1, columnspacing=1.4)
    legend.get_title().set_color(INK_2)
    legend.get_title().set_fontsize(FS_LEGEND - 2)
    for t in legend.get_texts():
        t.set_color(INK_2)

    # (b) what the copy does to each system on its own
    axis = axes[1]
    axis.axhline(0, color=GRID, linewidth=1.0)
    width2 = 0.36
    for i, system in enumerate(('baseline', 'f2load')):
        xs = [p - width2 / 2 - 0.01 + i * (width2 + 0.02) for p in range(len(LETTERS))]
        ys = [100 * (pool[(system, True, w)] / pool[(system, False, w)] - 1) for w in LETTERS]
        axis.bar(xs, ys, width=width2, color=SYS[system], linewidth=0,
                 label={'baseline': 'Baseline', 'f2load': 'F2Load'}[system])
        for x, y in zip(xs, ys):
            axis.text(x, y + (0.6 if y >= 0 else -0.6), '%+.1f' % y, ha='center',
                      va='bottom' if y >= 0 else 'top', color=INK_2, fontsize=FS_TICK - 2)
    style(axis, 'Throughput change from copying (%)')
    axis.set_title('(b) Effect of copying, per system',
                   color=INK, fontsize=FS_TITLE, loc='left', pad=10)
    legend = axis.legend(frameon=False, fontsize=FS_LEGEND, loc='upper left', handlelength=1.1)
    for t in legend.get_texts():
        t.set_color(INK_2)

    # (c) the device tail that explains it
    axis = axes[2]
    for i, system in enumerate(('baseline', 'f2load')):
        for copied, marker, size in ((False, 'o', 11), (True, 'D', 9)):
            runs = [lab for lab, _, b, f in SESSIONS
                    if (b if system == 'baseline' else f) == copied]
            ys = [st.mean(data[r][(system, w)][1] for r in runs) for w in LETTERS]
            axis.plot(range(len(LETTERS)), ys, marker=marker, markersize=size,
                      color=SYS[system], linewidth=1.6,
                      linestyle='-' if not copied else (0, (4, 2)),
                      markeredgecolor=SURFACE, markeredgewidth=1.6,
                      label='%s, %s' % ({'baseline': 'Baseline', 'f2load': 'F2Load'}[system],
                                        'byte copy' if copied else 'as loaded'))
    style(axis, 'Device read latency p99 (µs)')
    axis.set_title('(c) Device read tail',
                   color=INK, fontsize=FS_TITLE, loc='left', pad=10)
    axis.set_ylim(0, axis.get_ylim()[1] * 1.18)
    legend = axis.legend(frameon=False, fontsize=FS_LEGEND - 3, loc='upper center',
                         ncol=2, handlelength=2.2, columnspacing=1.2)
    for t in legend.get_texts():
        t.set_color(INK_2)

    figure.suptitle('Byte-copying each 1 TB DB as one sequential stream: YCSB A-F, '
                    '50 GiB cache, arms interleaved within each session',
                    color=INK, fontsize=FS_TITLE + 2, x=0.5, y=0.97)
    out = EXP / 'results/physcopy_matrix_260910.png'
    figure.savefig(out, dpi=170, facecolor=SURFACE, bbox_inches='tight')
    print('wrote', out)

    print('\n%-3s %11s %11s %11s %11s' % ('wl', 'neither', 'base copied', 'f2 copied', 'both'))
    acc = {lab: 0.0 for lab, _, _, _ in SESSIONS}
    for w in LETTERS:
        row = '%-3s' % w.upper()
        for lab, _, _, _ in SESSIONS:
            r = data[lab][('f2load', w)][0] / data[lab][('baseline', w)][0]
            acc[lab] += abs(r - 1) * 100
            row += '%11.3f' % r
        print(row)
    print('%-3s' % 'mean' + ''.join('%10.2f%%' % (acc[lab] / 6) for lab, _, _, _ in SESSIONS))


if __name__ == '__main__':
    main()
