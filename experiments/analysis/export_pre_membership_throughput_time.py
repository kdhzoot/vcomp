#!/usr/bin/env python3
"""Export historical throughput variation; never run a benchmark.

report.rep stores operations in nominal one-second reporter intervals, with
rounded elapsed seconds. Its elapsed origin is reporter launch, not process
launch; process UTC timestamps are recorded separately without false alignment.
"""
import argparse
import csv
import hashlib
import json
import statistics as st
from datetime import datetime
from pathlib import Path


def write_tsv(path, rows):
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, delimiter='\t', fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--io-bundle', type=Path, required=True)
    parser.add_argument('--raw-output-dir', type=Path, required=True)
    args = parser.parse_args()
    out, raw_out = args.io_bundle, args.raw_output_dir
    raw_out.mkdir(parents=True, exist_ok=True)
    io = list(csv.DictReader((out / 'io_latency_all_af.tsv').open(), delimiter='\t'))
    series, windows, summaries, raw_rows, sources, pid_rows = {}, [], [], [], [], []
    for row in io:
        path = Path(row['raw_dir']).parent / 'report.rep'
        qps = [(int(x['secs_elapsed']), int(x['interval_qps']))
               for x in csv.DictReader(path.open())]
        assert len(qps) >= 298
        assert [x[0] for x in qps] == list(range(1, qps[-1][0] + 1))
        assert 299 <= qps[-1][0] <= 301
        key = row['system'], row['arm'], row['workload']
        series[key] = qps
        sources.append(dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        if key[2] == 'C':
            monitor = Path(row['raw_dir']) / 'monitor.jsonl'
            samples = [json.loads(line) for line in monitor.read_text().splitlines()]
            pid_sets = {tuple(sorted(sample['pids'])) for sample in samples}
            pid_rows.append(dict(system=key[0], arm=key[1], samples=len(samples),
                max_monitored_benchmarks=max(len(sample['pids']) for sample in samples),
                distinct_pid_sets=len(pid_sets), stable_pid_set=sorted(pid_sets),
                recorded_names='db_bench,titandb_bench only; no general-process telemetry',
                monitor_path=str(monitor)))
            sources.append(dict(path=str(monitor), sha256=hashlib.sha256(monitor.read_bytes()).hexdigest()))
        for sec, value in qps:
            raw_rows.append(dict(system=key[0], arm=key[1], workload=key[2],
                                 reporter_elapsed_sec=sec, nominal_interval_qps=value))
        item = dict(system=key[0], arm=key[1], workload=key[2],
                    process_start_utc=row['start_utc'], process_end_utc=row['end_utc'],
                    measured_throughput_ops_s=float(row['throughput_ops_s']),
                    reporter_sample_count=len(qps), reporter_last_sec=qps[-1][0])
        for start in range(0, 300, 60):
            item[f'qps_mean_{start}_{start + 60}_sec'] = st.mean(v for t, v in qps if start < t <= start + 60)
        steady = [v for t, v in qps if 60 < t <= 300]
        item['qps_mean_60_300_sec'] = st.mean(steady)
        item['qps_median_60_300_sec'] = st.median(steady)
        item['qps_CV_pct_60_300_sec'] = st.stdev(steady) / st.mean(steady) * 100
        item['one_sec_samples_below_90pct_own_steady_median'] = sum(v < .9 * st.median(steady) for v in steady)
        item['steady_samples'] = len(steady)
        for width in (30, 60):
            for start in range(0, 300, width):
                values = [v for t, v in qps if start < t <= start + width]
                windows.append(dict(system=key[0], arm=key[1], workload=key[2],
                                    window_sec=width, start_sec_exclusive=start,
                                    end_sec_inclusive=start + width, sample_count=len(values),
                                    mean_nominal_qps=st.mean(values), min_nominal_qps=min(values),
                                    max_nominal_qps=max(values)))
        summaries.append(item)
    assert len(summaries) == 120
    write_tsv(out / 'throughput_time_summary.tsv', summaries)
    write_tsv(out / 'throughput_30s_60s_all_af.tsv', windows)
    write_tsv(raw_out / 'throughput_1s_all_af.tsv', raw_rows)
    c_rows = [r for r in summaries if r['workload'] == 'C']
    write_tsv(out / 'throughput_C_minutes.tsv', c_rows)
    (out / 'C_process_monitor_audit.json').write_text(json.dumps(pid_rows, indent=2) + '\n')

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import numpy as np
    colors = dict(f01='#b62633', f04='#087e8b', f08='#d28b16', f09='#7055b0', f10='#227f32')
    focus = ['f01', 'f08', 'f09', 'f04', 'f10']
    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True, layout='constrained')
    base_bins = []
    for key, qps in series.items():
        if key[0] == 'baseline' and key[2] == 'C':
            base_bins.append([st.mean(v for t, v in qps if s < t <= s + 10) / 1e6 for s in range(0, 300, 10)])
    x = np.arange(5, 300, 10)
    axes[0].fill_between(x, np.min(base_bins, axis=0), np.max(base_bins, axis=0),
                         color='gray', alpha=.18, label='Baseline 10 range (10 s bins)')
    for tag in focus:
        qps = series['f2load', tag, 'C']
        times, values = zip(*qps)
        smooth = [st.mean(v for t, v in qps if s < t <= s + 10) for s in range(0, 300, 10)]
        axes[0].plot(times, np.array(values) / 1e6, color=colors[tag], alpha=.18, lw=.65)
        axes[0].plot(x, np.array(smooth) / 1e6, color=colors[tag], label=tag, lw=1.8)
        denominator = st.mean(v for t, v in qps if 60 < t <= 300)
        axes[1].plot(x, np.array(smooth) / denominator, color=colors[tag], label=tag, lw=1.8)
    axes[0].set_title('YCSB C · 300-second throughput traces\n1000 GiB · 48 threads · 50 GiB cache')
    axes[0].set_ylabel('Throughput (Mops/s)')
    axes[0].legend(ncol=3, fontsize=9, loc='lower right')
    axes[0].set_ylim(.7, 1.8)
    axes[1].set_ylabel('Throughput / own 60–300 s mean')
    axes[1].set_ylim(.7, 1.06)
    axes[1].axhline(1, color='gray', ls='--', lw=.8)
    axes[1].axvline(60, color='gray', ls=':', lw=.8)
    axes[1].set_xlabel('Reporter elapsed time (s); thick lines: 10 s means, faint: nominal 1 s')
    for ax in axes:
        ax.grid(alpha=.2)
    fig.savefig(out / 'C_throughput_over_time.png', dpi=160)
    fig.savefig(out / 'C_throughput_over_time.pdf')
    plt.close(fig)

    # All 10 F2Load traces, without picking only the most extreme arms.
    fig, axes = plt.subplots(5, 2, figsize=(11, 11), sharex=True, sharey=True, layout='constrained')
    for index, ax in enumerate(axes.flat, 1):
        tag = f'f{index:02d}'
        times, values = zip(*series['f2load', tag, 'C'])
        ax.fill_between(x, np.min(base_bins, axis=0), np.max(base_bins, axis=0), color='gray', alpha=.18)
        ax.plot(times, np.array(values) / 1e6, color=colors.get(tag, '#23658d'), lw=.8)
        item = next(r for r in c_rows if r['arm'] == tag)
        stamp = datetime.fromisoformat(item['process_start_utc']).strftime('%m-%d %H:%M:%S UTC')
        ax.set_title(f'{tag} · {stamp}', fontsize=11)
        ax.set_ylim(.7, 1.8)
        ax.grid(alpha=.2)
    fig.suptitle('Original F2Load f01–f10 · YCSB C · nominal one-second throughput', fontsize=14)
    fig.supxlabel('Reporter elapsed time (s)')
    fig.supylabel('Throughput (Mops/s); gray: baseline 10 range of 10 s means')
    fig.savefig(out / 'C_all_10_time_traces.png', dpi=140)
    plt.close(fig)

    fig, axes = plt.subplots(2, 1, figsize=(10, 5.8), layout='constrained')
    for ax, tags in zip(axes, [set(f'f{i:02d}' for i in range(1, 6)), set(f'f{i:02d}' for i in range(6, 11))]):
        selection = [r for r in c_rows if r['system'] == 'f2load' and r['arm'] in tags]
        for item in selection:
            start, end = datetime.fromisoformat(item['process_start_utc']), datetime.fromisoformat(item['process_end_utc'])
            qps = item['measured_throughput_ops_s'] / 1e6
            ax.plot([start, end], [qps, qps], color=colors.get(item['arm'], '#23658d'), lw=3)
            ax.annotate(item['arm'], (start, qps), xytext=(2, 6), textcoords='offset points')
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M', tz=start.tzinfo))
        ax.set_ylabel('C mean (Mops/s)')
        ax.set_ylim(1.08, 1.76)
        ax.grid(alpha=.2)
    axes[0].set_title('Actual campaign timing · each segment is one 300-second C process')
    axes[-1].set_xlabel('UTC · gaps between C runs contain other workloads; no throughput imputed')
    fig.savefig(out / 'C_campaign_UTC.png', dpi=160)
    plt.close(fig)
    (out / 'throughput_time_manifest.json').write_text(json.dumps(dict(
        scope='All original 120 full A-F cells; C figures focus on the largest between-run discrepancy.',
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        raw_1s_tsv=str(raw_out / 'throughput_1s_all_af.tsv'),
        methods=[__doc__, '60 s is a descriptive common cutoff for the initial ramp, not a replacement for the original 300 s measurement.',
                 'C r02 and f06 report 299 complete intervals; the unreported final fraction is not invented.',
                 'Different workloads are not mixed into hourly averages; actual process UTC spans are retained.',
                 'A persistent outside workload could produce a stable slowdown. Smooth throughput does not rule it out.'],
        sources=sources), indent=2) + '\n')
    print(json.dumps(dict(cells=len(summaries), nominal_one_sec_samples=len(raw_rows), output=str(out))))


if __name__ == '__main__':
    main()
