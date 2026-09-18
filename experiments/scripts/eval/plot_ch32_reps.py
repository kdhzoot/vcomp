#!/usr/bin/env python3
"""no-bitmap PLR 경로 5회 반복: baseline(1회) vs F2Load(평균±표준편차) — ch32 격자 스타일."""
import json, glob, csv, statistics as stat
from collections import defaultdict
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator, FuncFormatter

REPS = 'experiments/artifacts/nobitmap_reps_plr_20260917'
TSV  = 'experiments/results/paper_ch32/ch32_grid_zipfian_nobitmap.tsv'
OUT  = 'experiments/results/paper_ch32/ch32_grid_zipfian_plr_reps'

GROUPS = [('KV size',        [('100 B', 'E1-100B'), ('1 KB', 'P0fix-r1'), ('10 KB', 'E1-10KB')]),
          ('Dataset size',   [('1 TB', 'P0fix-r1'), ('2 TB', 'E2-2T'), ('4 TB', 'E2-4T'), ('8 TB', 'E2-8T')]),
          ('Key uniqueness', [('25%', 'E3-u25'), ('50%', 'E3-u50'), ('63%', 'P0fix-r1'), ('100%', 'E3-u100')]),
          ('Compression',    [('off', 'P0fix-r1'), ('on', 'E7-lz4')]),
          ('Level structure',[('256MB x 4', 'E4-256m4'), ('256MB x 10', 'P0fix-r1'), ('1024MB x 10', 'E4-1024m10')])]
METRICS = [('ops', 'Throughput\n(ops/s)', 'big'), ('fpr', 'Filter checks\n/ lookup', 'dec'),
           ('found', 'Positive\nlookup (%)', 'pct')]

# F2Load: 5회 반복
rep = defaultdict(list)
for p in sorted(glob.glob(REPS + '/rep*/*/ycsb.json')):
    d = json.load(open(p))
    rep[d['point']].append(dict(ops=d['ops_per_sec'], fpr=d['filter_probes_per_read'],
                                found=d['positive_lookup_pct']))
F2 = {k: {m: (stat.mean([x[m] for x in v]),
             stat.stdev([x[m] for x in v]) if len(v) > 1 else 0.0, len(v))
          for m in ('ops', 'fpr', 'found')} for k, v in rep.items()}

# baseline: 기존 단일 측정 (같은 DB, 같은 조건)
LBL2PT = {lab: pt for _, pts in GROUPS for lab, pt in pts}
BASE = {}
for r in csv.DictReader(open(TSV), delimiter='\t'):
    if r['system'] != 'baseline' or r['x_label'] not in LBL2PT: continue
    BASE[LBL2PT[r['x_label']]] = dict(ops=float(r['ops_per_sec']),
                                      fpr=float(r['filter_per_lookup']),
                                      found=float(r['positive_lookup_pct']))

fam = 'Cambria' if any('cambria' in f.name.lower() for f in font_manager.fontManager.ttflist) else 'DejaVu Serif'
plt.rcParams.update({'font.family': fam, 'hatch.linewidth': 2.0})

def fmt(kind):
    def big(v, _=None):
        if v >= 1e6: return f'{v/1e6:.2f}'.rstrip('0').rstrip('.') + 'M'
        if v >= 1e3: return f'{v/1e3:.0f}K'
        return f'{v:.0f}'
    return {'big': big, 'dec': lambda v, _=None: f'{v:.1f}', 'pct': lambda v, _=None: f'{v:.0f}'}[kind]

ratios = [len(p) for _, p in GROUPS]
fig, axes = plt.subplots(len(METRICS), len(GROUPS), figsize=(1.35 * sum(ratios) + 3.0, 13.5),
                         dpi=200, sharey='row', gridspec_kw={'width_ratios': ratios})
for i, (key, ylabel, kind) in enumerate(METRICS):
    f = fmt(kind)
    rowmax = max(max(BASE[pt][key], F2[pt][key][0] + F2[pt][key][1])
                 for _, pts in GROUPS for _, pt in pts)
    for j, (gname, pts) in enumerate(GROUPS):
        ax = axes[i][j]; x = list(range(len(pts))); w = 0.38
        bv = [BASE[pt][key] for _, pt in pts]
        fv = [F2[pt][key][0] for _, pt in pts]
        fe = [F2[pt][key][1] for _, pt in pts]
        ax.bar([k - w/2 for k in x], bv, w, color='#0072B2', edgecolor='black', linewidth=1.4, zorder=3)
        ax.bar([k + w/2 for k in x], fv, w, color='white', edgecolor='#009E73', linewidth=0.0,
               hatch='////', zorder=3)
        ax.bar([k + w/2 for k in x], fv, w, color='none', edgecolor='black', linewidth=1.4, zorder=4)
        ax.errorbar([k + w/2 for k in x], fv, yerr=fe, fmt='none', ecolor='black',
                    elinewidth=1.4, capsize=5, capthick=1.4, zorder=6)
        pad = rowmax * 0.02
        for k in x:
            ax.text(k - w/2, bv[k] + pad, f(bv[k]), ha='center', va='bottom',
                    fontsize=17, rotation=90, zorder=5)
            ax.text(k + w/2, fv[k] + fe[k] + pad, f(fv[k]), ha='center', va='bottom',
                    fontsize=17, rotation=90, zorder=5)
        ax.set_xlim(-0.6, len(pts) - 0.4); ax.set_xticks(x)
        ax.set_xticklabels([l for l, _ in pts] if i == len(METRICS) - 1 else [],
                           fontsize=19, rotation=45, ha='right', rotation_mode='anchor')
        ax.tick_params(axis='x', length=0); ax.tick_params(axis='y', labelsize=21, pad=4, length=4, width=1.0)
        ax.set_axisbelow(True); ax.grid(axis='y', color='#D9D9D9', linewidth=0.75, zorder=0)
        ax.grid(axis='x', visible=False)
        for sp in ('top', 'right'): ax.spines[sp].set_visible(False)
        for sp in ('left', 'bottom'): ax.spines[sp].set_color('black'); ax.spines[sp].set_linewidth(1.0)
        if i == 0: ax.set_title(gname, fontsize=25, pad=14)
        if j == 0:
            ax.set_ylabel(ylabel, fontsize=30, labelpad=14, linespacing=1.1)
            ax.set_ylim(0, rowmax * 1.62)
            ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=(kind == 'pct')))
            ax.yaxis.set_major_formatter(FuncFormatter(f))
fig.legend(handles=[Patch(facecolor='#0072B2', edgecolor='black', linewidth=1.4, label='Baseline'),
                    Patch(facecolor='white', edgecolor='#009E73', hatch='////', linewidth=1.4,
                          label='F2Load (mean of 5 runs)'),
                    Line2D([0], [0], color='black', linewidth=1.4, label='$\\pm$1 s.d.')],
           fontsize=22, frameon=False, loc='upper center', ncol=3, bbox_to_anchor=(0.5, 1.0),
           handlelength=1.8, handleheight=1.1, columnspacing=2.2)
fig.tight_layout(rect=[0, 0, 1, 0.955])
for ext in ('png', 'pdf'): fig.savefig(f'{OUT}.{ext}', bbox_inches='tight')
print('saved', OUT + '.png /.pdf')

with open(OUT + '.tsv', 'w', newline='') as fh:
    w = csv.writer(fh, delimiter='\t')
    w.writerow(['group', 'x_label', 'point', 'metric', 'baseline', 'f2load_mean', 'f2load_sd', 'n'])
    for g, pts in GROUPS:
        for lab, pt in pts:
            for m, _, _ in METRICS:
                mu, sd, n = F2[pt][m]
                w.writerow([g, lab, pt, m, f'{BASE[pt][m]:.4f}', f'{mu:.4f}', f'{sd:.4f}', n])
print('saved', OUT + '.tsv')
