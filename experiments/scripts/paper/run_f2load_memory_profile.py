#!/usr/bin/env python3
"""F2Load memory overhead: peak RSS and its composition across dataset size and
record size.

Six loads, three dataset sizes by two KV sizes. Each one samples process RSS
every 100 ms next to the live descriptor set and jemalloc's arena statistics, so
a sample's parts add up to that sample's RSS:

    RSS = descriptors + (block cache + memtables) + other live + overhead

The parts above partition jemalloc's live allocation exactly; RSS sits beside
them rather than inside them, because allocated bytes need not be resident and
resident pages need not still be allocated.

Loading overhead is a difference, not an absolute: the profiler records the
process RSS before any loading work and resets the kernel's peak-RSS counter, so
VmHWM at the end covers the loading window alone and never misses a short spike
the way sampling can.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

EXP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXP / 'lib'))
from ch23_common import (active_benchmarks, command, levels, load_options,  # noqa: E402
                         require, save_json, sha)

BINARY = EXP.parent / 'db_bench'
DB_ROOT = Path('/work/vcomp/exp/memprofile_260914')
MIB = 1048576


def arms(sizes, kvs):
    return [(g, kv) for g in sizes for kv in kvs]


def membership_bytes(records):
    """Two bitmaps of one bit per key id, rounded up to 64-bit words."""
    return 2 * ((records + 63) // 64) * 8


def run_one(root, gib, kv, keep_db, membership_mb=0, phase1_shards=0):
    name = '{}gib_{}b'.format(gib, kv)
    db, log = DB_ROOT / name, root / name
    log.mkdir(parents=True)
    require(not db.exists(), 'refusing to reuse a db path: ' + str(db))
    db.mkdir(parents=True)

    profile = log / 'profile.jsonl'
    opts = load_options(gib, kv, db, log, 'f2load')
    opts['vcomp_memory_profile_out'] = str(profile)
    opts['vcomp_memory_profile_interval_ms'] = 100
    if phase1_shards:
        opts['vcomp_phase1_shards'] = phase1_shards
    if membership_mb:
        # One bitmap of the ingested ids and one of the ids a shallower level
        # has claimed; Create() budgets each at half of this flag.
        require(membership_mb * MIB // 2 >= membership_bytes(opts['num']) // 2,
                'membership budget too small for {} keys'.format(opts['num']))
        opts['vcomp_exact_membership'] = True
        opts['vcomp_exact_membership_max_mb'] = membership_mb
    argv = command(BINARY, opts)
    (log / 'command.txt').write_text(' '.join(argv) + '\n')

    started = time.time()
    with open(log / 'load.out', 'w') as out:
        code = subprocess.call(argv, stdout=out, stderr=subprocess.STDOUT)
    elapsed = time.time() - started
    text = (log / 'load.out').read_text()
    require(code == 0, 'db_bench failed for ' + name)
    require('fillvirtual' in text, 'no fillvirtual line for ' + name)
    require(re.search(r'waitforcompaction\(.*\): finished with status \(OK\)', text),
            'compaction did not drain for ' + name)

    rows = [json.loads(line) for line in profile.read_text().splitlines() if line]
    require(rows, 'empty memory profile for ' + name)
    bad = [r for r in rows
           if r['descriptor_total'] + r['block_cache'] + r['memtables']
           + r['other_live'] != r['je_allocated']]
    require(not bad,
            '%d samples do not partition live allocation for %s' % (len(bad), name))

    base = rows[0]['base_rss']
    hwm = max(r['hwm'] for r in rows)
    peak = max(rows, key=lambda r: r['rss'])
    record = dict(
        arm=name, dataset_gib=gib, kv_bytes=kv,
        key_bytes=opts['key_size'], value_bytes=opts['value_size'],
        records=opts['num'], elapsed_sec=round(elapsed, 1),
        db_dir=str(db), samples=len(rows),
        baseline_rss=base, peak_rss_sampled=peak['rss'], peak_rss_hwm=hwm,
        overhead_sampled=peak['rss'] - base, overhead_hwm=hwm - base,
        sampling_miss=hwm - peak['rss'],
        peak_phase=peak['phase'], peak_files=peak['files'],
        peak_descriptor_total=peak['descriptor_total'],
        peak_plr_segment_bytes=peak['plr_segment_bytes'],
        peak_kmv_sample_bytes=peak['kmv_sample_bytes'],
        peak_kmv_bucket_bytes=peak['kmv_bucket_bytes'],
        peak_descriptor_object_bytes=peak['descriptor_object_bytes'],
        peak_registry_index_bytes=peak['registry_index_bytes'],
        peak_block_cache=peak['block_cache'], peak_memtables=peak['memtables'],
        peak_other_live=peak['other_live'],
        peak_rss_minus_allocated=peak['rss_minus_allocated'],
        peak_je_allocated=peak['je_allocated'], peak_je_active=peak['je_active'],
        peak_je_resident=peak['je_resident'], peak_je_retained=peak['je_retained'],
        levels=levels(text), profile=str(profile), status='validated',
        exact_membership=bool(membership_mb),
        membership_budget_mb=membership_mb,
        phase1_shards=opts['vcomp_phase1_shards'],
        membership_bitmap_bytes=membership_bytes(opts['num']) if membership_mb else 0)
    save_json(log / 'validated.json', record)
    print('  {:<12} {:>7.1f} s  peak(HWM) {:>7.2f} GiB  over baseline {:>7.2f} GiB  '
          'desc {:>6.2f}  other {:>6.2f}  files {}'.format(
              name, elapsed, hwm / MIB / 1024, (hwm - base) / MIB / 1024,
              peak['descriptor_total'] / MIB / 1024,
              peak['other_live'] / MIB / 1024, peak['files']), flush=True)
    if not keep_db:
        subprocess.run(['rm', '-rf', str(db)], check=True)
    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-id', default='f2load_memprofile_260914')
    ap.add_argument('--sizes', default='1000,2000,4000')
    ap.add_argument('--kv', default='1024,100')
    ap.add_argument('--keep-db', action='store_true')
    ap.add_argument('--exact-membership', action='store_true',
                    help='load with the membership bitmap enabled')
    ap.add_argument('--phase1-shards', type=int, default=0,
                    help='override --vcomp_phase1_shards (default: the frozen value, 8)')
    ap.add_argument('--membership-max-mb', type=int, default=1024,
                    help='budget for both bitmaps together (default 1024)')
    ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()

    sizes = [int(x) for x in args.sizes.split(',') if x]
    kvs = [int(x) for x in args.kv.split(',') if x]
    plan = arms(sizes, kvs)
    root = EXP / 'artifacts/log_loads' / args.run_id

    print('binary {} ({})'.format(BINARY, sha(BINARY)[:12]))
    for gib, kv in plan:
        o = load_options(gib, kv, Path('/tmp'), Path('/tmp'), 'f2load')
        print('  {:>5} GiB  kv {:<5} key {:<3} value {:<5} records {:>15,}'.format(
            gib, kv, o['key_size'], o['value_size'], o['num']))
    free = os.statvfs('/work')
    print('free on /work: {:.1f} TiB; largest arm needs {:.1f} TiB'.format(
        free.f_bavail * free.f_frsize / 2 ** 40, max(sizes) / 1024))
    if not args.execute:
        print('plan only; pass --execute to run')
        return 0

    require(not active_benchmarks(), 'another storage benchmark is active')
    require(not root.exists(), 'run id already used: ' + str(root))
    root.mkdir(parents=True)
    DB_ROOT.mkdir(parents=True, exist_ok=True)
    save_json(root / 'manifest.json', dict(
        run_id=args.run_id, binary=str(BINARY), binary_sha256=sha(BINARY),
        runner_sha256=sha(__file__), sizes=sizes, kv=kvs, keep_db=args.keep_db,
        started=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())))

    records = []
    for gib, kv in plan:
        print('=== {} GiB, {} B ==='.format(gib, kv), flush=True)
        records.append(run_one(root, gib, kv, args.keep_db,
                               args.membership_max_mb if args.exact_membership else 0,
                               args.phase1_shards))
        save_json(root / 'results.json', records)
    (root / 'COMPLETED.json').write_text(json.dumps(
        dict(status='complete', arms=len(records)), indent=1) + '\n')
    print('done: ' + str(root))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
