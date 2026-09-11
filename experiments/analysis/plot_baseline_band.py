#!/usr/bin/env python3
"""Run-to-run variation of conventional loading, one dot per load.

Ten independent 1 TB loads of the same input (seed fixed, so the key set is a
constant and the spread is the loading process itself), each read with YCSB A-F
from a fresh single-stream byte copy with the page cache dropped before every
cell. F2Load loads are overlaid when their campaigns exist.

Panel (a) is the measured throughput on a log axis, because the workloads span
25x and a shared linear axis would flatten five of them. Panel (b) divides each
workload by its own baseline mean, which is the only way to compare spreads
across workloads on one scale.
"""
import argparse
import glob
import json
from pathlib import Path
import statistics as st

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

EXP = Path(__file__).resolve().parents[1]
RUNS = EXP / 'artifacts/log_runs'
LETTERS = list('abcdef')
ORDER = ['n01', 'n02', 'n03', 'n04', 'r01', 'r02', 'run3', 'b01', 'b02', 'b03']

# f01 is excluded because unrelated work ran on the machine during its
# campaign, which the experimenter reported and the evidence matches: kernel
# time in its workload C cell was 28.3% of all cores against 10.2% in every
# other F2Load campaign, and its device read p99 was 132/137/155 us in the
# C/D/E cells against 109-110 everywhere else, with per-operation work
# unchanged. The whole campaign is dropped rather than the three cells that
# look worst, since picking cells after seeing them is its own bias.
EXCLUDE = {'f01': 'unrelated load on the machine during the campaign'}

SURFACE, INK, INK_2, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#d8d7d2'
BASE, F2, BANDC, MEANC = '#2a78d6', '#eb6834', '#dce7f6', '#e34948'
# Panel (b) is clipped here so one disturbed cell cannot flatten the other five.
FLOOR = 0.93
FS_TITLE, FS_LABEL, FS_TICK, FS_LEGEND = 17, 15, 14, 15


def arm(run, system):
    path = RUNS / run / 'results.json'
    if not path.is_file():
        return {}
    return {r['workload'][-1]: r['throughput_ops_sec'] / 1e3
            for r in json.loads(path.read_text())
            if r.get('phase') == 'full' and r.get('status') == 'ok'
            and r['system'] == system}


def collect():
    base = {}
    for tag in ORDER:
        cells = arm('ycsb_band_%s_260910' % tag, 'baseline')
        if len(cells) == 6:
            base[tag] = cells
    f2 = {}
    for path in sorted(glob.glob(str(RUNS / 'ycsb_f2band_*_260911' / 'results.json'))):
        tag = Path(path).parent.name.split('_')[2]
        if tag in EXCLUDE:
            print('excluded %s: %s' % (tag, EXCLUDE[tag]))
            continue
        cells = arm(Path(path).parent.name, 'f2load')
        if len(cells) == 6:
            f2[tag] = cells
    extra = arm('ycsb_band_f2_260910', 'f2load')
    if len(extra) == 6:
        f2['copy'] = extra
    return base, f2


def spread(axis, xs, ys, colour, marker, size):
    axis.plot(xs, ys, marker=marker, markersize=size, linestyle='none', color=colour,
              markeredgecolor=SURFACE, markeredgewidth=1.5, zorder=4, alpha=0.95)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out')
    args = parser.parse_args()
    base, f2 = collect()
    if not base:
        raise SystemExit('no band campaigns found')
    tags = [t for t in ORDER if t in base]
    means = {w: st.mean(base[t][w] for t in tags) for w in LETTERS}

    figure, axes = plt.subplots(1, 2, figsize=(18, 6.8), facecolor=SURFACE)
    figure.subplots_adjust(wspace=0.22, bottom=0.24, top=0.80)

    for panel, axis in enumerate(axes):
        axis.set_facecolor(SURFACE)
        for position, w in enumerate(LETTERS):
            vals = [base[t][w] for t in tags]
            norm = (lambda v: v) if panel == 0 else (lambda v: v / means[w])
            lo, hi = min(vals), max(vals)
            axis.add_patch(plt.Rectangle((position - 0.30, norm(lo)), 0.60,
                                         norm(hi) - norm(lo), facecolor=BANDC,
                                         edgecolor='none', zorder=1))
            axis.plot([position - 0.32, position + 0.32], [norm(means[w])] * 2,
                      color=MEANC, linewidth=1.4, zorder=2)
            offs = [position - 0.22 + 0.44 * i / (len(vals) - 1) for i in range(len(vals))]
            spread(axis, offs, [norm(v) for v in vals], BASE, 'o', 9)
            tags2 = sorted(f2)
            for i, tag in enumerate(tags2):
                x = position + (0 if len(tags2) == 1 else
                                -0.13 + 0.26 * i / (len(tags2) - 1))
                y = norm(f2[tag][w])
                if panel == 1 and y < FLOOR:
                    # Off the clipped axis: mark it at the floor and say so,
                    # rather than letting one point set the scale for all six.
                    axis.plot([x], [FLOOR + 0.004], marker='v', markersize=12,
                              color=F2, markeredgecolor=SURFACE, markeredgewidth=1.5,
                              zorder=5, clip_on=False)
                    axis.text(x, FLOOR + 0.012, '%s %.2f' % (tag, y), ha='center',
                              va='bottom', color=F2, fontsize=FS_TICK - 3, rotation=90)
                else:
                    spread(axis, [x], [y], F2, 'D', 12)
        axis.set_xticks(range(len(LETTERS)))
        axis.set_xticklabels([w.upper() for w in LETTERS], fontsize=FS_LABEL, color=INK)
        axis.set_xlim(-0.6, len(LETTERS) - 0.4)
        axis.set_xlabel('YCSB workload', color=INK_2, fontsize=FS_LABEL)
        if panel == 0:
            axis.set_yscale('log')
            axis.set_ylabel('Throughput (K ops/s, log)', color=INK_2, fontsize=FS_LABEL)
            axis.set_title('(a) Measured throughput', color=INK, fontsize=FS_TITLE,
                           loc='left', pad=10)
            axis.yaxis.grid(True, which='minor', color=GRID, linewidth=0.3, alpha=0.6)
        else:
            axis.set_ylabel('relative to the %d-load mean' % len(tags),
                            color=INK_2, fontsize=FS_LABEL)
            axis.set_ylim(FLOOR, None)
            axis.set_title('(b) Same data, each workload against its own mean',
                           color=INK, fontsize=FS_TITLE, loc='left', pad=10)
            for position, w in enumerate(LETTERS):
                vals = [base[t][w] for t in tags]
                axis.text(position, max(vals) / means[w] + 0.004,
                          '%.1f%%' % (100 * (max(vals) - min(vals)) / means[w]),
                          ha='center', va='bottom', color=INK_2, fontsize=FS_TICK - 1)
            # Name the load at each end so an outlier is identifiable.
            for position, w in enumerate(LETTERS):
                vals = {t: base[t][w] for t in tags}
                lo = min(vals, key=vals.get); hi = max(vals, key=vals.get)
                axis.text(position - 0.34, vals[lo] / means[w], lo + ' ', ha='right',
                          va='center', color=INK_2, fontsize=FS_TICK - 3)
                axis.text(position + 0.34, vals[hi] / means[w], ' ' + hi, ha='left',
                          va='center', color=INK_2, fontsize=FS_TICK - 3)
        axis.yaxis.grid(True, color=GRID, linewidth=0.5)
        axis.set_axisbelow(True)
        for edge in ('top', 'right', 'left'):
            axis.spines[edge].set_visible(False)
        axis.spines['bottom'].set_color(GRID)
        axis.tick_params(colors=INK_2, labelsize=FS_TICK, length=0)

    handles = [plt.Line2D([], [], marker='o', markersize=9, linestyle='none', color=BASE,
                          markeredgecolor=SURFACE, markeredgewidth=1.5,
                          label='one conventional load (%d)' % len(tags)),
               plt.Rectangle((0, 0), 1, 1, facecolor=BANDC, edgecolor='none',
                             label='range the %d loads span' % len(tags)),
               plt.Line2D([], [], color=MEANC, linewidth=1.4, label='their mean')]
    if f2:
        handles.append(plt.Line2D([], [], marker='D', markersize=12, linestyle='none',
                                  color=F2, markeredgecolor=SURFACE, markeredgewidth=1.5,
                                  label='F2Load (%d)' % len(f2)))
    legend = figure.legend(handles=handles, frameon=False, fontsize=FS_LEGEND,
                           ncol=len(handles), loc='lower center', bbox_to_anchor=(0.5, 0.01))
    for t in legend.get_texts():
        t.set_color(INK_2)
    figure.suptitle('Run-to-run variation of conventional loading: %d independent 1 TB '
                    'loads of the same input, read from fresh copies' % len(tags),
                    color=INK, fontsize=FS_TITLE + 2, x=0.5, y=0.93)

    out = Path(args.out) if args.out else EXP / 'results/baseline_band10_260910.png'
    figure.savefig(out, dpi=170, facecolor=SURFACE, bbox_inches='tight')
    print('wrote', out)
    print('\n%-7s' % 'wl' + ''.join('%9s' % t for t in tags) + '%9s %8s' % ('range%', 'CV%'))
    for w in LETTERS:
        vals = [base[t][w] for t in tags]
        print('%-7s' % w.upper() + ''.join('%9.1f' % base[t][w] for t in tags)
              + '%8.2f%% %7.2f%%' % (100 * (max(vals) - min(vals)) / means[w],
                                     100 * st.stdev(vals) / means[w]))
    for tag, cells in sorted(f2.items()):
        print('%-7s' % ('F2:' + tag) + ''.join('%9s' % '' for _ in tags[:-1])
              + '%9.1f' % 0 if False else '')
    if f2:
        print('\nF2Load:')
        for tag, cells in sorted(f2.items()):
            print('  %-6s' % tag + ''.join('%9.1f' % cells[w] for w in LETTERS))


if __name__ == '__main__':
    main()
