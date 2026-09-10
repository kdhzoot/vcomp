#!/usr/bin/env python3
"""YCSB A-F at a 50 GiB cached block cache on freshly loaded baseline DBs.

Companion to run_baseline_repeat_ycsb_c.py. That script froze YCSB C at the
cached-zero reference; this one runs the full A-F set against the 50 GiB
cached configuration (the B_cache_5pct point of the Chapter 3 read matrix) so
that repeated baseline loads can be compared to one another across every
workload. Sources come from a run_baseline_coverage_repeats.py bundle.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import traceback

import run_ycsb_alternatives as common

EXPERIMENTS = common.EXPERIMENTS
REFERENCE_RUN = 'paper_alternatives_ycsb_cached0_260908_no_flush_run2'
WORKLOADS = tuple('workload' + letter for letter in 'abcdef')
# B_cache_5pct in results/paper_figure4_uniform_read_cache_5m_single.tsv.
CACHE_BYTES = 53687091200


def comparable_argv(argv):
    """Only executable location, output/DB paths and cache size may differ."""
    return [value for value in argv[1:]
            if not value.startswith(('--db=', '--report_file=', '--cache_size='))]


class Campaign(common.Campaign):
    def __init__(self, args):
        super().__init__(args)
        self.owns_run_paths = False
        self.load_root = EXPERIMENTS / 'artifacts/log_loads' / args.load_run
        status = json.loads((self.load_root / 'status.json').read_text())
        common.require(status['state'] == 'complete', 'baseline loads incomplete')
        loads = json.loads((self.load_root / 'results.json').read_text())
        repeats = [row for row in loads if row['phase'] == 'full']
        common.require(len(repeats) == status['full_repetitions'] and repeats,
                       'validated repeat count differs from status')
        self.systems = tuple('baseline_' + row['name'] for row in repeats)
        self.cells = [(system, workload)
                      for system in self.systems for workload in WORKLOADS]
        for row, system in zip(repeats, self.systems):
            source = row['loading']
            common.require(source['status'] == 'validated' and
                           source['validation'] ==
                           '10000_of_10000_found_after_readonly_reopen' and
                           source['dataset_gib'] == 1000 and
                           row['coverage']['source_unchanged'], 'unqualified source')
            self.sources[system + '_1kb'] = source
        self.reference_root = EXPERIMENTS / 'artifacts/log_runs' / REFERENCE_RUN
        reference_manifest = json.loads((self.reference_root / 'manifest.json').read_text())
        common.require(reference_manifest['binary_sha256'] == common.BINARY_HASH,
                       'reference binary mismatch')
        # workloada has no baseline cell in the reference run, so fall back to
        # any system: at 1 KB every system shares num/key/value and therefore
        # the whole comparable argument vector.
        self.reference_argv = {}
        for workload in WORKLOADS:
            directory = self.reference_root / 'full' / workload
            candidates = ['baseline'] + sorted(p.name for p in directory.iterdir())
            for name in candidates:
                path = directory / name / 'raw/command.json'
                if path.is_file():
                    self.reference_argv[workload] = json.loads(path.read_text())
                    break
            common.require(workload in self.reference_argv,
                           'no reference command for ' + workload)
        self.check_parity()

    def check_parity(self):
        for system, workload in self.cells:
            opts = common.options(self.sources[system + '_1kb'],
                self.dbroot / 'full' / workload / system,
                self.root / 'full' / workload / system, workload,
                self.args.duration, self.cache_size)
            argv = common.command(self.binary, opts)
            common.require(
                comparable_argv(argv) == comparable_argv(self.reference_argv[workload]),
                'configuration differs from reference beyond cache size: ' + workload)
            common.require('--cache_size={}'.format(CACHE_BYTES) in argv,
                           'cache size not applied: ' + workload)

    def prepare(self):
        common.require(not self.root.exists() and not self.dbroot.exists() and
                       not (EXPERIMENTS / 'results' / self.args.run_id).exists(),
                       'run ID already exists')
        self.owns_run_paths = True
        super().prepare()
        provenance = self.root / 'provenance'
        shutil.copy2(Path(__file__), provenance / Path(__file__).name)
        for name in ('manifest.json', 'results.json', 'status.json'):
            shutil.copy2(self.load_root / name, provenance / ('source_load_' + name))
        reference = provenance / 'reference_commands'
        reference.mkdir()
        for workload, argv in self.reference_argv.items():
            common.save_json(reference / (workload + '.json'), argv)
        hashes = json.loads((provenance / 'source_hashes.json').read_text())
        hashes[str(Path(__file__))] = common.sha(Path(__file__))
        common.save_json(provenance / 'source_hashes.json', hashes)
        manifest = json.loads((self.root / 'manifest.json').read_text())
        manifest.update(
            source_bundle=str(self.load_root), reference_read_run=REFERENCE_RUN,
            scope='YCSB A-F at 50 GiB cached cache on freshly loaded baselines',
            cache_configuration='cached metadata, 50 GiB block cache (B_cache_5pct)',
            cache_size_bytes=self.cache_size, workloads=list(WORKLOADS),
            exact_reference_argument_parity='all arguments except cache size',
            excluded_systems=[], pilot_seconds=[3])
        common.save_json(self.root / 'manifest.json', manifest)

    def run_cell(self, phase, system, workload, duration):
        super().run_cell(phase, system, workload, duration)
        common.require(self.rows[-1]['status'] == 'ok',
                       'cell did not complete normally: {} {}'.format(system, workload))

    def publish(self):
        super().publish()
        destination = EXPERIMENTS / 'results' / self.args.run_id
        for row in self.rows:
            log = Path(row['log_dir'])
            evidence = (destination / 'evidence' / row['phase'] /
                        row['workload'] / row['system'])
            shutil.copy2(log / 'bench.out', evidence / 'bench.log')
        (destination / 'RESULTS.md').write_text(
            '# Baseline loading repeats: YCSB A-F at 50 GiB cached cache\n\n'
            'Each freshly loaded baseline DB ran the full YCSB A-F set for '
            '300 seconds per workload after a 3-second pilot, with 48 threads, '
            'Zipfian requests, automatic compaction enabled, cached metadata '
            'and a 50 GiB block cache. Every argument except the block-cache '
            'size and the DB/report paths is identical to the frozen '
            'cached-zero reference run, so results are comparable across these '
            'repeats but not against that cached-zero campaign.\n\n'
            'Every cell used a fresh hardlink clone; original DB identities '
            'were verified unchanged and only validated disposable clones were '
            'removed. No manuscript change or commit was performed.\n')

    def run(self):
        try:
            self.prepare()
            for system in self.systems:
                self.run_cell('pilot', system, WORKLOADS[0], 3)
            self.verify_sources()
            common.save_json(self.root / 'PILOT_COMPLETED.json',
                             dict(utc=common.utc(), cells=len(self.systems)))
            self.current = None
            self.event('FULL_START', pending_full=len(self.cells))
            for system, workload in self.cells:
                self.run_cell('full', system, workload, self.args.duration)
            self.verify_sources()
            self.publish()
            self.current = None
            self.event('COMPLETED', message='all baseline-repeat YCSB cells validated')
            common.save_json(self.root / 'COMPLETED.json',
                dict(utc=common.utc(), full_cells=len(self.cells),
                     valid_full=len(self.cells), source_identities_unchanged=True))
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
    parser.add_argument('--load-run', required=True,
                        help='run_baseline_coverage_repeats.py run ID holding the sources')
    parser.add_argument('--duration', type=int, default=300)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    common.require(args.run_id.startswith('baseline_repeat_ycsb_all_'),
                   'use task-specific run ID')
    args.exclude_flush_only, args.reuse_run = True, None
    args.cache_size = CACHE_BYTES
    campaign = Campaign(args)
    if args.dry_run:
        print(json.dumps(dict(
            cells=campaign.cells, total_cells=len(campaign.cells),
            duration_sec=args.duration, cache_size_bytes=campaign.cache_size,
            reference_argument_parity='all but cache size',
            sources={s: campaign.sources[s + '_1kb']['db_dir']
                     for s in campaign.systems}), indent=2))
        return
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    campaign.run()


if __name__ == '__main__':
    main()
