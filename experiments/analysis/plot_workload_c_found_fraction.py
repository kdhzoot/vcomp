#!/usr/bin/env python3
"""Why workload C's found fraction differs, from the read distribution itself.

C is the only YCSB cell with no writes, so it is the only one whose found
fraction reports the loaded key set rather than keys the benchmark inserted.
Its reads use db_bench's scrambled zipfian: rank r is drawn with weight
(r+1)^-0.99 / 26.469 over 10^10 ranks and read at key FNVHash64(r) % keyspace.
That puts 3.78% of every read on ONE key and 16% on the top hundred, so the
statistic is decided by whether a handful of specific integers happen to exist
- not by how many keys the loader produced.
"""
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

EXP = Path(__file__).resolve().parents[1]
SCRATCH = Path('/tmp/claude-1000/-home-smrc-virtual-compaction/'
               'bbb1d9b6-1476-4d33-815f-5e42bedf27f2/scratchpad')
RUNS = EXP / 'artifacts/log_runs'

# The confirming experiment: workload C rerun with uniform reads, everything
# else identical (same DBs, same fresh copies, same cache, same 300 s).
MEASURED = [('zipfian\n(as shipped)', 'ycsb_50g_physcopy_C_full_260910'),
            ('uniform\n(control)', 'ycsb_50g_physcopy_Cuni_260910')]

SURFACE, INK, INK_2, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#d8d7d2'
BASE, F2, IDEAL = '#2a78d6', '#eb6834', '#e34948'
FS_TITLE, FS_LABEL, FS_TICK, FS_LEGEND = 17, 15, 14, 15

SERIES = [('Baseline (conventional)', 'zipfcum_2.csv', BASE, 0.6029),
          ('F2Load', 'zipfcum_3.csv', F2, 0.6689)]
DENSITY = 0.6321          # 1 - 1/e, the key density both loads actually have
TAIL_MASS = 1.0 - 0.5123  # ranks beyond the 200k enumerated here


def load(name):
    with (SCRATCH / name).open() as handle:
        rows = [(int(r['rank']), float(r['cum_weighted_found']), float(r['cum_weight']))
                for r in csv.DictReader(handle)]
    return rows


def main():
    figure, axes = plt.subplots(1, 3, figsize=(21, 6.6), facecolor=SURFACE)
    figure.subplots_adjust(wspace=0.30, bottom=0.26, top=0.82)

    # (a) how the found fraction accumulates as ranks are added
    axis = axes[0]
    for label, name, colour, measured in SERIES:
        rows = load(name)
        # Ranks past the enumerated head contribute at the plain key density.
        xs = [r for r, _, _ in rows]
        ys = [f + (1.0 - w) * DENSITY for _, f, w in rows]
        axis.plot(xs, ys, color=colour, linewidth=2.4, label=label, solid_capstyle='round')
        axis.axhline(measured, color=colour, linewidth=1.1, linestyle=(0, (4, 3)), alpha=0.8)
        axis.text(1.7e5, measured, '%.4f measured ' % measured, color=colour,
                  fontsize=FS_TICK - 1, va='bottom', ha='right')
    axis.axhline(DENSITY, color=IDEAL, linewidth=1.4)
    axis.text(1.7e5, DENSITY, 'key density 0.6321 (both loads) ', color=IDEAL,
              fontsize=FS_TICK - 1, va='bottom', ha='right')
    axis.set_xscale('log')
    axis.set_xlim(1, 2e5)
    axis.set_ylim(0.598, 0.678)
    axis.set_xlabel('zipfian ranks included (log)', color=INK_2, fontsize=FS_LABEL)
    axis.set_ylabel('predicted found fraction', color=INK_2, fontsize=FS_LABEL)
    axis.set_title('(a) The statistic is settled by the first few ranks',
                   color=INK, fontsize=FS_TITLE, loc='left', pad=10)
    legend = axis.legend(frameon=False, fontsize=FS_LEGEND, loc='center left',
                         bbox_to_anchor=(0.02, 0.52))
    for t in legend.get_texts():
        t.set_color(INK_2)

    # (b) the two things the loaders do agree on, and the one they cannot
    axis = axes[1]
    groups = [('Key density\n16.8M-key scan', 0.631958, 0.632205),
              ('Hot positions\nunweighted', 126247 / 200000, 126427 / 200000),
              ('Found fraction\nzipfian-weighted', 0.6029, 0.6689)]
    width = 0.34
    for index, (system, colour) in enumerate(((0, BASE), (1, F2))):
        xs = [p - width / 2 - 0.01 + index * (width + 0.02) for p in range(len(groups))]
        ys = [g[1 + index] for g in groups]
        axis.bar(xs, ys, width=width, color=colour, linewidth=0,
                 label=SERIES[index][0])
        for x, y in zip(xs, ys):
            axis.text(x, y + 0.008, '%.4f' % y, ha='center', va='bottom',
                      color=INK_2, fontsize=FS_TICK)
    axis.axhline(DENSITY, color=IDEAL, linewidth=1.4)
    axis.set_xticks(range(len(groups)))
    axis.set_xticklabels([g[0] for g in groups], fontsize=FS_TICK - 1, color=INK)
    axis.set_ylim(0, 0.78)
    axis.set_ylabel('fraction', color=INK_2, fontsize=FS_LABEL)
    axis.set_title('(b) Same key set by every unweighted measure',
                   color=INK, fontsize=FS_TITLE, loc='left', pad=10)
    axis.yaxis.grid(True, color=GRID, linewidth=0.5)
    axis.set_axisbelow(True)

    # (c) the control: same DBs, same cells, uniform reads instead of zipfian
    axis = axes[2]
    width = 0.34
    for index, (system, colour) in enumerate((('baseline', BASE), ('f2load', F2))):
        xs, ys = [], []
        for position, (_, run) in enumerate(MEASURED):
            row = next(r for r in json.loads((RUNS / run / 'results.json').read_text())
                       if r.get('phase') == 'full' and r.get('status') == 'ok'
                       and r['workload'].endswith('c') and r['system'] == system)
            xs.append(position - width / 2 - 0.01 + index * (width + 0.02))
            ys.append(row['get_found_fraction'])
        axis.bar(xs, ys, width=width, color=colour, linewidth=0,
                 label=SERIES[index][0])
        for x, y in zip(xs, ys):
            axis.text(x, y + 0.008, '%.4f' % y, ha='center', va='bottom',
                      color=INK_2, fontsize=FS_TICK)
    axis.axhline(DENSITY, color=IDEAL, linewidth=1.4)
    axis.text(-0.48, DENSITY + 0.012, 'key density 0.6321', color=IDEAL,
              fontsize=FS_TICK - 1, ha='left')
    axis.set_xticks(range(len(MEASURED)))
    axis.set_xticklabels([n for n, _ in MEASURED], fontsize=FS_TICK, color=INK)
    axis.set_xlabel('read distribution', color=INK_2, fontsize=FS_LABEL)
    axis.set_ylim(0, 0.78)
    axis.set_ylabel('found fraction', color=INK_2, fontsize=FS_LABEL)
    axis.set_title('(c) Uniform reads: the gap disappears',
                   color=INK, fontsize=FS_TITLE, loc='left', pad=10)

    for a in axes:
        a.set_facecolor(SURFACE)
        a.yaxis.grid(True, color=GRID, linewidth=0.5)
        a.set_axisbelow(True)
        for edge in ('top', 'right', 'left'):
            a.spines[edge].set_visible(False)
        a.spines['bottom'].set_color(GRID)
        a.tick_params(colors=INK_2, labelsize=FS_TICK, length=0)

    handles = [plt.Rectangle((0, 0), 1, 1, color=c, label=n)
               for n, _, c, _ in SERIES]
    handles.append(plt.Line2D([], [], color=IDEAL, linewidth=1.4,
                              label='actual key density, 0.6321 in both loads'))
    shared = figure.legend(handles=handles, frameon=False, fontsize=FS_LEGEND,
                           ncol=3, loc='lower center', bbox_to_anchor=(0.5, -0.03))
    for t in shared.get_texts():
        t.set_color(INK_2)
    figure.suptitle("Workload C found fraction: 3.78% of every read lands on one key, "
                    "and the two loads differ on it", color=INK,
                    fontsize=FS_TITLE + 2, x=0.5, y=0.95)
    out = EXP / 'results/workload_c_found_fraction_260910.png'
    figure.savefig(out, dpi=170, facecolor=SURFACE, bbox_inches='tight')
    print('wrote', out)

    for label, name, _, measured in SERIES:
        rows = load(name)
        head_found, head_weight = rows[-1][1], rows[-1][2]
        predicted = head_found + (1 - head_weight) * DENSITY
        print('%-24s top-200k weighted %.4f + tail %.4f = %.4f   measured %.4f'
              % (label, head_found, (1 - head_weight) * DENSITY, predicted, measured))


if __name__ == '__main__':
    main()
