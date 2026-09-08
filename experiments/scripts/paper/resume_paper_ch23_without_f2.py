#!/usr/bin/env python3
"""Resume the approved campaign with F2Load explicitly deferred by the author."""
import argparse
import fcntl
import json
from pathlib import Path
import shutil
import signal
import time
import traceback

from run_paper_ch23_common import Campaign
from ch23_common import (CONFIGS, EXPERIMENTS, SYSTEMS, db_identity, require,
                         save_json, sha)


class WithoutF2Campaign(Campaign):
    def read_case(self, *args, **kwargs):
        # Each continuation gets fresh disposable staging, preserving interrupted
        # reader attempts. Source DB paths in the validated rows do not change.
        original = self.dbroot
        self.dbroot = original / 'continuations' / self.args.attempt
        try:
            return super().read_case(*args, **kwargs)
        finally:
            self.dbroot = original

    def run(self):
        self.prepare()  # Rechecks the exact pilot-qualified runner/library/binaries.
        self.readroot = self.readroot / 'continuations' / self.args.attempt
        require('f2load_1kb' not in self.loads,
                'this continuation expects the unvalidated F2Load case to remain excluded')
        modes = tuple(s for s in SYSTEMS if s != 'f2load')
        for case, row in self.loads.items():
            require(row['status'] == 'validated', 'unvalidated prior load: ' + case)
            expected = json.loads(Path(row['source_identity_file']).read_text())
            require(db_identity(row['db_dir']) == expected,
                    'previously validated source changed: ' + case)
        self.event('F2LOAD_DEFERRED',
                   'Author instruction 2026-09-06: no F2Load recovery or read cases; '
                   'preserve its failed validation and original DB/logs.')
        for mode in modes:
            self.load_case(mode)
        # Retain the preregistered orders, simply removing the deferred method.
        orders = (SYSTEMS, SYSTEMS[::-1], SYSTEMS[2:] + SYSTEMS[:2],
                  (SYSTEMS[2:] + SYSTEMS[:2])[::-1])
        for config, order in zip(CONFIGS, orders):
            for system in order:
                if system != 'f2load':
                    self.read_case(system, config)
        self.load_case('flush_only', 91)
        self.load_case('baseline', 91)
        expected_loads = {s + '_1kb' for s in modes} | {'flush_only_91b', 'baseline_91b'}
        expected_reads = {c + '/' + s for c in CONFIGS for s in modes}
        require(set(self.loads) == expected_loads, 'incomplete non-F2Load loading matrix')
        require(set(self.reads) == expected_reads, 'incomplete non-F2Load reading matrix')
        save_json(self.root / 'NON_F2_COMPLETED.json', dict(
            utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            validated_loads=sorted(self.loads), validated_reads=sorted(self.reads),
            deferred=['f2load_1kb'] + [c + '/f2load' for c in CONFIGS],
            full_campaign_complete=False, paper_promoted=False))
        self.event('NON_F2_COMPLETED', '7 validated loads and 20 reads; F2Load deferred. '
                   'Full-matrix promotion remains disabled; no old F2Load rows substituted.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--attempt', required=True)
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args()
    require(args.run_id == 'paper_ch23_common_260905_approved_run3',
            'this continuation is scoped to the approved interrupted campaign')
    require(args.attempt.replace('_', '').isalnum(), 'invalid attempt name')
    root = EXPERIMENTS / 'artifacts' / 'log_loads' / args.run_id
    args.phase = 'full'
    args.pilot_root = str(root / 'pilot')
    args.resume = True
    campaign = WithoutF2Campaign(args)
    original = json.loads((campaign.root / 'manifest.json').read_text())
    runner = Path(__file__).with_name('run_paper_ch23_common.py')
    library = EXPERIMENTS / 'lib' / 'ch23_common.py'
    require(sha(runner) == original['runner_sha256'], 'original runner changed')
    require(sha(library) == original['library_sha256'], 'original library changed')
    if args.preflight:
        loads = json.loads((campaign.root / 'loads.json').read_text())
        for case, row in loads.items():
            require(case != 'f2load_1kb' and row['status'] == 'validated',
                    'unexpected prior load: ' + case)
            require(db_identity(row['db_dir']) == json.loads(Path(row['source_identity_file']).read_text()),
                    'source DB identity mismatch: ' + case)
        print(json.dumps(dict(prior_validated_loads=sorted(loads),
                              remaining_reads=20, remaining_loads=['flush_only_91b', 'baseline_91b'],
                              f2load='excluded', frozen_runner_and_library='match'), indent=2))
        return
    signal.signal(signal.SIGTERM,
                  lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    lockpath = EXPERIMENTS / 'artifacts' / 'paper_ch23_common.lock'
    with lockpath.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        evidence = root / args.attempt
        evidence.mkdir(exist_ok=False)
        shutil.copy2(__file__, evidence / Path(__file__).name)
        save_json(evidence / 'continuation.json', dict(
            author_instruction='Exclude F2Load and proceed with all remaining experiments first.',
            runner_sha256=sha(__file__), original_manifest_sha256=sha(campaign.root / 'manifest.json'),
            original_failure_sha256=sha(campaign.root / 'FAILED.txt'),
            paper_promotion='Deferred until the active scope is validated and reviewed.',
            unchanged=['binaries', 'load/read options', 'source DBs', 'pilot-qualified runner/library'],
            original_failed_validation='pending compaction remains in f2load_1kb'))
        try:
            campaign.run()
        except BaseException as exc:
            # Preserve the first failure; each continuation has its own evidence.
            (evidence / 'FAILED.txt').write_text(traceback.format_exc())
            campaign.event('FAILED_NON_F2_CONTINUATION', str(exc))
            raise


if __name__ == '__main__':
    main()
