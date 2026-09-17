#!/usr/bin/env python3
"""mixgraph on ten baseline and ten no-bitmap F2Load loadings, one DB at a time.

Baseline arms are deep-copied immediately before measurement so all ten read
from fresh, cp-allocated files, and the copy is deleted afterwards; the ten
source databases are never opened. F2Load arms are loaded fresh without the
membership bitmap (the f06-f15 series was deleted after its YCSB campaigns) and
measured on the loaded database, then deleted. Arms alternate between the two
systems so machine drift over the chain does not land on one system.

Each cell is mixgraph for 300 s followed by waitforcompaction, so the compaction
bytes are read after the pending work has drained. The workload parameters are
the published prefix-dist command; see docs/PLAN_MIXGRAPH_PAIRS_260914.md.

The first completed arm is gated on the mixgraph aggregate line reporting the
value and scan averages the published parameters imply (~36 B, ~570 entries).
If they are off, the parameters did not take effect and the chain stops before
spending the remaining nineteen arms' worth of machine time.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

EXP = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(EXP / 'lib'), str(EXP / 'scripts/paper')]
from ch23_common import F2, GIB, HASHES, active_benchmarks, require, save_json, sha  # noqa: E402
import run_deepcopy_cu_mixgraph as dc  # noqa: E402
import run_f2load_band_chain as band  # noqa: E402

RUNNER = EXP / 'scripts/read/run_ycsb_alternatives.py'
CACHE = 50 * GIB
DURATION = 300
COPY_ROOT = Path('/work/vcomp/exp/mgcopy_260914')
LOAD_ROOT = Path('/work/vcomp/exp/f2band_260914_nb')
LOCK = '/work/vcomp/exp/pebble_current_campaign.lock'

BASELINE_ARMS = ('b01', 'b02', 'b03', 'n01', 'n02', 'n03', 'n04', 'r01', 'r02', 'run3')
F2LOAD_ARMS = tuple('g{:02d}'.format(i) for i in range(1, 11))

# Refused as deletion targets whatever a bundle says. Only the two roots this
# chain creates are disposable.
PROTECTED = ('f2load_91b_260911', 'approved_run3', 'f2band_260913_em', 'f2band_260912',
             'baseline_coverage', 'paper_ch23_common', 'memprofile')

AGGREGATE_RE = re.compile(
    r'^mixgraph\s*:.*avg size:\s*([0-9.]+)\s*value,\s*([0-9.]+)\s*scan', re.M)
EXPECTED_VALUE = (30.0, 45.0)      # published fit gives ~36 B
EXPECTED_SCAN = (480.0, 660.0)     # uncapped iter fit gives ~570 entries


def utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def wait_for_idle(queue, wait_pid, poll=30):
    """Block until the machine is ours: no db_bench, no watched supervisor."""
    lock = open(LOCK, 'a')
    waited = 0
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not active_benchmarks() and not alive(wait_pid):
                save_json(queue / 'STATUS.json',
                          dict(status='started', supervisor_pid=os.getpid(),
                               waited_sec=waited, updated_utc=utc()))
                return lock
            fcntl.flock(lock, fcntl.LOCK_UN)
        except BlockingIOError:
            pass
        save_json(queue / 'STATUS.json',
                  dict(status='waiting', supervisor_pid=os.getpid(),
                       waiting_for=dict(db_bench=active_benchmarks(),
                                        pid=wait_pid if alive(wait_pid) else None),
                       waited_sec=waited, updated_utc=utc()))
        time.sleep(poll)
        waited += poll


def alive(pid):
    if not pid:
        return False
    try:
        return 'run_f2load_memory_profile' in Path('/proc/{}/cmdline'.format(pid)).read_bytes().decode('utf-8', 'ignore')
    except OSError:
        return False


def baseline_sources():
    out = {}
    for path in (EXP / 'artifacts/log_loads').rglob('validated.json'):
        record = json.loads(path.read_text())
        if (record.get('dataset_gib') == 1000 and record.get('key_bytes') == 24
                and record.get('system') == 'baseline'
                and record.get('final_sst_count') in dc.BASE_ARMS):
            out[dc.BASE_ARMS[record['final_sst_count']]] = record
    return out


def remove(path, roots):
    resolved = str(Path(path).resolve())
    require(any(resolved.startswith(str(root) + '/') for root in roots),
            'refusing to delete outside this chain\'s roots: ' + resolved)
    require(not any(tag in resolved for tag in PROTECTED),
            'refusing to delete a protected path: ' + resolved)
    shutil.rmtree(path)


def campaign(run_id, system, bundle, drain_timeout):
    cmd = [sys.executable, str(RUNNER), '--run-id', run_id, '--systems', system,
           '--benchmark', 'mixgraph', '--duration', str(DURATION),
           '--cache-size', str(CACHE), '--drain-timeout', str(drain_timeout),
           '--source-bundle', str(bundle)]
    print('  ' + ' '.join(cmd), flush=True)
    code = subprocess.call(cmd)
    if code != 0:
        return code, None
    root = EXP / 'artifacts/log_runs' / run_id
    done = json.loads((root / 'COMPLETED.json').read_text())
    require(done['valid_full'] == done['full_cells'] == 1,
            'campaign did not validate its single full cell: ' + run_id)
    row = [r for r in json.loads((root / 'results.json').read_text())
           if r['phase'] == 'full'][0]
    require(row['status'] == 'ok', 'full cell did not validate: ' + row['status'])
    require(row['pending_bytes_end'] == 0, 'pending compaction did not drain')
    return 0, row


def check_parameters(run_id, arm):
    """The published parameters imply a value and scan average; check them once."""
    for path in sorted((EXP / 'artifacts/log_runs' / run_id / 'full/mixgraph').glob('*/bench.out')):
        match = AGGREGATE_RE.search(path.read_text(errors='ignore'))
        if match:
            value, scan = float(match.group(1)), float(match.group(2))
            ok = (EXPECTED_VALUE[0] <= value <= EXPECTED_VALUE[1]
                  and EXPECTED_SCAN[0] <= scan <= EXPECTED_SCAN[1])
            print('  {} aggregate: avg value {} B, avg scan {} entries -> {}'.format(
                arm, value, scan, 'as published' if ok else 'UNEXPECTED'), flush=True)
            return ok, dict(avg_value_bytes=value, avg_scan_entries=scan)
    print('  {}: no mixgraph aggregate line found to check'.format(arm), flush=True)
    return False, {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-id', default='mixgraph_pairs_260914')
    ap.add_argument('--date', default='260914')
    ap.add_argument('--arms', default=','.join(
        x for pair in zip(BASELINE_ARMS, F2LOAD_ARMS) for x in pair))
    ap.add_argument('--copy-workers', type=int, default=32)
    ap.add_argument('--drain-timeout', type=int, default=7200)
    ap.add_argument('--wait-for-pid', type=int,
                    help='also wait until this supervisor pid has exited')
    ap.add_argument('--keep-artifacts', action='store_true',
                    help='keep the copies and the loaded DBs instead of deleting them')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    base = baseline_sources()
    arms = [a.strip() for a in args.arms.split(',') if a.strip()]
    for arm in arms:
        if arm in BASELINE_ARMS:
            require(arm in base, 'no source load record for baseline arm ' + arm)
            require(Path(base[arm]['db_dir']).is_dir(),
                    'baseline source missing: ' + base[arm]['db_dir'])
        else:
            require(arm in F2LOAD_ARMS, 'unknown arm ' + arm)

    binary = F2.resolve()
    HASHES[str(binary)] = sha(binary)
    logroot = EXP / 'artifacts/log_loads' / ('mixgraph_pairs_' + args.date)
    queue = EXP / 'artifacts/queues' / args.run_id
    print('load binary {} ({})'.format(binary, sha(binary)[:16]))
    for arm in arms:
        if arm in BASELINE_ARMS:
            print('  {:<5} baseline  copy {} -> {}'.format(
                arm, base[arm]['db_dir'], COPY_ROOT / arm))
        else:
            print('  {:<5} f2load    load (no bitmap) -> {}'.format(arm, LOAD_ROOT / arm))
    if args.dry_run:
        return 0

    logroot.mkdir(parents=True, exist_ok=True)
    queue.mkdir(parents=True, exist_ok=True)
    save_json(queue / 'STATUS.json', dict(status='queued', supervisor_pid=os.getpid(),
                                          arms=arms, updated_utc=utc()))
    lock = wait_for_idle(queue, args.wait_for_pid)
    print('machine idle at {}; starting'.format(utc()), flush=True)

    done, failed, rows = [], [], []
    try:
        for index, arm in enumerate(arms):
            print('=== {} ==='.format(arm), flush=True)
            started = time.time()
            if arm in BASELINE_ARMS:
                record, system = base[arm], 'baseline'
                target = COPY_ROOT / arm
                seconds, total = dc.deep_copy(Path(record['db_dir']), target,
                                              args.copy_workers, logroot / 'copies.jsonl')
                print('  copy {:.0f} s, {:.1f} GB, {:.2f} GB/s'.format(
                    seconds, total / 1e9, total / seconds / 1e9), flush=True)
                bundle = dc.bundle_for(system, record, target, logroot / (arm + '_bundle'))
                prepare_sec = seconds
            else:
                system = 'f2load'
                target = LOAD_ROOT / arm
                row = band.load_one(arm, target, logroot / arm, binary, None)
                require(not row['load_extra'], 'f2load arm must carry no bitmap')
                # The f06-f15 no-bitmap series ran 13,167-13,290 SSTs; a load
                # far outside that says this build no longer reproduces it.
                print('  load {:.1f} s, {} ssts (f06-f15 were 13,167-13,290)'.format(
                    row['elapsed_sec'], row['final_sst_count']), flush=True)
                bundle = band.bundle_for(row, logroot / (arm + '_bundle'))
                prepare_sec = row['elapsed_sec']

            run_id = 'mg_{}_{}_{}'.format('base' if system == 'baseline' else 'nb',
                                          arm, args.date)
            code, cell = campaign(run_id, system, bundle, args.drain_timeout)
            if code == 0:
                rows.append(dict(arm=arm, system=system, run_id=run_id,
                                 prepare_sec=round(prepare_sec, 1),
                                 arm_sec=round(time.time() - started, 1),
                                 throughput_ops_sec=cell.get('throughput_ops_sec'),
                                 compaction_write_bytes=cell.get('compaction_write_bytes'),
                                 compaction_read_bytes=cell.get('compaction_read_bytes'),
                                 flush_write_bytes=cell.get('flush_write_bytes'),
                                 pending_bytes_end=cell.get('pending_bytes_end'),
                                 process_elapsed_sec=cell.get('process_elapsed_sec')))
                save_json(logroot / 'chain_results.json', rows)
                done.append(arm)
            else:
                failed.append(arm)

            if not args.keep_artifacts:
                remove(target, (COPY_ROOT, LOAD_ROOT))
                print('  removed ' + str(target), flush=True)
            print('  free {} GiB'.format(shutil.disk_usage('/work').free // GIB), flush=True)

            if code != 0:
                print('  campaign failed, stopping', flush=True)
                break
            if index == 0:
                ok, observed = check_parameters(run_id, arm)
                rows[-1].update(observed)
                save_json(logroot / 'chain_results.json', rows)
                if not ok:
                    failed.append('parameter-gate')
                    print('  first arm did not reproduce the published averages, '
                          'stopping before the rest', flush=True)
                    break
    finally:
        save_json(queue / 'STATUS.json',
                  dict(status='failed' if failed else 'complete', done=done,
                       failed=failed or None, supervisor_pid=os.getpid(),
                       results=str(logroot / 'chain_results.json'), updated_utc=utc()))
        fcntl.flock(lock, fcntl.LOCK_UN)
    print('done={} failed={}'.format(done, failed or ['none']))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
