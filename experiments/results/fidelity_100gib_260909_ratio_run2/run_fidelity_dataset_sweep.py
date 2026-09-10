#!/usr/bin/env python3
"""Run twelve concurrent, exact-cardinality trace loads after storage qualification."""
import argparse
import concurrent.futures
import csv
import datetime
import fcntl
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import struct
import subprocess
import threading
import time
import traceback

REPO = Path(__file__).resolve().parents[3]
ARTIFACTS = REPO / 'experiments/artifacts'
HELPERS = ARTIFACTS / 'fidelity_tools_20260908'
GIB = 1024 ** 3
COMMON = dict(statistics=1, stats_interval_seconds=60, stats_per_interval=1,
              enable_index_compression=False, bloom_bits=10, disable_wal=True,
              max_background_jobs=48, subcompactions=1, threads=1, batch_size=1,
              memtablerep='vector', write_buffer_size=64 * 1024**2,
              max_write_buffer_number=2, seed=12345678, keys_per_prefix=0,
              use_direct_reads=True, use_direct_io_for_flush_and_compaction=True,
              compression_type='none', format_version=6,
              level_compaction_dynamic_level_bytes=False,
              max_bytes_for_level_base=256 * 1024**2,
              max_bytes_for_level_multiplier=10, target_file_size_base=64 * 1024**2)
F2_OPTIONS = dict(use_virtual_compaction=True, plr_error_bound=8,
                  memtable_flush_size=64, vcomp_register_batch_max=256,
                  vcomp_visible_l0_batch_mb=0, vcomp_phase1_shards=8,
                  vcomp_materialize_workers=48, vcomp_log_apply_timing=False,
                  vcomp_sort_detail_timing=False)
RUNTIME_VCOMP_ENV = dict(VCOMP_KMV_ENABLED='1', VCOMP_BG_COMMIT_BATCH_MAX='16',
                        VCOMP_BG_COMMIT_DELAY_US='100')
AUDITED_VCOMP_ENV = tuple(RUNTIME_VCOMP_ENV) + ('VCOMP_COV_PERFILE',)
MIN_NOFILE = 65536
COUNTS = ['stage1_live_descriptor_entries', 'stage2_live_sst_keys_written',
          'stage2_sst_keys_written', 'dropped_live_entries']


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def within(path, parent):
    """Path containment compatible with the server's Python 3.8."""
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(8 * 1024**2), b''):
            digest.update(chunk)
    return digest.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def command(binary, options):
    return [str(binary)] + [f'--{key}={str(value).lower() if isinstance(value, bool) else value}'
                            for key, value in options.items()]


def cases(gib):
    result = []
    for key, value in ((24, 1000), (48, 43)):
        n = gib * GIB // (key + value)
        for distribution, ratio, alpha in [('unique100', 1, 0),
                                             ('uniform50', .5, 0),
                                             ('zipf99_50', .5, .99)]:
            result.append(dict(case_id=f's{gib}gib_{key+value}B_{distribution}',
                               size_gib=gib, key_size=key, value_size=value,
                               distribution=distribution, unique_ratio=ratio,
                               zipf_alpha=alpha, num_records=n, key_domain=n,
                               unique_count=n if ratio == 1 else n // 2))
    return result


class Campaign:
    def __init__(self, args):
        self.args = args
        self.root = Path(args.artifact_root or ARTIFACTS / args.run_id).resolve()
        self.dbroot = Path(args.db_root or Path('/work/vcomp/exp') / args.run_id).resolve()
        require(within(self.dbroot, Path('/work')), 'DB and trace root must be under /work')
        require(within(self.root, ARTIFACTS.resolve()), 'artifacts must be under experiments/artifacts')
        self.bin = self.root / 'bin'
        self.mutex = threading.RLock()
        self.children = set()
        self.stopping = threading.Event()
        self.processes = {}
        self.child_env = {name: value for name, value in os.environ.items()
                          if not name.startswith('VCOMP_')}
        self.child_env.update(RUNTIME_VCOMP_ENV)
        self.runtime = dict(effective_vcomp_environment=RUNTIME_VCOMP_ENV,
                            inherited_audited_vcomp_environment={name: os.environ.get(name)
                                                               for name in AUDITED_VCOMP_ENV},
                            removed_inherited_vcomp_names=sorted(name for name in os.environ
                                                                 if name.startswith('VCOMP_')),
                            intentionally_unset=['VCOMP_COV_PERFILE'])

    def qualify_runtime(self):
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        self.runtime['rlimit_nofile_inherited'] = dict(soft=soft, hard=hard)
        if soft != resource.RLIM_INFINITY and soft < MIN_NOFILE:
            require(hard == resource.RLIM_INFINITY or hard >= MIN_NOFILE,
                    f'RLIMIT_NOFILE hard limit {hard} is below required {MIN_NOFILE}')
            resource.setrlimit(resource.RLIMIT_NOFILE, (MIN_NOFILE, hard))
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        require(soft == resource.RLIM_INFINITY or soft >= MIN_NOFILE, 'insufficient file descriptor limit')
        self.runtime['rlimit_nofile_effective'] = dict(soft=soft, hard=hard)
        self.runtime['rlimit_nofile_required_minimum'] = MIN_NOFILE
        save(self.root / ('runtime_' + self.args.phase + '.json'), self.runtime)

    def event(self, kind, detail):
        with self.mutex:
            with (self.root / 'events.jsonl').open('a') as output:
                output.write(json.dumps(dict(time=now(), event=kind, detail=detail)) + '\n')
        print(f'[{now()}] {kind}: {detail}', flush=True)

    def run_process(self, argv, directory, label):
        directory.mkdir(parents=True, exist_ok=True)
        save(directory / 'argv.json', argv)
        save(directory / 'runtime.json', self.runtime)
        require(not self.stopping.is_set(), 'campaign interrupted')
        started = time.time()
        with (directory / 'stdout_stderr.log').open('wb') as output:
            with self.mutex:
                require(not self.stopping.is_set(), 'campaign interrupted')
                process = subprocess.Popen(argv, stdout=output, stderr=subprocess.STDOUT,
                                           start_new_session=True, env=self.child_env)
                self.children.add(process)
                self.processes[label] = dict(pid=process.pid, state='running', started=now(), argv=argv)
                save(self.root / 'process_status.json', self.processes)
            try:
                code = process.wait()
            finally:
                with self.mutex:
                    self.children.discard(process)
                    self.processes[label].update(state='finished', exit_code=process.returncode,
                                                  elapsed_sec=time.time() - started, finished=now())
                    save(self.root / 'process_status.json', self.processes)
        save(directory / 'process.json', self.processes[label])
        require(code == 0, f'{label}: exit {code}; see {directory}')
        return (directory / 'stdout_stderr.log').read_text(errors='replace')

    def interrupt(self):
        self.stopping.set()
        with self.mutex:
            for process in self.children:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def prepare(self):
        continuing = self.root.exists()
        if continuing:
            require(self.args.phase == 'full', 'run output exists; choose a fresh run ID')
            require((self.root / 'pilot/COMPLETED').is_file(), 'full run requires completed pilot')
            require(not (self.root / 'full').exists(), 'full output already exists')
            manifest = json.loads((self.root / 'manifest.json').read_text())
            require(manifest['runner_sha256'] == sha(__file__), 'runner changed since pilot')
            require(manifest['db_root'] == str(self.dbroot), 'DB root changed since pilot')
            require(manifest['size_gib'] == self.args.size_gib, 'full size changed since pilot')
            require(manifest['runtime']['effective_vcomp_environment'] == RUNTIME_VCOMP_ENV,
                    'effective VCOMP environment differs from pilot')
            for name, digest in manifest['binary_sha256'].items():
                require(sha(self.bin / name) == digest, f'frozen binary changed: {name}')
            self.qualify_runtime()
            return
        require(self.args.phase != 'full', 'run pilot first, or use --phase all')
        require(not self.dbroot.exists(), 'DB root already exists; choose a fresh run ID')
        self.bin.mkdir(parents=True)
        self.qualify_runtime()
        shutil.copy2(__file__, self.root / Path(__file__).name)
        for name, source in [('f2_db_bench', REPO / 'db_bench'),
                             ('clean_db_bench', REPO.parent / 'rocksdb/db_bench'),
                             ('verify_load_trace', HELPERS / 'verify_load_trace'),
                             ('db_fidelity_check', HELPERS / 'db_fidelity_check')]:
            require(os.access(source, os.X_OK), f'missing executable: {source}')
            shutil.copy2(source, self.bin / name)
        generator_source = REPO / 'tools/generate_load_trace_fast.cc'
        shutil.copy2(generator_source, self.root / generator_source.name)
        self.run_process(['g++-11', '-O3', '-std=c++17', str(self.root / generator_source.name),
                          '-o', str(self.bin / 'generate_load_trace_fast')],
                         self.root / 'build_generator', 'build_generator')
        for source in (REPO / 'tools/db_fidelity_check.cc', REPO / 'tools/verify_load_trace.cc',
                       REPO / 'tools/db_bench_tool.cc'):
            shutil.copy2(source, self.root / source.name)
        revisions = {}
        for name, repository in [('f2', REPO), ('clean', REPO.parent / 'rocksdb')]:
            revisions[name] = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip()
            for suffix, arguments in [('status', ['status', '--short']), ('diff', ['diff', '--binary'])]:
                (self.root / f'{name}.{suffix}').write_bytes(subprocess.check_output(['git', '-C', str(repository)] + arguments))
        with (self.root / 'environment.txt').open('wb') as output:
            for argv in (['uname', '-a'], ['lscpu'], ['free', '-b'], ['df', '-B1', '/work'], ['findmnt', '/work']):
                subprocess.run(argv, stdout=output, stderr=subprocess.STDOUT, check=True)
        save(self.root / 'manifest.json', dict(run_id=self.args.run_id, created=now(),
             db_root=str(self.dbroot), artifact_root=str(self.root), size_gib=self.args.size_gib,
             pilot_size_gib=1, concurrency=12, common_options=COMMON, f2_options=F2_OPTIONS,
             source_revisions=revisions, runner_sha256=sha(__file__),
             runtime=self.runtime,
             runtime_control_audit={
                 'db/virtual_compaction/virtual_sst.cc': ['VCOMP_KMV_ENABLED'],
                 'db/db_impl/db_impl_compaction_flush.cc': ['VCOMP_BG_COMMIT_BATCH_MAX', 'VCOMP_BG_COMMIT_DELAY_US'],
                 'tools/db_bench_tool.cc': ['VCOMP_COV_PERFILE'],
                 'historical_wrapper_environment': 'Runner passes explicit flags and removes inherited VCOMP_* variables'},
             binary_sha256={p.name: sha(p) for p in self.bin.iterdir()},
             input_protocol='same verified VLOADTR1 file for each baseline/F2 pair',
             baseline='f2_db_bench baseload with virtual compaction disabled',
             endpoint='clean RocksDB reopen, wait until settled, read-only exact iterator count',
             timing_interpretation='Concurrent cardinality experiment; elapsed times are operational only',
             scheduling_limit='Concurrent scheduling can affect F2 approximate compaction outcomes',
             full_matrix=cases(self.args.size_gib)))

    def storage_probe(self, phase):
        """Require /work mount and round-trip buffered+direct I/O; retain evidence."""
        report = dict(status='running', started=now(), db_root=str(self.dbroot))
        target = self.root / phase / 'storage_probe.json'
        save(target, report)
        try:
            require(os.path.ismount('/work'), '/work must be a mounted filesystem; no root-filesystem fallback')
            array_state = Path('/sys/block/md0/md/array_state')
            if array_state.exists():
                report['md0_array_state'] = array_state.read_text().strip()
                require(report['md0_array_state'] != 'broken',
                        'md0 array is broken; repair /work storage before any probe or workload')
            self.dbroot.mkdir(parents=True, exist_ok=True)
            require(os.stat(self.dbroot).st_dev == os.stat('/work').st_dev,
                    'DB root must remain on the /work filesystem')
            size = 4 * 1024**2
            payload = bytes(range(256)) * (size // 256)
            probe = self.dbroot / f'storage_probe_{phase}_{time.time_ns()}'
            probe.mkdir()
            path = probe / 'buffered.bin'
            with path.open('xb') as output:
                require(output.write(payload) == size, 'short buffered probe write')
                output.flush()
                os.fsync(output.fileno())
            require(path.read_bytes() == payload, 'buffered probe read mismatch')
            path = probe / 'direct.bin'
            with mmap.mmap(-1, size) as buffer:
                buffer[:] = payload
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_DIRECT, 0o600)
                try:
                    require(os.write(fd, buffer) == size, 'short direct probe write')
                    os.fsync(fd)
                finally:
                    os.close(fd)
                buffer[:] = b'\0' * size
                fd = os.open(path, os.O_RDONLY | os.O_DIRECT)
                try:
                    require(os.readv(fd, [buffer]) == size, 'short direct probe read')
                finally:
                    os.close(fd)
                require(buffer[:] == payload, 'direct probe read mismatch')
            report.update(status='passed', probe_directory=str(probe), finished=now())
        except BaseException as error:
            report.update(status='failed', error=str(error), finished=now())
            raise
        finally:
            save(target, report)
        self.event('STORAGE_PASSED', phase)

    def traces(self, phase, matrix):
        trace_dir = self.dbroot / phase / 'traces'
        trace_dir.mkdir(parents=True)
        def generate(case):
            case = dict(case)
            trace = trace_dir / (case['case_id'] + '.vload')
            evidence = self.root / phase / 'traces' / case['case_id']
            args = [str(self.bin / 'generate_load_trace_fast'), str(trace), '--num', str(case['num_records']),
                    '--key-size', str(case['key_size']), '--value-size', str(case['value_size']),
                    '--key-domain', str(case['key_domain']), '--unique-count', str(case['unique_count']),
                    '--zipf-alpha', str(case['zipf_alpha']), '--seed', '12345678']
            self.run_process(args, evidence / 'generate', phase + ':' + case['case_id'] + ':generate')
            verification = evidence / 'verification.json'
            self.run_process([str(self.bin / 'verify_load_trace'), '--trace', str(trace),
                              '--output', str(verification)], evidence / 'verify',
                             phase + ':' + case['case_id'] + ':verify')
            require(json.loads(verification.read_text())['status'] == 'ok', 'invalid trace')
            with trace.open('rb') as source:
                header = struct.unpack('<8sIIQQQIIddQQQ', source.read(88))
            require(header[:3] == (b'VLOADTR1', 1, 88), 'invalid trace header')
            require(header[3:8] == (case['num_records'], case['key_domain'], case['unique_count'],
                                    case['key_size'], case['value_size']), 'trace header differs from case')
            case.update(trace_path=str(trace), trace_sha256=sha(trace), trace_verification=str(verification))
            save(evidence / 'case.json', case)
            self.event('TRACE_VERIFIED', phase + ':' + case['case_id'])
            return case
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            return list(pool.map(generate, matrix))

    @staticmethod
    def check_wait(text):
        statuses = re.findall(r'waitforcompaction\([^\n]*\): finished with status \(([^\n]*)\)', text)
        require(statuses and all(value == 'OK' for value in statuses), 'missing or failed compaction wait')

    def load_case(self, phase, case, system, barrier):
        label = phase + ':' + case['case_id'] + ':' + system
        log = self.root / phase / 'cases' / (case['case_id'] + '_' + system)
        db = self.dbroot / phase / 'db' / (case['case_id'] + '_' + system)
        row = dict(case_id=case['case_id'], system=system, size_gib=case['size_gib'],
                   key_size=case['key_size'], value_size=case['value_size'],
                   input_operations=case['num_records'], expected_unique_keys=case['unique_count'],
                   db_dir=str(db), log_dir=str(log), status='preparing')
        try:
            require(not db.exists(), 'DB exists: ' + str(db))
            log.mkdir(parents=True)
            db.parent.mkdir(parents=True, exist_ok=True)
            options = dict(COMMON, num=case['num_records'], key_size=case['key_size'],
                           value_size=case['value_size'], db=str(db), load_trace_file=case['trace_path'],
                           use_virtual_compaction=False,
                           benchmarks='baseload,flush,compact0,waitforcompaction,stats,levelstats')
            if system == 'f2load':
                options.update(F2_OPTIONS, benchmarks='fillvirtual,flush,compact0,waitforcompaction,stats,levelstats',
                               vcomp_fidelity_report_dir=str(log / 'fidelity'))
                # Global-unique materialization asserts a property of the input,
                # so it is only requested for datasets whose trace is entirely
                # distinct. Every case records what it actually ran.
                if self.args.global_unique_keys and case['unique_count'] == case['num_records']:
                    options.update(vcomp_global_unique_keys=True,
                                   vcomp_global_unique_keys_deep_first=self.args.global_unique_keys_deep_first)
            save(log / 'options.json', options)
            barrier.wait(timeout=60)
            self.event('LOAD_STARTED', label)
            text = self.run_process(command(self.bin / 'f2_db_bench', options), log / 'load', label + ':load')
            self.check_wait(text)
            benchmark = 'fillvirtual' if system == 'f2load' else 'baseload'
            require(re.search(r'^' + benchmark + r'\s*:', text, re.MULTILINE), 'load benchmark did not finish')
            if system == 'f2load':
                require(re.search(r'L0 visible window final: .*pending=0 visible=0', text), 'virtual L0 not drained')
                fidelity = json.loads((log / 'fidelity/fidelity.json').read_text())
                require(fidelity['phase'] == 'after_version_edit', 'incomplete fidelity report')
                for field in ['workers_done', 'snapshot_complete', 'materialization_ok',
                              'version_edit_applied', 'manifest_snapshot_ok']:
                    require(fidelity[field] is True, 'invalid fidelity state: ' + field)
                require(fidelity['input_operations'] == case['num_records'], 'F2 input count mismatch')
                require(fidelity['trace_declared_unique_count'] == case['unique_count'], 'F2 trace unique mismatch')
                row.update({key: fidelity[key] for key in COUNTS})
            else:
                require(re.search(r'baseload: ' + str(case['num_records']) + r' records, malformed=0', text),
                        'baseline did not ingest the complete trace')
            settle = dict(COMMON, num=case['num_records'], key_size=case['key_size'],
                          value_size=case['value_size'], db=str(db), use_existing_db=True,
                          benchmarks='waitforcompaction,stats,levelstats')
            text = self.run_process(command(self.bin / 'clean_db_bench', settle), log / 'settle', label + ':settle')
            self.check_wait(text)
            pending = re.findall(r'Estimated pending compaction bytes:\s*(\d+)', text)
            require(pending and int(pending[-1]) == 0, 'clean reopen leaves pending compaction')
            result_path = log / 'exact_cardinality.json'
            self.run_process([str(self.bin / 'db_fidelity_check'), '--db', str(db),
                              '--key-size', str(case['key_size']), '--value-size', str(case['value_size']),
                              '--key-domain', str(case['key_domain']), '--expected-unique', str(case['unique_count']),
                              '--output', str(result_path)], log / 'scan', label + ':scan')
            result = json.loads(result_path.read_text())
            require(result['status'] == 'ok', 'exact scan failed')
            require(result['estimated_pending_compaction_bytes'] == 0, 'scan endpoint has pending bytes')
            require(result['strict_increasing'] is True, 'iterator keys are not strictly increasing')
            for field in ['key_size_mismatch_count', 'value_size_mismatch_count',
                          'key_encoding_mismatch_count', 'outside_domain_count']:
                require(result[field] == 0, 'exact scan detected malformed data: ' + field)
            if system == 'baseline':
                require(result['exact_unique_keys'] == case['unique_count'] and result['fidelity_matches'] is True,
                        'baseline fidelity differs from verified trace')
            row.update({key: result[key] for key in ['exact_unique_keys', 'fidelity_matches', 'delta',
                                                     'sst_entry_sum', 'estimated_pending_compaction_bytes', 'levels']})
            row['relative_error'] = result['delta'] / case['unique_count']
            row['status'] = 'validated'
            self.event('VALIDATED', label + f" unique={result['exact_unique_keys']} delta={result['delta']}")
        except BaseException as error:
            barrier.abort()
            row.update(status='failed', error=str(error), traceback=traceback.format_exc())
            self.event('CASE_FAILED', label + ': ' + str(error))
        finally:
            save(log / 'result.json', row)
        return row

    def phase(self, phase):
        root = self.root / phase
        require(not root.exists(), 'phase output already exists: ' + str(root))
        root.mkdir()
        self.storage_probe(phase)
        size = 1 if phase == 'pilot' else self.args.size_gib
        require(shutil.disk_usage(self.dbroot).free >= size * GIB * 18,
                'insufficient /work free space (requires 18x logical input size)')
        matrix = self.traces(phase, cases(size))
        save(root / 'trace_manifest.json', matrix)
        barrier = threading.Barrier(12)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(self.load_case, phase, case, system, barrier)
                       for case in matrix for system in ('baseline', 'f2load')]
            results = [future.result() for future in futures]
        save(root / 'results.json', results)
        fields = ['case_id', 'system', 'size_gib', 'key_size', 'value_size', 'status',
                  'input_operations', 'expected_unique_keys'] + COUNTS + [
                  'exact_unique_keys', 'delta', 'relative_error', 'fidelity_matches',
                  'sst_entry_sum', 'estimated_pending_compaction_bytes', 'db_dir', 'log_dir', 'error']
        with (root / 'summary.tsv').open('w', newline='') as output:
            writer = csv.DictWriter(output, fieldnames=fields, delimiter='\t', extrasaction='ignore')
            writer.writeheader()
            writer.writerows(results)
        validated = sum(row['status'] == 'validated' for row in results)
        report = [f'# {phase}: {size} GiB fidelity sweep', '',
                  f'{validated}/12 loads and exact scans validated. F2 cardinality mismatches are measurements.',
                  '', '| Case | System | Expected unique | Exact unique | Delta | Status |',
                  '|---|---|---:|---:|---:|---|']
        report += [f"| {r['case_id']} | {r['system']} | {r['expected_unique_keys']} | {r.get('exact_unique_keys', '')} | {r.get('delta', '')} | {r['status']} |" for r in results]
        report += ['', 'Descriptor and materialization counts are physical per-file totals; iterator count is visible unique.',
                   'Physical-entry excess includes cross-level versions and cannot alone identify synthetic collisions.',
                   'All twelve loads started concurrently. Timing is operational only; F2 scheduling may affect approximation.',
                   'See summary.tsv, results.json, per-case logs, and the frozen manifest for evidence.']
        (root / 'REPORT.md').write_text('\n'.join(report) + '\n')
        require(validated == 12, f'{phase}: only {validated}/12 cases validated; all DBs retained')
        (root / 'COMPLETED').write_text(now() + '\n')
        self.event('PHASE_COMPLETED', phase)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--db-root')
    parser.add_argument('--artifact-root', help='Ignored raw output directory under experiments/artifacts')
    parser.add_argument('--phase', choices=['pilot', 'full', 'all'], default='all')
    parser.add_argument('--size-gib', type=int, default=100)
    parser.add_argument('--global-unique-keys', action='store_true',
                        help='Materialize a globally distinct key set for the 100%%-unique datasets')
    parser.add_argument('--global-unique-keys-deep-first', action='store_true',
                        help='With --global-unique-keys, materialize the deepest level first')
    args = parser.parse_args()
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', args.run_id), 'invalid run ID')
    require(args.size_gib > 0, 'size must be a positive integer GiB')
    require(args.global_unique_keys or not args.global_unique_keys_deep_first,
            '--global-unique-keys-deep-first requires --global-unique-keys')
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    campaign = Campaign(args)
    def interrupted(signum, frame):
        campaign.interrupt()
        raise KeyboardInterrupt()
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    with (ARTIFACTS / 'fidelity_dataset_sweep.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            campaign.prepare()
            for phase in (('pilot', 'full') if args.phase == 'all' else (args.phase,)):
                campaign.phase(phase)
            save(campaign.root / 'status.json', dict(status='completed', phase=args.phase, finished=now()))
        except BaseException as error:
            campaign.interrupt()
            if campaign.root.exists():
                save(campaign.root / 'status.json', dict(status='failed', phase=args.phase, error=str(error),
                                                        traceback=traceback.format_exc(), finished=now()))
            raise


if __name__ == '__main__':
    main()
