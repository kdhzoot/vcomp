#!/usr/bin/env python3
"""Deep-copy each source DB, measure YCSB C (uniform) and mixgraph on the copy,
then delete the copy.

Copying every DB right before it is measured gives all twenty the same fresh
file layout on the RAID, so the comparison is not confounded by where each
original's files happen to sit (the 13 September no-cache probe showed a 4x
device-latency swing from that alone) or by how long ago it was written. The
copy is disposable: both campaigns stage their own hard-link clone from it,
and it is removed once both have validated.

Order per DB: copy -> identity + bundle -> C uniform (read-only, leaves the copy
untouched) -> mixgraph (writes 14 %) -> delete copy -> next DB.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

EXP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXP / 'lib'))
from ch23_common import GIB, active_benchmarks, require, save_json  # noqa: E402
sys.path.insert(0, str(EXP / 'scripts/read'))
import importlib.util  # noqa: E402

RUNNER = EXP / 'scripts/read/run_ycsb_alternatives.py'
TEMPLATE = EXP / 'results/paper_ch23_common_260907_f2_completion1/loads.json'
CACHE = 50 * GIB
COPY_ROOT = Path('/work/vcomp/exp/deepcopy_260913')
BASE_ARMS = {13483: 'n01', 13480: 'n02', 13549: 'n03', 13509: 'n04', 13497: 'r01',
             13522: 'r02', 13461: 'run3', 13460: 'b01', 13496: 'b02', 13520: 'b03'}
# Never deleted, whatever a bundle says.
PROTECTED = ('f2load_91b_260911', 'approved_run3', 'f2band_260913_em',
             'baseline_coverage', 'paper_ch23_common')


def db_identity(path):
    spec = importlib.util.spec_from_file_location('runner', RUNNER)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        pass
    return module.db_identity(Path(path))


def sources():
    """arm -> (system, db_dir, load record) for the ten baseline and ten F2Load DBs."""
    out = {}
    for p in (EXP / 'artifacts/log_loads').rglob('validated.json'):
        d = json.loads(p.read_text())
        if d.get('dataset_gib') != 1000 or d.get('key_bytes') != 24:
            continue
        if d.get('system') == 'baseline' and d['final_sst_count'] in BASE_ARMS:
            out[BASE_ARMS[d['final_sst_count']]] = ('baseline', d['db_dir'], d)
        elif d.get('system') == 'f2load' and str(d.get('arm', '')).startswith('e'):
            out[d['arm']] = ('f2load', d['db_dir'], d)
    return out


def deep_copy(src, dst, workers, log):
    """File-level parallel copy; returns (seconds, bytes)."""
    require(not dst.exists(), 'refusing to write an existing path: ' + str(dst))
    dst.mkdir(parents=True)
    names = sorted(os.listdir(src))
    started = time.time()
    # Data files in parallel, the handful of metadata files after them.
    ssts = [n for n in names if n.endswith('.sst')]
    others = [n for n in names if not n.endswith('.sst')]
    subprocess.run(['xargs', '-0', '-P', str(workers), '-I{}', 'cp', '--preserve=timestamps',
                    str(src / '{}'), str(dst / '{}')],
                   input='\0'.join(ssts).encode(), check=True)
    for n in others:
        shutil.copy2(src / n, dst / n)
    subprocess.run(['sync'], check=True)
    seconds = time.time() - started
    total = sum((dst / n).stat().st_size for n in names)
    with open(log, 'a') as f:
        f.write(json.dumps(dict(src=str(src), dst=str(dst), files=len(names), bytes=total,
                                seconds=round(seconds, 1), gbps=round(total / seconds / 1e9, 2),
                                workers=workers, utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))) + '\n')
    return seconds, total


def bundle_for(system, record, copy_dir, path):
    loads = json.loads(TEMPLATE.read_text())
    ident = db_identity(copy_dir)
    path.mkdir(parents=True, exist_ok=True)
    save_json(path / 'db_identity.json', ident)
    entry = dict(loads[system + '_1kb'])
    entry.update(db_dir=str(copy_dir), source_identity_file=str(path / 'db_identity.json'),
                 final_sst_count=len(ident['ssts']),
                 final_sst_bytes=sum(v[1] for v in ident['ssts'].values()),
                 elapsed_sec=record['elapsed_sec'], loading_min=record['elapsed_sec'] / 60,
                 arm=record.get('arm', ''), copied_from=record['db_dir'])
    loads[system + '_1kb'] = entry
    save_json(path / 'loads.json', loads)
    return path


def campaign(run_id, system, bundle, extra):
    cmd = [sys.executable, str(RUNNER), '--run-id', run_id, '--systems', system,
           '--duration', '300', '--cache-size', str(CACHE), '--source-bundle', str(bundle)] + extra
    print('  ' + ' '.join(cmd), flush=True)
    rc = subprocess.call(cmd)
    if rc == 0:
        done = json.loads((EXP / 'artifacts/log_runs' / run_id / 'COMPLETED.json').read_text())
        require(done['valid_full'] == done['full_cells'] == 1,
                'campaign did not validate its full cell: ' + run_id)
    return rc


def remove_copy(copy_dir):
    resolved = str(copy_dir.resolve())
    require(resolved.startswith(str(COPY_ROOT) + '/'), 'refusing to delete outside the copy root')
    require(not any(tag in resolved for tag in PROTECTED), 'refusing to delete a protected path')
    shutil.rmtree(copy_dir)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--arms', default='e01,e02,e03,e04,e05,e06,e07,e08,e09,e10,'
                                      'b01,b02,b03,n01,n02,n03,n04,r01,r02,run3')
    ap.add_argument('--date', default='260913')
    ap.add_argument('--copy-workers', type=int, default=32)
    ap.add_argument('--keep-copy', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    src = sources()
    arms = [a.strip() for a in args.arms.split(',') if a.strip()]
    for a in arms:
        require(a in src, 'no source DB recorded for arm ' + a)
        require(Path(src[a][1]).is_dir(), 'source DB missing: ' + src[a][1])
    copy_log = EXP / 'artifacts/log_loads' / ('deepcopy_' + args.date) / 'copies.jsonl'
    copy_log.parent.mkdir(parents=True, exist_ok=True)
    for a in arms:
        print('  {:<5} {:<9} {} -> {}'.format(a, src[a][0], src[a][1], COPY_ROOT / a))
    if args.dry_run:
        return 0
    require(not active_benchmarks(), 'another storage benchmark is active')

    done, failed = [], []
    for a in arms:
        system, db_dir, record = src[a]
        copy_dir = COPY_ROOT / a
        print('=== {} copy ==='.format(a), flush=True)
        seconds, total = deep_copy(Path(db_dir), copy_dir, args.copy_workers, copy_log)
        print('  {:.0f} s, {:.1f} GB, {:.2f} GB/s'.format(seconds, total / 1e9, total / seconds / 1e9), flush=True)
        bundle = bundle_for(system, record, copy_dir,
                            EXP / 'artifacts/log_loads' / ('deepcopy_' + args.date) / (a + '_bundle'))
        rc = campaign('dc_cu_{}_{}'.format(a, args.date), system, bundle,
                      ['--workloads', 'c', '--request-distribution', 'uniform'])
        if rc == 0:
            rc = campaign('dc_mg_{}_{}'.format(a, args.date), system, bundle,
                          ['--benchmark', 'mixgraph'])
        (done if rc == 0 else failed).append(a)
        if not args.keep_copy:
            remove_copy(copy_dir)
            print('  removed ' + str(copy_dir), flush=True)
        print('  free {}'.format(shutil.disk_usage('/work').free // GIB), flush=True)
        if rc != 0:
            print('  campaign failed, stopping', flush=True)
            break
    print('done={} failed={}'.format(done, failed or ['none']))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
