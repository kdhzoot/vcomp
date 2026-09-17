#!/usr/bin/env python3
"""YCSB A (fixed-work 1억 op + drain): baseline vs F2Load(no bitmap) — ch32 격자 스타일."""
import json, glob, os
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator, FuncFormatter

OUT = 'experiments/results/paper_ch32/ch32_grid_a_fixed'
GROUPS = [('KV size', [('E1-100B', 'E1-100B', '100 B'), ('P0', 'P0', '1 KB'), ('E1-10KB', 'E1-10KB', '10 KB')]),
          ('Dataset size', [('P0', 'P0', '1 TB'), ('E2-2T', 'E2-2T', '2 TB'), ('E2-4T', 'E2-4T', '4 TB'), ('E2-8T', 'E2-8T', '8 TB')]),
          ('Key uniqueness', [('E3-u25', 'E3-u25', '25%'), ('E3-u50', 'E3-u50', '50%'),
                              ('P0', 'P0', '63%'), ('E3-u100', 'E3-u100@gu', '100%')]),
          ('Compression', [('P0', 'P0', 'off'), ('E7-lz4', 'E7-lz4', 'on')]),
          ('Level structure', [('E4-256m4', 'E4-256m4', '256MB x 4'), ('P0', 'P0', '256MB x 10'),
                               ('E4-1024m10', 'E4-1024m10', '1024MB x 10')])]
METRICS = [('ops', 'Throughput\n(ops/s)', 'big'), ('fpr', 'Filter checks\n/ lookup', 'dec'),
           ('cw', 'Compaction\nwrite (GB)', 'big')]
fam = 'Cambria' if any('cambria' in f.name.lower() for f in font_manager.fontManager.ttflist) else 'DejaVu Serif'
plt.rcParams.update({'font.family': fam, 'hatch.linewidth': 2.0})

def load(p):
    r = json.load(open(p))
    return dict(cw=r['compact_write_bytes'] / 1e9, tot=r['total_seconds_incl_drain'],
                ops=r['ops_per_sec'], run=r['run_seconds'],
                fpr=r['filter_probes_per_read'])
BASE, F2 = {}, {}
for p in glob.glob('experiments/artifacts/fix_20260914_ycsba_fixed_deep/*__workloada/result.json'):
    n = os.path.basename(json.load(open(p))['source'])
    if n.endswith('_baseline'): BASE[n] = load(p)
for p in glob.glob('experiments/artifacts/fix_20260915_ycsba_*/*__workloada/result.json'):
    src = json.load(open(p))['source']; root = os.path.basename(os.path.dirname(src))
    F2[os.path.basename(src) + ('@gu' if 'global_unique' in root else '')] = load(p)
def cell(name, side):
    if side == 0:
        return BASE.get('P0-r1_baseline' if name == 'P0' else name + '_baseline')
    key = 'P0fix-r1_f2load' if name == 'P0' else (name.replace('@gu', '') + '_f2load' + ('@gu' if name.endswith('@gu') else ''))
    return F2.get(key)
def fmt(kind):
    def big(v, _=None):
        if v >= 1e6: return f'{v/1e6:.2f}'.rstrip('0').rstrip('.') + 'M'
        if v >= 1e3: return f'{v/1e3:.1f}K'.replace('.0K', 'K')
        return f'{v:.0f}'
    return {'big': big, 'plain': lambda v, _=None: f'{v:.0f}', 'dec': lambda v, _=None: f'{v:.1f}'}[kind]

ratios = [len(p) for _, p in GROUPS]
fig, axes = plt.subplots(len(METRICS), len(GROUPS), figsize=(1.35 * sum(ratios) + 3.0, 13.5), dpi=200,
                         sharey='row', gridspec_kw={'width_ratios': ratios})
for i, (key, ylabel, kind) in enumerate(METRICS):
    f = fmt(kind)
    rowmax = max(c[key] for _, pts in GROUPS for b, fl, _ in pts for c in (cell(b, 0), cell(fl, 1)) if c)
    for j, (gname, pts) in enumerate(GROUPS):
        ax = axes[i][j]; x = list(range(len(pts))); w = 0.38
        B = [cell(b, 0) for b, _, _ in pts]; F = [cell(fl, 1) for _, fl, _ in pts]
        bv = [(c[key] if c else 0) for c in B]; fv = [(c[key] if c else 0) for c in F]
        ax.bar([k - w/2 for k in x], bv, w, color='#0072B2', edgecolor='black', linewidth=1.4, zorder=3)
        ax.bar([k + w/2 for k in x], fv, w, color='white', edgecolor='#009E73', linewidth=0.0, hatch='////', zorder=3)
        ax.bar([k + w/2 for k in x], fv, w, color='none', edgecolor='black', linewidth=1.4, zorder=4)
        pad = rowmax * 0.02
        for k in x:
            ax.text(k - w/2, bv[k] + pad, f(bv[k]) if B[k] else 'n/a', ha='center', va='bottom', fontsize=17, rotation=90, zorder=5)
            ax.text(k + w/2, fv[k] + pad, f(fv[k]) if F[k] else 'n/a', ha='center', va='bottom', fontsize=17, rotation=90, zorder=5)
        ax.set_xlim(-0.6, len(pts) - 0.4); ax.set_xticks(x)
        ax.set_xticklabels([l for _, _, l in pts] if i == len(METRICS) - 1 else [],
                           fontsize=19, rotation=45, ha='right', rotation_mode='anchor')
        ax.tick_params(axis='x', length=0); ax.tick_params(axis='y', labelsize=21, pad=4, length=4, width=1.0)
        ax.set_axisbelow(True); ax.grid(axis='y', color='#D9D9D9', linewidth=0.75, zorder=0); ax.grid(axis='x', visible=False)
        for sp in ('top', 'right'): ax.spines[sp].set_visible(False)
        for sp in ('left', 'bottom'): ax.spines[sp].set_color('black'); ax.spines[sp].set_linewidth(1.0)
        if i == 0: ax.set_title(gname, fontsize=25, pad=14)
        if j == 0:
            ax.set_ylabel(ylabel, fontsize=30, labelpad=14, linespacing=1.1)
            ax.set_ylim(0, rowmax * 1.55)
            ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=(kind == 'plain')))
            ax.yaxis.set_major_formatter(FuncFormatter(f))
leg = fig.legend(handles=[Patch(facecolor='#0072B2', edgecolor='black', linewidth=1.4, label='Baseline'),
                          Patch(facecolor='white', edgecolor='#009E73', hatch='////', linewidth=1.4, label='F2Load')],
                 fontsize=22, frameon=False, loc='upper center', ncol=2, bbox_to_anchor=(0.5, 1.0),
                 handlelength=1.8, handleheight=1.1, columnspacing=2.2)
fig.canvas.draw()
for i in range(len(METRICS)):
    lo, hi = axes[i][0].get_ylim()
    t = [v for v in axes[i][0].get_yticks() if lo <= v <= hi]
    if t:
        for j in range(len(GROUPS)): axes[i][j].spines['left'].set_bounds(min(t), max(t))
fig.subplots_adjust(left=0.085, right=0.995, top=0.915, bottom=0.135, wspace=0.14, hspace=0.10)
fig.align_ylabels([axes[i][0] for i in range(len(METRICS))])
extra = ([leg] + [axes[i][0].yaxis.label for i in range(len(METRICS))]
         + [axes[0][j].title for j in range(len(GROUPS))]
         + [t for j in range(len(GROUPS)) for t in axes[-1][j].get_xticklabels()])
for ext in ('png', 'pdf'):
    fig.savefig(f'{OUT}.{ext}', dpi=200, transparent=True, bbox_inches='tight', bbox_extra_artists=extra, pad_inches=0.1)
print('saved', OUT + '.png/.pdf')
