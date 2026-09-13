#!/usr/bin/env python3
"""Compare an immutable sparse sorted load with its original-keyspace baseline.

Every cell receives a fresh hardlink clone; source DBs are never opened through
RocksDB. Default protocol is sparse-only YCSB A and C, matching the historical
baseline and flush-only options. Supplemental measurements are opt-in. This
wrapper never modifies shared runners.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import traceback

EXP = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(EXP / 'scripts/read'), str(EXP / 'scripts/paper'),
                str(EXP / 'lib')]
import run_ycsb_alternatives as ycsb
import run_ch3_write_fixed_ops as fixed
from ch23_common import (GIB, bench_stats, command, db_identity, levels,
                        read_metrics, read_options, require, save_json, sha,
                        stage_db)

# Process-local extension; shared campaign and parser files remain unchanged.
ycsb.SYSTEMS = ('baseline', 'fillseq_sparse')


class SparseCampaign(ycsb.Campaign):
    def __init__(self, args):
        super().__init__(args)
        self.extra = []
        domains = {self.sources[s + '_1kb']['num_keys']
                   for s in set(self.systems) | {'baseline'}}
        require(len(domains) == 1, 'baseline and sparse request keyspaces differ')
        for s in self.systems:
            source = self.sources[s + '_1kb']
            require(source['key_bytes'] == 24 and source['value_bytes'] == 1000,
                    'this comparison requires 24 B keys and 1000 B values')
        require(args.operations > 0 and args.operations % 48 == 0,
                'operations must be positive and divisible by 48')
        if args.db_root:
            self.dbroot = Path(args.db_root).resolve() / args.run_id
            require(Path('/work/vcomp/exp') in self.dbroot.parents,
                    'private stage root must be beneath /work/vcomp/exp')
        for source in self.sources.values():
            if 'db_dir' in source:
                src = Path(source['db_dir']).resolve()
                require(src != self.dbroot and src not in self.dbroot.parents
                        and self.dbroot not in src.parents,
                        'source and stage roots overlap')
        self.reference_audit = self.audit_reference_options()

    def audit_reference_options(self):
        if self.args.duration != 300:
            return dict(status='qualification', reason='short pilot duration')
        references = [('ch3_ycsb_cache50_260911_run2', 'flush_only'),
                      ('ycsb_band_run3_260910', 'baseline')]
        evidence = []
        for system, workload in self.cells:
            actual = ycsb.options(self.sources[system + '_1kb'], Path('/tmp/db'),
                                  Path('/tmp/log'), workload, self.args.duration,
                                  self.cache_size, self.request_distribution)
            actual = {k: str(v).lower() if isinstance(v, bool) else str(v)
                      for k, v in actual.items() if k not in ('db', 'report_file')}
            for run_id, ref_system in references:
                root = EXP / 'artifacts/log_runs' / run_id
                path = root / 'full' / workload / ref_system / 'raw/command.json'
                argv = json.loads(path.read_text())
                expected = dict(s[2:].split('=', 1) for s in argv[1:])
                expected.pop('db')
                expected.pop('report_file')
                require(actual == expected, 'options differ from historical ' + str(path))
                manifest = json.loads((root / 'manifest.json').read_text())
                require(manifest['binary_sha256'] == ycsb.BINARY_HASH,
                        'historical profiler binary differs')
                evidence.append(dict(system=system, workload=workload,
                                     reference_command=str(path), command_sha256=sha(path),
                                     binary_sha256=ycsb.BINARY_HASH,
                                     matched_options=actual))
        return dict(status='ok', ignored_path_options=['db', 'report_file'],
                    comparisons=evidence)

    def prepare(self):
        super().prepare()
        path = self.root / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest.update(protocol='fillseq_sparse_matched_keyspace',
                        dataset_gib=self.sources['baseline_1kb'].get('dataset_gib'),
                        request_keyspace=self.sources['baseline_1kb']['num_keys'],
                        fixed_operations=self.args.operations,
                        skip_fixed=self.args.skip_fixed,
                        skip_uniform=self.args.skip_uniform,
                        uniform_read_config=None if self.args.skip_uniform else 'B_cache_5pct',
                        uniform_read_binary_sha256=ycsb.BINARY_HASH,
                        qualification=self.args.duration != 300,
                        source_bundle_rows=self.sources)
        save_json(path, manifest)
        save_json(self.root / 'reference_option_audit.json', self.reference_audit)
        shutil.copy2(__file__, self.root / 'provenance' / Path(__file__).name)
        shutil.copy2(fixed.__file__, self.root / 'provenance' / Path(fixed.__file__).name)
        save_json(self.root / 'provenance/sparse_wrapper_hashes.json',
                  {__file__: sha(__file__), fixed.__file__: sha(fixed.__file__)})

    def extra_cell(self, system, kind, pilot=False):
        phase = 'pilot_extra' if pilot else 'full_extra'
        source = self.sources[system + '_1kb']
        log = self.root / phase / kind / system
        db = self.dbroot / phase / kind / system
        self.current = dict(phase=phase, system=system, workload=kind)
        before = stage_db(source['db_dir'], db)
        require(before == self.identities[system], 'source changed before staging')
        self.event('EXTRA_STAGED', **self.current, destination=str(db))
        if kind == 'uniform_read':
            seconds = 3 if pilot else self.args.duration
            opts = read_options(1, 1024, db, log, 'B_cache_5pct',
                                duration=seconds)
            opts.update(num=source['num_keys'], cache_size=self.cache_size)
            timeout = seconds + 600
        else:
            operations = min(self.args.operations, 48000) if pilot else self.args.operations
            opts = fixed.options(source, db, log, 'workloada', operations // 48,
                                 self.cache_size)
            timeout = 180 if pilot else self.args.fixed_timeout
        try:
            execution, output = self.measure(command(self.binary, opts), log, timeout)
            require(execution['exit_code'] == 0 and not execution['timed_out'],
                    'extra measurement failed or timed out')
            if kind == 'uniform_read':
                metrics = read_metrics(output, source['value_bytes'])
                require(abs(metrics['measured_seconds'] - seconds) <= 5,
                        'uniform read duration mismatch')
            else:
                self.audit_options(db, log, opts)
                metrics = dict(bench_stats(output, 'workloada'),
                               total=fixed.counters(output),
                               pending_bytes_end=fixed.pending_bytes(output))
                require(metrics['operations'] == operations, 'fixed operation count mismatch')
                require(metrics['pending_bytes_end'] == 0, 'compaction drain incomplete')
                require('waitforcompaction' in output and 'status (OK)' in output,
                        'successful compaction drain missing')
                metrics['operations_requested'] = operations
            row = dict(phase=phase, system=system, workload=kind, status='ok',
                       source_db_dir=source['db_dir'], clone_db_dir=str(db),
                       binary_sha256=ycsb.BINARY_HASH, log_dir=str(log),
                       final_levels=levels(output), **execution, **metrics)
            save_json(log / 'validated.json', row)
            save_json(log / 'source_identity.json', before)
            self.extra.append(row)
            save_json(self.root / 'supplemental_results.json', self.extra)
            require(db.resolve().parent == (self.dbroot / phase / kind).resolve()
                    and not db.is_symlink(), 'unsafe disposable clone cleanup')
            shutil.rmtree(db)
            self.event('EXTRA_VALIDATED', **self.current)
        finally:
            require(db_identity(source['db_dir']) == before, 'SOURCE DB CHANGED')

    def run(self):
        try:
            self.prepare()
            for system in self.systems:
                for workload, seconds in (('workloadc', 3), ('workloada', 5),
                                           ('workloade', 5)):
                    if workload[-1] in self.workloads:
                        self.run_cell('pilot', system, workload, seconds)
                if not self.args.skip_uniform:
                    self.extra_cell(system, 'uniform_read', pilot=True)
                if not self.args.skip_fixed:
                    self.extra_cell(system, 'fixed_a', pilot=True)
            self.verify_sources()
            save_json(self.root / 'PILOT_COMPLETED.json', dict(utc=ycsb.utc(), status='ok'))
            for system in self.systems:
                if not self.args.skip_uniform:
                    self.extra_cell(system, 'uniform_read')
            for system, workload in self.cells:
                self.run_cell('full', system, workload, self.args.duration)
                require(self.rows[-1]['status'] == 'ok', 'full YCSB cell invalid')
            if not self.args.skip_fixed:
                for system in reversed(self.systems):
                    self.extra_cell(system, 'fixed_a')
            self.verify_sources()
            full = [r for r in self.rows if r['phase'] == 'full']
            require(len(full) == len(self.cells) and all(r['status'] == 'ok' for r in full),
                    'incomplete YCSB matrix')
            extra_full = [r for r in self.extra if r['phase'] == 'full_extra']
            require(len(extra_full) == len(self.systems) *
                    (int(not self.args.skip_uniform) + int(not self.args.skip_fixed)),
                    'incomplete supplemental matrix')
            self.publish()
            dst = EXP / 'results' / self.args.run_id
            save_json(self.root / 'supplemental_results.json', self.extra)
            shutil.copy2(self.root / 'supplemental_results.json', dst)
            shutil.copy2(self.root / 'reference_option_audit.json', dst)
            for row in self.extra:
                log = Path(row['log_dir'])
                evidence = dst / 'evidence' / row['phase'] / row['workload'] / row['system']
                evidence.mkdir(parents=True)
                for name in ('validated.json', 'source_identity.json'):
                    if (log / name).is_file():
                        shutil.copy2(log / name, evidence / name)
                for name in ('command.json', 'execution.json', 'time.out'):
                    if (log / 'raw' / name).is_file():
                        shutil.copy2(log / 'raw' / name, evidence / name)
            fields = sorted({k for r in self.extra for k, v in r.items()
                             if not isinstance(v, (dict, list))})
            with (dst / 'supplemental_summary.tsv').open('w') as stream:
                writer = csv.DictWriter(stream, fields, delimiter='\t', extrasaction='ignore')
                writer.writeheader()
                writer.writerows(self.extra)
            (dst / 'RESULTS.md').write_text(
                '# Sparse sorted loading comparison\n\n'
                'All cells use fresh hardlink clones of immutable source DBs. '
                'Request keyspace is held fixed; distinct count is not used as '
                'the query domain. The default sparse A/C protocol matches the '
                'historical baseline and flush-only commands apart from output '
                'paths. See reference_option_audit.json and manifest.json. '
                'Supplemental measurements, when explicitly enabled, are '
                'recorded separately in supplemental_results.json.\n')
            completion = dict(utc=ycsb.utc(), status='ok', full_cells=len(full),
                              extra_full_cells=len(extra_full), sources_preserved=True)
            save_json(self.root / 'COMPLETED.json', completion)
            save_json(dst / 'COMPLETED.json', completion)
            self.current = None
            self.event('COMPLETED', full_cells=len(full),
                       extra_full_cells=len(extra_full), sources_preserved=True)
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
    parser.add_argument('--source-bundle', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--systems', default='fillseq_sparse')
    parser.add_argument('--workloads', default='ac')
    parser.add_argument('--duration', type=int, default=300)
    parser.add_argument('--operations', type=int, default=210000000)
    parser.add_argument('--fixed-timeout', type=int, default=7200)
    parser.add_argument('--cache-size', type=int, default=50 * GIB)
    parser.add_argument('--db-root', help='parent of private stages, under /work/vcomp/exp')
    parser.add_argument('--skip-fixed', action='store_true', default=True)
    parser.add_argument('--with-fixed', dest='skip_fixed', action='store_false')
    parser.add_argument('--with-uniform', dest='skip_uniform', action='store_false', default=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.set_defaults(exclude_flush_only=False, request_distribution=None, reuse_run=None)
    args = parser.parse_args()
    require(args.duration > 0 and args.fixed_timeout > 0, 'timeouts must be positive')
    campaign = SparseCampaign(args)
    if args.dry_run:
        print(json.dumps(dict(ycsb_cells=campaign.cells, duration=args.duration,
                              systems=campaign.systems, stage_root=str(campaign.dbroot),
                              uniform_read=None if args.skip_uniform else 'B_cache_5pct',
                              fixed_operations=None if args.skip_fixed else args.operations,
                              skip_fixed=args.skip_fixed,
                              source_bundle=str(campaign.source_bundle)), indent=2))
        return
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    campaign.run()


if __name__ == '__main__':
    main()
