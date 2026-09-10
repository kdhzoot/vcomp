#!/usr/bin/env python3
"""Repeat the original 1TB baseline twice, then inspect immutable L1 ranges."""
import argparse
import csv
import fcntl
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import subprocess
import sys
import time
import traceback
from types import SimpleNamespace

EXP = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(EXP / 'lib'), str(EXP / 'scripts/paper'), str(EXP / 'analysis')]
from ch23_common import (BASE, CLEAN, HASHES, TIB, active_benchmarks,
                         check_binary, command, db_identity, levels,
                         load_options, require, save_json, sha, stage_db)
from run_paper_ch23_common import Campaign
from parse_coverage import parse_file
from compare_ycsb_l1_coverage import union_count

SOURCE_RUN = 'paper_ch23_common_260905_approved_run3'
SOURCE_COMMAND = EXP / 'results' / SOURCE_RUN / 'provenance/log_loads' / SOURCE_RUN / 'full/baseline_1kb/phase1/raw/command.json'
PROF = EXP / 'artifacts/log_runs/paper_alternatives_ycsb_cached0_260908_no_flush_run2/bin/db_bench'
PROF_SHA = '20d67c38612cee9a34d9ef7114c81bc8366b2d4cf99c598b642e63f21ab74266'
COMMIT = 'f455ab7bd6a8c67f00d48075bb310f131d9fae5f'


def normalized(argv, pilot=False):
    ignored = {'db', 'report_file'} | ({'num'} if pilot else set())
    return {arg.split('=', 1)[0][2:]: arg.split('=', 1)[1]
            for arg in argv[1:] if arg.split('=', 1)[0][2:] not in ignored}


class RepeatCase(Campaign):
    def __init__(self, run_id, phase, name):
        super().__init__(SimpleNamespace(run_id=run_id, phase=phase))
        self.root /= name
        self.dbroot /= name
        self.readroot /= name

    def prepare(self):
        require(not active_benchmarks(), 'another benchmark is active')
        check_binary(CLEAN)
        expected = json.loads(SOURCE_COMMAND.read_text())
        opts = load_options(self.gib, 1024, self.dbroot / 'baseline_1kb',
                            self.root / 'baseline_1kb/phase1', 'baseline')
        argv = command(CLEAN, opts)
        require(argv[0] == expected[0] and
                normalized(argv, self.gib == 1) == normalized(expected, self.gib == 1),
                'loading command differs from original beyond output paths/pilot scale')
        for root in (self.root, self.dbroot, self.readroot):
            require(not root.exists(), 'fresh path already exists: ' + str(root))
            root.mkdir(parents=True)
        save_json(self.root / 'manifest.json', dict(
            dataset_gib=self.gib, binary=str(CLEAN), binary_sha256=HASHES[str(CLEAN)],
            commit=COMMIT, original_command=str(SOURCE_COMMAND), command=argv,
            configuration_match=True, same_seed=True))
        self.event('READY', str(self.root))

    def coverage(self, result):
        require(not active_benchmarks(), 'concurrent benchmark before coverage')
        require(sha(PROF) == PROF_SHA, 'coverage executable changed')
        src = Path(result['db_dir'])
        dst = self.dbroot / 'coverage_staging'
        out = self.root / 'coverage'
        out.mkdir()
        with (src / 'LOCK').open('r+b') as lock:
            fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            identity = stage_db(src, dst)
            require(identity == json.loads(Path(result['source_identity_file']).read_text()),
                    'new source changed before coverage')
            opts = dict(BASE, db=str(dst), num=result['num_keys'], key_size=24,
                value_size=1000, benchmarks='coverage,levelstats', use_existing_db=True,
                readonly=True, disable_auto_compactions=True, memtablerep='skip_list',
                open_files=20, cache_size=1, cache_index_and_filter_blocks=True,
                pin_l0_filter_and_index_blocks_in_cache=False,
                pin_top_level_index_and_filter=False,
                report_interval_seconds=0, statistics=False)
            argv = command(PROF, opts)
            save_json(out / 'command.json', argv)
            with (out / 'baseline.cov').open('w') as stream:
                proc = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT,
                                      timeout=120, check=False)
            text = (out / 'baseline.cov').read_text(errors='replace')
            require(proc.returncode == 0 and '=== coverage(' in text and
                    'Corruption:' not in text, 'coverage inspection failed')
            require(db_identity(src) == identity == db_identity(dst),
                    'read-only coverage changed source or clone identity')
            require(levels(text) == result['levels'], 'coverage layout mismatch')
            rows, ranges = parse_file(out / 'baseline.cov')
            require(len(ranges) == result['levels'][1]['files'], 'incomplete L1 dump')
            require(all(0 <= r['key_lo'] <= r['key_hi'] < result['num_keys']
                        for r in ranges), 'L1 range outside key domain')
            covered = union_count([(r['key_lo'], r['key_hi']) for r in ranges])
            cov = dict(domain_keys=result['num_keys'], l1_covered_integer_keys=covered,
                l1_global_coverage_pct=100 * covered / result['num_keys'],
                definition='inclusive L1 range union / full integer query domain',
                legacy_definition='union span / level-local min-to-max span',
                levels=rows, l1_ranges=ranges, binary=str(PROF), binary_sha256=PROF_SHA,
                diagnostic_only=True, source_unchanged=True)
            save_json(out / 'coverage.json', cov)
            save_json(out / 'source_identity.json', identity)
            require(dst.parent == self.dbroot and not dst.is_symlink(), 'unsafe cleanup')
            shutil.rmtree(dst)
        self.event('COVERAGE', '{:.6f}% global L1'.format(cov['l1_global_coverage_pct']))
        return cov


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--repeats', type=int, default=2,
                        help='number of full 1000-GiB repeats (default 2)')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    require(re.fullmatch(r'baseline_coverage_[A-Za-z0-9_]+', args.run_id), 'invalid run ID')
    require(1 <= args.repeats <= 16, 'repeats out of range')
    cases = [RepeatCase(args.run_id, 'pilot', 'qualification')]
    cases += [RepeatCase(args.run_id, 'full', 'repeat_{:02}'.format(i))
              for i in range(1, args.repeats + 1)]
    expected = json.loads(SOURCE_COMMAND.read_text())
    for case in cases:
        argv = command(CLEAN, load_options(case.gib, 1024, case.dbroot / 'baseline_1kb',
                       case.root / 'baseline_1kb/phase1', 'baseline'))
        require(normalized(argv, case.gib == 1) == normalized(expected, case.gib == 1),
                'original command mismatch')
        if args.dry_run:
            print(json.dumps(dict(dataset_gib=case.gib, command=argv)))
    if args.dry_run:
        return
    root = EXP / 'artifacts/log_loads' / args.run_id
    dbroot = Path('/work/vcomp/exp') / args.run_id
    readroot = EXP / 'artifacts/log_runs' / args.run_id
    require(not any(p.exists() for p in (root, dbroot, readroot)), 'fresh run paths required')
    require(not active_benchmarks(), 'another benchmark is active')
    budget = max(5, 2 + args.repeats) * TIB
    require(shutil.disk_usage('/work').free >= budget,
            'initial space budget unavailable: need {} TiB'.format(budget // TIB))
    check_binary(CLEAN)
    require(sha(PROF) == PROF_SHA, 'coverage binary mismatch')
    require(subprocess.check_output(['git', '-C', str(CLEAN.parent), 'rev-parse', 'HEAD'],
            text=True).strip() == COMMIT, 'baseline commit mismatch')
    require(not subprocess.check_output(['git', '-C', str(CLEAN.parent), 'status', '--porcelain'],
            text=True), 'baseline source worktree is dirty')
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(1048576, hard), hard))
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with (EXP / 'artifacts/paper_ch23_common.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        root.mkdir(parents=True)
        archive = root / 'analysis_sources'
        archive.mkdir()
        for path in (Path(__file__), EXP / 'scripts/paper/run_paper_ch23_common.py',
                     EXP / 'lib/ch23_common.py', EXP / 'lib/common.sh',
                     EXP / 'analysis/parse_coverage.py',
                     EXP / 'analysis/compare_ycsb_l1_coverage.py'):
            shutil.copy2(path, archive / path.name)
        shutil.copy2(SOURCE_COMMAND, root / 'original_load_command.json')
        save_json(root / 'manifest.json', dict(run_id=args.run_id,
            question='same-input baseline final-layout variability',
            full_repetitions=args.repeats, full_dataset_gib=1000, pilot_dataset_gib=1,
            binary=str(CLEAN), binary_sha256=HASHES[str(CLEAN)], commit=COMMIT,
            coverage_binary=str(PROF), coverage_binary_sha256=PROF_SHA,
            seed=12345678, preserve_completed_dbs=True, command=sys.argv,
            source_hashes={p.name: sha(p) for p in archive.iterdir()},
            started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())))
        with (root / 'environment.txt').open('w') as stream:
            for cmd in (['uname', '-a'], ['lscpu'], ['free', '-b'], ['df', '-B1', '/work'],
                        ['findmnt', '/work']):
                subprocess.run(cmd, stdout=stream, stderr=subprocess.STDOUT, check=True)
            stream.write(Path('/proc/mdstat').read_text())
        completed = []
        try:
            for case in cases:
                save_json(root / 'status.json', dict(state='running', case=str(case.root)))
                case.prepare()
                result = case.load_case('baseline', kv=1024)
                cov = case.coverage(result)
                completed.append(dict(phase=case.args.phase, name=case.root.name,
                                      loading=result, coverage=cov))
                save_json(root / 'results.json', completed)
                (case.root / 'COMPLETED').write_text('validated loading, reopen, and coverage\n')
            save_json(root / 'status.json', dict(state='complete',
                full_repetitions=args.repeats,
                completed_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())))
            (root / 'COMPLETED').write_text(
                '{} full baseline repeat(s) validated\n'.format(args.repeats))
            with (root / 'summary.tsv').open('w', newline='') as stream:
                writer = csv.writer(stream, delimiter='\t')
                writer.writerow(['phase', 'name', 'loading_sec', 'l1_files',
                                 'l1_size_mib', 'l1_global_coverage_pct', 'db_dir'])
                for row in completed:
                    load, cov = row['loading'], row['coverage']
                    writer.writerow([row['phase'], row['name'], load['elapsed_sec'],
                        load['levels'][1]['files'], load['levels'][1]['size_mib'],
                        cov['l1_global_coverage_pct'], load['db_dir']])
            print('COMPLETED ' + str(root), flush=True)
        except BaseException:
            (root / 'FAILED.txt').write_text(traceback.format_exc())
            save_json(root / 'status.json', dict(state='failed', completed_cases=len(completed)))
            raise


if __name__ == '__main__':
    main()
