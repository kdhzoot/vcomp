#!/usr/bin/env python3
"""Dump L1 range coverage for an already-loaded baseline DB, read-only.

The coverage step of run_baseline_coverage_repeats.py only runs as part of that
campaign, so loads promoted by other campaigns (for example the shared
paper_ch23_common baseline) carry no coverage record. This script reproduces
exactly that step against any validated load: it stages a hardlink clone, reads
metadata with the qualified profiler binary, verifies that neither the original
nor the clone changed, and removes the clone. The original DB is never opened
through RocksDB and never written.
"""
import argparse
import fcntl
import json
from pathlib import Path
import shutil
import subprocess
import sys

EXP = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(EXP / 'lib'), str(EXP / 'analysis')]
from ch23_common import (BASE, command, db_identity, levels, require, save_json,
                         sha, stage_db)
from parse_coverage import parse_file
from compare_ycsb_l1_coverage import union_count

PROF = EXP / 'artifacts/log_runs/paper_alternatives_ycsb_cached0_260908_no_flush_run2/bin/db_bench'
PROF_SHA = '20d67c38612cee9a34d9ef7114c81bc8366b2d4cf99c598b642e63f21ab74266'


def dump(load_json, out_root, stage_root):
    result = json.loads(Path(load_json).read_text())
    require(result['status'] == 'validated', 'load is not validated')
    require(sha(PROF) == PROF_SHA, 'coverage executable changed')
    source = Path(result['db_dir'])
    require((source / 'CURRENT').is_file(), 'source DB missing')
    out = Path(out_root)
    out.mkdir(parents=True, exist_ok=True)
    clone = Path(stage_root) / 'coverage_staging'
    require(not clone.exists(), 'staging path already exists: ' + str(clone))

    with (source / 'LOCK').open('r+b') as lock:
        fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        identity = stage_db(source, clone)
        archived = json.loads(Path(result['source_identity_file']).read_text())
        require(identity == archived, 'source changed since the load was validated')
        options = dict(BASE, db=str(clone), num=result['num_keys'], key_size=24,
                       value_size=1000, benchmarks='coverage,levelstats',
                       use_existing_db=True, readonly=True,
                       disable_auto_compactions=True, memtablerep='skip_list',
                       open_files=20, cache_size=1,
                       cache_index_and_filter_blocks=True,
                       pin_l0_filter_and_index_blocks_in_cache=False,
                       pin_top_level_index_and_filter=False,
                       report_interval_seconds=0, statistics=False)
        argv = command(PROF, options)
        save_json(out / 'command.json', argv)
        with (out / 'baseline.cov').open('w') as stream:
            proc = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT,
                                  timeout=600, check=False)
        text = (out / 'baseline.cov').read_text(errors='replace')
        require(proc.returncode == 0 and '=== coverage(' in text and
                'Corruption:' not in text, 'coverage inspection failed')
        require(db_identity(source) == identity == db_identity(clone),
                'read-only coverage changed source or clone identity')
        # levels() keys by int; a load record read back from JSON keys by str.
        parsed = {str(k): v for k, v in levels(text).items()}
        require(parsed == result['levels'], 'coverage layout mismatch')
        rows, ranges = parse_file(out / 'baseline.cov')
        require(len(ranges) == result['levels']['1']['files'], 'incomplete L1 dump')
        require(all(0 <= r['key_lo'] <= r['key_hi'] < result['num_keys']
                    for r in ranges), 'L1 range outside key domain')
        covered = union_count([(r['key_lo'], r['key_hi']) for r in ranges])
        coverage = dict(
            case_id=result.get('case_id'), db_dir=str(source),
            domain_keys=result['num_keys'], l1_covered_integer_keys=covered,
            l1_global_coverage_pct=100 * covered / result['num_keys'],
            definition='inclusive L1 range union / full integer query domain',
            levels=rows, l1_ranges=ranges, binary=str(PROF),
            binary_sha256=PROF_SHA, diagnostic_only=True, source_unchanged=True)
        save_json(out / 'coverage.json', coverage)
        require(clone.name == 'coverage_staging' and not clone.is_symlink(),
                'unsafe cleanup target')
        shutil.rmtree(clone)
    print('{:.6f}% global L1 coverage -> {}'.format(
        coverage['l1_global_coverage_pct'], out / 'coverage.json'))
    return coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--load-json', required=True,
                        help='validated.json of the load to inspect')
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--stage-dir', required=True,
                        help='scratch directory on the same filesystem as the DB')
    args = parser.parse_args()
    dump(args.load_json, args.out_dir, args.stage_dir)


if __name__ == '__main__':
    main()
