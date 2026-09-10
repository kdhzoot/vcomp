#!/usr/bin/env python3
"""Plot validated YCSB results for discussion; never modify the manuscript.

Panel A compares same-workload mean throughput. Panel B exposes nonstationary
Workload D behavior using non-overlapping ten-second report intervals.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np

SYSTEMS = ('baseline', 'last_comp', 'fillseq', 'fillseq_ow', 'f2load')
LABELS = ('Baseline', 'Last compaction', 'Fillseq', 'Fillseq + 10% OW', 'F2Load')
COLORS = ('#575757', '#B56596', '#009E73', '#E69F00', '#0072B2')
HATCHES = ('', '///', '..', '\\\\', '')
MIXES = ('50% read / 50% update', '95% read / 5% update', '100% read',
         '95% read / 5% insert', '95% scan / 5% insert', '50% read / 50% RMW')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_tsv(path, rows):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    result_dir, output = args.result_dir.resolve(), args.output_dir.resolve()
    workspace = Path(__file__).resolve().parents[3]
    if (workspace / 'paper').resolve() in (output, *output.parents):
        raise ValueError('This preview script must not write to the paper tree')
    if output.exists():
        raise ValueError('Use a fresh output directory; do not overwrite previous previews')
    manifest = json.loads((result_dir / 'manifest.json').read_text())
    all_rows = json.loads((result_dir / 'results.json').read_text())
    rows = [r for r in all_rows if r['phase'] == 'full']
    expected = {(s, 'workload' + w) for s in SYSTEMS for w in 'abcdef'}
    matrix = {(r['system'], r['workload']): r for r in rows}
    assert len(rows) == len(matrix) == 30 and set(matrix) == expected
    assert manifest['excluded_systems'] == ['flush_only']
    assert manifest['cache_size_bytes'] == 1 and manifest['automatic_compaction']
    for row in rows:
        assert row['status'] == 'ok' and row['exit_code'] == 0 and not row['timed_out']
        assert row['duration_sec'] == 300 and abs(row['measured_seconds'] - 300) < 0.1
        assert row['threads'] == 48 and row['binary_sha256'] == manifest['binary_sha256']
        assert not row['missing_tickers']
        assert sum(row['workload_operation_counts'].values()) == row['operations']

    metrics, intervals, report_sources = [], [], {}
    for letter in 'abcdef':
        workload = 'workload' + letter
        baseline = matrix['baseline', workload]['throughput_ops_sec']
        for system in SYSTEMS:
            row = matrix[system, workload]
            op = row['operations']
            gets = row['engine_keys_read']
            metrics.append(dict(
                workload=letter.upper(), system=system,
                throughput_kops_sec=row['throughput_ops_sec'] / 1000,
                throughput_relative_to_baseline=row['throughput_ops_sec'] / baseline,
                measured_seconds=row['measured_seconds'],
                get_found_fraction=row['get_found_fraction'],
                memtable_hits_per_get=row['memtable_hits'] / gets if gets else None,
                filter_misses_per_op=row['filter_cache_miss'] / op,
                index_misses_per_op=row['index_cache_miss'] / op,
                data_misses_per_op=row['data_cache_miss'] / op,
                compaction_read_gib=row['compaction_read_bytes'] / 2**30,
                compaction_write_gib=row['compaction_write_bytes'] / 2**30,
                flush_write_gib=row['flush_write_bytes'] / 2**30,
                engine_stall_seconds=row['stall_micros'] / 1e6,
                reused_from_run=row.get('reused_from_run', ''),
                raw_log_dir=row['log_dir']))
            report = Path(row['log_dir']) / 'report.rep'
            with report.open() as stream:
                records = list(csv.DictReader(stream))
            samples = [(float(r['secs_elapsed']), float(r['interval_qps'])) for r in records]
            assert all(samples[i][0] > samples[i-1][0] for i in range(1, len(samples)))
            assert len(samples) in (299, 300)
            assert [t for t, _ in samples] == list(range(1, len(samples) + 1))
            report_sources[str(report)] = digest(report)
            for start in range(0, 300, 10):
                values = [q for t, q in samples if start < t <= start + 10]
                assert len(values) == 10 or (start == 290 and len(values) == 9), \
                    (report, start, len(values))
                intervals.append(dict(workload=letter.upper(), system=system,
                    start_sec=start, end_sec=min(start + 10, samples[-1][0]),
                    report_samples=len(values),
                    mean_throughput_kops_sec=float(np.mean(values)) / 1000))

    output.mkdir(parents=True)
    write_tsv(output / 'throughput_and_diagnostics.tsv', metrics)
    write_tsv(output / 'throughput_10s.tsv', intervals)
    plt.rcParams.update({
        'font.family': 'DejaVu Sans', 'font.size': 12,
        'axes.labelsize': 12, 'axes.titlesize': 13,
        'xtick.labelsize': 11, 'ytick.labelsize': 11,
        'axes.linewidth': 0.8, 'pdf.fonttype': 42, 'ps.fonttype': 42,
        'hatch.linewidth': 0.6,
    })
    fig = plt.figure(figsize=(14.5, 10.8), facecolor='white')
    grid = fig.add_gridspec(2, 1, height_ratios=(1.35, 1),
                          left=0.08, right=0.985, bottom=0.14, top=0.84, hspace=0.43)
    top, bottom = fig.add_subplot(grid[0]), fig.add_subplot(grid[1])
    fig.suptitle('YCSB throughput after loading', x=0.08, ha='left',
                 y=0.978, fontsize=19, fontweight='semibold')
    fig.text(0.08, 0.942,
             '1,000 GiB input  |  48 threads  |  300 s/run  |  Cached 0 GB  |  Compaction ON',
             color='#444444', fontsize=12)
    handles = [Patch(facecolor=c, edgecolor='#262626', hatch=h, linewidth=0.65, label=l)
               for c, h, l in zip(COLORS, HATCHES, LABELS)]
    fig.legend(handles=handles, loc='upper center', bbox_to_anchor=(0.53, 0.913),
               ncol=5, frameon=False, fontsize=12, handlelength=2.0, columnspacing=2.0)

    x, width = np.arange(6), 0.174
    for i, system in enumerate(SYSTEMS):
        values = [matrix[system, 'workload' + w]['throughput_ops_sec'] /
                  matrix['baseline', 'workload' + w]['throughput_ops_sec'] for w in 'abcdef']
        bars = top.bar(x + (i - 2) * width, values, width=width * 0.93,
                       color=COLORS[i], edgecolor='#262626', linewidth=0.65,
                       hatch=HATCHES[i], zorder=3)
        for bar, value in zip(bars, values):
            top.text(bar.get_x() + bar.get_width() / 2, value + 0.075,
                     f'{value:.2f}', ha='center', va='bottom', fontsize=9.5,
                     fontweight='semibold' if system == 'f2load' else 'normal')
    top.set_title('(a) Mean throughput relative to the same-workload Baseline', loc='left', pad=13)
    top.set_ylabel('Normalized throughput (×)')
    top.set_ylim(0, 6.2)
    top.set_yticks(np.arange(0, 7))
    top.set_xlim(-0.6, 5.6)
    top.set_xticks(x)
    top.set_xticklabels([f'{w.upper()}\n{mix}' for w, mix in zip('abcdef', MIXES)], fontsize=10.5)
    top.tick_params(axis='x', length=0, pad=9)
    top.axhline(1, color='#909090', linewidth=0.8, zorder=2)

    for i, system in enumerate(SYSTEMS):
        series = [r for r in intervals if r['workload'] == 'D' and r['system'] == system]
        bottom.plot([(r['start_sec'] + r['end_sec']) / 2 for r in series],
                    [r['mean_throughput_kops_sec'] for r in series],
                    color=COLORS[i], linewidth=2.3 if system == 'f2load' else 1.8,
                    marker=('o', 's', '^', 'D', 'o')[i], markersize=4,
                    markevery=3, label=LABELS[i], zorder=4 if system == 'f2load' else 3)
    bottom.set_title('(b) Workload D over time: averages of non-overlapping 10-second intervals',
                     loc='left', pad=12)
    bottom.set_xlabel('Elapsed workload time (s)')
    bottom.set_ylabel('Throughput (kops/s)')
    bottom.set_xlim(0, 300)
    bottom.set_ylim(0, 240)
    bottom.set_xticks(np.arange(0, 301, 30))
    bottom.set_yticks(np.arange(0, 241, 40))
    for ax in (top, bottom):
        ax.spines[['top', 'right']].set_visible(False)
        ax.grid(axis='y', color='#DFDFDF', linewidth=0.65, zorder=0)
        ax.set_axisbelow(True)
    fig.text(0.08, 0.028,
             'One run per cell; no error bars. Flush-only excluded; Baseline A reused from run1.\n'
             'Initial key membership differs across DBs. A/B/C/E/F: Zipfian; D: Latest. Preview only.',
             fontsize=10, color='#505050', linespacing=1.5)
    for suffix in ('pdf', 'svg', 'png'):
        fig.savefig(output / ('ycsb_overview.' + suffix), dpi=180, facecolor='white')
    plt.close(fig)
    shutil.copy2(Path(__file__), output / Path(__file__).name)
    (output / 'provenance.json').write_text(json.dumps(dict(
        result_directory=str(result_dir), source_hashes={
            str(result_dir / name): digest(result_dir / name)
            for name in ('manifest.json', 'results.json')},
        source_report_hashes=report_sources,
        plot_script_sha256=digest(Path(__file__)),
        selected_cells=30, aggregation='same-workload baseline normalization; 10s non-overlapping QPS means; final bin uses 9 or 10 available samples without extrapolation',
        manuscript_modified=False), indent=2) + '\n')
    print(output / 'ycsb_overview.png')
    for letter in 'ABCDEF':
        print(letter, [(r['system'], round(r['throughput_kops_sec'], 3),
                        round(r['throughput_relative_to_baseline'], 4))
                       for r in metrics if r['workload'] == letter])


if __name__ == '__main__':
    main()
