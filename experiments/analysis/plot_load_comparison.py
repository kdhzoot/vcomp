#!/usr/bin/env python3
"""
Plot single-load comparison (baseline fillrandom vs vcomp fillvirtual)
across two size classes (250 GB and 1 TB).

Reads each load directory's raw/elapsed_sec.txt for elapsed time and
parses bench.out's final 'Sum' compaction-stats line plus the final
'Cumulative writes' line for byte counts. The "total disk writes" figure
is computed as ingest_GB + compaction_write_GB for baseline (memtable
flush ≈ ingest + compaction reshuffle) and as the final DB size for
vcomp (Phase 2 materializes SSTs directly, no compaction or ingest is
counted).

Usage:
    python3 plot_load_comparison.py
"""

import os
import re
import subprocess
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator
import numpy as np

from paths import LOG_LOADS as LOG_LOADS_PATH

LOG_LOADS = str(LOG_LOADS_PATH)

EXPERIMENTS = [
    ('250 GB', 'baseline', 'baseline_260414_1305_250gb'),
    ('250 GB', 'vcomp',    'vcomp_260414_1648_250gb'),
    ('1 TB',   'baseline', 'baseline_260409_1144_1000gb'),
    ('1 TB',   'vcomp',    'vcomp_260414_1346_1000gb'),
    ('5 TB',   'baseline', 'baseline_260430_1619_5120gb'),
    ('5 TB',   'vcomp',    'vcomp_260430_2201_5120gb'),
    ('10 TB',  'baseline', 'baseline_260430_2232_10240gb'),
    ('10 TB',  'vcomp',    'vcomp_260501_1239_10240gb'),
]


SIZE_UNIT_TO_GB = {'KB': 1/(1024*1024), 'MB': 1/1024, 'GB': 1.0, 'TB': 1024.0}


def parse_load(dirname):
    base = os.path.join(LOG_LOADS, dirname)
    with open(os.path.join(base, 'raw', 'elapsed_sec.txt')) as f:
        elapsed = int(f.read().strip())
    with open(os.path.join(base, 'bench.out'), errors='replace') as f:
        text = f.read()

    sum_lines = [l for l in text.splitlines() if l.startswith(' Sum')]
    # Format: "Sum  files/sub  SIZE UNIT  score  Read  Rn  Rnp1  Write  WPreComp  Wnew  Moved  W-Amp ..."
    # Tokens after split: 0=Sum 1=files/sub 2=SIZE 3=UNIT(GB|TB) 4=score 5=Read 6=Rn 7=Rnp1 8=Write
    parts = sum_lines[-1].split()
    db_size_val      = float(parts[2])
    db_size_unit     = parts[3]
    db_size_gb       = db_size_val * SIZE_UNIT_TO_GB[db_size_unit]
    comp_write_gb    = float(parts[8])  # always GB regardless of size column unit

    ingest_match = list(re.finditer(r'ingest:\s+([\d.]+)\s+GB', text))
    ingest_gb = float(ingest_match[-1].group(1)) if ingest_match else 0.0

    if comp_write_gb == 0.0 and ingest_gb == 0.0:
        total_write_gb = db_size_gb
    else:
        total_write_gb = comp_write_gb + ingest_gb

    return {
        'elapsed_s': elapsed,
        'comp_write_gb': comp_write_gb,
        'ingest_gb': ingest_gb,
        'db_size_gb': db_size_gb,
        'total_write_gb': total_write_gb,
    }


def main():
    rows = []
    for size_class, mode, dirname in EXPERIMENTS:
        d = parse_load(dirname)
        d['size_class'] = size_class
        d['mode'] = mode
        rows.append(d)
        print(f"{size_class:6} {mode:8} elapsed={d['elapsed_s']:>5}s  "
              f"comp_write={d['comp_write_gb']:>8.1f} GB  "
              f"ingest={d['ingest_gb']:>7.1f} GB  "
              f"total_write={d['total_write_gb']:>8.1f} GB")

    size_classes = ['250 GB', '1 TB', '5 TB', '10 TB']
    modes = ['baseline', 'vcomp']
    colors = {'baseline': '#1f77b4', 'vcomp': '#ff7f0e'}

    def get(metric, sc, m):
        for r in rows:
            if r['size_class'] == sc and r['mode'] == m:
                return r[metric]
        return 0

    plt.rcParams.update({
        'font.size': 22,
        'axes.titlesize': 26,
        'axes.labelsize': 24,
        'xtick.labelsize': 22,
        'ytick.labelsize': 22,
        'legend.fontsize': 20,
    })

    fig, axes = plt.subplots(1, 2, figsize=(20, 7))

    bar_w = 0.35
    x = np.arange(len(size_classes))

    def draw(ax, metric, scale, label_fmt, ratio_word, title, ylabel,
             tick_step=None):
        ymax_global = max(get(metric, sc, 'baseline') for sc in size_classes) * scale
        for i, m in enumerate(modes):
            raw_vals = [get(metric, sc, m) for sc in size_classes]
            vals = [v * scale for v in raw_vals]
            offset = (i - 0.5) * bar_w
            bars = ax.bar(x + offset, vals, bar_w, label=m, color=colors[m])
            for b, v_plot, v_raw in zip(bars, vals, raw_vals):
                ax.annotate(label_fmt(v_raw),
                            (b.get_x() + b.get_width()/2, v_plot),
                            ha='center', va='bottom', fontsize=18)
        for i, sc in enumerate(size_classes):
            b = get(metric, sc, 'baseline')
            v = get(metric, sc, 'vcomp')
            ax.text(i, b * scale + ymax_global * 0.16,
                    f'{b/v:.0f}× {ratio_word}',
                    ha='center', fontsize=22, color='green', fontweight='bold')
        ax.set_xticks(x)
        ax.set_xticklabels(size_classes)
        ax.set_ylabel(ylabel, labelpad=15)
        ax.set_title(title, pad=20)
        ax.set_ylim(0, ymax_global * 1.40)
        if tick_step is not None:
            ax.yaxis.set_major_locator(MultipleLocator(tick_step))
        ax.legend(loc='upper left')
        ax.grid(axis='y', alpha=0.3)

    def fmt_time(s):
        if s < 60:
            return f'{int(s)}s'
        if s < 3600:
            return f'{s/60:.0f}m'
        return f'{s/3600:.1f}h'

    draw(axes[0], 'elapsed_s', 1/3600.0,
         fmt_time,
         'faster', 'Loading time', 'Loading time (h)',
         tick_step=2)
    draw(axes[1], 'total_write_gb', 1/1024.0,
         lambda v: f'{v/1024:.1f} TB' if v >= 1024 else f'{v:.0f} GB',
         'less', 'Total disk writes', 'Total disk writes (TB)',
         tick_step=50)

    plt.tight_layout()

    out = os.path.join(LOG_LOADS, 'load_comparison.png')
    plt.savefig(out, dpi=120, bbox_inches='tight')
    print(f"\nSaved: {out}")


if __name__ == '__main__':
    main()
