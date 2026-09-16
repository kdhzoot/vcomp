#!/usr/bin/env python3
"""Peak-RSS scaling and composition for the f2load_memprofile_260914 campaign.

Panel (b) and (c) are built from the components TSV, which splits the single
100 ms sample at which RSS was highest, so the segments sum to that sample's RSS
exactly rather than stacking per-component maxima taken at different instants.
Run build_memprofile_tsv.py first.
"""
import csv
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch
from matplotlib.lines import Line2D

fam = 'Cambria' if any('cambria' in f.name.lower() for f in font_manager.fontManager.ttflist) else 'DejaVu Serif'
plt.rcParams.update({'font.family': fam, 'font.size': 11})

EXP = Path(__file__).resolve().parents[2]
SRCS = sorted((EXP / 'artifacts/log_loads').glob('f2load_memprofile_*/results.json'))
COMP = EXP / 'results/f2load_memprofile_260914_components.tsv'
MODEL = json.loads((EXP / 'results/f2load_memprofile_260914_model.json').read_text())
OUT = EXP / 'results/f2load_memprofile_260914'

BLUE, GREEN, ORANGE, SKY, PINK, YELLOW, GREY = \
    '#0072B2', '#009E73', '#E69F00', '#56B4E9', '#CC79A7', '#F0E442', '#8C8C8C'
# Fixed order, largest term first; never cycled.
STACK = [('PLR segments', BLUE),
         ('other live heap', ORANGE),
         ('allocator + non-heap', SKY),
         ('registry index + descriptor objects', GREEN),
         ('KMV sketches', PINK),
         ('idle RSS (22 MB, not visible)', GREY)]
SLOPE = MODEL['c_record_bytes']
PER_FILE = MODEL['c_sst_bytes']

comp = list(csv.DictReader(COMP.open(), delimiter='\t'))
# The campaign grows a cell at a time, so the series come from the data.
rows = {}
for src in SRCS:
    for r in json.loads(src.read_text()):
        rows[r['arm']] = r


def series_for(kv):
    cells = sorted((r for r in rows.values() if r['kv_bytes'] == kv),
                   key=lambda r: r['dataset_gib'])
    return [(r['arm'], r['dataset_gib'] / 1000.0) for r in cells]


NEW, NEW1K = series_for(100), series_for(1024)
SIZES = sorted({r['dataset_gib'] / 1000.0 for r in rows.values()})

fig, (ax, bx, cx) = plt.subplots(1, 3, figsize=(20.4, 5.9), dpi=200,
                                 gridspec_kw=dict(width_ratios=[1.0, 1.32, 1.0], wspace=0.24))

# --- (a) peak RSS against dataset size, linear axes ------------------------
def series(pairs):
    return ([t for _, t in pairs], [rows[a]['peak_rss_hwm'] / 1e9 for a, _ in pairs])

FILES_PER_TB = {kv: (lambda c: c['peak_files'] / (c['dataset_gib'] / 1000.0))(
                    max((r for r in rows.values() if r['kv_bytes'] == kv),
                        key=lambda r: r['dataset_gib']))
                for kv in (100, 1024)}


def model_gb(kv, tb):
    records = tb * 1024 * (1024 ** 3) / kv
    return (SLOPE * records + PER_FILE * FILES_PER_TB[kv] * tb) / 1e9


for (xs, ys), colour, label, marker, off, ha in (
        (series(NEW), BLUE, '100 B KV', 'o', (-8, 8), 'right'),
        (series(NEW1K), GREEN, '1 KB KV', 's', (-8, 8), 'right')):
    ax.plot(xs, ys, marker=marker, color=colour, linewidth=2.2, markersize=9,
            markeredgecolor='white', markeredgewidth=1.6, zorder=4, label=label)
    for x, y in zip(xs, ys):
        ax.annotate('{:.2f}'.format(y), (x, y), textcoords='offset points',
                    xytext=off, ha=ha, fontsize=9.5, color=colour)

ax.annotate('fitted model\n{:.3f} B/record + {:.0f} KB/SST'.format(SLOPE, PER_FILE / 1024),
            (0.97, 0.05), xycoords='axes fraction', ha='right', va='bottom',
            fontsize=9.3, color='#5A5A5A', linespacing=1.5)
ax.set_xlabel('dataset size (TB)', fontsize=11.5, labelpad=7)
ax.set_ylabel('peak RSS (GB)', fontsize=11.5, labelpad=7)
ax.set_title('(a)  peak RSS against dataset size', fontsize=12.5, pad=11)
ax.set_xticks([int(s) if s == int(s) else s for s in SIZES])
ax.set_xlim(min(SIZES) - 0.45, max(SIZES) + 0.45); ax.set_ylim(0, 1.17 * max(r['peak_rss_hwm'] for r in rows.values()) / 1e9)
ax.legend(fontsize=10.5, frameon=False, loc='upper left', borderaxespad=0.6)

# --- (b) exact composition at the peak sample ------------------------------
order = [a for a, _ in NEW1K] + [a for a, _ in NEW]
xs = ([float(i) for i in range(len(NEW1K))]
      + [len(NEW1K) + 0.7 + i for i in range(len(NEW))])
table = {(c['arm'], c['component']): float(c['bytes']) / 1e9 for c in comp}
bottom = [0.0] * len(order)
for name, colour in STACK:
    values = [table[(a, name.split(' (')[0])] for a in order]
    bx.bar(xs, values, 0.66, bottom=bottom, color=colour, edgecolor='white',
           linewidth=2.0, zorder=3)
    bottom = [b + v for b, v in zip(bottom, values)]
for x, a, top in zip(xs, order, bottom):
    plr = table[(a, 'PLR segments')]
    bx.annotate('{:.2f} GB'.format(top), (x, top), textcoords='offset points',
                xytext=(0, 16), ha='center', fontsize=8.8, color='#3A3A3A')
    bx.annotate('{:.0f}% PLR'.format(100 * plr / top), (x, top),
                textcoords='offset points', xytext=(0, 4), ha='center',
                fontsize=8.2, color=BLUE)
bx.set_xticks(xs)
bx.set_xticklabels(['{:g} TB'.format(tb) for _, tb in NEW1K + NEW], fontsize=10.5)
for x, label in ((sum(xs[:len(NEW1K)]) / len(NEW1K), '1 KB KV'),
                 (sum(xs[len(NEW1K):]) / len(NEW), '100 B KV')):
    bx.annotate(label, (x, -0.115), xycoords=('data', 'axes fraction'), ha='center',
                fontsize=11.5, color='#3A3A3A')
bx.set_ylabel('peak RSS (GB)', fontsize=11.5, labelpad=7)
bx.set_title('(b)  composition at the peak-RSS sample', fontsize=12.5, pad=11)
bx.set_ylim(0, 20.6)
bx.legend(handles=[Patch(facecolor=c, label=n) for n, c in STACK],
          fontsize=9.5, frameon=False, loc='upper left', labelspacing=0.35)

# --- (c) each term per record ----------------------------------------------
cells = sorted(order, key=lambda a: rows[a]['records'])
recs = [rows[a]['records'] / 1e9 for a in cells]
for name, colour in STACK:
    if name.startswith('idle RSS'):
        continue
    ys = [table[(a, name)] * 1e9 / rows[a]['records'] for a in cells]
    cx.plot(recs, ys, marker='o', markersize=6, color=colour, linewidth=1.9,
            markeredgecolor='white', markeredgewidth=1.2, zorder=4, label=name)
cx.axvspan(0.72, 7.0, color='#F4F4F4', zorder=0)
cx.annotate('1 KB KV', (2.1, 0.96), xycoords=('data', 'axes fraction'), fontsize=10,
            color='#7A7A7A', ha='center', va='top')
cx.annotate('100 B KV', (21, 0.96), xycoords=('data', 'axes fraction'), fontsize=10,
            color='#7A7A7A', ha='center', va='top')
plr_per_rec = [r['peak_plr_segment_bytes'] / r['records'] for r in rows.values()]
cx.annotate('PLR: {:.3f} - {:.3f} B/record\nacross all {} cells'.format(
                min(plr_per_rec), max(plr_per_rec), len(rows)),
            (recs[-1], 0.331), textcoords='offset points', xytext=(-2, 22),
            ha='right', fontsize=9.5, color=BLUE, linespacing=1.4)
cx.set_xscale('log'); cx.set_yscale('log')
cx.set_xlabel('records in the dataset (billions)', fontsize=11.5, labelpad=7)
cx.set_ylabel('bytes per record', fontsize=11.5, labelpad=7)
cx.set_title('(c)  only the PLR term is per-record', fontsize=12.5, pad=11)
cx.set_xlim(0.72, 70); cx.set_ylim(2.6e-4, 6.0)
cx.legend(fontsize=9.2, frameon=False, loc='lower left', labelspacing=0.3,
          borderaxespad=0.5)

for axis in (ax, bx, cx):
    axis.set_axisbelow(True)
    for sp in ('top', 'right'):
        axis.spines[sp].set_visible(False)
    for sp in ('left', 'bottom'):
        axis.spines[sp].set_color('#4A4A4A')
ax.grid(color='#D9D9D9', linewidth=0.75)
cx.grid(which='both', color='#E2E2E2', linewidth=0.7)
cx.grid(which='major', color='#D0D0D0', linewidth=0.8)
bx.grid(axis='y', color='#D9D9D9', linewidth=0.75)
bx.grid(axis='x', visible=False)

fig.suptitle('F2Load loading memory, campaign f2load_memprofile_260914  '
             '({} cells, {} TB x 1 KB/100 B KV, RSS sampled every 100 ms, build a04221e7)'.format(
                 len(rows), '/'.join('{:g}'.format(s) for s in SIZES)),
             fontsize=13, y=1.01)
for ext in ('png', 'pdf'):
    fig.savefig('{}.{}'.format(OUT, ext), bbox_inches='tight', pad_inches=0.25,
                facecolor='white')
print('wrote {}.png and {}.pdf'.format(OUT, OUT))
