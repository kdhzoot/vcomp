#!/usr/bin/env python3
"""Run sparse YCSB A with the historical fixed operation count and drain."""
import argparse
import csv
import json
import os
from pathlib import Path
import shutil
import signal
import traceback

from run_fillseq_sparse_measurements import SparseCampaign, EXP, fixed, ycsb
from ch23_common import GIB, command, require, save_json, sha

REFERENCE = EXP / 'results/ch3_write_fixed_260911'


class FixedCampaign(SparseCampaign):
    def audit_reference_options(self):
        source = self.sources['fillseq_sparse_1kb']
        opts = fixed.options(source, Path('/tmp/db'), Path('/tmp/log'),
                             'workloada', self.args.operations // 48, self.cache_size)
        actual = dict(arg[2:].split('=', 1) for arg in command(Path('db_bench'), opts)[1:])
        for name in ('db', 'report_file'):
            actual.pop(name)
        evidence = []
        for state in ('baseline', 'fillseq'):
            path = REFERENCE / state / 'raw/command.json'
            expected = dict(arg[2:].split('=', 1) for arg in json.loads(path.read_text())[1:])
            for name in ('db', 'report_file'):
                expected.pop(name)
            require(actual == expected, 'fixed A differs from ' + state)
            binary = json.loads((path.parent / 'binary.json').read_text())
            require(binary['sha256'] == ycsb.BINARY_HASH, 'reference binary differs')
            evidence.append(dict(state=state, command=str(path), sha256=sha(path)))
        return dict(status='ok', options=actual, references=evidence)

    def run(self):
        try:
            self.cells = []  # Only the fixed-count cells below; no timed YCSB rerun.
            self.prepare()
            manifest_path = self.root / 'manifest.json'
            manifest = json.loads(manifest_path.read_text())
            manifest.update(protocol='sparse_fixed_a_then_waitforcompaction',
                            operations=210000000, duration=0, duration_sec=0,
                            full_watchdog_sec=self.args.fixed_timeout,
                            fixed_full_cells=1, fixed_pilot_operations=48000,
                            fixed_order=[['fillseq_sparse', 'fixed_a']],
                            representative_options=fixed.options(
                                self.sources['fillseq_sparse_1kb'],
                                self.dbroot / 'full_extra/fixed_a/fillseq_sparse',
                                self.root / 'full_extra/fixed_a/fillseq_sparse',
                                'workloada', self.args.operations // 48, self.cache_size))
            save_json(manifest_path, manifest)
            shutil.copy2(__file__, self.root / 'provenance' / Path(__file__).name)
            self.extra_cell('fillseq_sparse', 'fixed_a', pilot=True)
            self.verify_sources()
            save_json(self.root / 'PILOT_COMPLETED.json', dict(status='ok', operations=48000))
            self.extra_cell('fillseq_sparse', 'fixed_a')
            self.verify_sources()
            row = self.extra[-1]
            require(row['total']['number_keys_written'] == 105003686 and
                    row['total']['number_keys_read'] == 104996314,
                    'read/write counts differ from historical fixed A')
            require(row['pending_bytes_end'] == 0, 'compaction did not finish')
            dst = EXP / 'results' / self.args.run_id
            require(not dst.exists(), 'result bundle already exists')
            dst.mkdir()
            for name in ('manifest.json', 'reference_option_audit.json', 'supplemental_results.json'):
                shutil.copy2(self.root / name, dst / name)
            shutil.copytree(self.root / 'provenance', dst / 'provenance')
            for item in self.extra:
                source = Path(item['log_dir'])
                evidence = dst / 'evidence' / item['phase']
                evidence.mkdir(parents=True)
                for name in ('validated.json', 'source_identity.json', 'bench.out'):
                    shutil.copy2(source / name, evidence / name)
                shutil.copytree(source / 'raw', evidence / 'raw',
                                ignore=shutil.ignore_patterns('iostat.log', 'monitor.jsonl'))
            total = row['total']
            metrics = dict(system='fillseq_sparse', workload='A', operations=row['operations'],
                           keys_written=total['number_keys_written'],
                           measured_sec=row['measured_seconds'],
                           throughput_ops_sec=row['throughput_ops_sec'],
                           avg_latency_us=row['avg_latency_us'],
                           process_elapsed_sec=row['process_elapsed_sec'],
                           flush_write_bytes=total['flush_write_bytes'],
                           compaction_read_bytes=total['compact_read_bytes'],
                           compaction_write_bytes=total['compact_write_bytes'],
                           pending_bytes_end=row['pending_bytes_end'])
            with (dst / 'write_metrics.tsv').open('x', newline='') as stream:
                writer = csv.DictWriter(stream, list(metrics), delimiter='\t')
                writer.writeheader()
                writer.writerow(metrics)
            (dst / 'RESULTS.md').write_text(
                '# Sparse fixed-count YCSB A with compaction drain\n\n'
                'Full measurement: 210,000,000 operations, including 105,003,686 writes; '
                'same options and profiler as both historical fixed-A references. '
                'The benchmark tail is workloada,stats,waitforcompaction,stats,levelstats. '
                'It does not add an explicit memtable flush, matching the references. '
                'Compaction byte counters include the drain; final pending bytes are zero. '
                'Throughput and mean latency cover workload execution. Process time includes drain/open/close. '
                'A 48,000-operation pilot passed first. Each cell used a fresh SST-hardlink clone '
                'with separate mutable metadata; the original sparse DB remains unchanged.\n')
            completion = dict(status='ok', full_fixed_cells=1, sources_preserved=True,
                              operations=row['operations'], pending_bytes_end=0)
            save_json(dst / 'COMPLETED.json', completion)
            save_json(self.root / 'COMPLETED.json', completion)
            self.current = None
            self.event('COMPLETED', **completion, result_dir=str(dst))
        except BaseException as exc:
            self.terminate()
            if self.root.exists():
                (self.root / 'failure.txt').write_text(traceback.format_exc())
                self.event('FAILED', error=str(exc))
            raise
        finally:
            if self.identities:
                self.verify_sources()
            for fd in self.locks:
                os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--source-bundle', required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.set_defaults(systems='fillseq_sparse', workloads='a', duration=300,
                        operations=210000000, fixed_timeout=7200, cache_size=50 * GIB,
                        db_root=None, skip_fixed=False, skip_uniform=True,
                        exclude_flush_only=False, request_distribution=None, reuse_run=None)
    args = parser.parse_args()
    campaign = FixedCampaign(args)
    if args.dry_run:
        print(json.dumps(campaign.reference_audit, indent=2))
        return
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    campaign.run()


if __name__ == '__main__':
    main()
