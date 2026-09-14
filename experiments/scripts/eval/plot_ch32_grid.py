#!/usr/bin/env python3
"""15쌍 uniform 결과를 한 파일에: 행 = 지표 4개, 열 = 축 6개 (각 열에 P0 기준 포함), ch32 막대 스타일."""
import json, re, sys, glob
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator, FuncFormatter

DIST = sys.argv[1] if len(sys.argv) > 1 else 'uniform'
F2SRC = sys.argv[2] if len(sys.argv) > 2 else 'bitmap'   # bitmap | nobitmap
A = 'experiments/artifacts/'
# uniform: 딥카피 체인 한 디렉토리. zipfian: 과거 캠페인 + 수정 후 F2Load 재측정(fix_20260913_ycsbc).
UNIFORM = A + 'fix_20260914_ycsbc_uniform_deep/'
OUT = ('experiments/results/paper_ch32/ch32_grid_' + DIST + ('' if F2SRC == 'bitmap' else '_nobitmap'))
C = dict(base='#0072B2', kv='#CC79A7', ds='#E69F00', uq='#56B4E9', lv='#009E73', cp='#F9F4B3', sst='#D55E00')
GROUPS = [('KV size',        'kv',  [('E1-100B', 'E1-100B', '100 B'), ('P0-r1', 'P0fix-r1', '1 KB'), ('E1-10KB', 'E1-10KB', '10 KB')]),
          ('Dataset size',   'ds',  [('P0-r1', 'P0fix-r1', '1 TB'), ('E2-2T', 'E2-2T', '2 TB'), ('E2-4T', 'E2-4T', '4 TB'), ('E2-8T', 'E2-8T', '8 TB')]),
          ('Key uniqueness', 'uq',  [('E3-u25', 'E3-u25', '25%'), ('E3-u50', 'E3-u50', '50%'),
                              ('P0-r1', 'P0fix-r1', '63%'),
                              ('E3-u100', 'E3-u100@global_unique', '100%')]),
          ('Compression',    'cp',  [('P0-r1', 'P0fix-r1', 'off'), ('E7-lz4', 'E7-lz4', 'on')]),
          ('Level structure','lv',  [('E4-256m4', 'E4-256m4', '256MB x 4'), ('P0-r1', 'P0fix-r1', '256MB x 10'), ('E4-1024m10', 'E4-1024m10', '1024MB x 10')])]
METRICS = [('ops', 'Throughput\n(ops/s)', 'big'), ('fpr', 'Filter checks\n/ lookup', 'dec'),
           ('found', 'Positive\nlookup (%)', 'pct'), ('ssts', 'SST count', 'big')]
fam = 'Cambria' if any('cambria' in f.name.lower() for f in font_manager.fontManager.ttflist) else 'DejaVu Serif'
plt.rcParams.update({'font.family': fam, 'hatch.linewidth': 2.0})
def resolve(name):
    hint = None
    if '@' in name:
        base, hint = name.split('@', 1)
        if hint.endswith('_f2load'): hint = hint[:-len('_f2load')]
        name = base if base.endswith('_f2load') else base + '_f2load'
    if hint:
        for p in sorted(glob.glob(A + '*/' + name + '__workloadc/result.json')):
            if 'uniform' in p.split('/')[2]: continue
            if hint in json.load(open(p)).get('source', ''): return p
        return None
    if F2SRC == 'nobitmap' and name.endswith('_f2load'):
        import os
        for p in sorted(glob.glob(A + '*/' + name + '__workloadc/result.json')):
            if 'nobitmap' in json.load(open(p)).get('source', ''):
                return p
        return None          # 아직 측정 전
    if DIST == 'uniform':
        return UNIFORM + name + '__workloadc/result.json'
    # zipfian: 수정 후 F2Load는 fix_20260913_ycsbc, baseline은 그 외 캠페인 디렉토리에서 최신
    pref = sorted(glob.glob(A + 'fix_20260913_ycsbc/' + name + '__workloadc/result.json'))
    if pref: return pref[-1]
    cand = [p for p in sorted(glob.glob(A + 'eval_*/' + name + '__workloadc/result.json'))
            if 'superseded' not in p]
    if not cand: raise SystemExit('zipfian 셀 없음: ' + name)
    return cand[-1]

def cell(name):
    path = resolve(name)
    if path is None: return None
    r = json.load(open(path))
    pl = r.get('positive_lookup_pct')
    if pl is None:   # 과거 셀: 레벨 hit 합 / Get 수로 재계산
        t = open(path.replace('result.json', 'stdout_stderr.log'), errors='ignore').read()
        c = {k: int(m.group(1)) for k in ('memtable.hit', 'l0.hit', 'l1.hit', 'l2andup.hit', 'number.keys.read')
             for m in [re.search(r'rocksdb\.' + re.escape(k) + r' COUNT : (\d+)', t)] if m}
        pl = 100 * sum(v for k, v in c.items() if k != 'number.keys.read') / c['number.keys.read']
    return dict(ops=r['ops_per_sec'], fpr=r['filter_probes_per_read'], found=pl, ssts=r['source_identity_ssts'])
def fmt(kind):
    def big(v, _=None):
        if v >= 1e6: return f'{v/1e6:.2f}'.rstrip('0').rstrip('.') + 'M'
        if v >= 1e3: return f'{v/1e3:.0f}K'
        return f'{v:.0f}'
    return {'big': big, 'dec': lambda v, _=None: f'{v:.1f}', 'pct': lambda v, _=None: f'{v:.0f}'}[kind]
cache = {}
def get(n):
    if n not in cache: cache[n] = cell(n)
    return cache[n]
P0 = get('P0-r1_baseline')

ratios = [len(pts) for _, _, pts in GROUPS]
fig, axes = plt.subplots(len(METRICS), len(GROUPS), figsize=(1.35 * sum(ratios) + 3.0, 16.0), dpi=200, sharey='row',
                         gridspec_kw={'width_ratios': ratios})
for i, (key, ylabel, kind) in enumerate(METRICS):
    f = fmt(kind)
    rowmax = max(c[key] for _, _, pts in GROUPS for b, fl, _ in pts
                 for c in (get(b + '_baseline'), get(fl + '_f2load')) if c)
    for j, (gname, ck, pts) in enumerate(GROUPS):
        ax = axes[i][j]; x = list(range(len(pts))); w = 0.38
        B = [get(b + '_baseline') for b, _, _ in pts]; F = [get(fl + '_f2load') for _, fl, _ in pts]
        bv = [(c[key] if c else 0) for c in B]; fv = [(c[key] if c else 0) for c in F]
        # 두 색만으로 구분: 채움은 각 색, 테두리는 같은 색을 어둡게 (해치 없음)
        ax.bar([k - w/2 for k in x], bv, w, color='#0072B2', edgecolor='black', linewidth=1.4, zorder=3)
        # F2Load: 흰 바탕 + 초록 빗금. 빗금 색은 edgecolor라 검정 테두리는 따로 덧그린다
        ax.bar([k + w/2 for k in x], fv, w, color='white', edgecolor='#009E73',
               linewidth=0.0, hatch='////', zorder=3)
        ax.bar([k + w/2 for k in x], fv, w, color='none', edgecolor='black', linewidth=1.4, zorder=4)
        pad = rowmax * 0.02
        for k in x:
            ax.text(k - w/2, bv[k] + pad, f(bv[k]) if B[k] else 'n/a', ha='center', va='bottom', fontsize=17, rotation=90, zorder=5)
            ax.text(k + w/2, fv[k] + pad, f(fv[k]) if F[k] else 'n/a', ha='center', va='bottom', fontsize=17, rotation=90, zorder=5)
        ax.set_xlim(-0.6, len(pts) - 0.4)
        ax.set_xticks(x)
        if i == len(METRICS) - 1:
            ax.set_xticklabels([l for _, _, l in pts], fontsize=19, rotation=45, ha='right', rotation_mode='anchor')
        else:
            ax.set_xticklabels([])
        ax.tick_params(axis='x', length=0); ax.tick_params(axis='y', labelsize=21, pad=4, length=4, width=1.0, color='black')
        ax.set_axisbelow(True); ax.grid(axis='y', color='#D9D9D9', linewidth=0.75, zorder=0); ax.grid(axis='x', visible=False)
        for s in ('top', 'right'): ax.spines[s].set_visible(False)
        for s in ('left', 'bottom'): ax.spines[s].set_color('black'); ax.spines[s].set_linewidth(1.0)
        if i == 0: ax.set_title(gname, fontsize=25, pad=14)
        if j == 0:
            ax.set_ylabel(ylabel, fontsize=30, labelpad=14, linespacing=1.1)
            ax.set_ylim(0, rowmax * 1.55)
            ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=(kind != 'dec')))
            ax.yaxis.set_major_formatter(FuncFormatter(f))
leg = fig.legend(handles=[Patch(facecolor='#0072B2', edgecolor='black', linewidth=1.4, label='Baseline'),
                    Patch(facecolor='white', edgecolor='#009E73', hatch='////', linewidth=1.4, label='F2Load')],
           fontsize=22, frameon=False, loc='upper center', ncol=2,
           bbox_to_anchor=(0.5, 1.0), handlelength=1.8, handleheight=1.1, columnspacing=2.2)
# y축 선을 마지막 눈금에서 끊는다 (헤드룸까지 뻗지 않게)
fig.canvas.draw()
for i in range(len(METRICS)):
    lo, hi = axes[i][0].get_ylim()
    ticks = [t for t in axes[i][0].get_yticks() if lo <= t <= hi]
    if ticks:
        for j in range(len(GROUPS)):
            axes[i][j].spines['left'].set_bounds(min(ticks), max(ticks))

fig.subplots_adjust(left=0.085, right=0.995, top=0.915, bottom=0.135, wspace=0.14, hspace=0.10)
fig.align_ylabels([axes[i][0] for i in range(len(METRICS))])
extra = ([leg] + [axes[i][0].yaxis.label for i in range(len(METRICS))]
         + [axes[0][j].title for j in range(len(GROUPS))]
         + [t for i in range(len(METRICS)) for j in range(len(GROUPS)) for t in axes[i][j].get_xticklabels()])
for ext in ('png', 'pdf'):
    fig.savefig(f'{OUT}.{ext}', dpi=200, transparent=True, bbox_inches='tight', bbox_extra_artists=extra, pad_inches=0.1)
print('saved', OUT + '.png/.pdf', '| dist:', DIST, '| font:', fam)
