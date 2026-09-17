#!/usr/bin/env python3
"""Unique-key comparison: ten baseline loadings against ten bitmap F2Load loadings.

Each database is reduced to a key-ID bitmap over the 1,048,576,000 generator
domain by keyset_scan, which opens SST files read-only through SstFileReader and
never opens the database itself. The ten baseline databases are retained
artifacts and are only read. The F2Load arms are loaded fresh with the
exact-membership bitmap, scanned, and deleted, one at a time.

Both systems draw keys from the same generator and seed, so the baseline key
set is the reference; a difference in the F2Load set is F2Load's.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

EXP = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(EXP / 'lib'), str(EXP / 'scripts/paper')]
from ch23_common import F2, GIB, HASHES, require, save_json, sha  # noqa: E402
import run_f2load_band_chain as band  # noqa: E402
import run_mixgraph_pairs_260914 as mg  # noqa: E402

SCAN = Path('/tmp/claude-1000/-home-smrc-virtual-compaction/'
            'ed5849c9-da3a-497a-8c76-743d615c0e52/scratchpad/keyset_scan')
DOMAIN = 1000 * (1024 ** 3) // 1024
BITMAPS = Path('/work/vcomp/exp/keysets_260915')
LOAD_ROOT = Path('/work/vcomp/exp/f2band_260915_keyset')
ARMS = tuple('i{:02d}'.format(i) for i in range(1, 11))
EXTRA = {'vcomp_exact_membership': True, 'vcomp_exact_membership_max_mb': 1024}
BASE_SSTS = {13483: 'n01', 13480: 'n02', 13549: 'n03', 13509: 'n04', 13497: 'r01',
             13522: 'r02', 13461: 'run3', 13460: 'b01', 13496: 'b02', 13520: 'b03'}


def scan(db, out, threads=48):
    require(SCAN.is_file(), 'keyset_scan binary is missing: ' + str(SCAN))
    started = time.time()
    proc = subprocess.run([str(SCAN), str(db), str(DOMAIN), str(out), str(threads)],
                          capture_output=True, text=True)
    require(proc.returncode == 0, 'keyset_scan failed: ' + proc.stderr[-400:])
    row = json.loads(proc.stdout.strip().splitlines()[-1])
    require(row['open_failed'] == 0, 'keyset_scan could not open every SST')
    require(row['keys_outside_domain'] == 0, 'keys outside the generator domain')
    row['scan_sec'] = round(time.time() - started, 1)
    row['bitmap'] = str(out)
    return row


def baseline_dbs():
    out = {}
    for path in (EXP / 'artifacts/log_loads').rglob('validated.json'):
        record = json.loads(path.read_text())
        if (record.get('system') == 'baseline' and record.get('dataset_gib') == 1000
                and record.get('key_bytes') == 24
                and record.get('final_sst_count') in BASE_SSTS):
            out[BASE_SSTS[record['final_sst_count']]] = record
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-id', default='keyset_compare_260915')
    ap.add_argument('--arms', default=','.join(ARMS))
    ap.add_argument('--skip-baseline', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(',') if a.strip()]
    base = baseline_dbs()
    require(len(base) == 10, 'expected ten baseline loadings, found {}'.format(len(base)))
    for name, record in sorted(base.items()):
        require(Path(record['db_dir']).is_dir(), 'baseline missing: ' + record['db_dir'])
    if args.dry_run:
        for name, record in sorted(base.items()):
            print('  baseline {:<5} {}'.format(name, record['db_dir']))
        for arm in arms:
            print('  f2load   {:<5} load (bitmap) -> {}'.format(arm, LOAD_ROOT / arm))
        return 0

    BITMAPS.mkdir(parents=True, exist_ok=True)
    logroot = EXP / 'artifacts/log_loads' / args.run_id
    logroot.mkdir(parents=True, exist_ok=True)
    queue = EXP / 'artifacts/queues' / args.run_id
    queue.mkdir(parents=True, exist_ok=True)
    binary = F2.resolve()
    HASHES[str(binary)] = sha(binary)
    save_json(queue / 'STATUS.json', dict(status='queued', supervisor_pid=os.getpid(),
                                          updated_utc=mg.utc()))
    lock = mg.wait_for_idle(queue, None)
    print('machine idle at {}; starting'.format(mg.utc()), flush=True)

    records = []
    results = logroot / 'keyset_records.json'
    if results.is_file():
        records = json.loads(results.read_text())
    seen = {(r['system'], r['arm']) for r in records}
    try:
        if not args.skip_baseline:
            for name, record in sorted(base.items()):
                if ('baseline', name) in seen:
                    continue
                print('=== baseline {} ==='.format(name), flush=True)
                row = scan(record['db_dir'], BITMAPS / ('base_' + name + '.bitmap'))
                row.update(system='baseline', arm=name,
                           final_sst_count=record['final_sst_count'])
                print('  {} distinct keys, {:.1f}% of the domain, {} s'.format(
                    row['distinct_keys'], row['coverage_pct'], row['scan_sec']), flush=True)
                records.append(row)
                save_json(results, records)

        for arm in arms:
            if ('f2load_bitmap', arm) in seen:
                continue
            print('=== f2load {} ==='.format(arm), flush=True)
            target = LOAD_ROOT / arm
            loaded = band.load_one(arm, target, logroot / arm, binary, dict(EXTRA))
            require(loaded['load_extra'] == EXTRA, 'bitmap flags were not recorded')
            print('  load {:.1f} s, {} ssts'.format(
                loaded['elapsed_sec'], loaded['final_sst_count']), flush=True)
            row = scan(target, BITMAPS / ('f2_' + arm + '.bitmap'))
            row.update(system='f2load_bitmap', arm=arm,
                       final_sst_count=loaded['final_sst_count'],
                       load_sec=round(loaded['elapsed_sec'], 1))
            print('  {} distinct keys, {:.1f}% of the domain, {} s'.format(
                row['distinct_keys'], row['coverage_pct'], row['scan_sec']), flush=True)
            records.append(row)
            save_json(results, records)
            mg.remove(target, (LOAD_ROOT,))
            print('  removed {}; free {} GiB'.format(
                target, shutil.disk_usage('/work').free // GIB), flush=True)
    finally:
        save_json(queue / 'STATUS.json',
                  dict(status='complete', records=str(results),
                       supervisor_pid=os.getpid(), updated_utc=mg.utc()))
        fcntl.flock(lock, fcntl.LOCK_UN)
    print('scanned {} databases'.format(len(records)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
