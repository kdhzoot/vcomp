#!/usr/bin/env python3
"""Read source SST range metadata through disposable, read-only DB clones.

No loading or read workload is run. Distinguish range coverage over the entire
numeric key domain from the existing coverage command's level-envelope ratio.
"""
import argparse
import csv
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

EXPERIMENTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXPERIMENTS / 'lib'))
from ch23_common import (BASE, active_benchmarks, command, db_identity, levels,
                         require, save_json, sha, stage_db)
from parse_coverage import parse_file

RUN = 'paper_alternatives_ycsb_cached0_260908_no_flush_run2'
SYSTEMS = ('baseline', 'f2load')


def union_count(ranges):
    merged = []
    for lo, hi in sorted(ranges):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return sum(hi - lo + 1 for lo, hi in merged)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    require(re.fullmatch(r'[A-Za-z0-9_-]+', args.run_id), 'unsafe run ID')
    require(not active_benchmarks(), 'another benchmark is active')
    root = EXPERIMENTS / 'artifacts/coverage_dumps' / args.run_id
    dbroot = Path('/work/vcomp/exp') / args.run_id
    require(not root.exists() and not dbroot.exists(), 'use a new diagnostic ID')
    bundle = EXPERIMENTS / 'results' / RUN
    manifest = json.loads((bundle / 'manifest.json').read_text())
    binary = Path(manifest['frozen_binary'])
    require(sha(binary) == manifest['binary_sha256'], 'binary hash mismatch')
    sources = json.loads((bundle / 'provenance/sources.json').read_text())
    identities = json.loads((bundle / 'provenance/source_identities.json').read_text())
    rows = json.loads((bundle / 'results.json').read_text())
    source_loads = json.loads((Path(manifest['source_bundle']) / 'loads.json').read_text())
    root.mkdir(parents=True)
    dbroot.mkdir(parents=True)
    locks, summary, ranges_output = [], [], []
    try:
        for system in SYSTEMS:
            src = Path(sources[system]['db_dir'])
            fd = os.open(src / 'LOCK', os.O_RDWR)
            locks.append(fd)
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require(db_identity(src) == identities[system], 'source changed: ' + system)
            c_row = next(r for r in rows if r['phase'] == 'full' and
                         r['system'] == system and r['workload'] == 'workloadc')
            require(c_row['final_levels'] == source_loads[system + '_1kb']['levels'],
                    'C final level layout differs from initial source')

        for system in SYSTEMS:
            source = sources[system]
            src, dst = Path(source['db_dir']), dbroot / system
            original = stage_db(src, dst)
            require(original == identities[system], 'staging identity differs')
            opts = dict(BASE, db=str(dst), num=source['num_keys'],
                        key_size=source['key_bytes'], value_size=source['value_bytes'],
                        benchmarks='coverage,levelstats', use_existing_db=True,
                        readonly=True, disable_auto_compactions=True,
                        memtablerep='skip_list', open_files=20,
                        cache_size=1, cache_index_and_filter_blocks=True,
                        pin_l0_filter_and_index_blocks_in_cache=False,
                        pin_top_level_index_and_filter=False,
                        report_interval_seconds=0, statistics=False)
            argv = command(binary, opts)
            save_json(root / (system + '.command.json'), argv)
            with (root / (system + '.cov')).open('w') as stream:
                completed = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT,
                                           timeout=90, check=False)
            text = (root / (system + '.cov')).read_text(errors='replace')
            require(completed.returncode == 0, 'coverage command failed: ' + system)
            require('=== coverage(' in text and 'Corruption:' not in text,
                    'invalid coverage output')
            require(db_identity(src) == original, 'original source was changed')
            require(db_identity(dst) == original, 'read-only clone identity was changed')
            save_json(root / (system + '.source_identity.json'), original)
            by_level, l1 = parse_file(root / (system + '.cov'))
            final = levels(text)
            require({str(k): v for k, v in final.items()} ==
                    source_loads[system + '_1kb']['levels'], 'unexpected diagnostic layout')
            l1_summary = next(r for r in by_level if r['level'] == 1)
            require(len(l1) == l1_summary['files'], 'incomplete L1 range dump')
            domain = source['num_keys']
            for row in l1:
                require(0 <= row['key_lo'] <= row['key_hi'] < domain,
                        'range outside numeric key domain')
                ranges_output.append(dict(system=system, level=1,
                    file_index=row['idx'], key_lo=row['key_lo'], key_hi=row['key_hi']))
            for row in by_level:
                exact = union_count([(r['key_lo'], r['key_hi']) for r in l1]) \
                    if row['level'] == 1 else None
                summary.append(dict(system=system, **row,
                    domain_keys=domain,
                    domain_coverage_pct=100 * (exact if exact is not None else row['union_span']) / domain,
                    exact_inclusive_L1=exact is not None))
            # Remove only the just-validated disposable clone; original SSTs
            # retain their own links. The clone can be recreated from the source.
            require(dst.parent == dbroot and not dst.is_symlink(), 'unsafe cleanup')
            shutil.rmtree(dst)
        for name, values in (('coverage.tsv', summary), ('l1_ranges.tsv', ranges_output)):
            with (root / name).open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(values[0]), delimiter='\t')
                writer.writeheader()
                writer.writerows(values)
        save_json(root / 'manifest.json', dict(
            source_result_bundle=str(bundle), binary=str(binary),
            binary_sha256=sha(binary), diagnostic_only=True, original_sources_unchanged=True,
            clone_mode='immutable SST hardlinks, mutable metadata copies, read-only Open',
            coverage_definition='range union / [0, num_keys); L1 inclusive exact, other levels use existing span output',
            legacy_cov_pct_definition='range union / [level minimum, level maximum]',
            source_paths={s: sources[s]['db_dir'] for s in SYSTEMS},
            script_sha256=sha(Path(__file__)), rows=summary))
        shutil.copy2(Path(__file__), root / Path(__file__).name)
        print(root)
        for row in summary:
            print(row['system'], 'L' + str(row['level']), 'files', row['files'],
                  'global_coverage_pct', round(row['domain_coverage_pct'], 6),
                  'legacy_envelope_pct', row['cov_pct'])
        print('L1 RANGES', json.dumps(ranges_output, indent=2))
    finally:
        for fd in locks:
            os.close(fd)


if __name__ == '__main__':
    main()
