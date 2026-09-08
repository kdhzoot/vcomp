#!/usr/bin/env python3
"""Freeze and run a resumable real-input compaction accuracy campaign.

Without --execute this only prints the plan.  --background detaches a controller;
the controller owns its subprocess groups and writes atomic STATUS.json heartbeats.
Existing DBs and incomplete steps are never overwritten or rerun.
"""
import argparse
import concurrent.futures
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import traceback

sys.dont_write_bytecode = True
from run_fidelity_dataset_sweep import (ARTIFACTS, COMMON, GIB, REPO, cases,
                                        command, now, require, save, sha, within)

RUN4 = ARTIFACTS / 'fidelity_100gib_20260908_discrete_run4'
PILOT_SOURCE = ARTIFACTS / 'fidelity_discrete_20260908_pilot_candidate1'
PREPARE = ARTIFACTS / 'real_input_accuracy_20260908_prepare'
CAPTURE_MARKER = 'VCOMP_ACCURACY_CAPTURE_FAILED'
RUNTIME = dict(VCOMP_KMV_ENABLED='1', VCOMP_BG_COMMIT_BATCH_MAX='16',
               VCOMP_BG_COMMIT_DELAY_US='100', VCOMP_DISCRETE_CDF_ENABLED='1')
BASE_CONFIGS = [dict(name='legacy_512_8_e8', variant='legacy', samples=512,
                     buckets=8, plr_error=8),
                dict(name='raw_512_8_e8', variant='discrete_raw', samples=512,
                     buckets=8, plr_error=8),
                dict(name='certified_512_8_e8', variant='discrete_certified', samples=512,
                     buckets=8, plr_error=8)]
SWEEP_CONFIGS = [dict(BASE_CONFIGS[2], name='certified_%s_%s_e%s' % (s, b, e),
                      samples=s, buckets=b, plr_error=e)
                 for s, b, e in [(1024, 8, 8), (2048, 8, 8), (4096, 8, 8),
                                 (512, 4, 8), (512, 16, 8), (512, 8, 4), (512, 8, 1)]]


def read_json(path):
    return json.loads(Path(path).read_text())


def object_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def process_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, ValueError, TypeError):
        return False


def capture_manifest(path, hash_payloads=True):
    """Validate schema/paths/sizes. The replay independently validates key order."""
    path = Path(path)
    lines = path.read_text().splitlines()
    require(lines and lines[-1] == 'status\tok', 'incomplete capture: ' + str(path))
    scalars, files, grandparents = {}, [], []
    for line in lines:
        parts = line.split('\t')
        if parts[0] in ('input', 'output'):
            require(len(parts) == 9, 'invalid file row: ' + str(path))
            kind, number, level, size, physical, unique, minimum, maximum, name = parts
            require(Path(name).name == name and name not in ('.', '..'), 'unsafe capture payload name')
            values = list(map(int, (number, level, size, physical, unique, minimum, maximum)))
            require(all(v >= 0 for v in values), 'negative capture field')
            require(values[4] > 0 and values[3] >= values[4] and values[5] <= values[6],
                    'invalid capture cardinality/range')
            payload = path.parent / name
            require(payload.is_file() and not payload.is_symlink(), 'missing/aliased payload: ' + str(payload))
            require(payload.stat().st_size == values[4] * 8, 'capture payload size mismatch: ' + str(payload))
            record = dict(kind=kind, file_number=values[0], level=values[1], physical_bytes=values[2],
                          physical_entries=values[3], unique_entries=values[4], key_min=values[5],
                          key_max=values[6], path=str(payload), bytes=payload.stat().st_size)
            if hash_payloads:
                record['sha256'] = sha(payload)
            files.append(record)
        elif parts[0] == 'gp':
            require(len(parts) == 3 and 0 <= int(parts[1]) <= int(parts[2]), 'invalid grandparent')
            grandparents.append([int(parts[1]), int(parts[2])])
        else:
            require(len(parts) == 2 and parts[0] not in scalars, 'invalid/duplicate capture scalar')
            scalars[parts[0]] = parts[1]
    require(scalars.get('schema') == 'vcomp_real_input_v1', 'unsupported capture schema')
    require(scalars.get('binary_encoding') == 'uint64_le', 'unsupported capture encoding')
    for name in ('job', 'cf_id', 'start_level', 'output_level', 'target_sst_size', 'key_size',
                 'value_size', 'plr_error', 'input_files', 'output_files', 'subcompactions'):
        scalars[name] = int(scalars[name])
        require(scalars[name] >= 0, 'negative capture scalar: ' + name)
    require(scalars['cf_id'] == 0 and scalars['subcompactions'] == 1, 'unsupported capture CF/subcompactions')
    require(scalars['target_sst_size'] > 0, 'zero target SST size')
    require(path.parent.name == 'cf0_job_' + str(scalars['job']), 'capture job directory mismatch')
    for kind in ('input', 'output'):
        entries = [r for r in files if r['kind'] == kind]
        require(entries and len(entries) == scalars[kind + '_files'], 'capture file count mismatch')
        require(len({r['file_number'] for r in entries}) == len(entries), 'duplicate capture file number')
    require(len({r['path'] for r in files}) == len(files), 'reused capture payload path')
    return dict(scalars, manifest=str(path), manifest_sha256=sha(path), files=files,
                grandparents=grandparents,
                total_input_unique_entries=sum(r['unique_entries'] for r in files if r['kind'] == 'input'),
                largest_file_unique_entries=max(r['unique_entries'] for r in files),
                captured_bytes=sum(r['bytes'] for r in files))


def log_coverage(log_paths, jobs):
    events = {}
    trivial = {}
    logs = []
    for path in log_paths:
        for line in path.open(errors='replace'):
            require(CAPTURE_MARKER not in line, 'collector failure marker in ' + str(path))
            if 'EVENT_LOG_v1 ' not in line:
                continue
            event = json.loads(line.split('EVENT_LOG_v1 ', 1)[1])
            if event.get('event') == 'compaction_finished':
                identity = int(event['job'])
                require(identity not in events or events[identity] == event, 'duplicate conflicting compaction job ID')
                events[identity] = event
            elif event.get('event') == 'trivial_move':
                trivial[int(event['job'])] = event
        logs.append(dict(path=str(path), bytes=path.stat().st_size, sha256=sha(path)))
    eligible = {job for job, event in events.items() if event.get('num_output_files', 0) > 0}
    captured = {job['job'] for job in jobs}
    require(len(captured) == len(jobs), 'duplicate captured job ID')
    require(eligible == captured, 'capture coverage mismatch: missing=%s extra=%s' %
            (sorted(eligible - captured), sorted(captured - eligible)))
    for job in jobs:
        event = events[job['job']]
        require(event['output_level'] == job['output_level'] and
                event['num_output_files'] == job['output_files'], 'capture/event metadata mismatch')
    return dict(eligible_job_coverage_verified=True, finished_compaction_jobs=len(events),
                eligible_nonempty_compaction_jobs=len(eligible), capture_jobs=len(captured),
                trivial_move_events=len(trivial), zero_output_jobs=sorted(set(events) - eligible),
                finished_events=list(events.values()), trivial_events=list(trivial.values()), logs=logs)


def select_jobs(jobs, max_buffer, max_total):
    """Three deterministic jobs per level pair; exclusions never silently replace extremes."""
    groups = {}
    for job in jobs:
        groups.setdefault((job['start_level'], job['output_level']), []).append(job)
    selected, strata, excluded = [], [], []
    for pair, group in sorted(groups.items()):
        ordered = sorted(group, key=lambda j: (j['total_input_unique_entries'], j['job']))
        indices = sorted({0, (len(ordered) - 1) // 2, len(ordered) - 1})
        chosen = []
        for index in indices:
            job = ordered[index]
            reasons = []
            if job['largest_file_unique_entries'] > max_buffer:
                reasons.append('per_file_vector_above_max_buffer_keys')
            if max_total and job['total_input_unique_entries'] > max_total:
                reasons.append('explicit_total_input_cap')
            if reasons:
                excluded.append(dict(job=job['job'], start_level=pair[0], output_level=pair[1],
                                     reasons=reasons, total_input_unique_entries=job['total_input_unique_entries'],
                                     largest_file_unique_entries=job['largest_file_unique_entries']))
            else:
                selected.append(job)
                chosen.append(job['job'])
        strata.append(dict(start_level=pair[0], output_level=pair[1], captured_jobs=len(group),
                           selected_jobs=chosen, intended_jobs=[ordered[i]['job'] for i in indices],
                           smallest_input_entries=ordered[0]['total_input_unique_entries'],
                           largest_input_entries=ordered[-1]['total_input_unique_entries']))
    return dict(method='smallest/lower-median/largest per (case,start_level,output_level), input-entry sum then job ID',
                captured_jobs=len(jobs), selected_jobs=[j['job'] for j in selected], strata=strata,
                exclusions=excluded, jobs=selected,
                limits=dict(max_buffer_keys=max_buffer, max_total_input_keys=max_total,
                            total_input_cap_enabled=bool(max_total)))


class Campaign:
    def __init__(self, args):
        self.args = args
        self.root = ARTIFACTS / args.run_id
        self.work = Path('/work/vcomp/exp') / args.run_id
        self.bin = self.root / 'bin'
        self.lock = threading.RLock()
        self.children = {}
        self.stop = threading.Event()
        self.heartbeat_stop = threading.Event()
        self.owned = False
        self.phase_name = 'preparing'
        self.state = dict(schema='real_input_accuracy_status_v1', run_id=args.run_id,
                          status='preparing', controller_pid=os.getpid(), started=now(),
                          completed_steps=0, resumed_steps=0, completed_cases=0,
                          completed_replays=0, errors=[])
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('VCOMP_')}
        self.env.update(RUNTIME)
        self.manifest = None

    def status(self, **updates):
        with self.lock:
            self.state.update(updates)
            self.state.update(heartbeat=now(), phase=self.phase_name,
                              active_processes=list(self.children.values()))
            if self.owned:
                save(self.root / 'STATUS.json', self.state)

    def event(self, event, detail):
        with self.lock:
            with (self.root / 'events.jsonl').open('a') as out:
                out.write(json.dumps(dict(time=now(), event=event, detail=detail)) + '\n')
        print('[%s] %s: %s' % (now(), event, detail), flush=True)

    def heartbeat(self):
        while not self.heartbeat_stop.wait(10):
            self.status()

    def interrupt(self):
        self.stop.set()
        with self.lock:
            for pid in self.children:
                try:
                    os.killpg(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def fixed_config(self):
        return dict(run_id=self.args.run_id, pilot_gib=4, full_gib=100,
                    capture_parallel=self.args.capture_parallel, replay_parallel=self.args.replay_parallel,
                    replay_memory_gib=self.args.replay_memory_gib, max_buffer_keys=self.args.max_buffer_keys,
                    max_job_input_keys=self.args.max_job_input_keys, runtime=RUNTIME,
                    baseline_options=COMMON, pilot_options=self.geometry('pilot'),
                    replay_configs=BASE_CONFIGS + SWEEP_CONFIGS,
                    pilot_trace_manifest=str(PILOT_SOURCE / 'pilot/trace_manifest.json'),
                    full_trace_manifest=str(RUN4 / 'full/trace_manifest.json'))

    @staticmethod
    def geometry(phase):
        options = dict(COMMON)
        if phase == 'pilot':
            options.update(write_buffer_size=16 * 1024**2, target_file_size_base=16 * 1024**2,
                           max_bytes_for_level_base=64 * 1024**2)
        return options

    def preflight(self):
        require(Path('/work').is_mount(), '/work is not mounted')
        md = Path('/sys/block/md0/md/array_state')
        md_state = md.read_text().strip() if md.exists() else 'not_md0'
        require(md_state not in ('broken', 'inactive', 'readonly', 'read-auto'), 'md0 not writable: ' + md_state)
        require(shutil.disk_usage('/work').free >= 4 * 1024**4, 'requires at least 4 TiB free on /work')
        require(shutil.which('prlimit'), 'prlimit executable required')
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft != resource.RLIM_INFINITY and soft < 65536:
            require(hard == resource.RLIM_INFINITY or hard >= 65536, 'nofile hard limit below 65536')
            resource.setrlimit(resource.RLIMIT_NOFILE, (65536, hard))
        return dict(time=now(), md0_state=md_state, work_free_bytes=shutil.disk_usage('/work').free,
                    rlimit_nofile=resource.getrlimit(resource.RLIMIT_NOFILE),
                    removed_inherited_vcomp_names=sorted(k for k in os.environ if k.startswith('VCOMP_')),
                    effective_vcomp_environment=RUNTIME, replay_limit_kind='RLIMIT_AS address space, not RSS',
                    affinity=sorted(os.sched_getaffinity(0)), cpu_count=os.cpu_count())

    def prepare(self):
        if self.root.exists():
            require(self.args.resume, 'artifact root exists; use --resume or a fresh run ID')
            require((self.root / 'manifest.json').is_file(), 'preparation incomplete; use a fresh run ID')
            manifest = read_json(self.root / 'manifest.json')
            require(manifest['config'] == self.fixed_config(), 'resume configuration differs')
            require(manifest['runner_sha256'] == sha(__file__), 'runner changed; refusing resume')
            previous = read_json(self.root / 'STATUS.json')
            require(not process_alive(previous.get('controller_pid')) or
                    previous.get('controller_pid') == os.getpid(), 'previous controller still alive')
            require(self.work.is_dir(), 'resume work root missing')
            for name, record in manifest['binaries'].items():
                require(sha(self.bin / name) == record['sha256'], 'frozen binary changed: ' + name)
            for name, digest in manifest['source_sha256'].items():
                require(sha(self.root / 'source_snapshot' / name) == digest, 'frozen source changed: ' + name)
            self.owned = True
            self.manifest = manifest
            save(self.root / ('runtime_resume_%d.json' % int(time.time())), self.preflight())
            self.status(status='running', resumed_at=now())
            return
        require(not self.args.resume, 'nothing to resume')
        require(not self.work.exists(), 'work root exists; refusing reuse')
        require(self.args.replay_binary and self.args.legacy_replay_binary, 'both replay binaries are required')
        build = read_json(PREPARE / 'build_release/manifest.json')
        require(build['exit_code'] == 0 and build['source_unchanged'] is True, 'collector release build not validated')
        collector_fixture = read_json(PREPARE / 'fixture_validation.json')
        require(collector_fixture['status'] == 'ok' and
                collector_fixture['cases']['put']['status'] == 'valid' and
                all(collector_fixture['cases'][name]['status'] == 'rejected'
                    for name in ('delete', 'padding', 'snapshot')),
                'collector positive/negative fixture qualification failed')
        capture = Path(self.args.capture_binary).resolve()
        require(sha(capture) == build['binary_sha256']['db_bench'], 'collector binary does not match release manifest')
        for name, digest in build['source_sha256'].items():
            require(sha(REPO / name) == digest, 'collector build source changed: ' + name)
        sources = dict(capture_db_bench=capture, replay=Path(self.args.replay_binary).resolve(),
                       replay_legacy=Path(self.args.legacy_replay_binary).resolve())
        sources.update({name: RUN4 / 'bin' / name for name in
                        ('clean_db_bench', 'db_fidelity_check', 'verify_load_trace', 'generate_load_trace_fast')})
        old_manifest = read_json(RUN4 / 'manifest.json')
        for name, source in sources.items():
            require(source.is_file() and os.access(source, os.X_OK), 'missing executable: ' + str(source))
            if name in old_manifest['binary_sha256']:
                require(sha(source) == old_manifest['binary_sha256'][name], 'run4 binary changed: ' + name)
        replay_builds = [read_json(path) for path in self.args.replay_provenance
                         if 'legacy_source_sha256' in read_json(path)]
        require(len(replay_builds) == 1, 'supply the qualified replay build manifest via --replay-provenance')
        replay_build = replay_builds[0]
        require(all(code == 0 for code in replay_build['exit_codes'].values()) and
                replay_build['library_unchanged'] is True, 'replay build not validated')
        require(sha(sources['replay']) == replay_build['binary_sha256']['replay'] and
                sha(sources['replay_legacy']) == replay_build['binary_sha256']['replay_archived_legacy'],
                'replay binary differs from supplied build manifest')
        require(sha(REPO / 'tools/virtual_compaction_replay.cc') == replay_build['source_sha256'],
                'replay source changed since build')
        require(replay_build['library_sha256'] == build['binary_sha256']['librocksdb.a'],
                'collector and candidate replay use different libraries')
        qualifications = [read_json(path) for path in self.args.replay_provenance
                          if read_json(path).get('status') == 'PASS' and 'runs' in read_json(path)]
        require(len(qualifications) == 1, 'supply the replay PASS validation via --replay-provenance')
        qualification = qualifications[0]
        for name in ('replay', 'replay_legacy'):
            source = sources[name]
            require(qualification['binary_sha256'].get(str(source)) == sha(source),
                    'replay validation does not match selected binary: ' + name)
        runtime = self.preflight()
        self.root.mkdir(parents=True)
        self.owned = True
        self.work.mkdir(parents=True)
        self.bin.mkdir()
        self.status()
        binaries = {}
        for name, source in sources.items():
            destination = self.bin / name
            shutil.copy2(source, destination)
            digest = sha(destination)
            require(digest == sha(source), 'binary changed while freezing: ' + name)
            binaries[name] = dict(source=str(source), sha256=digest, bytes=destination.stat().st_size)
        snapshot = self.root / 'source_snapshot'
        source_names = set(build['source_sha256'])
        source_names.update(str(p.relative_to(REPO)) for p in (REPO / 'db/virtual_compaction').glob('*')
                            if p.suffix in ('.h', '.cc'))
        source_names.update(['tools/virtual_compaction_replay.cc', 'tools/db_fidelity_check.cc',
                             'tools/verify_load_trace.cc', 'tools/generate_load_trace_fast.cc',
                             'Makefile', 'make_config.mk', str(Path(__file__).resolve().relative_to(REPO)),
                             'experiments/scripts/trace/run_fidelity_dataset_sweep.py'])
        source_hashes = {}
        for name in sorted(source_names):
            source = REPO / name
            require(source.is_file(), 'source missing: ' + name)
            destination = snapshot / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            source_hashes[name] = sha(destination)
        for name, digest in replay_build['legacy_source_sha256'].items():
            source = Path(replay_build['legacy_source_path']) / name
            require(sha(source) == digest, 'archived legacy source changed: ' + name)
            archived_name = 'archived_legacy/' + name
            destination = snapshot / archived_name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            source_hashes[archived_name] = sha(destination)
        provenance = self.root / 'provenance'
        provenance.mkdir()
        for name in ('build_release/manifest.json', 'fixture_validation.json'):
            source = PREPARE / name
            require(source.is_file(), 'missing qualification evidence: ' + str(source))
            shutil.copy2(source, provenance / name.replace('/', '_'))
        for index, supplied in enumerate(self.args.replay_provenance):
            source = Path(supplied).resolve()
            require(source.is_file(), 'replay provenance must be a file: ' + str(source))
            shutil.copy2(source, provenance / ('replay_%02d_%s' % (index, source.name)))
        for name, argv in [('git_status.txt', ['git', 'status', '--short']),
                           ('git_diff.patch', ['git', 'diff', '--binary']),
                           ('git_revision.txt', ['git', 'rev-parse', 'HEAD'])]:
            (provenance / name).write_bytes(subprocess.check_output(argv, cwd=str(REPO)))
        self.manifest = dict(schema='real_input_accuracy_campaign_v1', created=now(),
                             config=self.fixed_config(), artifact_root=str(self.root), work_root=str(self.work),
                             runner_sha256=sha(__file__), binaries=binaries, source_sha256=source_hashes,
                             source_provenance='release collector/current candidate plus separately frozen historical legacy replay',
                             trace_reuse='read-only symbolic links to independently verified frozen traces; no regeneration',
                             baseline='collector db_bench baseload with use_virtual_compaction=false; clean RocksDB only settle/reader',
                             interpretation='one matrix; accuracy experiment, not performance comparison; poor accuracy is not failure')
        save(self.root / 'manifest.json', self.manifest)
        save(self.root / 'runtime.json', runtime)
        if self.args.launch_evidence:
            save(self.root / 'launch_evidence.json', dict(path=self.args.launch_evidence))
        self.status(status='running')

    def process(self, argv, directory, env=None, outputs=(), mutating=False, fresh_path=None):
        """Resume completed steps after hash checks; preserve every incomplete attempt."""
        env = env or self.env
        relevant_env = {k: v for k, v in env.items() if k.startswith('VCOMP_') or k in ('PATH', 'LD_LIBRARY_PATH')}
        spec = dict(argv=list(map(str, argv)), environment=relevant_env, mutating=mutating)
        signature = object_sha(spec)
        directory = Path(directory)
        with self.lock:
            require(not self.stop.is_set(), 'campaign stopped')
        completed = directory / 'COMPLETED.json'
        if completed.exists():
            saved = read_json(completed)
            require(saved['signature'] == signature, 'completed command changed: ' + str(directory))
            for name, digest in saved['output_sha256'].items():
                require(sha(name) == digest, 'completed step output changed: ' + name)
            with self.lock:
                self.state['resumed_steps'] += 1
            return Path(saved['log'])
        attempts = sorted(directory.glob('attempt_*')) if directory.exists() else []
        if attempts:
            for attempt in attempts:
                record = read_json(attempt / 'process.json') if (attempt / 'process.json').exists() else {}
                require(not process_alive(record.get('pid')), 'unfinished child still alive: ' + str(attempt))
            require(False, 'incomplete step retained; no automatic rerun/overwrite: ' + str(directory))
        if fresh_path is not None:
            require(not Path(fresh_path).exists(), 'refusing existing DB: ' + str(fresh_path))
        attempt = directory / ('attempt_%03d' % (len(attempts) + 1))
        attempt.mkdir(parents=True)
        log = attempt / 'stdout_stderr.log'
        save(attempt / 'command.json', spec)
        started = time.time()
        record = dict(signature=signature, status='starting', started=now(), argv=spec['argv'])
        save(attempt / 'process.json', record)
        with log.open('wb') as out:
            with self.lock:
                require(not self.stop.is_set(), 'campaign stopped')
                child = subprocess.Popen(spec['argv'], cwd=str(REPO), env=env, stdout=out,
                                         stderr=subprocess.STDOUT, start_new_session=True)
                record.update(pid=child.pid, status='running')
                self.children[child.pid] = dict(record, step=str(directory.relative_to(self.root)))
                save(attempt / 'process.json', record)
            try:
                code = child.wait()
            finally:
                with self.lock:
                    self.children.pop(child.pid, None)
            record.update(status='completed' if code == 0 else 'failed', exit_code=code,
                          finished=now(), elapsed_sec=time.time() - started)
            save(attempt / 'process.json', record)
        require(code == 0, 'process exit %d: %s' % (code, directory))
        hashes = {str(path): sha(path) for path in [log] + list(map(Path, outputs))}
        saved = dict(record, output_sha256=hashes, log=str(log))
        save(completed, saved)
        with self.lock:
            self.state['completed_steps'] += 1
        return log

    def traces(self, phase):
        evidence = self.root / phase
        evidence.mkdir(exist_ok=True)
        source = PILOT_SOURCE / 'pilot/trace_manifest.json' if phase == 'pilot' else RUN4 / 'full/trace_manifest.json'
        original = read_json(source)
        expected = cases(4 if phase == 'pilot' else 100)
        require(len(original) == len(expected), 'trace case count differs')
        matrix = []
        for actual, case in zip(original, expected):
            for key, value in case.items():
                require(actual[key] == value, 'trace matrix mismatch: ' + key)
            trace = Path(actual['trace_path'])
            verification = read_json(actual['trace_verification'])
            require(verification['status'] == 'ok' and verification['unique_count_matches'] is True and
                    verification['exact_unique_keys'] == case['unique_count'] and
                    verification['records'] == case['num_records'], 'source trace not independently verified')
            require(sha(trace) == actual['trace_sha256'], 'source trace hash changed: ' + str(trace))
            with trace.open('rb') as file:
                header = struct.unpack('<8sIIQQQIIddQQQ', file.read(88))
            require(header[:3] == (b'VLOADTR1', 1, 88), 'invalid trace header')
            require(header[3:8] == (case['num_records'], case['key_domain'], case['unique_count'],
                                    case['key_size'], case['value_size']), 'trace metadata/header mismatch')
            link = self.work / phase / 'traces' / trace.name
            link.parent.mkdir(parents=True, exist_ok=True)
            if link.exists() or link.is_symlink():
                require(link.is_symlink() and link.resolve() == trace.resolve(), 'trace alias mismatch')
            else:
                link.symlink_to(trace)
            case.update(trace_path=str(link), original_trace_path=str(trace), trace_sha256=actual['trace_sha256'],
                        source_verification=actual['trace_verification'], source_verification_sha256=sha(actual['trace_verification']))
            matrix.append(case)
        destination = evidence / 'trace_manifest.json'
        if destination.exists():
            require(read_json(destination) == matrix, 'trace manifest changed on resume')
        else:
            save(destination, matrix)
            save(evidence / 'trace_source.json', dict(path=str(source), sha256=sha(source)))
        return matrix

    @staticmethod
    def validate_wait(path):
        text = Path(path).read_text(errors='replace')
        statuses = re.findall(r'waitforcompaction\([^\n]*\): finished with status \(([^\n]*)\)', text)
        require(statuses and all(s == 'OK' for s in statuses), 'missing/failed compaction wait: ' + str(path))
        require(CAPTURE_MARKER not in text, 'collector failure marker: ' + str(path))
        return text

    def catalogue(self, case, capture, db, load_log, output):
        failures = list(capture.rglob('*failed.txt'))
        require(not failures, 'collector failed files: ' + ', '.join(map(str, failures)))
        jobs = []
        for directory in sorted(capture.iterdir()):
            require(directory.is_dir() and not directory.is_symlink() and
                    re.fullmatch(r'cf0_job_[0-9]+', directory.name), 'unexpected capture entry: ' + str(directory))
            require((directory / 'manifest.tsv').is_file(), 'incomplete capture job: ' + str(directory))
            job = capture_manifest(directory / 'manifest.tsv')
            require(job['key_size'] == case['key_size'] and job['value_size'] == case['value_size'], 'capture key/value shape mismatch')
            require(all(f['key_max'] < case['key_domain'] for f in job['files']), 'captured key outside trace domain')
            jobs.append(job)
        require(jobs, 'no real compaction captures')
        # On resume after clean reopen, the collector LOG snapshot has already been certified.
        if output.exists():
            old = read_json(output)
            require(old['jobs'] == jobs, 'capture payload/manifest changed since catalogue')
            self.validate_wait(load_log)
            return old
        logs = sorted(db.glob('LOG*'))
        require(logs, 'DB LOG missing')
        coverage = log_coverage(logs + [load_log], jobs)
        result = dict(case_id=case['case_id'], jobs=jobs, coverage=coverage,
                      capture_root=str(capture), captured_bytes=sum(j['captured_bytes'] for j in jobs),
                      captured_jobs=len(jobs), created=now(),
                      scope='all nonempty real CompactionJob outputs; trivial moves have no replayable output')
        save(output, result)
        return result

    def load_case(self, phase, case):
        evidence = self.root / phase / 'cases' / case['case_id']
        evidence.mkdir(parents=True, exist_ok=True)
        db = self.work / phase / 'db' / case['case_id']
        capture = self.work / phase / 'captures' / case['case_id']
        db.parent.mkdir(parents=True, exist_ok=True)
        capture.mkdir(parents=True, exist_ok=True)
        options = dict(self.geometry(phase), num=case['num_records'], key_size=case['key_size'],
                       value_size=case['value_size'], db=str(db), load_trace_file=case['trace_path'],
                       use_virtual_compaction=False,
                       benchmarks='baseload,flush,compact0,waitforcompaction,stats,levelstats')
        env = dict(self.env, VCOMP_ACCURACY_CAPTURE_DIR=str(capture))
        save(evidence / 'options.json', options)
        self.event('CAPTURE_LOAD', phase + ':' + case['case_id'])
        load_log = self.process(command(self.bin / 'capture_db_bench', options), evidence / 'load',
                                env=env, mutating=True, fresh_path=db)
        text = self.validate_wait(load_log)
        require(re.search(r'baseload: ' + str(case['num_records']) + r' records, malformed=0', text),
                'baseline did not ingest complete trace')
        catalogue = self.catalogue(case, capture, db, load_log, evidence / 'capture_inventory.json')
        settle_options = dict(self.geometry(phase), num=case['num_records'], key_size=case['key_size'],
                              value_size=case['value_size'], db=str(db), use_existing_db=True,
                              benchmarks='waitforcompaction,stats,levelstats')
        settle_log = self.process(command(self.bin / 'clean_db_bench', settle_options), evidence / 'settle', mutating=True)
        text = self.validate_wait(settle_log)
        pending = re.findall(r'Estimated pending compaction bytes:\s*(\d+)', text)
        require(pending and int(pending[-1]) == 0, 'clean settle leaves pending compaction')
        result_path = evidence / 'exact_cardinality.json'
        argv = [self.bin / 'db_fidelity_check', '--db', db, '--key-size', case['key_size'],
                '--value-size', case['value_size'], '--key-domain', case['key_domain'],
                '--expected-unique', case['unique_count'], '--output', result_path]
        self.process(argv, evidence / 'scan', outputs=[result_path])
        result = read_json(result_path)
        require(result['status'] == 'ok' and result['strict_increasing'] is True, 'baseline strict scan failed')
        require(result['fidelity_matches'] is True and result['exact_unique_keys'] == case['unique_count'],
                'baseline unique count differs from verified trace')
        for key in ('estimated_pending_compaction_bytes', 'key_size_mismatch_count',
                    'value_size_mismatch_count', 'key_encoding_mismatch_count', 'outside_domain_count'):
            require(result[key] == 0, 'baseline shape/pending failure: ' + key)
        row = dict(case_id=case['case_id'], status='validated', db=str(db), capture=str(capture),
                   expected_unique_keys=case['unique_count'], exact_unique_keys=result['exact_unique_keys'],
                   sst_entry_sum=result['sst_entry_sum'], levels=result['levels'],
                   captured_jobs=catalogue['captured_jobs'], capture_bytes=catalogue['captured_bytes'],
                   capture_output_levels=sorted({j['output_level'] for j in catalogue['jobs']}),
                   capture_inventory=str(evidence / 'capture_inventory.json'))
        save(evidence / 'result.json', row)
        with self.lock:
            self.state['completed_cases'] += 1
        self.event('CASE_VALIDATED', phase + ':' + case['case_id'])
        return row

    def parallel(self, function, items, workers):
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(function, item) for item in items]
            results = []
            try:
                for future in concurrent.futures.as_completed(futures):
                    results.append(future.result())
            except BaseException:
                self.interrupt()
                for future in futures:
                    future.cancel()
                raise
        return results

    def replay_job(self, item):
        phase, case_id, job, config = item
        evidence = self.root / phase / 'replay' / case_id / ('job_%d' % job['job']) / config['name']
        temp = self.work / phase / 'replay_tmp' / case_id / ('job_%d' % job['job']) / config['name']
        temp.mkdir(parents=True, exist_ok=True)
        output = evidence / 'result.json'
        evidence.mkdir(parents=True, exist_ok=True)
        binary = self.bin / ('replay_legacy' if config['variant'] == 'legacy' else 'replay')
        argv = ['prlimit', '--as=' + str(self.args.replay_memory_gib * GIB), '--', binary,
                '--manifest', job['manifest'], '--output', output, '--variant', config['variant'],
                '--samples', config['samples'], '--buckets', config['buckets'], '--plr-error', config['plr_error'],
                '--max-buffer-keys', self.args.max_buffer_keys, '--work-dir', temp]
        env = dict(self.env, VCOMP_DISCRETE_CDF_ENABLED='0' if config['variant'] == 'legacy' else '1',
                   VCOMP_KMV_SAMPLES=str(config['samples']), VCOMP_KMV_RANGE_BUCKETS=str(config['buckets']))
        self.process(argv, evidence / 'run', env=env, outputs=[output])
        result = read_json(output)
        require(result['status'] == 'ok', 'replay I/O or invariant failure: ' + str(output))
        require(result['invariants']['required_pass'] is True, 'required replay invariant failed: ' + str(output))
        require(result['invariants']['input_output_union_match'] is True,
                'real captured input/output exact unions differ: ' + str(output))
        require(result['variant'] == config['variant'] and result['samples'] == config['samples'] and
                result['buckets'] == config['buckets'] and result['plr_error'] == config['plr_error'],
                'replay configuration mismatch')
        require(result['effective_samples'] == config['samples'] and result['effective_buckets'] == config['buckets'],
                'replay runtime budget differs from requested configuration')
        row = dict(case_id=case_id, job=job['job'], start_level=job['start_level'],
                   output_level=job['output_level'], config=config['name'], variant=config['variant'],
                   samples=config['samples'], buckets=config['buckets'], plr_error=config['plr_error'],
                   result_path=str(output), result_sha256=sha(output), manifest_sha256=job['manifest_sha256'],
                   total_input_unique_entries=job['total_input_unique_entries'],
                   actual_unique_entries=result['actual_unique_entries'], dedup=result['dedup'],
                   stages=result['stages'], invariants=result['invariants'],
                   replay_provenance='archived_true_legacy' if config['variant'] == 'legacy' else 'current_candidate')
        with self.lock:
            self.state['completed_replays'] += 1
        self.event('REPLAY_VALIDATED', phase + ':' + case_id + ':job%d:' % job['job'] + config['name'])
        return row

    def phase(self, phase):
        self.phase_name = phase + ':trace_verification'
        self.status()
        matrix = self.traces(phase)
        self.phase_name = phase + ':capture_and_baseline_oracle'
        self.status()
        results = self.parallel(lambda c: self.load_case(phase, c), matrix, self.args.capture_parallel)
        results.sort(key=lambda r: r['case_id'])
        save(self.root / phase / 'baseline_results.json', results)
        selections = []
        items = []
        configs = BASE_CONFIGS if phase == 'pilot' else BASE_CONFIGS + SWEEP_CONFIGS
        for row in results:
            jobs = read_json(row['capture_inventory'])['jobs']
            selection = select_jobs(jobs, self.args.max_buffer_keys, self.args.max_job_input_keys)
            selection['case_id'] = row['case_id']
            if phase == 'pilot':
                require(any(j['output_level'] >= 3 for j in jobs), 'pilot did not reach L3: ' + row['case_id'])
                require(any(j['output_level'] >= 3 for j in selection['jobs']), 'pilot selected replay lacks L3')
            require(selection['jobs'], 'all jobs excluded from replay: ' + row['case_id'])
            selections.append(selection)
            for job in selection['jobs']:
                for config in configs:
                    items.append((phase, row['case_id'], job, config))
        selection_path = self.root / phase / 'replay_selection.json'
        if selection_path.exists():
            require(read_json(selection_path) == selections, 'replay job selection changed on resume')
        else:
            save(selection_path, selections)
        self.phase_name = phase + ':offline_replay'
        self.status(planned_replays_in_phase=len(items), selected_jobs_in_phase=sum(len(s['jobs']) for s in selections))
        replay_rows = self.parallel(self.replay_job, items, self.args.replay_parallel)
        replay_rows.sort(key=lambda r: (r['case_id'], r['job'], r['config']))
        save(self.root / phase / 'replay_results.json', replay_rows)
        # Same oracle and input manifest must hold across every variant/parameter setting.
        oracles = {}
        for row in replay_rows:
            key = (row['case_id'], row['job'])
            value = (row['actual_unique_entries'], row['manifest_sha256'])
            require(key not in oracles or oracles[key] == value, 'variants disagree about exact captured oracle')
            oracles[key] = value
        self.report(phase, results, selections, replay_rows)
        save(self.root / phase / 'COMPLETED.json', dict(completed=now(), baseline_cases=len(results),
             replay_runs=len(replay_rows), eligible_capture_jobs=sum(r['captured_jobs'] for r in results),
             selected_jobs=len(oracles), excluded_jobs=sum(len(s['exclusions']) for s in selections),
             accuracy_improvement_claimed=False))
        self.event('PHASE_COMPLETED', phase)

    def report(self, phase, baselines, selections, rows):
        fields = ['case_id', 'job', 'start_level', 'output_level', 'config', 'actual_unique_entries',
                  'chosen_D', 'relative_count_error', 'stage', 'generated_U', 'normalized_ecdf_ks',
                  'count_sup_over_truth_N', 'missing_original_keys', 'invented_keys']
        with (self.root / phase / 'accuracy.tsv').open('w', newline='') as output:
            writer = csv.DictWriter(output, fields, delimiter='\t', extrasaction='ignore')
            writer.writeheader()
            for row in rows:
                for stage, metric in row['stages'].items():
                    if isinstance(metric, dict):
                        flat = dict(row)
                        flat.update(row['dedup'])
                        flat.update(metric)
                        flat['stage'] = stage
                        writer.writerow(flat)
        report = ['# Real-input compaction accuracy: ' + phase, '',
                  '%d/6 baseline strict oracles passed; %d capture jobs, %d selected jobs, %d replay configurations executed.' %
                  (len(baselines), sum(r['captured_jobs'] for r in baselines),
                   sum(len(s['jobs']) for s in selections), len(rows)), '',
                  'All eligible nonempty real CompactionJob IDs were matched to complete capture manifests before clean reopen. '
                  'Trivial moves are separately inventoried. All captured input/output key IDs and their SHA256 hashes are retained.', '',
                  'Replay uses identical captured inputs and exact input-union oracles. Baseline uses the collector binary with '
                  '`use_virtual_compaction=false`; frozen clean RocksDB performs settling and strict final iteration. '
                  'The legacy replay is a separate archived implementation; current modes use the current candidate.', '',
                  'Job selection is smallest/lower-median/largest input-entry sum within each case and level pair. '
                  'The selected sample is not full replay coverage. Exclusions and every captured job appear in the inventories. '
                  'Pilot uses three initial variants; full adds seven independent one-parameter settings on the same jobs.', '',
                  'This is one accuracy matrix, not a timing comparison or proof of improved fidelity. '
                  'Low accuracy is recorded as a result; only collection, oracle, I/O and required structural invariants gate completion.', '',
                  '| Case | Baseline U | Captured jobs | Captured output levels | Replay selected | Excluded |',
                  '|---|---:|---:|---|---:|---:|']
        for row, selection in zip(baselines, selections):
            report.append('| %s | %d | %d | %s | %d | %d |' %
                          (row['case_id'], row['exact_unique_keys'], row['captured_jobs'],
                           ','.join(map(str, row['capture_output_levels'])), len(selection['jobs']), len(selection['exclusions'])))
        report += ['', 'See `accuracy.tsv`, `replay_results.json`, `replay_selection.json`, per-case '
                   '`capture_inventory.json`, exact command files, and the frozen campaign manifest.']
        (self.root / phase / 'REPORT.md').write_text('\n'.join(report) + '\n')

    def run(self):
        self.prepare()
        thread = threading.Thread(target=self.heartbeat, daemon=True)
        thread.start()
        try:
            for phase in (('pilot', 'full') if self.args.phase == 'all' else ('pilot',)):
                self.phase(phase)
            self.phase_name = 'completed'
            self.status(status='completed', finished=now())
            (self.root / 'REPORT.md').write_text(
                '# Real-input accuracy campaign\n\nCompleted phases: %s.\n\n'
                'See %s for oracle checks, capture coverage, selected-job accuracy and exclusions. '
                'Completion establishes successful measurement; it does not claim improved fidelity.\n' %
                (self.args.phase, '[full report](full/REPORT.md)' if self.args.phase == 'all' else '[pilot report](pilot/REPORT.md)'))
        finally:
            self.heartbeat_stop.set()
            thread.join(timeout=2)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', default='real_input_accuracy_100gib_20260908_run1')
    parser.add_argument('--phase', choices=['pilot', 'all'], default='all')
    parser.add_argument('--capture-binary', default=str(REPO / 'db_bench'))
    parser.add_argument('--replay-binary')
    parser.add_argument('--legacy-replay-binary')
    parser.add_argument('--replay-provenance', action='append', default=[])
    parser.add_argument('--capture-parallel', type=int, choices=range(1, 7), default=6)
    parser.add_argument('--replay-parallel', type=int, choices=range(1, 5), default=2)
    parser.add_argument('--replay-memory-gib', type=int, default=64)
    parser.add_argument('--max-buffer-keys', type=int, default=50000000)
    parser.add_argument('--max-job-input-keys', type=int, default=0, help='0 disables total-input cap')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--background', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--launch-evidence', help=argparse.SUPPRESS)
    args = parser.parse_args()
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', args.run_id), 'invalid run ID')
    require(args.replay_memory_gib > 0 and args.max_buffer_keys > 0 and args.max_job_input_keys >= 0, 'invalid resource limit')
    return args


def main():
    args = parse_args()
    campaign = Campaign(args)
    if not args.execute:
        print(json.dumps(dict(action='plan_only_no_writes', artifact_root=str(campaign.root),
                              work_root=str(campaign.work), config=campaign.fixed_config(),
                              capture_binary=args.capture_binary, replay_binary=args.replay_binary,
                              legacy_replay_binary=args.legacy_replay_binary), indent=2, sort_keys=True))
        return
    if args.background:
        # Launcher evidence is separate, so failed preflight cannot overwrite an existing campaign.
        if not args.resume:
            require(not campaign.root.exists() and not campaign.work.exists(), 'run ID already exists')
        launch = ARTIFACTS / (args.run_id + '_launch') / ('attempt_%d_%d' % (time.time_ns(), os.getpid()))
        launch.mkdir(parents=True)
        argv = [sys.executable, str(Path(__file__).resolve())] + [a for a in sys.argv[1:] if a != '--background']
        argv += ['--launch-evidence', str(launch)]
        save(launch / 'argv.json', argv)
        with (launch / 'controller.log').open('wb') as log, open(os.devnull, 'rb') as null:
            child = subprocess.Popen(argv, cwd=str(REPO), stdout=log, stderr=subprocess.STDOUT,
                                     stdin=null, start_new_session=True)
        save(launch / 'launcher.json', dict(pid=child.pid, launched=now(), argv=argv,
                                           log=str(launch / 'controller.log'), runner_sha256=sha(__file__)))
        print(json.dumps(dict(pid=child.pid, log=str(launch / 'controller.log'),
                              status=str(campaign.root / 'STATUS.json'))))
        return
    def interrupted(signum, frame):
        campaign.interrupt()
        raise KeyboardInterrupt('signal %d' % signum)
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    with (ARTIFACTS / 'fidelity_dataset_sweep.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            campaign.run()
        except BaseException as error:
            campaign.interrupt()
            campaign.status(status='failed', finished=now(), errors=[str(error)], traceback=traceback.format_exc())
            raise


if __name__ == '__main__':
    main()
