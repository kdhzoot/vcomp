#!/usr/bin/env python3
"""mixgraph on ten F2Load loadings that use the exact-membership bitmap.

The companion chain (run_mixgraph_pairs_260914.py) measured ten baseline
copies and ten bitmap-free F2Load loadings. This chain adds the third arm:
F2Load loaded with the membership bitmap, so the dataset carries the
baseline's own key set rather than approximate keys. Its baseline comparison
is the mg_base_* cells already measured on 2026-09-14; those databases are not
re-measured here.

The e01-e10 bitmap series was deleted after its YCSB campaigns, so each arm is
loaded fresh, measured, and deleted, one database at a time. Each cell is
mixgraph for 300 s followed by waitforcompaction, so the compaction bytes are
read after the pending work has drained.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time

EXP = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(EXP / 'lib'), str(EXP / 'scripts/paper')]
from ch23_common import F2, GIB, HASHES, require, save_json, sha  # noqa: E402
import run_f2load_band_chain as band  # noqa: E402
import run_mixgraph_pairs_260914 as mg  # noqa: E402

LOAD_ROOT = Path('/work/vcomp/exp/f2band_260915_em')
ARMS = tuple('h{:02d}'.format(i) for i in range(1, 11))
EXTRA = {'vcomp_exact_membership': True, 'vcomp_exact_membership_max_mb': 1024}
# The bitmap-free g01-g10 series on this same build produced 13,700-13,771
# SSTs; a bitmap load far outside that range is reported, not silently used.
EXPECTED_SSTS = (13400, 14100)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-id', default='mixgraph_bitmap_260915')
    ap.add_argument('--date', default='260915')
    ap.add_argument('--arms', default=','.join(ARMS))
    ap.add_argument('--drain-timeout', type=int, default=7200)
    ap.add_argument('--wait-for-pid', type=int)
    ap.add_argument('--keep-artifacts', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(',') if a.strip()]
    for arm in arms:
        require(arm in ARMS, 'unknown arm ' + arm)

    binary = F2.resolve()
    HASHES[str(binary)] = sha(binary)
    logroot = EXP / 'artifacts/log_loads' / ('mixgraph_bitmap_' + args.date)
    queue = EXP / 'artifacts/queues' / args.run_id
    print('load binary {} ({})'.format(binary, sha(binary)[:16]))
    print('bitmap flags {}'.format(EXTRA))
    for arm in arms:
        print('  {:<5} f2load    load (bitmap) -> {}'.format(arm, LOAD_ROOT / arm))
    if args.dry_run:
        return 0

    logroot.mkdir(parents=True, exist_ok=True)
    queue.mkdir(parents=True, exist_ok=True)
    save_json(queue / 'STATUS.json', dict(status='queued', supervisor_pid=os.getpid(),
                                          arms=arms, updated_utc=mg.utc()))
    lock = mg.wait_for_idle(queue, args.wait_for_pid)
    print('machine idle at {}; starting'.format(mg.utc()), flush=True)

    done, failed, rows = [], [], []
    try:
        for index, arm in enumerate(arms):
            print('=== {} ==='.format(arm), flush=True)
            started = time.time()
            target = LOAD_ROOT / arm
            row = band.load_one(arm, target, logroot / arm, binary, dict(EXTRA))
            require(row['load_extra'] == EXTRA,
                    'bitmap arm did not record the membership flags: '
                    + json.dumps(row['load_extra']))
            note = ('as expected' if EXPECTED_SSTS[0] <= row['final_sst_count'] <= EXPECTED_SSTS[1]
                    else 'OUTSIDE the g01-g10 range')
            print('  load {:.1f} s, {} ssts ({})'.format(
                row['elapsed_sec'], row['final_sst_count'], note), flush=True)
            bundle = band.bundle_for(row, logroot / (arm + '_bundle'))

            run_id = 'mg_em_{}_{}'.format(arm, args.date)
            code, cell = mg.campaign(run_id, 'f2load', bundle, args.drain_timeout)
            if code == 0:
                rows.append(dict(arm=arm, system='f2load_bitmap', run_id=run_id,
                                 load_sec=round(row['elapsed_sec'], 1),
                                 final_sst_count=row['final_sst_count'],
                                 final_sst_bytes=row['final_sst_bytes'],
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
                mg.remove(target, (LOAD_ROOT,))
                print('  removed ' + str(target), flush=True)
            print('  free {} GiB'.format(shutil.disk_usage('/work').free // GIB), flush=True)

            if code != 0:
                print('  campaign failed, stopping', flush=True)
                break
            if index == 0:
                ok, observed = mg.check_parameters(run_id, arm)
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
                       results=str(logroot / 'chain_results.json'), updated_utc=mg.utc()))
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_UN)
    print('done={} failed={}'.format(done, failed or ['none']))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
