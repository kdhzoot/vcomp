#!/usr/bin/env python3
"""§3.3: baseline만, 설정 변경이 읽기 동작을 바꾸는지 — YCSB C uniform, 딥카피."""
import json, glob
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
R = 'experiments/artifacts/fix_20260914_ycsbc_uniform_deep/'
CFG = [('P0-r1_baseline', 'P0'), ('E2-4T_baseline', 'dataset 4 TB'), ('E7-lz4_baseline', 'LZ4'),
       ('E3-u25_baseline', 'uniqueness 25%'), ('E4-256m4_baseline', 'level 256M x4')]
rows = [json.load(open(R + c + '__workloadc/result.json')) for c, _ in CFG]
METRICS = [('Throughput (M ops/s)', lambda r: r['ops_per_sec'] / 1e6),
           ('Filter checks per lookup', lambda r: r['filter_probes_per_read']),
           ('Positive lookups (%)', lambda r: r['positive_lookup_pct'])]
fig, axes = plt.subplots(1, 3, figsize=(11, 3.3))
colors = ['#4C72B0', '#DD8452', '#55A868', '#C44E52', '#8172B2']
for ax, (title, fn) in zip(axes, METRICS):
    vals = [fn(r) for r in rows]
    ax.bar(range(len(vals)), vals, color=colors, width=0.65)
    for i, v in enumerate(vals):
        ax.text(i, v * 1.01, f'{v:.2f}' if v < 10 else f'{v:.1f}', ha='center', va='bottom', fontsize=8)
    ax.set_xticks(range(len(vals))); ax.set_xticklabels([l for _, l in CFG], fontsize=8, rotation=20, ha='right')
    ax.set_title(title, fontsize=10); ax.grid(axis='y', alpha=0.3); ax.set_ylim(0, max(vals) * 1.18)
fig.suptitle('Baseline RocksDB, one option changed from P0 — YCSB C uniform, 48 threads, 300 s, 50 GiB cache, deep-copied DB', fontsize=9)
fig.tight_layout(rect=(0, 0, 1, 0.93))
out = 'experiments/results/baseline_config_effect_uniform_260914'
fig.savefig(out + '.png', dpi=140); fig.savefig(out + '.pdf')
with open(out + '.tsv', 'w') as f:
    f.write('config\tlabel\tops_per_sec\tfilter_checks_per_lookup\tpositive_lookup_pct\tp99_us\tmean_latency_us\n')
    for (c, l), r in zip(CFG, rows):
        f.write(f"{c}\t{l}\t{r['ops_per_sec']}\t{r['filter_probes_per_read']:.4f}\t{r['positive_lookup_pct']:.2f}\t{r['p99_us']}\t{r['micros_per_op']:.1f}\n")
print('saved', out + '.{png,pdf,tsv}')
