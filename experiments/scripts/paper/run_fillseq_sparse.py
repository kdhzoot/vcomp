#!/usr/bin/env python3
"""Qualify and run exact-membership sparse sequential loading, preserving DBs."""
import argparse
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import time
import traceback

EXP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXP / 'lib'))
from ch23_common import (CLEAN, GIB, TIB, HASHES, active_benchmarks, audit_options,
    bench_stats, command, db_identity, levels, load_options, measure, require,
    save_json, sha, stage_db, ticker)

TEMPLATE = EXP / 'results/paper_ch23_common_260905_approved_run3/loads.json'
MEASURE = EXP / 'scripts/paper/run_fillseq_sparse_measurements.py'


class Run:
    def __init__(self, args):
        self.args = args
        require(re.fullmatch(r'[A-Za-z0-9_-]+', args.run_id), 'unsafe run ID')
        self.root = EXP / 'artifacts/log_loads' / args.run_id
        self.dbroot = Path('/work/vcomp/exp') / args.run_id
        self.build = args.build_dir.resolve()
        self.binary = self.build / 'db_bench'
        self.helper = self.build / 'sparse_keys'
        self.started = time.time()
        self.mode = args.mode
        self.status = self.root / (self.mode + '_status.json')

    def mark(self, stage, **kw):
        self.root.mkdir(parents=True, exist_ok=True)
        obj = dict(status='running', stage=stage, mode=self.mode, pid=os.getpid(),
                   epoch=time.time(), started_epoch=self.started)
        obj.update(kw)
        save_json(self.status, obj)
        save_json(self.root / 'status.json', obj)
        print(json.dumps(obj), flush=True)

    def helper_call(self, mode, db, keys, n, log):
        log.mkdir(parents=True, exist_ok=False)
        cmd = [str(self.helper), mode, '--db', str(db), '--keys', str(keys),
               '--domain', str(n), '--key-size', '24', '--value-size', '1000',
               '--json', str(log / 'result.json')]
        save_json(log / 'command.json', cmd)
        before = db_identity(db)
        started = time.time()
        with (log / 'stdout.log').open('w') as out:
            subprocess.run(['/usr/bin/time', '-v', '-o', str(log / 'time.out')] + cmd,
                           stdout=out, stderr=subprocess.STDOUT, check=True)
        require(db_identity(db) == before, 'helper changed source metadata/SSTs')
        result = json.loads((log / 'result.json').read_text())
        require(result['status'] == 'ok', 'key helper failed validation')
        result['elapsed_sec'] = time.time() - started
        result['keys_sha256'] = sha(keys)
        save_json(log / 'result.json', result)
        return result

    def load(self, method, db, log, n, keys=None, unique=None):
        require(not db.exists(), 'refusing to overwrite DB: ' + str(db))
        db.parent.mkdir(parents=True, exist_ok=True)
        log.parent.mkdir(parents=True, exist_ok=True)
        opts = load_options(n * 1024 / GIB, 1024, db, log,
                            'baseline' if method == 'baseline' else 'fillseq')
        opts['num'] = n
        binary = CLEAN if method == 'baseline' else self.binary
        if keys is not None:
            opts.update(fillseq_sparse_keys=str(keys), writes=unique)
        HASHES[str(binary)] = sha(binary)
        phase, output = measure(command(binary, opts), log, lambda *a: None)
        audit_options(db, log, opts)
        bench = bench_stats(output, 'fillrandom' if method == 'baseline' else 'fillseq')
        require(bench['operations'] == (n if unique is None else unique),
                'wrong load insertion count')
        pending = re.findall(r'Estimated pending compaction bytes:\s*(\d+)', output)
        require(pending and int(pending[-1]) == 0, 'load did not drain')
        require('finished with status (OK)' in output, 'missing successful drain')
        ident = db_identity(db)
        save_json(log / 'db_identity.json', ident)
        row = dict(case_id=method + '_1kb', system=method,
                   dataset_gib=n * 1024 / GIB, num_keys=n, key_domain=n,
                   key_bytes=24, value_bytes=1000, load_insertions=bench['operations'],
                   logical_input_bytes=bench['operations'] * 1024,
                   reference_input_bytes=n * 1024, benchmark=bench,
                   db_dir=str(db), status='validated',
                   loading_min=phase['elapsed_sec'] / 60, levels=levels(output),
                   final_sst_count=len(ident['ssts']),
                   final_sst_bytes=sum(v[1] for v in ident['ssts'].values()),
                   source_identity_file=str(log / 'db_identity.json'),
                   flush_sst_write_bytes=ticker(output, 'rocksdb.flush.write.bytes'),
                   compaction_sst_write_bytes=ticker(output, 'rocksdb.compact.write.bytes'),
                   compaction_read_bytes=ticker(output, 'rocksdb.compact.read.bytes'),
                   repetitions=1, load_protocol='fillseq_sparse_flush_compact0_drain'
                   if keys else 'fillrandom_flush_compact0_drain', **phase)
        if unique is not None:
            row.update(distinct_keys=unique, unique_count=unique,
                       key_uniqueness=unique / n, key_file=str(keys), keys_sha256=sha(keys))
        save_json(log / 'validated.json', row)
        return row

    def execute(self):
        require(self.binary.is_file() and self.helper.is_file(), 'build tools first')
        require(sha(CLEAN) == HASHES[str(CLEAN)], 'clean binary changed')
        require(not active_benchmarks(), 'another benchmark is active')
        require(__import__('shutil').disk_usage('/work').free >= 3 * TIB,
                'less than 3 TiB free')
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(hard, max(soft, 1048576)), hard))
        phase_root = self.root / self.mode
        resume = self.args.resume_export
        reuse = self.args.reuse_export_from
        require(not (resume and reuse), 'choose one export reuse mode')
        qualification_root = self.root
        export_root = phase_root
        if reuse:
            require(self.mode == 'full' and re.fullmatch(r'[A-Za-z0-9_-]+', reuse),
                    'export reuse requires full mode and a safe source run ID')
            require(reuse != self.args.run_id, 'reuse requires a new run ID')
            qualification_root = EXP / 'artifacts/log_loads' / reuse
            export_root = qualification_root / 'full'
        if resume:
            require(self.mode == 'full', 'export resume is full-only')
            previous = json.loads(self.status.read_text())
            require(previous['status'] == 'failed' and
                    previous['error'] == 'another storage benchmark is active',
                    'resume only supports the pre-load benchmark conflict')
            require(not (self.dbroot / 'full_sparse').exists() and
                    not (phase_root / 'sparse_load').exists(),
                    'sparse load already started; refusing resume')
        else:
            require(not phase_root.exists(), 'mode already started; use new run ID')
            phase_root.mkdir(parents=True)
        self.dbroot.mkdir(parents=True, exist_ok=True)
        if self.mode == 'full':
            pilot = json.loads((qualification_root / 'pilot_completed.json').read_text())
            require(pilot['status'] == 'ok' and pilot['binary_sha256'] == sha(self.binary),
                    'pilot not qualified for this binary')
            require(pilot['helper_sha256'] == sha(self.helper),
                    'helper changed after qualification')
            if pilot['measurement_runner_sha256'] != sha(MEASURE):
                amendment = json.loads((qualification_root / 'measurement_scope_amendment.json').read_text())
                require((resume or reuse) and amendment['previous_qualified_measurement_runner_sha256'] ==
                        pilot['measurement_runner_sha256'] and
                        amendment['amended_measurement_runner_sha256'] == sha(MEASURE) and
                        amendment['selected_systems'] == ['fillseq_sparse'] and
                        amendment['selected_workloads'] == ['workloada', 'workloadc'],
                        'measurement runner has no qualified scope amendment')
            base = json.loads(TEMPLATE.read_text())['baseline_1kb']
            source = Path(base['db_dir'])
            require(db_identity(source) == json.loads(
                Path(base['source_identity_file']).read_text()), 'baseline identity changed')
        else:
            self.mark('pilot_baseline_load')
            source = self.dbroot / 'pilot_baseline'
            base = self.load('baseline', source, phase_root / 'baseline_load', 1048576)
        n = base['num_keys']
        source_identity = db_identity(source)
        provenance = dict(
            baseline=base, source_identity=source_identity,
            sparse_binary_sha256=sha(self.binary), helper_sha256=sha(self.helper),
            controller_sha256=sha(Path(__file__)), measurement_runner_sha256=sha(MEASURE),
            key_source='readonly iterator of existing baseline, sorted distinct user keys',
            values='original db_bench generated 1000-byte values, not per-key source values')
        keys = self.dbroot / (self.mode + '_sorted_unique_keys.u64le')
        if reuse:
            keys = Path('/work/vcomp/exp') / reuse / 'full_sorted_unique_keys.u64le'
            provenance.update(reused_export_from=str(export_root),
                              qualification_root=str(qualification_root),
                              key_file=str(keys))
        if resume or reuse:
            old = json.loads((export_root / 'provenance.json').read_text())
            require(old['source_identity'] == source_identity and
                    old['sparse_binary_sha256'] == sha(self.binary) and
                    old['helper_sha256'] == sha(self.helper), 'export provenance changed')
            exported = json.loads((export_root / 'export/result.json').read_text())
            require(exported['status'] == 'ok' and exported['mode'] == 'export' and
                    exported['key_domain'] == n and
                    exported['keys_sha256'] == sha(keys), 'export validation failed')
            if resume:
                attempt = phase_root / ('resume_' + str(time.time_ns()))
                attempt.mkdir()
                save_json(attempt / 'previous_status.json', previous)
                save_json(attempt / 'provenance.json', provenance)
                save_json(attempt / 'export_validation.json', exported)
            else:
                save_json(phase_root / 'reused_export_validation.json', exported)
        if not resume:
            save_json(phase_root / 'source_identity.json', source_identity)
            save_json(phase_root / 'provenance.json', provenance)
        try:
            if not (resume or reuse):
                self.mark('extract_unique_keys', source_db=str(source))
                view = self.dbroot / (self.mode + '_export_view')
                stage_db(source, view)
                exported = self.helper_call('export', view, keys, n, phase_root / 'export')
            u = exported['unique_count']
            require(0 < u <= n and keys.stat().st_size == u * 8, 'invalid key export')
            self.mark('sparse_sequential_load', unique_count=u, key_domain=n)
            sparse = self.load('fillseq_sparse', self.dbroot / (self.mode + '_sparse'),
                               phase_root / 'sparse_load', n, keys, u)
            self.mark('verify_exact_membership', unique_count=u)
            verify = self.helper_call('verify', Path(sparse['db_dir']), keys, n,
                                      phase_root / 'verify')
            require(verify['unique_count'] == u, 'membership count mismatch')
            base = dict(base, key_domain=n, distinct_keys=u, unique_count=u,
                        load_insertions=n, key_uniqueness=u / n)
            sparse.update(extraction_sec=exported['elapsed_sec'],
                          preparation_sec=exported['elapsed_sec'] + sparse['elapsed_sec'],
                          exact_membership_verified=True)
            bundle = phase_root / 'source_bundle'
            bundle.mkdir()
            save_json(bundle / 'loads.json', dict(baseline_1kb=base, fillseq_sparse_1kb=sparse))
            self.mark('measure_workloads', source_bundle=str(bundle))
            cmd = [sys.executable, '-B', str(MEASURE), '--source-bundle', str(bundle),
                   '--run-id', self.args.run_id + '_' + self.mode + '_measure']
            if self.mode == 'pilot':
                cmd += ['--duration', '3', '--operations', '48000']
            save_json(phase_root / 'measurement_command.json', cmd)
            with (phase_root / 'measurements.log').open('w') as out:
                subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT, check=True)
            require(db_identity(source) == source_identity, 'SOURCE DB CHANGED')
            done = dict(status='ok', mode=self.mode, binary_sha256=sha(self.binary),
                        helper_sha256=sha(self.helper), sparse=sparse, baseline=base,
                        measurement_runner_sha256=sha(MEASURE),
                        membership=verify, extraction=exported,
                        elapsed_sec=time.time() - self.started,
                        measurement_run_id=self.args.run_id + '_' + self.mode + '_measure')
            save_json(self.root / (self.mode + '_completed.json'), done)
            self.mark('completed', status='completed', elapsed_sec=time.time() - self.started)
        finally:
            require(db_identity(source) == source_identity, 'SOURCE DB CHANGED')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-id', default='fillseq_sparse_260913_run1')
    p.add_argument('--build-dir', type=Path, required=True)
    p.add_argument('--mode', choices=['pilot', 'full'], required=True)
    p.add_argument('--resume-export', action='store_true',
                   help='reuse verified full export after a pre-load benchmark conflict')
    p.add_argument('--reuse-export-from', metavar='RUN_ID',
                   help='start a new full load using another run\'s qualified exact key export')
    args = p.parse_args()
    run = Run(args)
    try:
        run.execute()
    except Exception as exc:
        run.mark('failed', status='failed', error=str(exc), traceback=traceback.format_exc())
        raise


if __name__ == '__main__':
    main()
