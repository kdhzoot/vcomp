#!/usr/bin/env python3
"""Small serial qualification of logical/calibrated F2Load SST byte models."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

EXP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXP / 'lib'))
from ch23_common import (CLEAN, active_benchmarks, bench_stats, check_binary,
                         command, levels, load_options, read_metrics,
                         read_options, require, save_json, sha, ticker)


def invoke(argv, directory):
    require(not active_benchmarks(), 'another benchmark is active')
    directory.mkdir(parents=True)
    save_json(directory / 'command.json', argv)
    started = time.time()
    with (directory / 'bench.out').open('w') as stream:
        proc = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT,
                              timeout=600, check=False)
    raw = (directory / 'bench.out').read_text(errors='replace')
    save_json(directory / 'execution.json', dict(exit_code=proc.returncode,
        elapsed_sec=time.time() - started, started_epoch=started,
        binary_sha256=sha(argv[0])))
    require(proc.returncode == 0, 'command failed: ' + str(directory))
    require(not re.search(r'WARNING: (Optimization is disabled|Assertions are enabled)|'
                          r'Corruption:|Segmentation fault|FATAL|Assertion .*failed', raw),
            'invalid command output: ' + str(directory))
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    require(re.fullmatch(r'sst_size_model_[A-Za-z0-9_]+', args.run_id), 'unsafe run ID')
    binary = args.binary.resolve()
    require(binary.is_file(), 'missing candidate binary')
    check_binary(CLEAN)
    root = EXP / 'artifacts/validation' / args.run_id
    dbroot = Path('/work/vcomp/exp') / args.run_id
    require(not root.exists() and not dbroot.exists(), 'use a fresh run ID')
    require(not active_benchmarks(), 'another benchmark is active')
    root.mkdir(parents=True)
    dbroot.mkdir(parents=True)
    save_json(root / 'manifest.json', dict(binary=str(binary), binary_sha256=sha(binary),
        reader=str(CLEAN), reader_sha256=sha(CLEAN), dataset_gib=1,
        seed=12345678, qualification_only=True, runner_sha256=sha(__file__)))
    rows = []
    for kv in (1024, 91):
        for model in ('logical', 'calibrated'):
            name = '{}_{}b'.format(model, kv)
            db, log = dbroot / name, root / name
            opts = load_options(1, kv, db, log / 'load', 'f2load')
            opts['vcomp_sst_size_model'] = model
            print('BEGIN ' + name, flush=True)
            raw = invoke(command(binary, opts), log / 'load')
            require(re.search(r'waitforcompaction\(.*\): finished with status \(OK\)', raw),
                    'missing compaction drain')
            pending = re.findall(r'Estimated pending compaction bytes:\s*(\d+)', raw)
            require(pending and int(pending[-1]) == 0, 'pending compaction remains')
            require('FillVirtual: generating {} keys...'.format(opts['num']) in raw,
                    'input count mismatch')
            samples = re.findall(r'SST size calibration: entries=(\d+) stride=(\d+) '
                                 r'actual_bytes=(\d+) estimated_bytes=(\d+)', raw)
            require(bool(samples) == (model == 'calibrated'), 'wrong size-model path')
            audits = re.findall(r'SST size audit: level=(\d+) descriptor_bytes=(\d+) '
                                r'predicted_written_bytes=(\d+) actual_bytes=(\d+) '
                                r'actual_over_predicted_pct=([-\d.]+)', raw)
            require(audits, 'missing per-level size diagnostics')
            flushes = re.search(r'  Flushes: (\d+)', raw)
            require(flushes is not None, 'missing input batch count')
            verify = read_options(1, kv, db, log / 'verify', 'C_pinned_zero',
                                  duration=0, threads=1, reads=1000, seed=12345678)
            verification = invoke(command(CLEAN, verify), log / 'verify')
            metrics = read_metrics(verification, opts['value_size'])
            require(metrics['operations'] == 1000 and metrics['successful_gets'] > 0,
                    'read-only reopen did not find materialized data')
            predicted = sum(int(r[2]) for r in audits)
            actual = sum(int(r[3]) for r in audits)
            row = dict(case=name, kv_bytes=kv, model=model, input_keys=opts['num'],
                flush_batches=int(flushes[1]), benchmark=bench_stats(raw, 'fillvirtual'),
                levels=levels(raw), pending_bytes=int(pending[-1]),
                compaction_write_bytes=ticker(raw, 'rocksdb.compact.write.bytes'),
                predicted_written_bytes=predicted, materialized_bytes=actual,
                actual_over_predicted_pct=100 * (actual / predicted - 1),
                calibration_samples=samples, per_level_audits=audits,
                successful_reopen_gets=metrics['successful_gets'], db_dir=str(db),
                status='validated')
            rows.append(row)
            save_json(root / 'results.json', rows)
            print('PASS {} size_error={:.4f}%'.format(name, row['actual_over_predicted_pct']),
                  flush=True)
        pair = rows[-2:]
        require(pair[0]['flush_batches'] == pair[1]['flush_batches'],
                'physical size model changed logical input batching')
    (root / 'COMPLETED').write_text('four 1-GiB qualification cases validated\n')
    print('COMPLETED ' + str(root), flush=True)


if __name__ == '__main__':
    main()
