#!/usr/bin/env python3
"""Load F2Load repeatedly and measure YCSB A-F on each loading.

The run-to-run band in Section 3.2 needs independent loadings, not repeated
measurements of one database, so every arm loads a fresh 1 TB database, measures
it, and starts the next arm only after all six measurements validate. Use
--keep-db to retain every loaded source database after measurement.

The September 11 chain that produced f01 to f05 was written inline and not kept,
which left its provenance partly stale: the campaign bundle carried the previous
campaign's loading record with only db_dir replaced, so the recorded SST count
and loading time belonged to a different database. This script writes the fresh
loading's own values into the bundle, and exists as a file so the next repeat
does not have to be reconstructed from logs.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

EXP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXP / 'lib'))
from ch23_common import (F2, GIB, HASHES, active_benchmarks, command, db_identity,
    levels, load_options, measure, require, save_json, sha)

RUNNER = EXP / 'scripts/read/run_ycsb_alternatives.py'
TEMPLATE = EXP / 'results/paper_ch23_common_260907_f2_completion1/loads.json'
CACHE = 50 * GIB


def load_one(name, db, log, binary=F2):
    """One F2Load loading of the common 1 TB, 1 KB dataset.

    An arm whose load already finished is reused rather than reloaded, so a
    chain stopped between the load and the campaign does not throw away a
    completed database.
    """
    recorded = log / 'validated.json'
    if db.is_dir() and recorded.is_file():
        row = json.loads(recorded.read_text())
        require(row['db_dir'] == str(db), 'recorded load points elsewhere')
        require(db_identity(db) == json.loads((log / 'db_identity.json').read_text()),
                'existing DB does not match its recorded identity')
        print('  reusing the loaded DB ({} ssts)'.format(row['final_sst_count']))
        return row
    require(not db.exists(), 'refusing to write an existing path: ' + str(db))
    db.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    opts = load_options(1000, 1024, db, log, 'f2load')
    started = time.time()
    phase, text = measure(command(binary, opts), log, lambda *a: None)
    ident = db_identity(db)
    row = dict(case_id='f2load_1kb', system='f2load', dataset_gib=1000,
               key_bytes=opts['key_size'], value_bytes=opts['value_size'],
               num_keys=opts['num'], elapsed_sec=phase['elapsed_sec'],
               loading_min=phase['elapsed_sec'] / 60, levels=levels(text),
               peak_rss_kb=phase['peak_rss_kb'],
               final_sst_bytes=sum(v[1] for v in ident['ssts'].values()),
               final_sst_count=len(ident['ssts']),
               binary_sha256=sha(binary), db_dir=str(db), log_dir=str(log),
               source_identity_file=str(log / 'db_identity.json'),
               arm=name, wall_sec=round(time.time() - started, 1))
    save_json(log / 'db_identity.json', ident)
    save_json(log / 'validated.json', row)
    return row


def bundle_for(row, path):
    """A campaign bundle whose f2load entry describes this loading.

    The runner reads every system's entry even when only one is measured, so the
    other entries come from the common campaign unchanged.
    """
    loads = json.loads(TEMPLATE.read_text())
    # Do not inherit the template's old settle phases, validation, or I/O totals.
    loads['f2load_1kb'] = dict(row, load_protocol='fillvirtual_waitforcompaction',
        repetitions=1,
        logical_input_bytes=row['num_keys'] * (row['key_bytes'] + row['value_bytes']))
    path.mkdir(parents=True, exist_ok=True)
    save_json(path / 'loads.json', loads)
    return path


def validate_campaign(run_id):
    root = EXP / 'artifacts/log_runs' / run_id
    completed = json.loads((root / 'COMPLETED.json').read_text())
    require(completed['full_cells'] == 6 and completed['valid_full'] == 6,
            'campaign did not validate all six full measurements: ' + run_id)
    rows = json.loads((root / 'results.json').read_text())
    full = [r for r in rows if r['phase'] == 'full']
    require(len(full) == 6 and
            {(r['system'], r['workload']) for r in full} ==
            {('f2load', 'workload' + w) for w in 'abcdef'},
            'campaign has missing or duplicate workloads: ' + run_id)
    require(all(r['status'] == 'ok' and r['exit_code'] == 0 and
                not r['timed_out'] and not r['missing_tickers'] for r in full),
            'campaign contains an invalid full measurement: ' + run_id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--arms', default='f06,f07,f08,f09,f10')
    ap.add_argument('--date', default='260912')
    ap.add_argument('--db-root', default='/work/vcomp/exp/f2band_260912')
    ap.add_argument('--keep-db', action='store_true')
    ap.add_argument('--load-binary', type=Path, default=F2)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    binary = args.load_binary.resolve()
    HASHES[str(binary)] = sha(binary)
    arms = [a.strip() for a in args.arms.split(',') if a.strip()]
    root = Path(args.db_root)
    print('binary {} ({})'.format(binary, sha(binary)[:16]))
    print('keep_db={}'.format(args.keep_db))
    for a in arms:
        print('  {:<5} load {} -> campaign ycsb_f2band_{}_{}'.format(
            a, root / a, a, args.date))
    if args.dry_run:
        return 0
    require(not active_benchmarks(), 'another storage benchmark is active')

    done, failed = [], []
    for a in arms:
        db = root / a
        log = EXP / 'artifacts/log_loads' / ('f2band_' + args.date) / a
        print('=== {} load ==='.format(a), flush=True)
        row = load_one(a, db, log, binary)
        print('  {:.0f} s, {} ssts'.format(row['elapsed_sec'],
                                           row['final_sst_count']), flush=True)
        run_id = 'ycsb_f2band_{}_{}'.format(a, args.date)
        bundle = bundle_for(row, EXP / 'artifacts/log_loads' /
                            ('f2band_' + args.date) / (a + '_bundle'))
        cmd = [sys.executable, str(RUNNER), '--run-id', run_id,
               '--systems', 'f2load', '--workloads', 'abcdef',
               '--duration', '300', '--cache-size', str(CACHE),
               '--source-bundle', str(bundle)]
        print('  ' + ' '.join(cmd), flush=True)
        rc = subprocess.call(cmd)
        if rc == 0:
            try:
                validate_campaign(run_id)
                require(db_identity(db) == json.loads(
                    (log / 'db_identity.json').read_text()),
                    'source DB identity changed after campaign: ' + a)
            except Exception as error:
                print('  validation failed: {}'.format(error), flush=True)
                rc = 1
        (done if rc == 0 else failed).append(a)
        # Source deletion is explicitly disabled by --keep-db.
        if rc == 0 and not args.keep_db:
            for path in (db, db.with_name(db.name + '_materialized')):
                if path.is_dir():
                    shutil.rmtree(path)
                    print('  removed ' + str(path), flush=True)
        print('  free {}'.format(shutil.disk_usage('/work').free // GIB), flush=True)
        if rc != 0:
            print('  campaign failed, stopping', flush=True)
            break
    print('done={} failed={}'.format(done, failed or ['none']))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
