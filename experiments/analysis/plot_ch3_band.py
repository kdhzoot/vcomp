#!/usr/bin/env python3
"""Section 3.2 figure: what ten independent loadings of the same configuration produce.

Left panel is the state the loadings build, right panel is how YCSB behaves on
it. Both axes are the same quantity, percent of the mean across the ten
loadings, so the two panels can be read against each other: the state and the
behaviour it produces both sit in a narrow band, and that band is the tolerance
any fidelity claim has to be read against.

Loading time is deliberately absent. It spans 56.49 to 65.13 minutes, 14.7
percent, while nothing it produces moves more than 1.8 percent; putting it on
this axis would say the run is noisy when the point is that the result is not.
"""
import csv
import glob
import json
from pathlib import Path
import statistics as st

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

EXP = Path(__file__).resolve().parents[1]
LOADS = EXP / 'artifacts/log_loads'
RESULTS = EXP / 'results'
PAPER_FIGS = EXP.parents[1] / 'paper/figs'

# The ten independently loaded 1 TB baselines, keyed by final SST count, which
# is what the physical-copy log records for each arm.
ARMS = {13483: 'n01', 13480: 'n02', 13549: 'n03', 13509: 'n04', 13497: 'r01',
        13522: 'r02', 13461: 'run3', 13460: 'b01', 13496: 'b02', 13520: 'b03'}
STATE_KEYS = [('SSTs', lambda d: d['final_sst_count']),
              ('$L_3$ files', lambda d: d['levels']['3']['files']),
              ('$L_4$ files', lambda d: d['levels']['4']['files']),
              ('$L_5$ files', lambda d: d['levels']['5']['files']),
              ('Bytes', lambda d: d['final_sst_bytes']),
              ('WAF', lambda d: d['waf'])]
LETTERS = list('ABCDEF')

INK = '#111111'
GRID = '#cfcfcf'
BAND = '#c9d8ea'
POINT = '#1f5fa9'
SECOND = '#d1662a'
INK_2 = '#52514e'


def loads():
    out = {}
    for p in glob.glob(str(LOADS / '**/validated.json'), recursive=True):
        d = json.load(open(p))
        if (d.get('system') == 'baseline' and d.get('dataset_gib') == 1000
                and d.get('key_bytes') == 24
                and d.get('final_sst_count') in ARMS):
            d['levels'] = {str(k): v for k, v in d['levels'].items()}
            out[ARMS[d['final_sst_count']]] = d
    return out


def reads():
    out = {}
    for d in sorted(glob.glob(str(RESULTS / 'ycsb_band_*'))):
        p = Path(d) / 'summary.tsv'
        if not p.exists():
            continue
        for r in csv.DictReader(open(p), delimiter='\t'):
            if r['phase'] == 'full' and r['status'] == 'ok' and r['system'] == 'baseline':
                out.setdefault(r['workload'][-1].upper(), []).append(
                    float(r['throughput_ops_sec']))
    return out


def panel(ax, labels, series, title, unit, second=None, fmt='{:,.0f}'):
    """One metric on its own absolute axis.

    Each column holds the ten loadings' measured values. Where they coincide the
    points overlap into one mark, which is the statement the panel makes; the
    number above the column gives the min-to-max spread as a percentage so the
    reader does not have to judge it by eye.
    """
    top = 0
    for j, values in enumerate(series):
        top = max(top, draw(ax, j - (0.17 if second else 0), values, POINT))
    if second:
        for j, values in enumerate(second):
            top = max(top, draw(ax, j + 0.17, values, SECOND, marker='D'))
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=11)
    ax.set_xlim(-0.6, len(labels) - 0.4)
    ax.set_ylim(0, top * 1.22)
    ax.set_title(title, fontsize=12, pad=6)
    ax.set_ylabel(unit, fontsize=11)
    ax.set_xlabel('Workload', fontsize=11)
    ax.grid(True, axis='y', color=GRID, linewidth=0.5, zorder=0)
    ax.set_axisbelow(True)
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    ax.tick_params(labelsize=10)


def draw(ax, x, values, colour, marker='o'):
    if not values:
        return 0
    lo, hi, m = min(values), max(values), st.mean(values)
    ax.plot([x, x], [lo, hi], color=colour, linewidth=1.3, zorder=3,
            solid_capstyle='butt')
    ax.scatter([x] * len(values), values, s=16, color=colour, marker=marker,
               zorder=4, linewidths=0)
    ax.text(x, hi, '  %.1f%%' % (100 * (hi - lo) / m), ha='center', va='bottom',
            fontsize=9, color=INK_2, rotation=90)
    return hi


def cells():
    """Every baseline cell of the band campaigns, keyed by workload letter."""
    out = {}
    for d in sorted(glob.glob(str(RESULTS / 'ycsb_band_*'))):
        p = Path(d) / 'results.json'
        if not p.exists():
            continue
        for r in json.load(open(p)):
            if (r['phase'] == 'full' and r.get('status') == 'ok'
                    and r['system'] == 'baseline'):
                out.setdefault(r['workload'][-1].upper(), []).append(r)
    return out


def p99(r):
    return max([v.get('p99_us', 0)
                for v in (r.get('operation_histograms') or {}).values()] or [0])


def main():
    plt.rcParams.update({'font.family': 'serif', 'mathtext.fontset': 'dejavuserif'})
    C = cells()
    assert all(len(C[w]) == 10 for w in LETTERS), {w: len(C[w]) for w in LETTERS}
    # E scans and inserts, so it issues no point lookups; filter accesses and the
    # found-key ratio measure something else there. D and E trigger almost no
    # compaction inside a 300 s window, so their per-operation compaction is a
    # ratio of near-zero quantities.
    LOOKUP = [w for w in LETTERS if w != 'E']
    WRITE = ['A', 'B', 'F']
    per = lambda w, f: [f(r) for r in C[w]]

    fig, axes = plt.subplots(1, 4, figsize=(11.6, 3.1),
                             gridspec_kw=dict(width_ratios=[1, .88, .88, .66]))
    panel(axes[0], LETTERS,
          [per(w, lambda r: r['throughput_ops_sec'] / 1e6) for w in LETTERS],
          'Throughput', 'M ops/s')
    panel(axes[1], LOOKUP,
          [per(w, lambda r: r['filter_cache_accesses'] / r['operations']) for w in LOOKUP],
          'Filter checks', 'per lookup')
    panel(axes[2], LOOKUP,
          [per(w, lambda r: r['successful_gets'] / r['engine_keys_read']) for w in LOOKUP],
          'Found keys', 'per lookup')
    panel(axes[3], WRITE,
          [per(w, lambda r: r['tickers']['rocksdb.compact.read.bytes'] / r['operations'] / 1024)
           for w in WRITE], 'Compaction I/O', 'KiB per op',
          second=[per(w, lambda r: r['tickers']['rocksdb.compact.write.bytes'] / r['operations'] / 1024)
                  for w in WRITE])
    axes[3].legend(handles=[
        plt.Line2D([], [], marker='o', linestyle='', color=POINT, markersize=6, label='read'),
        plt.Line2D([], [], marker='D', linestyle='', color=SECOND, markersize=6, label='write')],
        frameon=False, fontsize=10, loc='upper left', ncol=1,
        handletextpad=0.3, labelspacing=0.2)
    fig.subplots_adjust(wspace=0.34)
    for out in (PAPER_FIGS / 'ch_band.pdf', RESULTS / 'ch3_band.png'):
        fig.savefig(out, dpi=200, bbox_inches='tight')
        print('wrote ' + str(out))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
