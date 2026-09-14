#!/usr/bin/env python3
"""Join original 10+10 A-F device, engine, and process-CPU evidence.

Consumes parse_pre_membership_device_io.py output. Does not open a database or
run a benchmark. Process CPU and device deltas include DB open/close; operation
counts and engine histograms have their original benchmark scope.
"""
import argparse
import csv
import hashlib
import json
import re
import statistics
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_tsv(path, rows):
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device-audit', type=Path, required=True)
    parser.add_argument('--feasibility-audit', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    audit = json.loads(args.device_audit.read_text())
    rows, provenance, windows = [], {}, []
    sources = {}

    def record(path):
        key = str(path)
        if key not in sources:
            sources[key] = {'path': key, 'sha256': digest(path), 'bytes': path.stat().st_size}

    for device in audit['rows']:
        source = Path(device['source_result'])
        if str(source) not in provenance:
            provenance[str(source)] = json.loads(source.read_text())
            run_manifest = source.with_name('manifest.json')
            assert json.loads(run_manifest.read_text())['profiler_commit'] == 'dbb0a44a65344f263356c507ec09762c8ed81a71'
            record(run_manifest)
        result, = [r for r in provenance[str(source)] if r['phase'] == 'full'
                   and r['system'] == device['system']
                   and r['workload'][-1].upper() == device['workload']]
        assert result['status'] == 'ok' and result['exit_code'] == 0
        assert result['threads'] == 48 and result['duration_sec'] == 300
        assert result['throughput_ops_sec'] == device['throughput_ops_s']
        assert device['nvme_members']['member_count'] == 31
        assert result['operations'] == sum(result['workload_operation_counts'].values())
        raw = Path(device['raw'])
        time_text = (raw / 'time.out').read_text()
        command = time_text.splitlines()[0].strip().split(': ', 1)[1]
        assert '--cache_size=53687091200' in command
        assert '--num=1048576000' in command
        assert '--key_size=24' in command and '--value_size=1000' in command
        assert '--use_direct_reads=true' in command and '--seed=87654321' in command
        for file in ('diskstats.start', 'diskstats.end', 'stat.start', 'stat.end',
                     'time.out', 'execution.json', 'iostat.log'):
            record(raw / file)
        record(source)
        record(raw.parent / 'report.rep')

        ops = result['operations']
        nvme, cpu, proc = device['nvme_members'], device['cpu'], device['process_cpu']
        row = dict(system=device['system'], arm=device['tag'], workload=device['workload'],
                   throughput_ops_s=device['throughput_ops_s'], operations=ops,
                   measured_sec=result['measured_seconds'],
                   process_elapsed_sec=device['process_elapsed_sec'],
                   nvme_read_await_us=nvme['read_await_ms'] * 1000,
                   nvme_read_iops=nvme['read_iops'], nvme_read_MiB_s=nvme['read_MiB_s'],
                   nvme_write_MiB_s=nvme['write_MiB_s'],
                   nvme_member_max_read_await_us=nvme['member_max_read_await_ms'] * 1000,
                   nvme_member_mean_util_pct=nvme['util_pct'],
                   queue_timing_counters_usable=False, nvme_aqu_sz=None,
                   device_read_p99_us=None,
                   process_user_cpu_seconds=proc['user_seconds'],
                   process_kernel_cpu_seconds=proc['system_seconds'],
                   process_user_cpu_us_per_ycsb_op=proc['user_seconds'] * 1e6 / ops,
                   process_kernel_cpu_us_per_ycsb_op=proc['system_seconds'] * 1e6 / ops)
        for label, key in [('Voluntary context switches', 'voluntary_context_switches'),
                           ('Involuntary context switches', 'involuntary_context_switches')]:
            match = re.search(re.escape(label) + r':\s*(\d+)', time_text)
            row[key] = int(match[1])
            row[key + '_per_ycsb_op'] = int(match[1]) / ops
        for histogram, prefix in [('rocksdb.sst.read.micros', 'engine_sst_read'),
                                  ('rocksdb.read.block.get.micros', 'engine_get_block_read'),
                                  ('rocksdb.db.get.micros', 'engine_get')]:
            values = result['engine_histograms'][histogram]
            row[prefix + '_count'] = values['count']
            for metric in ('average', 'p50', 'p95', 'p99'):
                row[prefix + '_' + ('mean' if metric == 'average' else metric) + '_us'] = (
                    values[metric] if values['count'] else None)
        read_hist = result.get('operation_histograms', {}).get('read', {})
        row['ycsb_read_p99_us'] = read_hist.get('p99_us')
        tickers = result['tickers']
        bloom_checks = tickers['rocksdb.bloom.filter.useful'] + tickers['rocksdb.bloom.filter.full.positive']
        row['filter_checks_per_engine_get'] = bloom_checks / result['engine_keys_read'] if result['engine_keys_read'] else None
        row['data_cache_misses_per_ycsb_op'] = result['data_cache_misses_per_op']
        row['get_found_fraction'] = result['get_found_fraction']
        row['host_cpu_user_pct'] = cpu['user_pct']
        row['host_cpu_system_pct'] = cpu['system_pct']
        row['host_cpu_iowait_pct'] = cpu['iowait_pct']
        row['host_cpu_snapshot_rows'] = device['cpu_snapshot_logical_cpu_rows']
        samples = list(csv.DictReader((raw.parent / 'report.rep').open()))
        steady_qps = [float(s['interval_qps']) for s in samples if 20 <= float(s['secs_elapsed']) <= 280]
        row['qps_mean_20_to_280_sec'] = statistics.mean(steady_qps)
        row['iostat_sample_count'] = device['iostat_sample_count']
        row['mean_member_util_ge99_intervals'] = device['mean_member_util_ge99_intervals']
        row['longest_mean_member_util_ge99_sec'] = device['longest_mean_member_util_ge99_sec']
        row['start_utc'], row['end_utc'] = device['start_utc'], device['end_utc']
        row['binary_sha256'] = result['binary_sha256']
        row['source_result'], row['raw_dir'] = str(source), str(raw)
        rows.append(row)
        if row['workload'] == 'C':
            for window in device['iostat_30s_windows']:
                windows.append(dict(system=row['system'], arm=row['arm'],
                    approximate_start_sec=window['start_sec'], approximate_end_sec=window['end_sec'],
                    nvme_read_iops=window['read_iops'], nvme_read_MiB_s=window['read_MiB_s'],
                    nvme_read_await_us=window['weighted_read_await_ms'] * 1000,
                    nvme_member_mean_util_pct=window['mean_member_util_pct']))

    assert len(rows) == 120 and len({(r['system'], r['arm'], r['workload']) for r in rows}) == 120
    assert {r['arm'] for r in rows if r['system'] == 'f2load'} == {f'f{i:02d}' for i in range(1, 11)}
    assert len({r['binary_sha256'] for r in rows}) == 1
    rows.sort(key=lambda r: (r['workload'], r['system'], r['arm']))
    write_tsv(out / 'io_latency_all_af.tsv', rows)
    c_rows = [r for r in rows if r['workload'] == 'C']
    write_tsv(out / 'io_latency_C.tsv', c_rows)
    write_tsv(out / 'device_C_30s.tsv', windows)
    summaries = []
    for workload in 'ABCDEF':
        for system in ('baseline', 'f2load'):
            group = [r for r in rows if r['workload'] == workload and r['system'] == system]
            item = dict(system=system, workload=workload, n=len(group))
            for metric in ('throughput_ops_s', 'nvme_read_await_us', 'engine_sst_read_mean_us',
                           'engine_sst_read_p99_us', 'process_kernel_cpu_us_per_ycsb_op'):
                values = [r[metric] for r in group]
                for name, fn in [('mean', statistics.mean), ('min', min), ('max', max)]:
                    item[metric + '_' + name] = fn(values)
            summaries.append(item)
    write_tsv(out / 'workload_summary.tsv', summaries)

    # Keep complete raw-derived monitor data in ignored artifacts, not results.
    for path in (args.device_audit, args.feasibility_audit, Path(__file__),
                 Path(__file__).with_name('parse_pre_membership_device_io.py')):
        record(path)
    feasibility = json.loads(args.feasibility_audit.read_text())
    (out / 'rerun_feasibility.json').write_text(json.dumps(feasibility, indent=2) + '\n')
    commands = []
    for row in rows:
        text = (Path(row['raw_dir']) / 'time.out').read_text()
        commands.append(dict(system=row['system'], arm=row['arm'], workload=row['workload'],
                             command=text.splitlines()[0].strip().split(': ', 1)[1]))
    (out / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    manifest = dict(kind='read-only retrospective analysis; no new measurements',
                    cohort='original baseline 10 and pre-membership F2Load f01-f10',
                    baseline_tags=sorted({r['arm'] for r in rows if r['system'] == 'baseline'}),
                    f2load_tags=[f'f{i:02d}' for i in range(1, 11)],
                    configuration=dict(input_GiB=1000, key_bytes=24, value_bytes=1000,
                                       threads=48, cache_GiB=50, seconds_per_workload=300),
                    measured_binary_sha256=rows[0]['binary_sha256'],
                    measured_profiler_commit='dbb0a44a65344f263356c507ec09762c8ed81a71',
                    methods=audit['method'] + [
                        'Engine SST p99 is a RocksDB histogram percentile, not block-device p99.',
                        'Process CPU/op divides full-process GNU time CPU seconds by measured YCSB operations; it is not individual-operation wall latency.',
                        'Summary means are arithmetic means of ten runs. Mean per-run p99 is not a pooled percentile.',
                        'No rows were excluded, replaced, or rerun.'],
                    sources=list(sources.values()))
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')

    # Three different layers, plotted for all original F2Load runs.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    f2 = [r for r in c_rows if r['system'] == 'f2load']
    baseline = [r for r in c_rows if r['system'] == 'baseline']
    fig, axes = plt.subplots(3, 1, figsize=(9, 7.5), sharex=True, layout='constrained')
    panels = [('throughput_ops_s', 1e6, 'Throughput (Mops/s)'),
              ('nvme_read_await_us', 1, 'NVMe mean read await (µs)'),
              ('process_kernel_cpu_us_per_ycsb_op', 1, 'Process kernel CPU (µs/op)')]
    for ax, (metric, scale, label) in zip(axes, panels):
        ax.axhspan(min(r[metric] for r in baseline) / scale,
                   max(r[metric] for r in baseline) / scale, color='#999999', alpha=.2,
                   label='Baseline 10: min–max')
        ax.plot([r['arm'] for r in f2], [r[metric] / scale for r in f2],
                'o-', color='#23658d', label='F2Load original f01–f10')
        ax.set_ylabel(label)
        ax.grid(axis='y', alpha=.2)
    axes[0].legend(fontsize=9, loc='lower right')
    axes[0].set_title('YCSB C · 1000 GiB · 48 threads · 50 GiB cache · 300 s')
    axes[1].set_ylim(65, 71)
    axes[-1].set_ylim(bottom=0)
    fig.savefig(out / 'C_latency_and_kernel_cpu.png', dpi=160)
    fig.savefig(out / 'C_latency_and_kernel_cpu.pdf')
    plt.close(fig)
    print(json.dumps({'rows': len(rows), 'C_rows': len(c_rows), 'output': str(out)}))


if __name__ == '__main__':
    main()
