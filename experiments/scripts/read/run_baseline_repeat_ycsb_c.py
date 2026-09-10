#!/usr/bin/env python3
"""Two fresh baseline-load layouts, otherwise the frozen five-minute YCSB C."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import traceback

import run_ycsb_alternatives as common

EXPERIMENTS = common.EXPERIMENTS
LOAD_RUN = 'baseline_coverage_260908_repeat1'
REFERENCE_RUN = 'paper_alternatives_ycsb_cached0_260908_no_flush_run2'
REPEATS = ('repeat_01', 'repeat_02')
PLAN = EXPERIMENTS / 'docs/BASELINE_REPEAT_YCSB_C.md'


def comparable_argv(argv):
    """Only executable location and output/DB paths may differ."""
    return [value for value in argv[1:]
            if not value.startswith(('--db=', '--report_file='))]


class Campaign(common.Campaign):
    def __init__(self, args):
        super().__init__(args)
        self.owns_run_paths = False
        self.load_root = EXPERIMENTS / 'artifacts/log_loads' / LOAD_RUN
        status = json.loads((self.load_root / 'status.json').read_text())
        common.require(status['state'] == 'complete' and
                       status['full_repetitions'] == 2, 'baseline loads incomplete')
        loads = json.loads((self.load_root / 'results.json').read_text())
        self.systems = tuple('baseline_' + repeat for repeat in REPEATS)
        self.cells = [(system, 'workloadc') for system in self.systems]
        for repeat, system in zip(REPEATS, self.systems):
            matches = [r for r in loads if r['phase'] == 'full' and r['name'] == repeat]
            common.require(len(matches) == 1, 'missing/duplicate repeat')
            row = matches[0]
            source = row['loading']
            common.require(source['status'] == 'validated' and
                           source['validation'] == '10000_of_10000_found_after_readonly_reopen'
                           and source['dataset_gib'] == 1000 and
                           row['coverage']['source_unchanged'], 'unqualified source')
            self.sources[system + '_1kb'] = source
        self.reference_root = EXPERIMENTS / 'artifacts/log_runs' / REFERENCE_RUN
        self.reference_argv = json.loads((self.reference_root /
            'full/workloadc/baseline/raw/command.json').read_text())
        reference_manifest = json.loads((self.reference_root / 'manifest.json').read_text())
        common.require(reference_manifest['binary_sha256'] == common.BINARY_HASH,
                       'reference binary mismatch')
        self.check_parity()

    def check_parity(self):
        for system, workload in self.cells:
            opts = common.options(self.sources[system + '_1kb'],
                self.dbroot / 'full' / workload / system,
                self.root / 'full' / workload / system, workload, 300)
            argv = common.command(self.binary, opts)
            common.require(comparable_argv(argv) == comparable_argv(self.reference_argv),
                           'configuration differs from original YCSB C')

    def prepare(self):
        # Reuse the qualified binary/host, source locks, identity checks,
        # hardlink staging, watchdog, cache reset, and measurement machinery.
        common.require(not self.root.exists() and not self.dbroot.exists() and
                       not (EXPERIMENTS / 'results' / self.args.run_id).exists(),
                       'run ID already exists')
        self.owns_run_paths = True
        super().prepare()
        provenance = self.root / 'provenance'
        for path in (Path(__file__), PLAN):
            shutil.copy2(path, provenance / path.name)
        for name in ('manifest.json', 'results.json', 'status.json'):
            shutil.copy2(self.load_root / name, provenance / ('source_load_' + name))
        reference = provenance / 'reference_ycsb_c'
        reference.mkdir()
        for system in ('baseline', 'f2load'):
            src = self.reference_root / 'full/workloadc' / system
            dst = reference / system
            dst.mkdir()
            for name in ('validated.json', 'bench.out'):
                shutil.copy2(src / name, dst / ('bench.log' if name == 'bench.out' else name))
            shutil.copy2(src / 'raw/command.json', dst / 'command.json')
        hashes = json.loads((provenance / 'source_hashes.json').read_text())
        hashes.update({str(p): common.sha(p) for p in (Path(__file__), PLAN)})
        common.save_json(provenance / 'source_hashes.json', hashes)
        manifest = json.loads((self.root / 'manifest.json').read_text())
        manifest.update(source_bundle=str(self.load_root),
                        reference_read_run=REFERENCE_RUN,
                        scope='YCSB C on two independently loaded baseline DBs',
                        excluded_systems=[], pilot_seconds=[3],
                        exact_reference_argument_parity=True,
                        representative_options=common.options(
                            self.sources[self.systems[0] + '_1kb'],
                            self.dbroot / 'full/workloadc' / self.systems[0],
                            self.root / 'full/workloadc' / self.systems[0],
                            'workloadc', 300))
        common.save_json(self.root / 'manifest.json', manifest)

    def audit_options(self, db, log, expected):
        super().audit_options(db, log, expected)
        # C-specific validation must happen before the inherited clone cleanup.
        output = (log / 'bench.out').read_text(errors='replace')
        if re.search(r'^workloadc\s*:', output, re.M):
            metrics = common.parse_metrics(output, 'workloadc')
            common.require(metrics['engine_keys_written'] == 0, 'unexpected C writes')
            common.require(metrics['engine_keys_read'] == metrics['operations'],
                           'Get attempts differ from aggregate C operations')

    def run_cell(self, phase, system, workload, duration):
        super().run_cell(phase, system, workload, duration)
        common.require(self.rows[-1]['status'] == 'ok',
                       'repeat C did not complete normally')

    def publish(self):
        super().publish()
        dst = EXPERIMENTS / 'results' / self.args.run_id
        # Keep parseable small evidence; .out build-output suffixes are ignored.
        for row in self.rows:
            log = Path(row['log_dir'])
            evidence = dst / 'evidence' / row['phase'] / row['workload'] / row['system']
            shutil.copy2(log / 'bench.out', evidence / 'bench.log')
        shutil.copy2(PLAN, dst / 'PLAN.md')
        (dst / 'RESULTS.md').write_text(
            '# Baseline loading repeats: YCSB C\n\n'
            'Two independently loaded baseline DBs, one 300-second C run each, '
            'after separate 3-second pilots. All full cells validated. '
            'Same frozen profiler binary, Zipfian distribution, 48 threads, '
            'cached-zero metadata, and automatic compaction as the reference.\n\n'
            'Original baseline and F2Load C logs are archived under '
            '`provenance/reference_ycsb_c/`; they are historical reference '
            'measurements, not reruns in this campaign. Exact commands, binary '
            'and source identities, small raw logs and configuration accompany '
            '`results.json`. See `PLAN.md` for interpretation limits.\n\n'
            'Every cell used a fresh hardlink clone. Original DB identities '
            'remained unchanged; only validated disposable clones were removed. '
            'No manuscript change or commit/push was performed.\n')

    def run(self):
        try:
            self.prepare()
            for system in self.systems:
                self.run_cell('pilot', system, 'workloadc', 3)
            self.verify_sources()
            common.save_json(self.root / 'PILOT_COMPLETED.json',
                             dict(utc=common.utc(), cells=2, workload='workloadc'))
            self.current = None
            self.event('FULL_START', nominal_seconds=600, pending_full=2)
            for system, workload in self.cells:
                self.run_cell('full', system, workload, 300)
            self.verify_sources()
            self.publish()
            self.current = None
            self.event('COMPLETED', message='both baseline-repeat C cells validated')
            common.save_json(self.root / 'COMPLETED.json',
                dict(utc=common.utc(), full_cells=2, valid_full=2,
                     source_identities_unchanged=True))
        except BaseException as error:
            self.terminate()
            if self.owns_run_paths and self.root.exists():
                (self.root / 'failure.txt').write_text(traceback.format_exc())
                self.event('FAILED', error=str(error))
            raise
        finally:
            for fd in self.locks:
                os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    common.require(args.run_id.startswith('baseline_repeat_ycsb_c_'), 'use task-specific run ID')
    args.duration, args.exclude_flush_only, args.reuse_run = 300, True, None
    campaign = Campaign(args)
    if args.dry_run:
        print(json.dumps(dict(cells=campaign.cells, duration_sec=300,
            pilot_seconds=3, reference_argument_parity=True,
            sources={s: campaign.sources[s + '_1kb']['db_dir'] for s in campaign.systems}), indent=2))
        return
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    campaign.run()


if __name__ == '__main__':
    main()
