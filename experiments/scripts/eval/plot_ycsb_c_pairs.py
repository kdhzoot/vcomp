#!/usr/bin/env python3
"""15쌍 YCSB C: baseline vs F2Load actual-value small multiples (축별 그룹)."""
import json, re, glob, sys
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
A = 'experiments/artifacts/'
def cell(path):
    r = json.load(open(path)); t = open(path.replace('result.json', 'stdout_stderr.log'), errors='ignore').read()
    c = {k: int(m.group(1)) for k in ['number.keys.read', 'memtable.hit', 'l0.hit', 'l1.hit', 'l2andup.hit', 'block.cache.miss']
         for m in [re.search(r'rocksdb\.' + re.escape(k) + r' COUNT : (\d+)', t)] if m}
    g = c['number.keys.read']
    def hist(name):
        m = re.search(r'rocksdb\.' + re.escape(name) + r' P50 : ([\d.]+) P95 : ([\d.]+) P99 : ([\d.]+) P100 : [\d.]+ COUNT : (\d+) SUM : (\d+)', t)
        return dict(p99=float(m.group(3)), mean=int(m.group(5)) / max(int(m.group(4)), 1))
    return dict(ops=r['ops_per_sec'] / 1e6, get=hist('db.get.micros')['mean'], sstp99=hist('sst.read.micros')['p99'],
                sstmean=hist('sst.read.micros')['mean'], fpr=r['filter_probes_per_read'],
                found=100 * sum(v for k, v in c.items() if k not in ('number.keys.read', 'block.cache.miss')) / g,
                miss=c['block.cache.miss'] / g, ssts=r['source_identity_ssts'] / 1e3)
def find(pats):
    for p in pats:
        g = sorted(glob.glob(p))
        if g: return cell(g[-1])
RUN = sys.argv[1] if len(sys.argv) > 1 else 'fix_20260913_ycsbc'   # 셀 디렉토리
TAG = sys.argv[2] if len(sys.argv) > 2 else 'zipfian, hard-link staging'
FIX = A + RUN + '/'
BASE = {'P0': [A + 'eval_20260912_ycsb_af/P0-r1_baseline__workloadc/result.json'],
        'E1-100B': [A + 'eval_*/E1-100B_baseline__workloadc/result.json'], 'E1-10KB': [A + 'eval_*/E1-10KB_baseline__workloadc/result.json'],
        'E2-2T': [FIX + 'E2-2T_baseline__workloadc/result.json'], 'E2-4T': [FIX + 'E2-4T_baseline__workloadc/result.json'], 'E2-8T': [FIX + 'E2-8T_baseline__workloadc/result.json'],
        'E3-u25': [A + 'eval_*/E3-u25_baseline__workloadc/result.json'], 'E3-u50': [A + 'eval_*/E3-u50_baseline__workloadc/result.json'],
        'E3-u75': [A + 'eval_*/E3-u75_baseline__workloadc/result.json'], 'E3-u100': [A + 'eval_*/E3-u100_baseline__workloadc/result.json'],
        'E7-lz4': [A + 'eval_*/E7-lz4_baseline__workloadc/result.json'],
        'E4-256m4': [A + 'eval_*/E4-256m4_baseline__workloadc/result.json'], 'E4-1024m10': [A + 'eval_*/E4-1024m10_baseline__workloadc/result.json'],
        'E5-sst16': [FIX + 'E5-sst16_baseline__workloadc/result.json'], 'E5-sst256': [FIX + 'E5-sst256_baseline__workloadc/result.json']}
F2 = {k: [FIX + ('P0fix-r1' if k == 'P0' else k) + '_f2load__workloadc/result.json'] for k in BASE}
# 축별 그룹 (표시 라벨)
GROUPS = [('KV size', [('E1-100B', '100 B'), ('P0', '1 KB'), ('E1-10KB', '10 KB')]),
          ('Dataset size', [('P0', '1 TB'), ('E2-2T', '2 TB'), ('E2-4T', '4 TB'), ('E2-8T', '8 TB')]),
          ('Key uniqueness', [('E3-u25', '25%'), ('E3-u50', '50%'), ('E3-u75', '75%'), ('E3-u100', '100%')]),
          ('Compression', [('P0', 'none'), ('E7-lz4', 'LZ4')]),
          ('Level structure', [('E4-256m4', '256M x4'), ('P0', '256M x10'), ('E4-1024m10', '1G x10')])]
METRICS = [('ops', 'Throughput (M ops/s)'), ('fpr', 'Filter checks / lookup'),
           ('found', 'Positive lookups (%)'), ('ssts', 'SST count (K)')]
if RUN != 'fix_20260913_ycsbc':
    BASE = {k: [FIX + ('P0-r1' if k == 'P0' else k) + '_baseline__workloadc/result.json'] for k in BASE}
data = {k: (find(BASE[k]), find(F2[k])) for k in BASE}
fig, axes = plt.subplots(len(METRICS), len(GROUPS), figsize=(3.1 * len(GROUPS), 2.2 * len(METRICS)), sharey='row')
C_B, C_F = '#4C72B0', '#DD8452'
for i, (mk, mlabel) in enumerate(METRICS):
    for j, (gname, pts) in enumerate(GROUPS):
        ax = axes[i][j]; xs = range(len(pts)); w = 0.38
        bv = [data[k][0][mk] for k, _ in pts]; fv = [data[k][1][mk] for k, _ in pts]
        ax.bar([x - w / 2 for x in xs], bv, w, color=C_B, label='baseline')
        ax.bar([x + w / 2 for x in xs], fv, w, color=C_F, label='F2Load')
        ax.set_xticks(list(xs)); ax.set_xticklabels([l for _, l in pts], fontsize=8)
        ax.tick_params(axis='y', labelsize=8); ax.grid(axis='y', alpha=0.3)
        for x, (b, f) in enumerate(zip(bv, fv)):
            if b: ax.text(x, max(b, f) * 1.02, f'{100 * (f / b - 1):+.0f}%', ha='center', fontsize=7, color='#333')
        if i == 0: ax.set_title(gname, fontsize=10)
        if j == 0: ax.set_ylabel(mlabel, fontsize=8)
        ax.set_ylim(0, ax.get_ylim()[1] * 1.15)
axes[0][0].legend(fontsize=8, loc='upper left')
fig.suptitle(f'YCSB C ({TAG}; 48 threads, 300 s, 50 GiB cache): baseline vs F2Load, one option changed from P0 per group; '
             'labels = F2Load relative to baseline', fontsize=10)
fig.tight_layout(rect=(0, 0, 1, 0.97))
out = 'experiments/results/ycsb_c_15pairs_260914' + ('' if RUN == 'fix_20260913_ycsbc' else '_' + RUN.split('_')[-2] + '_' + RUN.split('_')[-1])
fig.savefig(out + '.png', dpi=130); fig.savefig(out + '.pdf'); print('saved', out + '.png/.pdf')
