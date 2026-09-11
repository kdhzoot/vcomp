#!/usr/bin/env python3
"""Load a 91 B dataset with the current F2Load build and time it.

F2Load has only ever been measured at the 1 KB KV size. The 91 B configuration
holds the same bytes in 11.25 times as many keys, and F2Load's cost is expected
to follow key count rather than bytes, so the point has to be measured rather
than scaled from the 1 KB result.

The load writes into a directory that must not already exist, since db_bench
destroys whatever it opens without --use_existing_db. After loading, the DB is
reopened read-only and probed with the load seed, which selects keys the load
already submitted; a load that reports a time but cannot return its own keys is
a failure, not a fast result.
"""
import argparse
import json
import re
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lib'))
from ch23_common import (BASE, F2, CLEAN, GIB, HASHES, command, db_identity,
    levels, load_options, measure, require, save_json, sha, bench_stats,
    active_benchmarks)

# The frozen table pins the F2Load build used by the September campaigns, and
# measure() refuses anything else. This script exists to measure the current
# build, so it registers that build's hash here rather than relaxing the table:
# the check still catches a binary changing underneath a running campaign, and
# the hash actually used is recorded in the result.
HASHES[str(F2)] = sha(F2)

KV = 91


def verify_options(db, log, num, reads, seed=12345678):
    return dict(BASE, num=num, key_size=48, value_size=43, db=str(db),
                report_file=str(log / 'report.rep'), use_existing_db=True,
                readonly=True, disable_auto_compactions=True,
                benchmarks='readrandom,stats', read_random_exp_range=0,
                threads=1, reads=reads, seed=seed, merge_operator='put',
                cache_type='lru_cache', cache_size=50 * GIB,
                cache_index_and_filter_blocks=False, open_files=-1,
                stats_level=3, perf_level=3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--gib', type=int, required=True)
    ap.add_argument('--db-root', required=True)
    ap.add_argument('--log-root', required=True)
    ap.add_argument('--reads', type=int, default=1000)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    db = Path(args.db_root) / 'f2load_91b_{}gib'.format(args.gib)
    log = Path(args.log_root) / 'f2load_91b_{}gib'.format(args.gib)
    require(not db.exists(), 'refusing to write an existing path: ' + str(db))
    require(not active_benchmarks(), 'another storage benchmark is active')
    num = args.gib * GIB // KV
    opts = load_options(args.gib, KV, db, log, 'f2load')
    require(opts['num'] == num, 'unexpected key count')
    print('keys={:,}  db={}'.format(num, db))
    print(' '.join(command(F2, opts)))
    if args.dry_run:
        return 0

    log.parent.mkdir(parents=True, exist_ok=True)
    # db_bench creates only the leaf directory, never its parents.
    db.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    phase, text = measure(command(F2, opts), log, lambda *a: None)
    elapsed = phase['elapsed_sec']
    stats = bench_stats(text, 'fillvirtual')
    ident = db_identity(db)
    result = dict(system='f2load', dataset_gib=args.gib, key_bytes=48,
                  value_bytes=43, num_keys=num, elapsed_sec=elapsed,
                  loading_min=elapsed / 60, benchmark=stats,
                  levels=levels(text), peak_rss_kb=phase['peak_rss_kb'],
                  final_sst_bytes=sum(v[1] for v in ident['ssts'].values()),
                  final_sst_count=len(ident['ssts']),
                  binary=str(F2), binary_sha256=sha(F2),
                  db_dir=str(db), log_dir=str(log))
    save_json(log / 'db_identity.json', ident)
    print(json.dumps({k: v for k, v in result.items() if k != 'levels'},
                     indent=2, sort_keys=True), flush=True)

    # Read back with the load seed, using the clean release rather than the
    # F2Load build, so the check also proves the state is readable by stock
    # RocksDB and not only by the loader that wrote it.
    vlog = log / 'verify'
    _, vtext = measure(command(CLEAN, verify_options(db, vlog, num, args.reads)),
                       vlog, lambda *a: None)
    found = 0
    m = re.search(r'readrandom\s+:.*?\(\s*(\d+) of (\d+) found', vtext, re.S)
    if m:
        found, total = int(m.group(1)), int(m.group(2))
    else:
        total = args.reads
    result['validation'] = '{}_of_{}_found_after_readonly_reopen'.format(found, total)
    require(found > 0, 'loaded DB returned none of its own keys')
    result['status'] = 'validated'
    result['total_wall_sec'] = round(time.time() - started, 1)
    save_json(log / 'validated.json', result)
    print('validation: ' + result['validation'])
    print('loading_min = {:.2f}'.format(result['loading_min']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
