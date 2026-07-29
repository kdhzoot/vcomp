#!/usr/bin/env python3
"""
Aggregate readrandom results from batch directories and produce a
comparison table.

Usage:
  # Compare two batches (baseline vs vcomp)
  python3 analyze_batch_readrandom.py \
    --baseline artifacts/log_batch/260410_0339_250gb_x30/readrandom_1t_0p baseline \
    --vcomp    artifacts/log_batch/260414_1710_250gb_x30/readrandom_1t_0p vcomp

  # Single batch summary
  python3 analyze_batch_readrandom.py \
    --baseline artifacts/log_batch/260410_0339_250gb_x30/readrandom_1t_0p baseline

  # Export CSV (per-run raw data)
  python3 analyze_batch_readrandom.py \
    --baseline artifacts/log_batch/260410_0339_250gb_x30/readrandom_1t_0p baseline \
    --vcomp    artifacts/log_batch/260414_1710_250gb_x30/readrandom_1t_0p vcomp \
    --csv out.csv

  # Plot box+strip comparison (requires matplotlib + seaborn)
  python3 analyze_batch_readrandom.py \
    --baseline artifacts/log_batch/260410_0339_250gb_x30/readrandom_1t_0p baseline \
    --vcomp    artifacts/log_batch/260414_1710_250gb_x30/readrandom_1t_0p vcomp \
    --plot comparison.png
"""

import argparse
import csv
import glob
import os
import re
import statistics as st
import sys


TICKER_PATS = {
    'filter_miss':  r'rocksdb\.block\.cache\.filter\.miss COUNT : (\d+)',
    'filter_hit':   r'rocksdb\.block\.cache\.filter\.hit COUNT : (\d+)',
    'index_miss':   r'rocksdb\.block\.cache\.index\.miss COUNT : (\d+)',
    'index_hit':    r'rocksdb\.block\.cache\.index\.hit COUNT : (\d+)',
    'data_miss':    r'rocksdb\.block\.cache\.data\.miss COUNT : (\d+)',
    'data_hit':     r'rocksdb\.block\.cache\.data\.hit COUNT : (\d+)',
    'bloom_useful': r'rocksdb\.bloom\.filter\.useful COUNT : (\d+)',
    'bloom_pos':    r'rocksdb\.bloom\.filter\.full\.positive COUNT : (\d+)',
    'bloom_tp':     r'rocksdb\.bloom\.filter\.full\.true\.positive COUNT : (\d+)',
    'l0_hit':       r'rocksdb\.l0\.hit COUNT : (\d+)',
    'l1_hit':       r'rocksdb\.l1\.hit COUNT : (\d+)',
    'l2_hit':       r'rocksdb\.l2\.hit COUNT : (\d+)',
    'l3_hit':       r'rocksdb\.l3\.hit COUNT : (\d+)',
    'l4_hit':       r'rocksdb\.l4\.hit COUNT : (\d+)',
    'l5_hit':       r'rocksdb\.l5\.hit COUNT : (\d+)',
    'l6_hit':       r'rocksdb\.l6\.hit COUNT : (\d+)',
    'non_last_read_bytes': r'rocksdb\.non\.last\.level\.read\.bytes COUNT : (\d+)',
    'non_last_read_count': r'rocksdb\.non\.last\.level\.read\.count COUNT : (\d+)',
    'compact_read':  r'rocksdb\.compact\.read\.bytes COUNT : (\d+)',
    'compact_write': r'rocksdb\.compact\.write\.bytes COUNT : (\d+)',
}

HISTO_PATS = {
    'get_p50':  r'rocksdb\.db\.get\.micros P50 : ([\d.]+)',
    'get_p95':  r'rocksdb\.db\.get\.micros.*P95 : ([\d.]+)',
    'get_p99':  r'rocksdb\.db\.get\.micros.*P99 : ([\d.]+)',
    'get_p100': r'rocksdb\.db\.get\.micros.*P100 : ([\d.]+)',
    'sst_p50':  r'rocksdb\.sst\.read\.micros P50 : ([\d.]+)',
    'sst_p95':  r'rocksdb\.sst\.read\.micros.*P95 : ([\d.]+)',
    'sst_p99':  r'rocksdb\.sst\.read\.micros.*P99 : ([\d.]+)',
}

COUNT_SUM_PATS = {
    'get':  r'rocksdb\.db\.get\.micros.*COUNT : (\d+) SUM : (\d+)',
    'sst':  r'rocksdb\.sst\.read\.micros.*COUNT : (\d+) SUM : (\d+)',
}


def parse_run(stdout_path):
    """Parse a single stdout.txt → dict of raw values."""
    with open(stdout_path, errors='replace') as f:
        txt = f.read()

    row = {}

    m = re.search(
        r'readrandom\s+:\s+([\d.]+) micros/op (\d+) ops/sec '
        r'([\d.]+) seconds (\d+) operations;.*?\((\d+) of (\d+)', txt)
    if not m:
        return None
    row['micros_per_op'] = float(m.group(1))
    row['ops_per_s'] = int(m.group(2))
    row['elapsed'] = float(m.group(3))
    row['found'] = int(m.group(5))
    row['total_ops'] = int(m.group(6))

    for k, pat in HISTO_PATS.items():
        m2 = re.search(pat, txt)
        if m2:
            row[k] = float(m2.group(1))

    for prefix, pat in COUNT_SUM_PATS.items():
        m3 = re.search(pat, txt)
        if m3:
            row[f'{prefix}_count'] = int(m3.group(1))
            row[f'{prefix}_sum'] = int(m3.group(2))

    for k, pat in TICKER_PATS.items():
        m4 = re.search(pat, txt)
        if m4:
            row[k] = int(m4.group(1))

    # Derived
    gc = row.get('get_count', 1)
    row['filter_per_get'] = (row.get('filter_miss', 0) + row.get('filter_hit', 0)) / gc
    row['index_per_get'] = (row.get('index_miss', 0) + row.get('index_hit', 0)) / gc
    row['data_per_get'] = (row.get('data_miss', 0) + row.get('data_hit', 0)) / gc
    row['sst_per_get'] = row.get('sst_count', 0) / gc
    bp = row.get('bloom_pos', 0)
    row['bloom_fpr'] = (1 - row.get('bloom_tp', 0) / bp) * 100 if bp else 0
    nlrc = row.get('non_last_read_count', 0)
    row['bytes_per_sst_read'] = row.get('non_last_read_bytes', 0) / nlrc if nlrc else 0
    total = row.get('total_ops', 0)
    row['found_ratio'] = row.get('found', 0) / total * 100 if total else 0

    return row


def collect_batch(batch_dir, mode_prefix):
    """Collect all runs from a batch readrandom directory."""
    runs = sorted(glob.glob(f"{batch_dir}/{mode_prefix}_run*"))
    rows = []
    for r in runs:
        f = os.path.join(r, 'stdout.txt')
        if not os.path.exists(f):
            continue
        parsed = parse_run(f)
        if parsed:
            parsed['run_dir'] = r
            parsed['run_id'] = os.path.basename(r)
            rows.append(parsed)
    return rows


def print_summary(rows, label):
    """Print summary statistics for a single batch."""
    if not rows:
        print(f"  {label}: no data")
        return

    print(f"\n  {label} (n={len(rows)})")
    metrics = [
        ('elapsed_s',        'elapsed',          '.2f', 1),
        ('ops_per_s',        'ops_per_s',        '.0f', 1),
        ('get_p50 (us)',     'get_p50',          '.2f', 1),
        ('get_p95 (us)',     'get_p95',          '.2f', 1),
        ('get_p99 (us)',     'get_p99',          '.2f', 1),
        ('sst_read_p50 (us)','sst_p50',          '.2f', 1),
        ('sst_read_p95 (us)','sst_p95',          '.2f', 1),
        ('sst_read_p99 (us)','sst_p99',          '.2f', 1),
        ('sst_reads_per_get','sst_per_get',      '.2f', 1),
        ('bytes/sst_read',   'bytes_per_sst_read','.0f', 1),
        ('filter_per_get',   'filter_per_get',   '.4f', 1),
        ('index_per_get',    'index_per_get',    '.4f', 1),
        ('data_per_get',     'data_per_get',     '.4f', 1),
        ('bloom_fpr (%)',    'bloom_fpr',        '.2f', 1),
        ('bloom_useful (M)', 'bloom_useful',     '.2f', 1e6),
        ('found / total',    'found',            '.0f', 1),
        ('l1_hit',           'l1_hit',           '.0f', 1),
        ('l2_hit',           'l2_hit',           '.0f', 1),
        ('l3_hit',           'l3_hit',           '.0f', 1),
        ('l4_hit',           'l4_hit',           '.0f', 1),
    ]
    print(f"  {'metric':<22} {'mean':>12} {'std':>10} {'min':>12} {'max':>12} {'CoV':>7}")
    print(f"  {'-'*76}")
    for label, key, fmt, scale in metrics:
        vals = [r.get(key, 0) / scale for r in rows]
        if not vals:
            continue
        mean = st.mean(vals)
        std = st.pstdev(vals)
        cov = std / mean * 100 if mean else 0
        print(f"  {label:<22} {mean:>12{fmt}} {std:>10{fmt}} {min(vals):>12{fmt}} {max(vals):>12{fmt}} {cov:>6.1f}%")


def print_comparison(base_rows, vcomp_rows):
    """Print side-by-side comparison."""
    metrics = [
        ('elapsed_s',        'elapsed',          '.2f', 1,   'disk-sens'),
        ('ops_per_s',        'ops_per_s',        '.0f', 1,   'disk-sens'),
        ('get_p50 (us)',     'get_p50',          '.2f', 1,   'disk-sens'),
        ('get_p99 (us)',     'get_p99',          '.2f', 1,   'disk-sens'),
        ('sst_p50 (us)',     'sst_p50',          '.2f', 1,   'disk-sens'),
        ('sst_p99 (us)',     'sst_p99',          '.2f', 1,   'disk-sens'),
        ('sst_reads/get',    'sst_per_get',      '.2f', 1,   'disk-sens'),
        ('bytes/sst_read',   'bytes_per_sst_read','.0f',1,   'disk-sens'),
        ('filter/get',       'filter_per_get',   '.4f', 1,   'structural'),
        ('index/get',        'index_per_get',    '.4f', 1,   'structural'),
        ('data/get',         'data_per_get',     '.4f', 1,   'structural'),
        ('bloom_fpr (%)',    'bloom_fpr',        '.2f', 1,   'structural'),
        ('bloom_useful (M)', 'bloom_useful',     '.2f', 1e6, 'structural'),
        ('found / 1M',       'found',            '.0f', 1,   'structural'),
        ('l1_hit',           'l1_hit',           '.0f', 1,   'structural'),
        ('l2_hit',           'l2_hit',           '.0f', 1,   'structural'),
        ('l3_hit',           'l3_hit',           '.0f', 1,   'structural'),
        ('l4_hit',           'l4_hit',           '.0f', 1,   'structural'),
    ]

    nb, nv = len(base_rows), len(vcomp_rows)
    print(f"\n  Comparison: baseline (n={nb}) vs vcomp (n={nv})")
    print(f"  {'metric':<22} {'base mean':>10} {'std':>8}   {'vcomp mean':>10} {'std':>8}   {'Δ%':>8}  {'CoV_b':>5} {'CoV_v':>5}  {'cat':>10}")
    print(f"  {'='*105}")

    prev_cat = None
    for label, key, fmt, scale, cat in metrics:
        if cat != prev_cat:
            print(f"  --- {cat.upper()} ---")
            prev_cat = cat
        bv = [r.get(key, 0) / scale for r in base_rows]
        vv = [r.get(key, 0) / scale for r in vcomp_rows]
        bm, bs = st.mean(bv), st.pstdev(bv)
        vm, vs = st.mean(vv), st.pstdev(vv)
        d = (vm - bm) / bm * 100 if bm else 0
        cb = bs / bm * 100 if bm else 0
        cv = vs / vm * 100 if vm else 0
        print(f"  {label:<22} {bm:>10{fmt}} {bs:>8{fmt}}   {vm:>10{fmt}} {vs:>8{fmt}}   {d:>+7.1f}%  {cb:>4.1f}% {cv:>4.1f}%  {cat:>10}")


def write_csv(base_rows, vcomp_rows, path):
    """Write per-run raw data to CSV."""
    all_rows = []
    for r in base_rows:
        r['mode'] = 'baseline'
        all_rows.append(r)
    for r in vcomp_rows:
        r['mode'] = 'vcomp'
        all_rows.append(r)

    if not all_rows:
        return

    def run_sort_key(r):
        m = re.search(r'(\d+)$', r.get('run_id', ''))
        return (r.get('mode', ''), int(m.group(1)) if m else 0)
    all_rows.sort(key=run_sort_key)

    keys = ['mode', 'run_id', 'elapsed', 'ops_per_s', 'micros_per_op',
            'found', 'total_ops',
            'get_p50', 'get_p95', 'get_p99', 'get_p100',
            'sst_p50', 'sst_p95', 'sst_p99',
            'get_count', 'get_sum', 'sst_count', 'sst_sum',
            'filter_per_get', 'index_per_get', 'data_per_get',
            'sst_per_get', 'bytes_per_sst_read',
            'bloom_fpr', 'bloom_useful', 'bloom_pos', 'bloom_tp',
            'filter_miss', 'filter_hit', 'index_miss', 'index_hit',
            'data_miss', 'data_hit',
            'l0_hit', 'l1_hit', 'l2_hit', 'l3_hit', 'l4_hit', 'l5_hit', 'l6_hit',
            'non_last_read_bytes', 'non_last_read_count',
            'compact_read', 'compact_write']

    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction='ignore')
        w.writeheader()
        for r in all_rows:
            w.writerow(r)
    print(f"\n  CSV written: {path} ({len(all_rows)} rows)")


PLOT_METRICS = [
    # (display_label, data_key, scale, category, y_label)
    ('Throughput',       'ops_per_s',        1,   'Throughput & Latency', 'ops/s'),
    ('Get P50',          'get_p50',          1,   'Throughput & Latency', 'us'),
    ('Get P99',          'get_p99',          1,   'Throughput & Latency', 'us'),
    ('SST Read P50',     'sst_p50',          1,   'Throughput & Latency', 'us'),
    ('SST Read P99',     'sst_p99',          1,   'Throughput & Latency', 'us'),
    ('SST Reads per Get','sst_per_get',      1,   'Throughput & Latency', 'count'),
    ('Filter per Get',   'filter_per_get',   1,   'Block Access & Bloom', 'count'),
    ('Index per Get',    'index_per_get',    1,   'Block Access & Bloom', 'count'),
    ('Data per Get',     'data_per_get',     1,   'Block Access & Bloom', 'count'),
    ('Bloom FPR',        'bloom_fpr',        1,   'Block Access & Bloom', '%'),
    ('Bloom Useful',     'bloom_useful',     1e6, 'Block Access & Bloom', 'millions'),
    ('Found Ratio',      'found_ratio',      1,   'Block Access & Bloom', '%'),
]


def plot_comparison(base_rows, vcomp_rows, out_path):
    """Generate box+strip plot comparing baseline vs vcomp."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import seaborn as sns

    categories = []
    seen = set()
    for _, _, _, cat, *_ in PLOT_METRICS:
        if cat not in seen:
            categories.append(cat)
            seen.add(cat)

    cat_metrics = {c: [] for c in categories}
    for m in PLOT_METRICS:
        cat_metrics[m[3]].append(m)
    # print_comparison also references PLOT_METRICS with 5 fields now
    # but only uses first 4, so backward compat is fine

    nrows = len(categories)
    max_cols = max(len(v) for v in cat_metrics.values())

    plt.rcParams.update({'font.size': 16, 'axes.titlesize': 17,
                         'axes.labelsize': 16, 'xtick.labelsize': 14,
                         'ytick.labelsize': 14})
    fig, axes = plt.subplots(nrows, max_cols,
                             figsize=(4.0 * max_cols, 5.0 * nrows),
                             squeeze=False)
    fig.suptitle('readrandom 1M reads, 1 thread, cache=0  |  '
                 f'baseline n={len(base_rows)}, vcomp n={len(vcomp_rows)}',
                 fontsize=20, fontweight='bold', y=0.995)

    palette = {'baseline': '#4878CF', 'vcomp': '#E1812C'}

    for row_idx, cat in enumerate(categories):
        metrics = cat_metrics[cat]
        for col_idx in range(max_cols):
            ax = axes[row_idx][col_idx]
            if col_idx >= len(metrics):
                ax.set_visible(False)
                continue

            label, key, scale, _, y_label = metrics[col_idx]
            bv = [r.get(key, 0) / scale for r in base_rows]
            vv = [r.get(key, 0) / scale for r in vcomp_rows]

            import pandas as pd
            df = pd.DataFrame({
                'mode': ['baseline'] * len(bv) + ['vcomp'] * len(vv),
                'value': bv + vv,
            })

            sns.boxplot(x='mode', y='value', data=df, ax=ax,
                        palette=palette, width=0.5, linewidth=1.2,
                        fliersize=3, order=['baseline', 'vcomp'])

            ax.set_title(label, fontsize=16)
            ax.set_xlabel('')
            ax.set_ylabel(y_label, fontsize=14)
            ymax = max(bv + vv) * 1.15
            ax.set_ylim(bottom=0, top=ymax)

            bm, vm = st.mean(bv), st.mean(vv)
            d = (vm - bm) / bm * 100 if bm else 0
            ax.text(0.98, 0.02, f'{d:+.1f}%',
                    transform=ax.transAxes, ha='right', va='bottom',
                    fontsize=15, fontweight='bold',
                    color='green' if d < 0 else ('red' if d > 5 else 'gray'))

        axes[row_idx][0].annotate(
            cat, xy=(0, 0.5), xytext=(-60, 0),
            xycoords='axes fraction', textcoords='offset points',
            ha='right', va='center', fontsize=16, fontweight='bold',
            rotation=90)

    plt.tight_layout(rect=[0.04, 0.0, 1.0, 0.97])
    fig.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"\n  Plot saved: {out_path}")


def plot_hit_level(base_rows, vcomp_rows, out_path):
    """Generate grouped bar chart of per-level hit counts."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    plt.rcParams.update({'font.size': 16, 'axes.titlesize': 17,
                         'axes.labelsize': 16, 'xtick.labelsize': 14,
                         'ytick.labelsize': 14})

    levels = ['L1', 'L2', 'L3', 'L4']
    keys = ['l1_hit', 'l2_hit', 'l3_hit', 'l4_hit']

    base_means = [st.mean([r.get(k, 0) for r in base_rows]) for k in keys]
    base_stds  = [st.pstdev([r.get(k, 0) for r in base_rows]) for k in keys]
    vcomp_means = [st.mean([r.get(k, 0) for r in vcomp_rows]) for k in keys]
    vcomp_stds  = [st.pstdev([r.get(k, 0) for r in vcomp_rows]) for k in keys]

    x = np.arange(len(levels))
    width = 0.35

    fig, ax = plt.subplots(figsize=(10, 6))
    bars_b = ax.bar(x - width/2, base_means, width,
                    label=f'baseline (n={len(base_rows)})',
                    color='#4878CF')
    bars_v = ax.bar(x + width/2, vcomp_means, width,
                    label=f'vcomp (n={len(vcomp_rows)})',
                    color='#E1812C')

    ax.set_xlabel('Level')
    ax.set_ylabel('')
    ax.set_title('Per-level Get Hit Distribution', fontsize=18, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(levels)
    ax.legend(fontsize=14)
    ax.set_ylim(bottom=0)

    for bars, means in [(bars_b, base_means), (bars_v, vcomp_means)]:
        for bar, val in zip(bars, means):
            label = f'{val/1000:.1f}K'
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height(),
                    label, ha='center', va='bottom', fontsize=11)

    ax.set_ylabel('Hit count', fontsize=16)
    from matplotlib.ticker import FuncFormatter
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v/1000:.0f}K'))

    plt.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"\n  Hit-level plot saved: {out_path}")


def main():
    ap = argparse.ArgumentParser(description='Aggregate batch readrandom results')
    ap.add_argument('--baseline', nargs=2, metavar=('DIR', 'PREFIX'),
                    help='Baseline batch dir and run prefix (e.g. "artifacts/log_batch/.../readrandom_1t_0p baseline")')
    ap.add_argument('--vcomp', nargs=2, metavar=('DIR', 'PREFIX'),
                    help='VComp batch dir and run prefix')
    ap.add_argument('--csv', type=str, default=None,
                    help='Export per-run raw data to CSV')
    ap.add_argument('--plot', type=str, default='comparison.png',
                    help='Save box+strip comparison plot to file (default: comparison.png)')
    args = ap.parse_args()

    base_rows, vcomp_rows = [], []

    if args.baseline:
        base_rows = collect_batch(args.baseline[0], args.baseline[1])
        print_summary(base_rows, f"baseline ({args.baseline[0]})")

    if args.vcomp:
        vcomp_rows = collect_batch(args.vcomp[0], args.vcomp[1])
        print_summary(vcomp_rows, f"vcomp ({args.vcomp[0]})")

    if base_rows and vcomp_rows:
        print_comparison(base_rows, vcomp_rows)

    if args.csv:
        write_csv(base_rows, vcomp_rows, args.csv)

    if args.plot and base_rows and vcomp_rows:
        plot_comparison(base_rows, vcomp_rows, args.plot)
        hit_path = args.plot.rsplit('.', 1)
        hit_path = hit_path[0] + '_hit_level.' + hit_path[1] if len(hit_path) == 2 else args.plot + '_hit_level.png'
        plot_hit_level(base_rows, vcomp_rows, hit_path)


if __name__ == '__main__':
    main()
