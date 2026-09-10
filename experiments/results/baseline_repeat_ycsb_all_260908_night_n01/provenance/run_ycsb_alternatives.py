#!/usr/bin/env python3
"""Run the approved, sequential cached-zero YCSB alternatives campaign."""
import argparse
import csv
import fcntl
import json
import os
from pathlib import Path
import re
import resource
import shlex
import shutil
import signal
import subprocess
import sys
import time
import traceback

EXPERIMENTS = Path(__file__).resolve().parents[2]
REPO = EXPERIMENTS.parent
sys.path.insert(0, str(EXPERIMENTS / 'lib'))
sys.path.insert(0, str(EXPERIMENTS / 'analysis'))
from ch23_common import (BASE, GIB, TIB, SYSTEMS, active_benchmarks as all_benchmarks, command,
                         db_identity, levels, require, save_json, sha,
                         snapshot, stage_db, vm_swap)
from parse_ycsb_alternatives import parse_metrics

SOURCE_BUNDLE = EXPERIMENTS / 'results/paper_ch23_common_260907_f2_completion1'
PROFILER = REPO.parent / 'vcomp-prof'
BINARY_HASH = '20d67c38612cee9a34d9ef7114c81bc8366b2d4cf99c598b642e63f21ab74266'
SOURCE_COMMIT = 'dbb0a44a65344f263356c507ec09762c8ed81a71'


def utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def process_state(pid):
    try:
        # The command name can contain spaces or parentheses.
        fields = Path('/proc/{}/stat'.format(pid)).read_text().rsplit(')', 1)[1].split()
        return fields[0], int(fields[2])  # state, process group
    except (FileNotFoundError, ProcessLookupError):
        return None


def active_benchmarks():
    # Exited, unreaped children do not perform I/O and must not block the queue.
    return [pid for pid in all_benchmarks()
            if (process_state(pid) or ('Z',))[0] not in ('Z', 'X')]


def group_members(pgid):
    members = []
    for path in Path('/proc').iterdir():
        if path.name.isdigit():
            state = process_state(int(path.name))
            if state and state[0] not in ('Z', 'X') and state[1] == pgid:
                members.append(int(path.name))
    return members


def options(source, db, log, workload, duration, cache_size=1):
    result = dict(BASE, num=source['num_keys'], key_size=source['key_bytes'],
                  value_size=source['value_bytes'], db=str(db),
                  benchmarks=workload + ',stats,levelstats',
                  use_existing_db=True, readonly=False,
                  disable_auto_compactions=False, memtablerep='skip_list',
                  threads=48, duration=duration, reads=10000000000,
                  seed=87654321, ops_between_duration_checks=1,
                  cache_type='lru_cache', cache_size=cache_size,
                  cache_index_and_filter_blocks=True,
                  pin_l0_filter_and_index_blocks_in_cache=False,
                  pin_top_level_index_and_filter=False,
                  open_files=-1, stats_level=3, histogram=True, perf_level=3,
                  report_interval_seconds=1, report_file=str(log / 'report.rep'),
                  stats_interval_seconds=30, stats_per_interval=1,
                  ycsb_requestdistribution='latest' if workload == 'workloadd'
                  else 'zipfian', ycsb_minscanlength=1, ycsb_maxscanlength=100,
                  ycsb_scanlengthdistribution='uniform')
    return result


def order():
    cells = []
    for index, letter in enumerate('abcdef'):
        systems = SYSTEMS[index:] + SYSTEMS[:index]
        cells.extend((system, 'workload' + letter) for system in systems)
    return cells


class Campaign:
    def __init__(self, args):
        self.args = args
        self.cache_size = getattr(args, 'cache_size', 1)
        self.root = EXPERIMENTS / 'artifacts/log_runs' / args.run_id
        self.dbroot = Path('/work/vcomp/exp') / args.run_id
        self.sources = json.loads((SOURCE_BUNDLE / 'loads.json').read_text())
        self.identities = {}
        self.locks = []
        self.rows = []
        self.systems = tuple(s for s in SYSTEMS
                             if not (args.exclude_flush_only and s == 'flush_only'))
        self.cells = [(s, w) for s, w in order() if s in self.systems]
        self.reused = set()
        self.current = None
        self.proc = None
        self.binary = self.root / 'bin/db_bench'

    def event(self, kind, **values):
        row = dict(utc=utc(), kind=kind, **values)
        print(json.dumps(row), flush=True)
        with (self.root / 'events.jsonl').open('a') as stream:
            stream.write(json.dumps(row) + '\n')
        status = dict(row)
        status.update(pid=os.getpid(), current=self.current,
            finished_full=sum(r['phase'] == 'full' for r in self.rows),
            valid_full=sum(r['phase'] == 'full' and r['status'] == 'ok'
                           for r in self.rows), total_full=len(self.cells),
            reused_full=len(self.reused))
        save_json(self.root / 'status.json', status)

    def verify_sources(self):
        for system in self.systems:
            require(db_identity(self.sources[system + '_1kb']['db_dir']) ==
                    self.identities[system], 'source changed: ' + system)

    def prepare(self):
        require(re.fullmatch(r'[A-Za-z0-9_-]+', self.args.run_id), 'unsafe run ID')
        require(not active_benchmarks(), 'another storage benchmark is active')
        require(not self.root.exists() and not self.dbroot.exists(),
                'run ID already exists; never overwrite a campaign')
        require(shutil.disk_usage('/work').free >= 3 * TIB,
                'less than 3 TiB initial free space')
        require(sha(PROFILER / 'db_bench') == BINARY_HASH, 'binary hash changed')
        require(subprocess.check_output(['git', '-C', str(PROFILER), 'rev-parse',
                                        'HEAD'], text=True).strip() == SOURCE_COMMIT,
                'profiler source commit changed')
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE,
                           (min(hard, max(soft, 1048576)), hard))
        for system in self.systems:
            source = self.sources[system + '_1kb']
            src = Path(source['db_dir'])
            require((src / 'CURRENT').is_file(), 'source missing: ' + str(src))
            # RocksDB uses POSIX record locks, not BSD flock. Keep these fds
            # open for the campaign, and never open the source through RocksDB.
            fd = os.open(src / 'LOCK', os.O_RDWR)
            self.locks.append(fd)
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require(src.stat().st_dev == Path('/work/vcomp/exp').stat().st_dev,
                    'source and staging must share a filesystem')
            identity = db_identity(src)
            expected = json.loads(Path(source['source_identity_file']).read_text())
            require(identity == expected, 'archived source identity mismatch: ' + system)
            self.identities[system] = identity
        self.root.mkdir(parents=True)
        self.dbroot.mkdir(parents=True)
        (self.root / 'bin').mkdir()
        shutil.copy2(PROFILER / 'db_bench', self.binary)
        require(sha(self.binary) == BINARY_HASH, 'frozen copy hash mismatch')
        provenance = self.root / 'provenance'
        provenance.mkdir()
        paths = [Path(__file__), EXPERIMENTS / 'lib/ch23_common.py',
                 EXPERIMENTS / 'lib/common.sh',
                 Path(__file__).with_name('test_run_ycsb_alternatives.py'),
                 EXPERIMENTS / 'analysis/parse_ycsb_alternatives.py',
                 EXPERIMENTS / 'docs/PAPER_ALTERNATIVES_YCSB_CACHED0.md']
        for path in paths:
            shutil.copy2(path, provenance / path.name)
        save_json(provenance / 'source_hashes.json', {str(p): sha(p) for p in paths})
        save_json(provenance / 'sources.json', {
            system: self.sources[system + '_1kb'] for system in self.systems})
        save_json(provenance / 'source_identities.json', self.identities)
        for label, repo in (('vcomp', REPO), ('vcomp-prof', PROFILER)):
            for ext, argv in (('commit', ['rev-parse', 'HEAD']),
                              ('status', ['status', '--short']),
                              ('diff', ['diff', '--binary'])):
                (provenance / (label + '.' + ext)).write_bytes(
                    subprocess.check_output(['git', '-C', str(repo)] + argv))
        with (provenance / 'environment.txt').open('w') as stream:
            for argv in (['uname', '-a'], ['lscpu'], ['free', '-b'],
                         ['df', '-B1', '/work'], ['findmnt', '/work']):
                subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT,
                               check=True)
            stream.write(Path('/proc/mdstat').read_text())
        save_json(self.root / 'manifest.json', dict(
            run_id=self.args.run_id, prepared_utc=utc(), duration_sec=self.args.duration,
            threads=48, dataset_gib=1000, cache_size_bytes=self.cache_size,
            automatic_compaction=True, source_bundle=str(SOURCE_BUNDLE),
            profiler_commit=SOURCE_COMMIT, binary_sha256=BINARY_HASH,
            frozen_binary=str(self.binary), repetitions=1,
            selected_systems=self.systems,
            excluded_systems=[s for s in SYSTEMS if s not in self.systems],
            full_order=self.cells, total_full=len(self.cells), pilot_seconds=[3, 5],
            reuse_run=self.args.reuse_run,
            full_watchdog_sec=self.args.duration + 600, pilot_watchdog_sec=180,
            representative_options=options(self.sources['baseline_1kb'],
                self.dbroot / 'full/workloada/baseline',
                self.root / 'full/workloada/baseline', 'workloada', self.args.duration,
                self.cache_size)))
        self.event('READY', message='sources locked; executable and configuration frozen')

    def reuse_results(self):
        if not self.args.reuse_run:
            return
        require(re.fullmatch(r'[A-Za-z0-9_-]+', self.args.reuse_run), 'unsafe reuse run ID')
        previous = EXPERIMENTS / 'artifacts/log_runs' / self.args.reuse_run
        require(previous != self.root, 'cannot reuse the current run')
        manifest = json.loads((previous / 'manifest.json').read_text())
        require(manifest['binary_sha256'] == BINARY_HASH and
                manifest['profiler_commit'] == SOURCE_COMMIT,
                'reused binary provenance differs')
        require(sha(Path(manifest['frozen_binary'])) == BINARY_HASH,
                'reused executable hash mismatch')
        old_status = json.loads((previous / 'status.json').read_text())
        require(old_status['kind'] in ('FAILED', 'COMPLETED'), 'old queue is not terminal')
        previous_rows = json.loads((previous / 'results.json').read_text())
        for row in previous_rows:
            cell = (row['system'], row['workload'])
            if row['phase'] != 'full' or row['status'] != 'ok' or cell not in self.cells:
                continue
            require(cell not in self.reused, 'duplicate reused cell')
            require(row['duration_sec'] == self.args.duration and row['threads'] == 48
                    and row['exit_code'] == 0 and not row['timed_out']
                    and abs(row['measured_seconds'] - self.args.duration) <= 5
                    and row['binary_sha256'] == BINARY_HASH,
                    'invalid reused measurement')
            log = Path(row['log_dir'])
            require(log.resolve() == (previous / 'full' / cell[1] / cell[0]).resolve(),
                    'unexpected reused evidence path')
            require(json.loads((log / 'validated.json').read_text()) == row,
                    'reused row differs from original validation')
            require(json.loads((log / 'source_identity.json').read_text()) ==
                    self.identities[cell[0]], 'reused source identity differs')
            source = self.sources[cell[0] + '_1kb']
            require(row['source_db_dir'] == source['db_dir'], 'reused source path differs')
            argv = json.loads((log / 'raw/command.json').read_text())
            expected = command(Path(argv[0]), options(source,
                Path(row['clone_db_dir']), log, cell[1], self.args.duration,
                self.cache_size))
            require(argv == expected, 'reused workload configuration differs')
            output = (log / 'bench.out').read_text(errors='replace')
            metrics = parse_metrics(output, cell[1])
            require(all(row.get(key) == value for key, value in metrics.items()),
                    'reparsed reused measurements differ')
            require(not metrics['missing_tickers'] and metrics['operation_histograms'],
                    'reused metrics incomplete')
            # Keep all measured values and original paths unchanged.
            self.rows.append(dict(row, reused_from_run=self.args.reuse_run))
            self.reused.add(cell)
        require(self.reused, 'no matching valid results available to reuse')
        provenance = self.root / 'provenance/reused_run'
        shutil.copytree(previous / 'provenance', provenance / 'provenance')
        for name in ('manifest.json', 'status.json', 'PILOT_COMPLETED.json'):
            if (previous / name).exists():
                shutil.copy2(previous / name, provenance / name)
        save_json(provenance / 'evidence_hashes.json', {
            str(Path(row['log_dir']) / name): sha(Path(row['log_dir']) / name)
            for row in self.rows for name in ('bench.out', 'validated.json',
                                              'source_identity.json', 'raw/command.json')})
        save_json(self.root / 'results.json', self.rows)
        self.write_tsv(self.root / 'summary.tsv')
        manifest = json.loads((self.root / 'manifest.json').read_text())
        manifest.update(reused_cells=sorted(self.reused),
                        pending_order=[c for c in self.cells if c not in self.reused])
        save_json(self.root / 'manifest.json', manifest)
        self.event('REUSED', cells=sorted(self.reused), previous_run=self.args.reuse_run)

    def terminate(self, grace_seconds=20):
        if self.proc is None:
            return
        pgid = self.proc.pid
        # /usr/bin/time may exit before db_bench. Wait for the entire private
        # process group, not only the wrapper; never signal another campaign.
        require(pgid != os.getpgrp(), 'refusing to terminate controller group')
        for sig in (signal.SIGTERM, signal.SIGKILL):
            self.proc.poll()
            if not group_members(pgid):
                break
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + grace_seconds
            while group_members(pgid) and time.monotonic() < deadline:
                self.proc.poll()
                time.sleep(0.1)
        self.proc.poll()
        require(not group_members(pgid), 'benchmark descendants survived termination')

    def measure(self, argv, log, timeout):
        require(not active_benchmarks(), 'another storage benchmark is active')
        require(sha(self.binary) == BINARY_HASH, 'frozen executable changed')
        raw = log / 'raw'
        raw.mkdir(parents=True)
        save_json(raw / 'command.json', argv)
        (raw / 'command.sh').write_text('#!/usr/bin/env bash\n' +
                                       shlex.join(argv) + '\n')
        with (raw / 'cache_reset.log').open('w') as stream:
            subprocess.run(['bash', '-c', 'source "$1"; drop_page_cache', 'ycsb',
                            str(EXPERIMENTS / 'lib/common.sh')], check=True,
                           stdout=stream, stderr=subprocess.STDOUT, timeout=120)
        require('Page cache dropped' in (raw / 'cache_reset.log').read_text(),
                'cache reset failed')
        snapshot(raw, 'start')
        started = time.time()
        monitor = None
        timed_out = False
        if shutil.which('iostat'):
            monitor = subprocess.Popen(['iostat', '-dx', '5'],
                stdout=(raw / 'iostat.log').open('w'), stderr=subprocess.STDOUT)
        try:
            with (log / 'bench.out').open('w') as output, \
                    (raw / 'monitor.jsonl').open('w') as telemetry:
                self.proc = subprocess.Popen(
                    ['/usr/bin/time', '-v', '-o', str(raw / 'time.out'),
                     'stdbuf', '-oL', '-eL'] + argv,
                    stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
                save_json(raw / 'process.json', dict(wrapper_pid=self.proc.pid,
                    process_group=self.proc.pid, started_epoch=started))
                self.event('BEGIN', process_group=self.proc.pid, log=str(log))
                while self.proc.poll() is None:
                    try:
                        self.proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        elapsed = time.time() - started
                        free = shutil.disk_usage('/work').free
                        pids = active_benchmarks()
                        for pid in pids:
                            try:
                                require(os.getpgid(pid) == self.proc.pid,
                                        'concurrent benchmark interference')
                            except ProcessLookupError:
                                pass
                        telemetry.write(json.dumps(dict(epoch=time.time(),
                            elapsed_sec=elapsed, free_bytes=free, pids=pids)) + '\n')
                        telemetry.flush()
                        require(free >= 2 * TIB, 'free space below 2 TiB')
                        self.event('RUNNING', elapsed_sec=round(elapsed, 1),
                                   benchmark_pids=pids)
                        if elapsed > timeout:
                            timed_out = True
                            self.terminate()
                            break
                rc = self.proc.returncode
        finally:
            self.terminate()
            if monitor is not None:
                monitor.terminate()
                monitor.wait(timeout=10)
            snapshot(raw, 'end')
        result = dict(exit_code=rc, timed_out=timed_out,
                      process_elapsed_sec=time.time() - started,
                      started_epoch=started, ended_epoch=time.time())
        save_json(raw / 'execution.json', result)
        require(vm_swap((raw / 'vmstat.start').read_text()) ==
                vm_swap((raw / 'vmstat.end').read_text()), 'new swap activity')
        rss = re.search(r'Maximum resident set size \(kbytes\):\s*(\d+)',
                        (raw / 'time.out').read_text())
        result['peak_rss_kb'] = int(rss.group(1)) if rss else None
        self.proc = None
        return result, (log / 'bench.out').read_text(errors='replace')

    def audit_options(self, db, log, expected):
        files = sorted(db.glob('OPTIONS-*'), key=lambda p: int(p.name.split('-')[1]))
        require(files, 'no persisted OPTIONS')
        text = files[-1].read_text()
        shutil.copy2(files[-1], log / 'raw' / files[-1].name)
        values = dict(re.findall(r'^\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$', text, re.M))
        for key in ('disable_auto_compactions', 'write_buffer_size',
                    'max_write_buffer_number', 'max_background_jobs', 'num_levels',
                    'format_version', 'level0_file_num_compaction_trigger',
                    'level0_slowdown_writes_trigger', 'level0_stop_writes_trigger',
                    'soft_pending_compaction_bytes_limit',
                    'hard_pending_compaction_bytes_limit',
                    'use_direct_reads', 'use_direct_io_for_flush_and_compaction',
                    'cache_index_and_filter_blocks',
                    'pin_l0_filter_and_index_blocks_in_cache',
                    'pin_top_level_index_and_filter'):
            value = str(expected[key]).lower() if isinstance(expected[key], bool) \
                    else str(expected[key])
            require(values.get(key) == value, 'OPTIONS mismatch: ' + key)
        require('SkipListFactory' in values.get('memtable_factory', ''),
                'online memtable is not skip_list')
        startup = (db / 'LOG').read_text(errors='replace')[:262144]
        (log / 'raw/db_LOG_startup.txt').write_text(startup)
        require(re.search(r'block_cache_options:\s*\n\s*capacity\s*:\s*{}\b'
                          .format(self.cache_size), startup),
                'unable to verify {}-byte block cache in DB LOG'.format(self.cache_size))

    def run_cell(self, phase, system, workload, duration):
        self.current = dict(phase=phase, system=system, workload=workload)
        log = self.root / phase / workload / system
        db = self.dbroot / phase / workload / system
        source = self.sources[system + '_1kb']
        self.event('STAGING', source=source['db_dir'], destination=str(db))
        before = stage_db(source['db_dir'], db)
        require(before == self.identities[system], 'source changed before staging')
        opts = options(source, db, log, workload, duration, self.cache_size)
        execution, output = self.measure(command(self.binary, opts), log,
            timeout=180 if phase == 'pilot' else duration + 600)
        require(db_identity(source['db_dir']) == before, 'source DB changed')
        save_json(log / 'source_identity.json', before)
        self.audit_options(db, log, opts)
        row = dict(phase=phase, system=system, workload=workload,
                   duration_sec=duration, threads=48, log_dir=str(log),
                   source_db_dir=source['db_dir'], clone_db_dir=str(db),
                   binary_sha256=BINARY_HASH, **execution)
        if execution['timed_out']:
            row['status'] = 'timeout'
        else:
            require(execution['exit_code'] == 0, 'benchmark exited unsuccessfully')
            require(not re.search(r'Optimization is disabled|Assertions are enabled',
                                  output, re.I), 'binary is not a Release build')
            row.update(parse_metrics(output, workload))
            require(not row['missing_tickers'], 'required engine statistics missing')
            require(row['operation_histograms'], 'merged operation histograms missing')
            row['final_levels'] = levels(output)
            row['status'] = 'ok' if abs(row['measured_seconds'] - duration) <= 5 \
                            else 'duration_overrun'
        save_json(log / 'validated.json', row)
        self.rows.append(row)
        save_json(self.root / 'results.json', self.rows)
        self.write_tsv(self.root / 'summary.tsv')
        self.event('CELL_FINISHED', status=row['status'], log=str(log))
        if row['status'] == 'ok':
            # Only this call's successfully validated disposable clone is
            # removed. Never recurse outside the campaign's private subtree.
            require(db.resolve().parent ==
                    (self.dbroot / phase / workload).resolve(), 'unsafe cleanup path')
            require(db != Path(source['db_dir']) and not db.is_symlink(),
                    'cleanup target is not a private clone')
            shutil.rmtree(db)
            self.event('CLONE_REMOVED', path=str(db), original_preserved=True)
        if phase == 'pilot':
            require(row['status'] == 'ok', 'pilot did not complete normally')

    def write_tsv(self, path):
        fields = ['phase', 'system', 'workload', 'status', 'duration_sec', 'threads',
                  'measured_seconds', 'process_elapsed_sec', 'operations',
                  'throughput_ops_sec', 'avg_latency_us', 'successful_gets',
                  'engine_keys_read', 'engine_keys_written', 'peak_rss_kb', 'log_dir']
        with path.open('w') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, delimiter='\t',
                                    extrasaction='ignore')
            writer.writeheader()
            writer.writerows(self.rows)

    def publish(self):
        dst = EXPERIMENTS / 'results' / self.args.run_id
        require(not dst.exists(), 'result bundle already exists')
        dst.mkdir()
        for name in ('manifest.json', 'results.json', 'summary.tsv'):
            shutil.copy2(self.root / name, dst / name)
        shutil.copytree(self.root / 'provenance', dst / 'provenance')
        # Exclude the frozen executable and disposable databases from Git.
        for row in self.rows:
            log = Path(row['log_dir'])
            evidence = dst / 'evidence' / row['phase'] / row['workload'] / row['system']
            evidence.mkdir(parents=True)
            for name in ('validated.json', 'report.rep'):
                if (log / name).exists():
                    shutil.copy2(log / name, evidence / name)
            for name in ('command.json', 'command.sh', 'execution.json', 'time.out'):
                if (log / 'raw' / name).exists():
                    shutil.copy2(log / 'raw' / name, evidence / name)
        count = sum(r['phase'] == 'full' and r['status'] == 'ok' for r in self.rows)
        (dst / 'RESULTS.md').write_text(
            '# Cached-zero YCSB alternatives with online compaction\n\n'
            f'Run `{self.args.run_id}`: {count}/{len(self.cells)} valid full cells. '
            'See results.json for explicit timeout/overrun status.\n\n'
            f'Selected systems: {", ".join(self.systems)}. '
            f'{len(self.reused)} valid full cells reused from '
            f'`{self.args.reuse_run}`; original measurements and provenance retained. '
            'Excluded systems in the manifest were not run in this campaign.\n\n'
            'Each cell started from a fresh hardlink clone of its original DB. '
            'All original identities were revalidated. Automatic compaction was '
            'enabled; these are initial online-use results, not steady state. '
            'Numeric KV adaptation, default YCSB distributions, one repetition. '
            'Compare against the new same-workload baseline, not historical '
            'clean-binary readrandom results.\n\n'
            f'Raw artifacts: `{self.root}`. Build, options, ordering, provenance '
            'and exact commands are included in this bundle.\n')
        self.event('PUBLISHED', result_dir=str(dst), valid_full=count)

    def run(self):
        try:
            self.prepare()
            self.reuse_results()
            for system in self.systems:
                self.run_cell('pilot', system, 'workloadc', 3)
            for workload in ('workloada', 'workloade'):
                self.run_cell('pilot', 'baseline', workload, 5)
            self.verify_sources()
            save_json(self.root / 'PILOT_COMPLETED.json',
                      dict(utc=utc(), cells=len(self.systems) + 2))
            self.current = None
            pending = [cell for cell in self.cells if cell not in self.reused]
            self.event('FULL_START', nominal_seconds=len(pending) * self.args.duration,
                       pending_full=len(pending))
            for system, workload in pending:
                self.run_cell('full', system, workload, self.args.duration)
            self.verify_sources()
            self.publish()
            self.current = None
            self.event('COMPLETED', message='all selected full cells attempted or reused')
            save_json(self.root / 'COMPLETED.json', dict(utc=utc(),
                full_cells=len(self.cells), reused_full=len(self.reused),
                valid_full=sum(r['phase'] == 'full' and r['status'] == 'ok'
                               for r in self.rows)))
        except BaseException as error:
            self.terminate()
            if self.root.exists():
                (self.root / 'failure.txt').write_text(traceback.format_exc())
                self.event('FAILED', error=str(error))
            raise
        finally:
            for fd in self.locks:
                os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--duration', type=int, default=300)
    parser.add_argument('--exclude-flush-only', action='store_true')
    parser.add_argument('--reuse-run', help='Reuse revalidated successful full cells from a terminal run')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    require(args.duration == 300, 'this approved campaign is fixed at 300s')
    campaign = Campaign(args)
    if args.dry_run:
        print(json.dumps(dict(full_order=campaign.cells, cells=len(campaign.cells), duration=300,
            reuse_run=args.reuse_run,
            common_options=options(campaign.sources['baseline_1kb'],
                campaign.dbroot / 'full/workloada/baseline',
                campaign.root / 'full/workloada/baseline', 'workloada', 300,
                campaign.cache_size)), indent=2))
        return
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    campaign.run()


if __name__ == '__main__':
    main()
