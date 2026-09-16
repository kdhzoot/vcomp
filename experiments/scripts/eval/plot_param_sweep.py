#!/usr/bin/env python3
"""Parameter sensitivity: PLR error bound and KMV sample budget.

Two knobs, two panels each. The columns share a metric so the reader can see
which knob moves which cost: memory responds to the PLR error bound and not to
the KMV budget, while the final state responds to the KMV budget and not to the
error bound. The baseline band is the ten 1000 GiB incremental loadings.
"""
import csv
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

EXP = Path(__file__).resolve().parents[2]
SRC = EXP / 'artifacts/log_loads/paramsweep_260915/results.json'
OUT = EXP / 'results'
BASE_SSTS = (13460, 13549)
BASE_GB = (823.7, 824.0)
DEFAULT = dict(plr=8, kmv=512)

INK, MUTED, GRID = '#1f2933', '#7b8794', '#dfe3e8'
SERIES = '#2b6cb0'      # cost
FIDELITY = '#b7791f'    # final state
BASELINE = '#52606d'


def rows():
    data = json.loads(SRC.read_text())
    out = []
    for r in data:
        out.append(dict(axis=r['axis'], plr=r['plr_error_bound'], kmv=r['kmv_samples'],
                        rss_gb=r['peak_rss_hwm'] / 1e9,
                        plr_mb=r['peak_plr_segment_bytes'] / 1e6,
                        db_gb=r['final_sst_bytes'] / 1e9,
                        ssts=r['final_sst_count'],
                        load_min=r['loading_min'],
                        kops=r['throughput_ops_sec'] / 1000,
                        found=100 * r['get_found_fraction']))
    return out


def series(data, axis, key):
    xs = sorted({r['plr'] if axis == 'plr' else r['kmv'] for r in data if r['axis'] == axis})
    pick = {}
    for r in data:
        if r['axis'] != axis:
            continue
        pick[r['plr'] if axis == 'plr' else r['kmv']] = r[key]
    # The default configuration belongs to both sweeps.
    for r in data:
        if r['plr'] == DEFAULT['plr'] and r['kmv'] == DEFAULT['kmv']:
            k = DEFAULT['plr'] if axis == 'plr' else DEFAULT['kmv']
            pick.setdefault(k, r[key])
            if k not in xs:
                xs.append(k)
    xs.sort()
    return xs, [pick[x] for x in xs]


def panel(ax, xs, ys, xlabel, ylabel, color, band=None, default=None):
    if band:
        # The ten baseline loadings span 823.7-824.0 GB, too narrow to shade.
        ax.axhline(sum(band) / 2, color=BASELINE, lw=1.4, ls='--', zorder=1,
                   label='incremental construction')
    ax.plot(xs, ys, marker='o', ms=6, lw=2, color=color, zorder=3, clip_on=False)
    if default is not None and default in xs:
        i = xs.index(default)
        ax.plot([default], [ys[i]], marker='o', ms=11, mfc='none', mew=2,
                color=color, zorder=4, clip_on=False)
    ax.set_xscale('log', base=2)
    ax.set_xticks(xs)
    ax.set_xticklabels([str(x) for x in xs])
    ax.set_xlabel(xlabel, color=INK)
    ax.set_ylabel(ylabel, color=INK)
    ax.grid(axis='y', color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    for side in ('left', 'bottom'):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)


def main():
    data = rows()
    fig, axes = plt.subplots(2, 3, figsize=(9.6, 4.9))
    cols = (('load_min', 'Loading time (min)', SERIES, None, (0, 18)),
            ('rss_gb', 'Peak RSS (GB)', SERIES, None, (0, 5.2)),
            ('db_gb', 'Final DB size (GB)', FIDELITY, BASE_GB, (805, 960)))
    for row, axis, xlabel in ((0, 'plr', 'PLR error bound (ranks)'),
                              (1, 'kmv', 'KMV samples per vSST')):
        for col, (key, ylabel, color, band, ylim) in enumerate(cols):
            x, y = series(data, axis, key)
            panel(axes[row][col], x, y, xlabel, ylabel, color, band=band,
                  default=DEFAULT[axis])
            axes[row][col].set_ylim(*ylim)
    axes[0][2].legend(frameon=False, fontsize=8, loc='upper right', labelcolor=MUTED)
    fig.tight_layout(pad=0.9)
    OUT.mkdir(exist_ok=True)
    for ext in ('png', 'pdf'):
        path = OUT / ('param_sweep_260915.' + ext)
        fig.savefig(path, dpi=200, bbox_inches='tight')
        print('wrote', path)

    fields = ['axis', 'plr', 'kmv', 'load_min', 'rss_gb', 'plr_mb', 'ssts', 'db_gb',
              'kops', 'found']
    path = OUT / 'param_sweep_260915.tsv'
    with path.open('w', newline='') as h:
        w = csv.DictWriter(h, fieldnames=fields, delimiter='\t', extrasaction='ignore')
        w.writeheader()
        w.writerows(sorted(data, key=lambda r: (r['axis'], r['plr'], r['kmv'])))
    print('wrote', path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
