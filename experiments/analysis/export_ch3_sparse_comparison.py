#!/usr/bin/env python3
"""Assemble existing baseline/flush-only and new sparse results by protocol."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

from plot_ycsb_raw_metrics import DEFINITIONS, get_metrics
from parse_ycsb_alternatives import parse_metrics

EXP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXP / 'scripts/paper'))
import run_ch3_write_fixed_ops as fixed


def read_json(path):
    return json.loads(path.read_text())


def write_tsv(path, rows):
    with path.open('x', newline='') as stream:
        writer = csv.DictWriter(stream, list(rows[0]), delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    result = EXP / 'results'
    sparse_run = 'fillseq_sparse_260913_run2_full_measure'
    fixed_run = 'fillseq_sparse_fixed_a_260913_run1'
    assert read_json(result / sparse_run / 'COMPLETED.json')['status'] == 'ok'
    assert read_json(result / fixed_run / 'COMPLETED.json')['status'] == 'ok'
    inputs = set()
    template = None

    def row(system, workload, protocol, data, run, note=''):
        nonlocal template
        valid = data.get('status') == 'ok'
        if valid:
            metrics = get_metrics(data, 'md0', inputs)
            template = list(metrics)
        else:
            assert template is not None
            metrics = dict.fromkeys(template)
        return dict(system=system, source_system=data.get('system', system),
                    workload=workload, protocol=protocol, status=data['status'],
                    operations=data.get('operations') if valid else None,
                    **metrics, flush_write_bytes=data.get('flush_write_bytes') if valid else None,
                    measured_seconds=data.get('measured_seconds') if valid else None,
                    process_elapsed_sec=data.get('process_elapsed_sec'),
                    source_run=run, log_dir=data.get('log_dir', ''), note=note)

    timed = []
    references = {}
    for system, stored_system, run in (
        ('baseline', 'baseline', 'ycsb_band_n01_260910'),
        ('flush_only', 'flush_only', 'ch3_ycsb_cache50_260911_run2'),
        ('fillseq', 'fillseq_sparse', sparse_run)):
        path = result / run / 'results.json'
        inputs.add(path)
        for workload in ('A', 'C'):
            matches = [d for d in read_json(path) if d['phase'] == 'full' and
                       d['system'] == stored_system and d['workload'] == 'workload' + workload.lower()]
            assert len(matches) == 1
            data = matches[0]
            assert data['duration_sec'] == 300 and data['status'] in ('ok', 'timeout')
            command = Path(data['log_dir']) / 'raw/command.json'
            inputs.add(command)
            options = dict(a[2:].split('=', 1) for a in read_json(command)[1:])
            options = {k: v for k, v in options.items() if k not in ('db', 'report_file')}
            reference = references.setdefault(workload, (options, data['binary_sha256']))
            assert reference == (options, data['binary_sha256'])
            timed.append(row(system, workload, 'ycsb_300s', data, run,
                             'Timed out; metric cells are missing, not zero.' if data['status'] != 'ok' else ''))

    drained = []
    for system, log, run in (
        ('baseline', result / 'ch3_write_fixed_260911/baseline', 'ch3_write_fixed_260911'),
        ('fillseq', EXP / 'artifacts/log_runs' / fixed_run / 'full_extra/fixed_a/fillseq_sparse', fixed_run)):
        text = (log / 'bench.out').read_text(errors='replace')
        assert fixed.pending_bytes(text) == 0 and 'finished with status (OK)' in text
        metrics = parse_metrics(text, 'workloada')
        assert metrics['operations'] == 210000000 and metrics['engine_keys_written'] == 105003686
        execution = read_json(log / 'raw/execution.json')
        data = dict(metrics, status='ok', system='fillseq_sparse' if system == 'fillseq' else system,
                    log_dir=str(log), process_elapsed_sec=execution.get('process_elapsed_sec', execution.get('elapsed_sec')))
        inputs.update([log / 'bench.out', log / 'raw/execution.json', log / 'raw/command.json'])
        drained.append(row(system, 'A', 'ycsb_210M_ops_then_drain', data, run))
    drained.insert(1, row('flush_only', 'A', 'ycsb_210M_ops_then_drain',
                         dict(status='not_run'), '',
                         'No completed fixed-count A measurement; historical timed A stalled and timed out.'))
    args.out.mkdir(parents=True, exist_ok=False)
    write_tsv(args.out / 'plot_metrics.tsv', timed)
    write_tsv(args.out / 'write_metrics.tsv', drained)
    provenance = dict(definitions=DEFINITIONS,
                      baseline='Timed A/C retain n01 from the existing chapter-3 figure. Fixed A retains the historical fixed-A baseline.',
                      fillseq='Display label fillseq denotes the newly loaded sparse dataset; source_system records fillseq_sparse.',
                      missing='Blank values are unavailable measurements, never zeros.',
                      protocols='Do not mix ycsb_300s and ycsb_210M_ops_then_drain. Fixed-A compaction counters include the drain; throughput covers workload execution.',
                      inputs={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(inputs)})
    (args.out / 'provenance.json').write_text(json.dumps(provenance, indent=2))
    print(args.out / 'plot_metrics.tsv')
    print(args.out / 'write_metrics.tsv')


if __name__ == '__main__':
    main()
