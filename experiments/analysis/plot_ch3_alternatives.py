#!/usr/bin/env python3
"""Section 3 figures: what the loading alternatives leave behind, and what runs on it.

Three figures, one question each.

  ch3_final_state_shape  - the shape of the final LSM-Tree state each loader
    produces: per-level size and per-level SST count. Flush-only and last
    compaction collapse to a single level; fillseq fills the same level targets
    as incremental construction but carries a larger last level.
  ch3_ycsb_alternatives  - YCSB A-F throughput on each 1 TB state, 300 s per
    cell, block cache 0, automatic compaction enabled. Flush-only has no bar
    because its writes stop at DB open; that cell is annotated, not omitted.
    F2Load is absent by design: Section 3 weighs the design alternatives, and
    the proposal is measured in the evaluation instead. Its 1 TB database also
    predates the September 9 dedup-ratio fix, so its cells are not current.
  ch3_ycsb_noise_band    - run-to-run spread of YCSB throughput across
    independent 1 TB loadings, which is the tolerance any fidelity claim has to
    be read against. These cells run with a 50 GiB block cache, unlike the
    alternatives matrix above, so the two figures share a dataset but not a
    cache configuration and their absolute values are not interchangeable.

Every panel uses one scale. Absolute throughput spans an order of magnitude
across workloads, so the alternatives figure normalises to incremental
construction and prints the baseline value under each group.
"""
import csv
import glob
import json
from pathlib import Path
import statistics as st

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

EXP = Path(__file__).resolve().parents[1]
LOADS = EXP / 'artifacts/log_loads'
RESULTS = EXP / 'results'
GiB = 1024 ** 3

SURFACE = '#fcfcfb'
INK = '#0b0b0b'
INK_2 = '#52514e'
GRID = '#d8d7d2'
SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#4a3aa7', '#008300']
LETTERS = ['a', 'b', 'c', 'd', 'e', 'f']
WORKLOADS = ['workload' + c for c in LETTERS]

# The five loaded 1 TB states, in the order Section 3 introduces them.  The
# baseline campaign holds all but F2Load, whose DB was completed two days later.
RUN3 = LOADS / 'paper_ch23_common_260905_approved_run3/full'
F2 = LOADS / 'paper_ch23_common_260907_f2_completion1/full'
STATES = [
    ('Incremental\nconstruction', 'baseline', RUN3 / 'baseline_1kb'),
    ('Flush-only', 'flush_only', RUN3 / 'flush_only_1kb'),
    ('Last\ncompaction', 'last_comp', RUN3 / 'last_comp_1kb'),
    ('Fillseq', 'fillseq', RUN3 / 'fillseq_1kb'),
    ('Fillseq\n+10% OW', 'fillseq_ow', RUN3 / 'fillseq_ow_1kb'),
    ('F2Load', 'f2load', F2 / 'f2load_1kb'),
]
COLOR = {'baseline': '#3c3b38', 'flush_only': SERIES[0], 'last_comp': SERIES[1],
         'fillseq': SERIES[2], 'fillseq_ow': SERIES[3], 'f2load': SERIES[4]}
# YCSB A-D with a 50 GiB block cache. Flush-only and fillseq were measured on
# 2026-09-11; the baseline arm is reused from the run-to-run band, whose cells
# share this configuration. n01 is the arm closest to the median of the ten
# independent loadings on every one of A to D, within 0.42 percent.
CACHE50_RUN = RESULTS / 'ch3_ycsb_cache50_260911_run2/results.json'
CACHE50_BASE = RESULTS / 'ycsb_band_n01_260910/results.json'
CACHE50_WORKLOADS = ['workload' + c for c in 'abcd']
ALT_YCSB = RESULTS / 'paper_alternatives_ycsb_cached0_260908_no_flush_run2/summary.tsv'
# Flush-only never produced a full cell: run1 stopped writes 0.56 s after open
# with 16237 level-0 files against a stop trigger of 36, and the watchdog ended
# the process at 903.6 s.  The figure carries that as an annotation.
FLUSH_ONLY_NOTE = 'writes stopped 0.6 s after DB open,\n16,237 $L_0$ files against a stop trigger of 36'


def style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(True, axis='y', color=GRID, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    for side in ('left', 'bottom'):
        ax.spines[side].set_color(INK_2)
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors=INK_2, labelsize=8, length=3)


def save(fig, stem):
    fig.patch.set_facecolor(SURFACE)
    for ext in ('png', 'pdf'):
        out = RESULTS / (stem + '.' + ext)
        fig.savefig(out, dpi=200, bbox_inches='tight', facecolor=SURFACE)
    print('wrote ' + str(RESULTS / (stem + '.png')))
    plt.close(fig)


def load_states():
    rows = []
    for label, key, path in STATES:
        d = json.load(open(path / 'validated.json'))
        rows.append(dict(label=label, key=key, levels=d['levels'],
                         size_gib=d['final_sst_bytes'] / GiB,
                         ssts=d['final_sst_count'],
                         loading_min=d['loading_min'], waf=d['waf']))
    return rows


def fig_shape(rows):
    """Per-level size and per-level SST count.

    A stacked bar hides $L_1$ to $L_3$, which together hold under 4 percent of
    the data, so the panels group by level on a logarithmic axis instead. The
    shape of the state is what the reader has to compare, not its total.
    """
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 3.9))
    levels = list(range(6))
    width = 0.8 / len(rows)
    for ax, field, title, unit in (
            (axes[0], 'size_mib', 'Data per level', 'Size (GiB)'),
            (axes[1], 'files', 'SST files per level', 'Files')):
        style(ax)
        for i, r in enumerate(rows):
            xs, ys = [], []
            for lvl in levels:
                v = r['levels'].get(str(lvl), {}).get(field, 0)
                if not v:
                    continue
                xs.append(lvl - 0.4 + width * (i + 0.5))
                ys.append(v / 1024 if field == 'size_mib' else v)
            ax.bar(xs, ys, width * 0.9, label=r['label'].replace('\n', ' '),
                   color=COLOR[r['key']], edgecolor=SURFACE, linewidth=0.4,
                   zorder=3)
        ax.set_yscale('log')
        ax.set_xticks(levels)
        ax.set_xticklabels(['$L_%d$' % l for l in levels], fontsize=9, color=INK)
        ax.set_ylabel(unit, fontsize=9, color=INK)
        ax.set_title(title, fontsize=10, color=INK, pad=8)
        ax.grid(True, axis='y', which='both', color=GRID, linewidth=0.5, zorder=0)
    # Totals belong in the caption rather than on the bars, but the reader needs
    # them to weigh the per-level picture, so they go under the legend.
    totals = '   '.join('%s %s GiB / %s SSTs' % (
        r['label'].replace('\n', ' '), format(r['size_gib'], ',.0f'),
        format(r['ssts'], ',d')) for r in rows)
    axes[0].legend(frameon=False, fontsize=8, ncol=6, loc='upper center',
                   bbox_to_anchor=(1.08, -0.12), labelcolor=INK_2)
    fig.text(0.5, -0.12, totals, ha='center', fontsize=7.2, color=INK_2)
    save(fig, 'ch3_final_state_shape')


def read_alt_ycsb():
    out = {}
    for r in csv.DictReader(open(ALT_YCSB), delimiter='\t'):
        if r['phase'] != 'full' or r['status'] != 'ok':
            continue
        out[(r['system'], r['workload'])] = dict(
            tput=float(r['throughput_ops_sec']),
            ops=float(r['operations']),
            succ=float(r['successful_gets'] or 0),
            reads=float(r['engine_keys_read'] or 0))
    return out


def fig_alternatives(cells):
    """YCSB A-F on each 1 TB state, indexed to incremental construction.

    Absolute throughput spans an order of magnitude across the six workloads,
    so each group is normalised and the reference value printed under its tick.
    """
    systems = [(l, k) for l, k, _ in STATES
               if k not in ('baseline', 'f2load')]
    fig, axes = plt.subplots(2, 1, figsize=(10.6, 6.6),
                             gridspec_kw=dict(height_ratios=[2.0, 1.0]))
    ax = axes[0]
    style(ax)
    width = 0.8 / len(systems)
    for i, (label, key) in enumerate(systems):
        xs, ys = [], []
        for j, w in enumerate(WORKLOADS):
            base, cell = cells.get(('baseline', w)), cells.get((key, w))
            if cell is None or base is None:
                continue
            xs.append(j - 0.4 + width * (i + 0.5))
            ys.append(cell['tput'] / base['tput'])
        ax.bar(xs, ys, width * 0.9, label=label.replace('\n', ' '),
               color=COLOR[key], edgecolor=SURFACE, linewidth=0.4, zorder=3)
        for xi, yi in zip(xs, ys):
            ax.text(xi, yi + 0.07, format(yi, '.2f'), ha='center', va='bottom',
                    fontsize=6.0, color=INK_2, rotation=90)
    ax.axhline(1.0, color=INK, linewidth=1.0, zorder=4)
    ax.set_xticks(range(len(WORKLOADS)))
    ax.set_xticklabels(['YCSB ' + c.upper() + '\n%s ops/s' % format(
        cells[('baseline', w)]['tput'], ',.0f')
        for c, w in zip(LETTERS, WORKLOADS)], fontsize=8, color=INK)
    ax.set_ylabel('Throughput relative to\nincremental construction (=1.0)',
                  fontsize=9, color=INK)
    ax.set_title('YCSB on each 1 TB loaded state: 300 s per cell, block cache 0, '
                 'automatic compaction enabled', fontsize=10, color=INK, pad=20)
    ax.legend(frameon=False, fontsize=8, ncol=5, loc='upper left',
              bbox_to_anchor=(0, 1.10), labelcolor=INK_2)
    ax.set_ylim(0, 6.6)
    # Flush-only was attempted on workload A only. Its slot carries a cross
    # rather than a bar, and the reason sits under the panel.
    ax.plot([-0.4 + width * 0.5], [0.10], marker='x', markersize=7,
            markeredgewidth=1.6, color=COLOR['flush_only'], zorder=6,
            linestyle='')
    ax.text(0.0, -0.235, 'Flush-only (\u00d7): ' + FLUSH_ONLY_NOTE.replace(
        '\n', ' '), transform=ax.transAxes, fontsize=7.4,
        color=COLOR['flush_only'], ha='left', va='top', zorder=9)

    # Throughput diverges partly because the states hold different key sets, so
    # the same request stream finds a key at different rates on each of them.
    ax = axes[1]
    style(ax)
    allsys = [(l, k) for l, k, _ in STATES if k != 'f2load']
    width = 0.8 / len(allsys)
    for i, (label, key) in enumerate(allsys):
        xs, ys = [], []
        for j, w in enumerate(WORKLOADS):
            cell = cells.get((key, w))
            if cell is None or not cell['reads']:
                continue
            xs.append(j - 0.4 + width * (i + 0.5))
            ys.append(100 * cell['succ'] / cell['reads'])
        ax.bar(xs, ys, width * 0.9, label=label.replace('\n', ' '),
               color=COLOR[key], edgecolor=SURFACE, linewidth=0.4, zorder=3)
    ax.set_xticks(range(len(WORKLOADS)))
    ax.set_xticklabels(['YCSB ' + c.upper() for c in LETTERS], fontsize=8,
                       color=INK)
    ax.set_ylabel('Lookups that\nfind a key (%)', fontsize=9, color=INK)
    ax.set_ylim(0, 112)
    ax.text(4.0, 50, 'E scans and inserts,\nso it issues no lookups',
            fontsize=7.2, color=INK_2, ha='center', va='center')
    ax.legend(handles=[Patch(facecolor=COLOR['baseline'],
                             label='Incremental construction')],
              frameon=False, fontsize=8, loc='upper left',
              bbox_to_anchor=(0, 1.22), labelcolor=INK_2)
    fig.subplots_adjust(hspace=0.58)
    save(fig, 'ch3_ycsb_alternatives')


def read_cache50():
    rows = {(r['system'], r['workload']): r
            for r in json.load(open(CACHE50_RUN)) if r['phase'] == 'full'}
    rows.update({('baseline', r['workload']): r
                 for r in json.load(open(CACHE50_BASE)) if r['phase'] == 'full'})
    return rows


def fig_cache50(rows):
    """YCSB C throughput and SST accesses on three states, both in absolute terms.

    C is the only workload of the four that issues no writes, so it is the only
    one flush-only can serve at all: its A, B and D cells stop at DB open. Both
    panels use a linear axis, and the pair reads as one statement. Flush-only
    does not register on the throughput panel and is the only bar visible on the
    access panel, because a lookup there has to consider 10,399 SSTs.
    """
    systems = [('Incremental\nconstruction', 'baseline'), ('Fillseq', 'fillseq'),
               ('Flush-only', 'flush-only' if False else 'flush_only')]
    fig, axes = plt.subplots(1, 2, figsize=(9.4, 3.9))
    labels = [l for l, _ in systems]
    colors = [COLOR[k] for _, k in systems]
    xs = list(range(len(systems)))

    def panel(ax, values, ylabel, title, fmt):
        style(ax)
        ax.bar(xs, values, 0.56, color=colors, edgecolor=SURFACE, linewidth=0.5,
               zorder=3)
        top = max(values)
        for x, y in zip(xs, values):
            ax.text(x, y + top * 0.02, format(y, fmt), ha='center', va='bottom',
                    fontsize=9, color=INK)
        ax.set_xticks(xs)
        ax.set_xticklabels(labels, fontsize=9, color=INK)
        ax.set_ylabel(ylabel, fontsize=9, color=INK)
        ax.set_ylim(0, top * 1.16)
        ax.set_title(title, fontsize=10, color=INK, pad=10)

    cells = [rows.get((k, 'workloadc')) for _, k in systems]
    panel(axes[0], [c['throughput_ops_sec'] for c in cells],
          'Throughput (ops/s)', 'YCSB C throughput', ',.0f')
    # Every SST whose key range covers the key has its filter block consulted,
    # so filter accesses per operation is the number of SSTs a lookup considers.
    panel(axes[1], [c['filter_cache_accesses'] / c['operations'] for c in cells],
          'SSTs consulted per lookup', 'YCSB C SST accesses', ',.2f')
    fig.suptitle('Three 1 TB loaded states: 300 s, 50 GiB block cache',
                 fontsize=10, color=INK_2, y=1.03)
    fig.subplots_adjust(wspace=0.32)
    save(fig, 'ch3_ycsb_cache50')


def read_band():
    band = {}
    for d in sorted(glob.glob(str(RESULTS / 'ycsb_band_*'))
                    + glob.glob(str(RESULTS / 'ycsb_f2band_*'))):
        p = Path(d) / 'summary.tsv'
        if not p.exists():
            continue
        for r in csv.DictReader(open(p), delimiter='\t'):
            if r['phase'] != 'full' or r['status'] != 'ok':
                continue
            band.setdefault((r['system'], r['workload']), []).append(
                (Path(d).name, float(r['throughput_ops_sec'])))
    return band


def fig_band(band):
    """Run-to-run spread across independent 1 TB loadings, indexed to the baseline mean.

    One F2Load loading on workload C reads 71.4 percent of the baseline mean and
    would stretch the axis over an empty range, so it is drawn at the lower edge
    with its value printed rather than dropped.
    """
    floor = 92.0
    fig, ax = plt.subplots(figsize=(9.2, 3.9))
    style(ax)
    for j, w in enumerate(WORKLOADS):
        b = [v for _, v in band.get(('baseline', w), [])]
        f = band.get(('f2load', w), [])
        if not b:
            continue
        m = st.mean(b)
        lo, hi = 100 * min(b) / m, 100 * max(b) / m
        ax.add_patch(plt.Rectangle((j - 0.34, lo), 0.68, hi - lo,
                                   facecolor=COLOR['baseline'], alpha=0.16,
                                   edgecolor='none', zorder=2))
        ax.plot([j - 0.34, j + 0.34], [100, 100], color=COLOR['baseline'],
                linewidth=1.2, zorder=4)
        ax.scatter([j - 0.17] * len(b), [100 * v / m for v in b], s=14,
                   color=COLOR['baseline'], zorder=5, linewidths=0)
        inside = [(r, 100 * v / m) for r, v in f if 100 * v / m >= floor]
        below = [(r, 100 * v / m) for r, v in f if 100 * v / m < floor]
        if inside:
            ax.scatter([j + 0.17] * len(inside), [y for _, y in inside], s=16,
                       marker='D', color=SERIES[1], zorder=5, linewidths=0)
        for r, y in below:
            ax.scatter([j + 0.17], [floor + 0.45], s=26, marker='v',
                       color=SERIES[1], zorder=5, linewidths=0)
            ax.annotate('%s: %.1f%%' % (r.replace('ycsb_f2band_', '')
                                        .replace('_260911', ''), y),
                        xy=(j + 0.17, floor + 0.45), xytext=(9, 0),
                        textcoords='offset points', fontsize=6.8,
                        color=SERIES[1], va='center')
        ax.text(j, hi + 0.15, 'band %.1f to %.1f%%' % (lo, hi), ha='center',
                va='bottom', fontsize=6.8, color=INK_2)
    ax.set_xticks(range(len(WORKLOADS)))
    ax.set_xticklabels([
        'YCSB %s\n%d loadings / %d' % (c.upper(),
                                       len(band.get(('baseline', w), [])),
                                       len(band.get(('f2load', w), [])))
        for c, w in zip(LETTERS, WORKLOADS)], fontsize=8, color=INK)
    ax.set_ylabel('Throughput relative to the mean\nincremental-construction loading (%)',
                  fontsize=9, color=INK)
    ax.set_ylim(floor, 106.5)
    ax.set_title('Run-to-run spread across independent 1 TB loadings: '
                 '300 s per cell, 50 GiB block cache', fontsize=10, color=INK,
                 pad=8)
    ax.legend(handles=[
        Patch(facecolor=COLOR['baseline'], alpha=0.16,
              label='Band of independent incremental-construction loadings'),
        plt.Line2D([], [], marker='o', linestyle='', color=COLOR['baseline'],
                   markersize=5, label='One incremental-construction loading'),
        plt.Line2D([], [], marker='D', linestyle='', color=SERIES[1],
                   markersize=5, label='One F2Load loading'),
        plt.Line2D([], [], marker='v', linestyle='', color=SERIES[1],
                   markersize=6, label='Below the axis, value printed')],
        frameon=False, fontsize=8, loc='upper center', ncol=2,
        bbox_to_anchor=(0.5, -0.22), labelcolor=INK_2)
    save(fig, 'ch3_ycsb_noise_band')


def main():
    # Section 3 weighs the alternatives, so the proposal stays out of both of
    # its figures. The TSV beside them keeps every state for the evaluation.
    rows = [r for r in load_states() if r['key'] != 'f2load']
    fig_shape(rows)
    cells = read_alt_ycsb()
    fig_alternatives(cells)
    fig_band(read_band())
    fig_cache50(read_cache50())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
