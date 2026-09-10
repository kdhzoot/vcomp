#!/usr/bin/env python3
"""Analysis-only Figure-5 metrics and per-level diagnostics; no paper writes."""
import csv
import json
from pathlib import Path
import re
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

EXP = Path(__file__).resolve().parents[1]
BUNDLE = EXP / 'results/baseline_repeat_ycsb_c_260908_run1'
OUT = EXP / 'artifacts/figure_preview/baseline_repeat_ycsb_c_260908'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = json.loads((BUNDLE / 'comparison.json').read_text())['rows']
    labels = ['Baseline\noriginal*', 'Baseline\nrepeat 1', 'Baseline\nrepeat 2', 'F2Load*']
    colors = ['#5B5B5B', '#D89027', '#299572', '#3478B5']
    for row in rows:
        raw = Path(row['source_evidence']['log']['path']).read_text()
        tickers = {k: int(v) for k, v in re.findall(
            r'^(rocksdb\.[\w.]+)\s+COUNT\s*:\s*(\d+)', raw, re.M)}
        row['filter_checks_per_lookup'] = (tickers['rocksdb.bloom.filter.useful'] +
            tickers['rocksdb.bloom.filter.full.positive']) / row['gets']
        row['all_level_file_reads_per_get'] = sum(
            v['count_per_get'] for v in row['file_reads_by_level'].values())
        row['summed_file_read_us_per_get'] = sum(
            v['weighted_latency_us_per_get'] for v in row['file_reads_by_level'].values())
    (OUT / 'plot_data.json').write_text(json.dumps(rows, indent=2) + '\n')
    fields = ['label', 'filter_checks_per_lookup', 'get_found_fraction',
              'throughput_ops_sec', 'throughput_relative_to_original_baseline',
              'all_level_file_reads_per_get', 'summed_file_read_us_per_get']
    with (OUT / 'plot_data.tsv').open('w') as stream:
        writer = csv.DictWriter(stream, fields, delimiter='\t', extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 12,
                         'axes.titlesize': 13, 'axes.labelsize': 12,
                         'pdf.fonttype': 42})
    fig, axes = plt.subplots(2, 3, figsize=(16, 9))
    fig.subplots_adjust(left=.065, right=.985, bottom=.15, top=.875,
                        wspace=.3, hspace=.65)
    x = np.arange(4)
    def bars(ax, values, title, ylabel, upper, texts=None):
        ax.bar(x, values, color=colors, edgecolor='#222222', linewidth=.65, width=.66)
        ax.set(xticks=x, xticklabels=labels, ylim=(0, upper), title=title, ylabel=ylabel)
        for i, value in enumerate(values):
            ax.text(i, value + upper*.018, texts[i] if texts else f'{value:.2f}',
                    ha='center', va='bottom', fontsize=11)
    bars(axes[0, 0], [r['filter_checks_per_lookup'] for r in rows],
         '(a) Filter checks / lookup', 'Checks / Get', 4.6)
    bars(axes[0, 1], [100*r['get_found_fraction'] for r in rows],
         '(b) Positive lookups', 'Successful Gets (%)', 100,
         [f"{100*r['get_found_fraction']:.2f}%" for r in rows])
    bars(axes[0, 2], [r['throughput_relative_to_original_baseline'] for r in rows],
         '(c) Read throughput', 'Throughput / original baseline', 3.35,
         [f"{r['throughput_relative_to_original_baseline']:.2f}x\n"
          f"{r['throughput_ops_sec']/1000:.2f} Kops/s" for r in rows])
    axes[0, 2].axhline(1, color='#888888', linewidth=.8)
    levels = range(1, 6)
    for ax, field, title, ylabel in [
        (axes[1, 0], 'count_per_get', '(d) File-read count by level', 'SST file reads / Get'),
        (axes[1, 1], 'average_us', '(e) File-read latency by level', 'Mean per SST file read (us)')]:
        for i, row in enumerate(rows):
            ax.bar(np.arange(5)+(i-1.5)*.19,
                   [row['file_reads_by_level'][str(l)][field] for l in levels],
                   width=.18, color=colors[i], edgecolor='#222222', linewidth=.4,
                   label=labels[i].replace('\n', ' '))
        ax.set(xticks=np.arange(5), xticklabels=[f'L{l}' for l in levels],
               title=title, ylabel=ylabel)
        ax.set_ylim(bottom=0)
    axes[1, 0].legend(loc='upper left', ncol=2, fontsize=9, frameon=False)
    axes[1, 0].set_ylim(0, 1.85)
    bottom = np.zeros(4)
    level_colors = ['#AC6275', '#DCAC5B', '#71A591', '#719CC6', '#A8A2BA']
    for level, color in zip(levels, level_colors):
        vals = [r['file_reads_by_level'][str(level)]['weighted_latency_us_per_get'] for r in rows]
        axes[1, 2].bar(x, vals, bottom=bottom, color=color, width=.66,
                      edgecolor='white', linewidth=.6, label=f'L{level}')
        bottom += vals
    axes[1, 2].set(xticks=x, xticklabels=labels,
                  title='(f) File-read time per Get', ylabel='Sum(count x mean) / Gets (us)',
                  ylim=(0, max(bottom)*1.2))
    for i, value in enumerate(bottom):
        axes[1, 2].text(i, value+max(bottom)*.015, f'{value:.1f}', ha='center', fontsize=11)
    axes[1, 2].legend(ncol=5, fontsize=9, frameon=False, loc='upper right')
    for ax in axes.flat:
        ax.spines[['top', 'right']].set_visible(False)
        ax.set_axisbelow(True)
        ax.grid(axis='y', color='#dddddd', linewidth=.6)
        ax.tick_params(axis='x', length=0)
    fig.suptitle('Baseline loading variation: Figure 5 metrics + read-cost diagnostics',
                 fontsize=18, y=.978)
    fig.text(.5, .925, 'YCSB-C | Zipfian | cached 0 GiB (1-byte cache) | 48 threads | 300 s per DB',
             ha='center', fontsize=13)
    fig.text(.065, .062,
        '* Original baseline and F2Load reuse previous measurements; repeat 1/2 are new reads.\n'
        'Analysis preview only: not the paper\'s uniform-read / four-cache-configuration matrix.\n'
        '(f) is accumulated file-reader time per Get, not an exact wall-time breakdown. All four runs: compaction I/O = 0.',
        fontsize=10.5, va='center', linespacing=1.55)
    for suffix in ('png', 'pdf'):
        fig.savefig(OUT / ('figure5_comparison.'+suffix), dpi=170, facecolor='white')
    plt.close(fig)
    print(OUT)
    for row in rows:
        print(row['label'], 'filter checks', row['filter_checks_per_lookup'],
              'all-level reads/Get', row['all_level_file_reads_per_get'],
              'weighted read us/Get', row['summed_file_read_us_per_get'])


if __name__ == '__main__':
    main()
