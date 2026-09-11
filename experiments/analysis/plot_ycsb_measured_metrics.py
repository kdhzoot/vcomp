#!/usr/bin/env python3
"""Measured YCSB A-F fidelity metrics for one campaign, as run.

Absolute values, not ratios: each panel is one metric, the x axis carries the
six workloads, and the two systems sit side by side within each workload.
Counters are per operation, because a cell runs for fixed wall time and every
absolute counter scales with how many operations the arm got through.

Grouped bars, one pair per workload, every axis zero-based so bar length is
proportional to the value. Some metrics span more than an order of magnitude
across workloads (data cache misses run 0.15 to 9.0 per operation), so the
small workloads have short bars; the printed table carries the exact numbers.
"""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_f2load_fidelity import cells, metrics

EXP = Path(__file__).resolve().parents[1]
LETTERS = list('abcdef')

SURFACE, INK, INK_2, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#d8d7d2'
IDEAL = '#e34948'
COLOR = {'baseline': '#2a78d6', 'f2load': '#eb6834'}
FS_TITLE, FS_LABEL, FS_TICK, FS_LEGEND = 16, 14, 13, 16

# (panel title, accessor over the parsed row, unit label). Most are per
# operation because a cell runs for fixed wall time, so a faster arm performs
# more operations and every absolute counter scales with it. Compaction is the
# exception: it is reported as the total the cell actually wrote.
#
# Two metrics were dropped from this figure on purpose. Bloom filter positives
# track the found fraction one-for-one, and engine bytes read is exactly
# found_fraction * value_size (602.9 / 0.6029 = 1000.0 B), so neither adds
# anything the found-fraction panel does not already carry.
GIB = 1073741824.0


def device_bytes(row, field):
    """Sectors the array actually moved during the cell, from /proc/diskstats.

    RocksDB's own tickers count engine-level bytes (compaction, flush, the
    values Get returned) and miss everything the block cache and the readahead
    do underneath. The runner snapshots /proc/diskstats around each cell and
    nothing else touches the array while a cell runs, so the md0 delta is the
    device traffic the workload caused. Field 5 is sectors read and field 9
    sectors written, 512 B each.
    """
    raw = Path(row['log_dir']) / 'raw'
    def read(name):
        for line in (raw / name).read_text().splitlines():
            parts = line.split()
            if len(parts) > 9 and parts[2] == 'md0':
                return int(parts[5]), int(parts[9])
        return None
    try:
        start, end = read('diskstats.start'), read('diskstats.end')
    except OSError:
        return None
    if start is None or end is None:
        return None
    return (end[field] - start[field]) * 512.0 / GIB

PANELS = [
    ('Throughput', lambda r, m: r['throughput_ops_sec'] / 1e3, 'K ops/s'),
    ('Average latency', lambda r, m: r['avg_latency_us'], 'µs'),
    ('Device read latency p50', lambda r, m: m.get('device read p50 (us)'), 'µs'),
    ('Bloom filter checks', lambda r, m: m.get('filter checks / op'), 'per operation'),
    ('Found fraction', lambda r, m: m.get('found fraction'), 'fraction of gets'),
    ('Data block cache misses', lambda r, m: m.get('data cache miss / op'), 'per operation'),
    ('Compaction bytes written', lambda r, m: r.get('compaction_write_bytes', 0) / GIB,
     'GiB written in the cell'),
    ('Device bytes read', lambda r, m: device_bytes(r, 0), 'GiB read in the cell'),
    ('Device bytes written', lambda r, m: device_bytes(r, 1), 'GiB written in the cell'),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', default='ycsb_50g_physcopy_C_full_260910')
    parser.add_argument('--normalize', action='store_true',
                        help='plot each metric as percent difference from the '
                             'baseline arm instead of the measured value')
    parser.add_argument('--out')
    args = parser.parse_args()
    arms = {s: cells(args.run_id, s) for s in ('baseline', 'f2load')}

    figure, axes = plt.subplots(3, 3, figsize=(18.5, 13.4), facecolor=SURFACE)
    figure.subplots_adjust(wspace=0.26, hspace=0.42, bottom=0.11, top=0.89)
    for spare in range(len(PANELS), axes.size):
        axes.ravel()[spare].set_visible(False)

    for index, (title, accessor, unit) in enumerate(PANELS):
        axis = axes.ravel()[index]
        axis.set_facecolor(SURFACE)
        seen, missing = [], []
        width = 0.38
        for position, letter in enumerate(LETTERS):
            pair = []
            for system in ('baseline', 'f2load'):
                row = arms[system].get(letter)
                value = accessor(row, metrics(row)) if row else None
                pair.append(value)
            if args.normalize:
                # Each metric divided by the baseline arm, so the baseline is
                # 1.0 everywhere and F2Load is its ratio to it.
                base, f2 = pair
                if base in (None, 0) or f2 is None:
                    missing.append(position)
                    continue
                pair = [1.0, f2 / base]
            elif all(v is None for v in pair):
                missing.append(position)
                continue
            seen.extend(v for v in pair if v is not None)
            for offset, (system, value) in zip((-width / 2 - 0.01, width / 2 + 0.01),
                                               zip(('baseline', 'f2load'), pair)):
                if value is None:
                    continue
                axis.bar([position + offset], [value], width=width,
                         color=COLOR[system], linewidth=0, zorder=2)
        top = max(seen) if seen else 1.0
        axis.set_ylim(0, top * 1.16)
        if args.normalize:
            axis.axhline(1.0, color=IDEAL, linewidth=1.2, zorder=3)
        for position in missing:
            axis.text(position, top * 0.03, 'n/a', color=INK_2, fontsize=FS_TICK - 1,
                      ha='center', va='bottom', alpha=0.7, style='italic')
        axis.set_xlim(-0.6, len(LETTERS) - 0.4)
        axis.set_xticks(range(len(LETTERS)))
        axis.set_xticklabels([w.upper() for w in LETTERS], fontsize=FS_LABEL, color=INK)
        axis.set_xlabel('YCSB workload', color=INK_2, fontsize=FS_LABEL)
        axis.set_ylabel('relative to baseline' if args.normalize else unit,
                        color=INK_2, fontsize=FS_LABEL)
        axis.yaxis.grid(True, color=GRID, linewidth=0.5)
        axis.set_axisbelow(True)
        for edge in ('top', 'right', 'left'):
            axis.spines[edge].set_visible(False)
        axis.spines['bottom'].set_color(GRID)
        axis.tick_params(colors=INK_2, labelsize=FS_TICK, length=0)
        axis.set_title('(%s) %s' % ('abcdefghi'[index], title),
                       color=INK, fontsize=FS_TITLE, loc='left', pad=10)

    handles = [plt.Rectangle((0, 0), 1, 1, color=COLOR[s], label=n)
               for s, n in (('baseline', 'Baseline (conventional loading)'),
                            ('f2load', 'F2Load'))]
    if args.normalize:
        handles.append(plt.Line2D([], [], color=IDEAL, linewidth=1.2,
                                  label='Baseline = 1.0'))
    legend = figure.legend(handles=handles, frameon=False, fontsize=FS_LEGEND, ncol=2,
                           loc='lower center', bbox_to_anchor=(0.5, 0.01), handlelength=1.4,
                           columnspacing=2.6)
    for text in legend.get_texts():
        text.set_color(INK_2)
    figure.suptitle(('YCSB A-F normalised to the baseline arm: ' if args.normalize else
                     'Measured YCSB A-F: ') +
                    '1 TB dataset, 50 GiB block cache, 48 threads, '
                    '300 s per cell, fresh copy of both DBs', color=INK,
                    fontsize=FS_TITLE + 3, x=0.5, y=0.955)

    out = Path(args.out) if args.out else (
        EXP / 'results' / ('ycsb_%s_metrics_' % ('normalised' if args.normalize
                                                 else 'measured') + args.run_id + '.png'))
    figure.savefig(out, dpi=170, facecolor=SURFACE, bbox_inches='tight')
    print('wrote', out)

    width = 15
    print('\n%-32s%s' % ('metric', ''.join('%*s' % (width, w.upper()) for w in LETTERS)))
    for title, accessor, unit in PANELS:
        for system in ('baseline', 'f2load'):
            line = '%-32s' % ((title if system == 'baseline' else '') + ' ' + system[:4])
            for letter in LETTERS:
                row = arms[system].get(letter)
                v = accessor(row, metrics(row)) if row else None
                line += '%*s' % (width, '-' if v is None else
                                 ('{:,.0f}'.format(v) if abs(v) >= 1000 else '{:.4f}'.format(v)))
            print(line)


if __name__ == '__main__':
    main()
