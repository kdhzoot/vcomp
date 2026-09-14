#!/usr/bin/env python3
"""13설정 × 3계열(baseline / F2Load / F2Load no bitmap) — YCSB C zipfian, ch32 격자 스타일."""
import json, re, glob, os
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator, FuncFormatter

OUT = 'experiments/results/paper_ch32/ch32_grid3_zipfian'
GROUPS = [('KV size', [('E1-100B', '100 B'), ('P0', '1 KB'), ('E1-10KB', '10 KB')]),
          ('Dataset size', [('P0', '1 TB'), ('E2-2T', '2 TB'), ('E2-4T', '4 TB'), ('E2-8T', '8 TB')]),
          ('Key uniqueness', [('E3-u25', '25%'), ('E3-u50', '50%'), ('P0', '63%'),
                              ('E3-u75', '75%'), ('E3-u100', '100%')]),
          ('Compression', [('P0', 'off'), ('E7-lz4', 'on')]),
          ('Level structure', [('E4-256m4', '256MB x 4'), ('P0', '256MB x 10'), ('E4-1024m10', '1024MB x 10')])]
METRICS = [('pl', 'Positive\nlookup (%)', 'pct'), ('ops', 'Throughput\n(ops/s)', 'big'),
           ('fpr', 'Filter checks\n/ lookup', 'dec'), ('ssts', 'SST count', 'big')]
SERIES = [('Baseline', '#0072B2', '#00456B', None), ('F2Load', '#009E73', '#006147', None),
          ('F2Load, no bitmap', '#009E73', '#006147', '////')]
fam = 'Cambria' if any('cambria' in f.name.lower() for f in font_manager.fontManager.ttflist) else 'DejaVu Serif'
plt.rcParams.update({'font.family': fam})

def load(p):
    r = json.load(open(p)); t = open(p.replace('result.json', 'stdout_stderr.log'), errors='ignore').read()
    pl = r.get('positive_lookup_pct')
    if pl is None:
        c = {k: int(m.group(1)) for k in ('memtable.hit', 'l0.hit', 'l1.hit', 'l2andup.hit', 'number.keys.read')
             for m in [re.search(r'rocksdb\.' + re.escape(k) + r' COUNT : (\d+)', t)] if m}
        pl = 100 * sum(v for k, v in c.items() if k != 'number.keys.read') / c['number.keys.read']
    return dict(ops=r['ops_per_sec'], fpr=r['filter_probes_per_read'], pl=pl, ssts=r['source_identity_ssts'])
def index(pred):
    out = {}
    for p in glob.glob('experiments/artifacts/*/*__workloadc/result.json'):
        r = json.load(open(p)); src = r.get('source', ''); run = p.split('/')[2]
        if 'uniform' in run: continue
        if pred(src): out[os.path.basename(src)] = p
    return out
BASE = index(lambda s: s.startswith('/work/vcomp/exp/eval_20260909_night1') or ('/fix_20260913/' in s and s.endswith('_baseline')))
BM   = index(lambda s: '/fix_20260913/' in s and s.endswith('_f2load'))
NB   = index(lambda s: 'nobitmap' in s)
def cell(name, side):
    key = ('P0-r1_baseline' if name == 'P0' else name + '_baseline') if side == 0 else \
          ('P0fix-r1_f2load' if name == 'P0' else name + '_f2load')
    src = [BASE, BM, NB][side]
    return load(src[key]) if key in src else None
def fmt(kind):
    def big(v, _=None):
        if v >= 1e6: return f'{v/1e6:.2f}'.rstrip('0').rstrip('.') + 'M'
        if v >= 1e3: return f'{v/1e3:.0f}K'
        return f'{v:.0f}'
    return {'big': big, 'dec': lambda v, _=None: f'{v:.1f}', 'pct': lambda v, _=None: f'{v:.0f}'}[kind]

ratios = [len(p) for _, p in GROUPS]
fig, axes = plt.subplots(len(METRICS), len(GROUPS), figsize=(1.7 * sum(ratios) + 3.0, 16.0), dpi=200,
                         sharey='row', gridspec_kw={'width_ratios': ratios})
w = 0.26
for i, (key, ylabel, kind) in enumerate(METRICS):
    f = fmt(kind)
    rowmax = max(c[key] for _, pts in GROUPS for n, _ in pts for c in [cell(n, s) for s in range(3)] if c)
    for j, (gname, pts) in enumerate(GROUPS):
        ax = axes[i][j]; x = list(range(len(pts)))
        for s, (lab, fc, ec, hat) in enumerate(SERIES):
            vals = [cell(n, s) for n, _ in pts]
            xs = [k + (s - 1) * w for k in x]
            hs = [(v[key] if v else 0) for v in vals]
            ax.bar(xs, hs, w, color=('white' if hat else fc), edgecolor=ec, linewidth=1.3, hatch=hat, zorder=3)
            for k, v in zip(xs, vals):
                ax.text(k, (v[key] if v else 0) + rowmax * 0.02, f(v[key]) if v else 'n/a',
                        ha='center', va='bottom', fontsize=13, rotation=90, zorder=5)
        ax.set_xlim(-0.55, len(pts) - 0.45); ax.set_xticks(x)
        ax.set_xticklabels([l for _, l in pts] if i == len(METRICS) - 1 else [],
                           fontsize=18, rotation=45, ha='right', rotation_mode='anchor')
        ax.tick_params(axis='x', length=0); ax.tick_params(axis='y', labelsize=20, pad=4, length=4, width=1.0)
        ax.set_axisbelow(True); ax.grid(axis='y', color='#D9D9D9', linewidth=0.75, zorder=0); ax.grid(axis='x', visible=False)
        for sp in ('top', 'right'): ax.spines[sp].set_visible(False)
        for sp in ('left', 'bottom'): ax.spines[sp].set_color('black'); ax.spines[sp].set_linewidth(1.0)
        if i == 0: ax.set_title(gname, fontsize=24, pad=14)
        if j == 0:
            ax.set_ylabel(ylabel, fontsize=28, labelpad=14, linespacing=1.1)
            ax.set_ylim(0, rowmax * 1.55)
            ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=(kind != 'dec')))
            ax.yaxis.set_major_formatter(FuncFormatter(f))
leg = fig.legend(handles=[Patch(facecolor=('white' if h else c), edgecolor=e, linewidth=1.3, hatch=h, label=l)
                          for l, c, e, h in SERIES],
                 fontsize=22, frameon=False, loc='upper center', ncol=3, bbox_to_anchor=(0.5, 1.0),
                 handlelength=1.8, handleheight=1.1, columnspacing=2.0)
fig.canvas.draw()
for i in range(len(METRICS)):
    lo, hi = axes[i][0].get_ylim()
    ticks = [t for t in axes[i][0].get_yticks() if lo <= t <= hi]
    if ticks:
        for j in range(len(GROUPS)): axes[i][j].spines['left'].set_bounds(min(ticks), max(ticks))
fig.subplots_adjust(left=0.085, right=0.995, top=0.915, bottom=0.135, wspace=0.14, hspace=0.10)
fig.align_ylabels([axes[i][0] for i in range(len(METRICS))])
extra = ([leg] + [axes[i][0].yaxis.label for i in range(len(METRICS))]
         + [axes[0][j].title for j in range(len(GROUPS))]
         + [t for j in range(len(GROUPS)) for t in axes[-1][j].get_xticklabels()])
for ext in ('png', 'pdf'):
    fig.savefig(f'{OUT}.{ext}', dpi=200, transparent=True, bbox_inches='tight', bbox_extra_artists=extra, pad_inches=0.1)
print('saved', OUT + '.png/.pdf')
