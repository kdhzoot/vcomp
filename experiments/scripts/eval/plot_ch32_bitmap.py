#!/usr/bin/env python3
"""Key uniqueness 4점: baseline vs F2Load(bitmap) vs F2Load(no bitmap) — ch32 격자 스타일."""
import json, re, glob
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator, FuncFormatter

A = 'experiments/artifacts/'
OUT = 'experiments/results/paper_ch32/ch32_bitmap_uniqueness'
PTS = [('25%', 'E3-u25'), ('50%', 'E3-u50'), ('75%', 'E3-u75'), ('100%', 'E3-u100')]
SERIES = [('Baseline', '#0072B2', None), ('F2Load', '#009E73', '////'), ('F2Load, no bitmap', '#D55E00', '\\\\\\\\')]
METRICS = [('pl', 'Positive\nlookup (%)', 'pct'), ('ops', 'Throughput\n(ops/s)', 'big'),
           ('fpr', 'Filter checks\n/ lookup', 'dec'), ('ssts', 'SST count', 'big')]
fam = 'Cambria' if any('cambria' in f.name.lower() for f in font_manager.fontManager.ttflist) else 'DejaVu Serif'
plt.rcParams.update({'font.family': fam})

def get(path):
    r = json.load(open(path)); t = open(path.replace('result.json', 'stdout_stderr.log'), errors='ignore').read()
    pl = r.get('positive_lookup_pct')
    if pl is None:
        c = {k: int(m.group(1)) for k in ('memtable.hit', 'l0.hit', 'l1.hit', 'l2andup.hit', 'number.keys.read')
             for m in [re.search(r'rocksdb\.' + re.escape(k) + r' COUNT : (\d+)', t)] if m}
        pl = 100 * sum(v for k, v in c.items() if k != 'number.keys.read') / c['number.keys.read']
    return dict(ops=r['ops_per_sec'], fpr=r['filter_probes_per_read'], pl=pl, ssts=r['source_identity_ssts'])
def find(pat):
    g = [p for p in sorted(glob.glob(pat)) if 'uniform' not in p]
    return get(g[-1])
data = [[find(f'{A}eval_*/{c}_baseline__workloadc/result.json'),
         find(f'{A}fix_20260913_ycsbc/{c}_f2load__workloadc/result.json'),
         find(f'{A}fix_20260914_ycsbc_nobitmap/{c}_f2load__workloadc/result.json')] for _, c in PTS]
def fmt(kind):
    def big(v, _=None):
        if v >= 1e6: return f'{v/1e6:.2f}'.rstrip('0').rstrip('.') + 'M'
        if v >= 1e3: return f'{v/1e3:.0f}K'
        return f'{v:.0f}'
    return {'big': big, 'dec': lambda v, _=None: f'{v:.1f}', 'pct': lambda v, _=None: f'{v:.0f}'}[kind]

fig, axes = plt.subplots(len(METRICS), 1, figsize=(9.0, 16.0), dpi=200)
x = list(range(len(PTS))); w = 0.26
for i, (key, ylabel, kind) in enumerate(METRICS):
    ax = axes[i]; f = fmt(kind)
    vals = [[d[s][key] for d in data] for s in range(3)]
    top = max(max(v) for v in vals)
    for s, (lab, col, hat) in enumerate(SERIES):
        ax.bar([k + (s - 1) * w for k in x], vals[s], w, color=col, edgecolor='black',
               linewidth=1.0, hatch=hat, zorder=3)
        for k in x:
            ax.text(k + (s - 1) * w, vals[s][k] + top * 0.02, f(vals[s][k]),
                    ha='center', va='bottom', fontsize=15, rotation=90, zorder=5)
    ax.set_xlim(-0.55, len(PTS) - 0.45); ax.set_ylim(0, top * 1.5)
    ax.set_xticks(x)
    ax.set_xticklabels([l for l, _ in PTS] if i == len(METRICS) - 1 else [], fontsize=19)
    ax.tick_params(axis='x', length=0); ax.tick_params(axis='y', labelsize=21, pad=4, length=4, width=1.0)
    ax.set_ylabel(ylabel, fontsize=26, labelpad=14, linespacing=1.1)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=(kind != 'dec')))
    ax.yaxis.set_major_formatter(FuncFormatter(f))
    ax.set_axisbelow(True); ax.grid(axis='y', color='#D9D9D9', linewidth=0.75, zorder=0); ax.grid(axis='x', visible=False)
    for sp in ('top', 'right'): ax.spines[sp].set_visible(False)
    for sp in ('left', 'bottom'): ax.spines[sp].set_color('black'); ax.spines[sp].set_linewidth(1.0)
axes[-1].set_xlabel('Key uniqueness', fontsize=26, labelpad=10)
leg = fig.legend(handles=[Patch(facecolor=c, edgecolor='black', linewidth=1.0, hatch=h, label=l) for l, c, h in SERIES],
                 fontsize=21, frameon=False, loc='upper center', ncol=3, bbox_to_anchor=(0.5, 1.0),
                 handlelength=1.8, handleheight=1.1, columnspacing=1.8)
fig.canvas.draw()
for i in range(len(METRICS)):
    lo, hi = axes[i].get_ylim()
    ticks = [t for t in axes[i].get_yticks() if lo <= t <= hi]
    if ticks: axes[i].spines['left'].set_bounds(min(ticks), max(ticks))
fig.subplots_adjust(left=0.20, right=0.99, top=0.925, bottom=0.075, hspace=0.10)
fig.align_ylabels(axes)
extra = [leg] + [a.yaxis.label for a in axes] + [axes[-1].xaxis.label] + list(axes[-1].get_xticklabels())
for ext in ('png', 'pdf'):
    fig.savefig(f'{OUT}.{ext}', dpi=200, transparent=True, bbox_inches='tight', bbox_extra_artists=extra, pad_inches=0.1)
print('saved', OUT + '.png/.pdf')
