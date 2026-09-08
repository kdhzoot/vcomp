#!/usr/bin/env python3
"""Prepare/run one 4-GiB, twelve-load run3-vs-candidate fidelity matrix.

Default behavior prints a read-only plan. Use --execute only after the candidate
build and focused regression gate pass, with the approved binary SHA-256. This
runner changes no source/build files and never resumes or overwrites a DB.
It imports the existing sweep's process, trace, storage, and runtime helpers.
Run3 binaries and source provenance stay frozen; new paired 4-GiB traces use
run3's generator/verifier and leave the original 100-GiB traces untouched.
"""
import argparse
import concurrent.futures
import csv
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import traceback

sys.dont_write_bytecode = True
import run_fidelity_dataset_sweep as shared

REPO = Path(__file__).resolve().parents[3]
RUN3 = REPO / 'experiments/artifacts/fidelity_100gib_20260908_seqfix_run3'
MIB = 1024 ** 2
SIZE_GIB = 4
COMMON = dict(shared.COMMON, write_buffer_size=16*MIB,
              target_file_size_base=16*MIB, max_bytes_for_level_base=64*MIB)
F2_OPTIONS = dict(shared.F2_OPTIONS, memtable_flush_size=16)
DISCRETE_ENV = {'VCOMP_DISCRETE_CDF_ENABLED': '1'}
VARIANTS = ('run3_f2', 'candidate_f2')
require, save, sha = shared.require, shared.save, shared.sha


def active_benchmarks():
    """Inspect executable names without starting probes or touching DBs."""
    found = []
    for entry in Path('/proc').glob('[0-9]*/exe'):
        try:
            target = os.readlink(entry)
            if target.endswith(' (deleted)'):
                target = target[:-10]
            if Path(target).name.endswith('db_bench'):
                found.append(dict(pid=int(entry.parent.name), executable=target))
        except (OSError, ValueError):
            pass
    return found


def overlap_geometry(path):
    with path.open() as source:
        files = [r for r in csv.DictReader(source, delimiter='\t')
                 if r['in_version'] == '1' and int(r['keys_written']) > 0]
    counts = {}
    ranges = []
    for r in files:
        level = int(r['level'])
        counts[level] = counts.get(level, 0) + 1
        ranges.append((level, int(r['materialize_key_min']), int(r['materialize_key_max'])))
    cross, l3 = 0, 0
    for i, (la, amin, amax) in enumerate(ranges):
        for lb, bmin, bmax in ranges[i+1:]:
            if la != lb and max(amin, bmin) <= min(amax, bmax):
                cross += 1
                l3 += int(la == 3 or lb == 3)
    return dict(materialization_files_by_level=counts,
                materialization_cross_level_range_overlap_pairs=cross,
                materialization_l3_range_overlap_pairs=l3,
                range_semantics='materialization bounds from files.tsv; not an exact key-set comparison')


def plan(args):
    root = REPO / 'experiments/artifacts' / args.run_id
    dbroot = Path('/work/vcomp/exp') / args.run_id
    return dict(run_id=args.run_id, execution_requested=args.execute,
                execution_gate='Candidate build and focused regressions must pass before --execute; explicit approved SHA-256 required',
                artifact_root=str(root), db_root=str(dbroot), size_gib=SIZE_GIB,
                concurrency=12, variants=VARIANTS, common_options=COMMON,
                f2_options=F2_OPTIONS, cases=shared.cases(SIZE_GIB),
                explicit_discrete_environment=DISCRETE_ENV,
                reference_run=str(RUN3), candidate_binary=str(args.candidate_binary) if args.candidate_binary else None,
                candidate_sha256=args.candidate_sha256,
                roots_are_fresh=not root.exists() and not dbroot.exists(),
                active_benchmarks=active_benchmarks(),
                endpoint='Identical clean RocksDB settling then complete clean readonly iterator scan',
                expected_count='Each pair uses the same independently verified trace unique count; no baseline load in this pilot',
                fixture_gate='Both variants retain L3 SSTs and materialization range overlap involving L3; reference also retains extra physical versions',
                timing_interpretation='One concurrent accuracy pilot; elapsed times are operational only')


class Pilot(shared.Campaign):
    def __init__(self, args):
        super().__init__(args)
        self.owns_root = False
        self.child_env.update(DISCRETE_ENV)
        self.runtime['effective_vcomp_environment'] = dict(
            self.runtime['effective_vcomp_environment'], **DISCRETE_ENV)
        self.runtime['inherited_audited_vcomp_environment'].update(
            {name: os.environ.get(name) for name in DISCRETE_ENV})

    def prepare(self):
        initial_plan = plan(self.args)
        require(not self.root.exists() and not self.dbroot.exists(), 'choose a fresh run ID; no resume/overwrite')
        require(not active_benchmarks(), 'another db_bench is active')
        require(args_valid_candidate(self.args), 'explicit executable candidate and approved SHA-256 required')
        source_manifest = json.loads((RUN3 / 'manifest.json').read_text())
        expected = source_manifest['binary_sha256']
        candidate_hash = sha(self.args.candidate_binary)
        require(candidate_hash == self.args.candidate_sha256, 'candidate differs from approved binary SHA-256')
        require(candidate_hash != expected['f2_db_bench'], 'candidate is identical to run3 reference')
        for name, digest in expected.items():
            require(sha(RUN3 / 'bin' / name) == digest, 'run3 frozen tool changed: ' + name)
        require(os.path.ismount('/work'), '/work must be mounted; no filesystem fallback')
        require(Path('/sys/block/md0/md/array_state').read_text().strip() == 'clean', 'md0 must be clean before launch')
        require(shutil.disk_usage('/work').free >= 18*SIZE_GIB*shared.GIB, 'insufficient /work space')
        self.bin.mkdir(parents=True)
        self.owns_root = True
        self.qualify_runtime()
        shutil.copy2(__file__, self.root / Path(__file__).name)
        shutil.copy2(shared.__file__, self.root / Path(shared.__file__).name)
        for name in ('clean_db_bench', 'db_fidelity_check', 'generate_load_trace_fast', 'verify_load_trace'):
            shutil.copy2(RUN3 / 'bin' / name, self.bin / name)
        shutil.copy2(RUN3 / 'bin/f2_db_bench', self.bin / 'run3_f2_db_bench')
        shutil.copy2(self.args.candidate_binary, self.bin / 'candidate_f2_db_bench')
        require(sha(self.bin / 'candidate_f2_db_bench') == candidate_hash, 'candidate changed during freeze')
        source_root = self.root / 'source_snapshot'
        for relative in ('db/virtual_compaction/virtual_sst.h', 'db/virtual_compaction/virtual_sst.cc',
                         'db/virtual_compaction/plr_model.h', 'db/virtual_compaction/plr_model.cc',
                         'db/virtual_compaction/discrete_cdf.h', 'db/virtual_compaction/discrete_cdf.cc',
                         'db/virtual_compaction/discrete_merge.h', 'db/virtual_compaction/discrete_merge.cc',
                         'db/db_impl/db_impl.h', 'db/db_impl/db_impl_compaction_flush.cc',
                         'tools/db_bench_tool.cc', 'src.mk', 'CMakeLists.txt'):
            dest = source_root / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / relative, dest)
        reference = self.root / 'run3_provenance'
        reference.mkdir()
        for name in ('manifest.json', 'seqfix_source_hashes.json', 'f2.status', 'f2.diff'):
            shutil.copy2(RUN3 / name, reference / name)
        shutil.copytree(RUN3 / 'seqfix_source_snapshot', reference / 'seqfix_source_snapshot')
        for suffix, argv in [('revision', ['rev-parse', 'HEAD']), ('status', ['status', '--short']), ('diff', ['diff', '--binary'])]:
            (self.root / ('candidate.'+suffix)).write_bytes(subprocess.check_output(['git', '-C', str(REPO)] + argv))
        manifest = dict(initial_plan, created=shared.now(), runtime=self.runtime,
                        fresh_roots_checked_before_creation=True,
                        binary_sha256={p.name: sha(p) for p in self.bin.iterdir()},
                        runner_sha256=sha(__file__), shared_runner_sha256=sha(shared.__file__),
                        source_sha256={str(p.relative_to(source_root)): sha(p) for p in source_root.rglob('*') if p.is_file()},
                        source_capture='Candidate source captured before workloads after external build/regression gate; source/build files are never edited here',
                        input_protocol='Six newly generated 4-GiB traces; each run3/candidate pair shares one verified file',
                        repetitions=1)
        save(self.root / 'manifest.json', manifest)

    def load_pair_member(self, case, variant, barrier):
        label = 'pilot:' + case['case_id'] + ':' + variant
        log = self.root / 'pilot/cases' / (case['case_id'] + '_' + variant)
        db = self.dbroot / 'pilot/db' / (case['case_id'] + '_' + variant)
        row = dict(case_id=case['case_id'], variant=variant, status='preparing',
                   expected_unique_keys=case['unique_count'], input_operations=case['num_records'],
                   db_dir=str(db), log_dir=str(log), trace_sha256=case['trace_sha256'])
        try:
            require(not db.exists(), 'DB exists: ' + str(db))
            log.mkdir(parents=True)
            db.parent.mkdir(parents=True, exist_ok=True)
            options = dict(COMMON, **F2_OPTIONS)
            options.update(num=case['num_records'], key_size=case['key_size'], value_size=case['value_size'],
                           db=str(db), load_trace_file=case['trace_path'],
                           benchmarks='fillvirtual,flush,compact0,waitforcompaction,stats,levelstats',
                           vcomp_fidelity_report_dir=str(log / 'fidelity'))
            save(log / 'options.json', options)
            barrier.wait(timeout=60)
            self.event('LOAD_STARTED', label)
            text = self.run_process(shared.command(self.bin / (variant+'_db_bench'), options), log / 'load', label+':load')
            self.check_wait(text)
            require(re.search(r'^fillvirtual\s*:', text, re.MULTILINE), 'load did not finish')
            require(re.search(r'L0 visible window final: .*pending=0 visible=0', text), 'virtual L0 not drained')
            fidelity = json.loads((log / 'fidelity/fidelity.json').read_text())
            require(fidelity['phase'] == 'after_version_edit', 'incomplete fidelity report')
            for field in ('workers_done', 'snapshot_complete', 'materialization_ok', 'version_edit_applied', 'manifest_snapshot_ok'):
                require(fidelity[field] is True, 'invalid fidelity state: '+field)
            require(fidelity['input_operations'] == case['num_records'], 'input count mismatch')
            require(fidelity['trace_declared_unique_count'] == case['unique_count'], 'declared unique count mismatch')
            row.update({k: fidelity[k] for k in shared.COUNTS})
            row.update(overlap_geometry(log / 'fidelity/files.tsv'))
            settle = dict(COMMON, num=case['num_records'], key_size=case['key_size'], value_size=case['value_size'],
                          db=str(db), use_existing_db=True, benchmarks='waitforcompaction,stats,levelstats')
            text = self.run_process(shared.command(self.bin / 'clean_db_bench', settle), log / 'settle', label+':settle')
            self.check_wait(text)
            pending = re.findall(r'Estimated pending compaction bytes:\s*(\d+)', text)
            require(pending and int(pending[-1]) == 0, 'clean settling leaves pending bytes')
            exact_path = log / 'exact_cardinality.json'
            self.run_process([str(self.bin/'db_fidelity_check'), '--db', str(db), '--key-size', str(case['key_size']),
                              '--value-size', str(case['value_size']), '--key-domain', str(case['key_domain']),
                              '--expected-unique', str(case['unique_count']), '--output', str(exact_path)],
                             log/'scan', label+':scan')
            exact = json.loads(exact_path.read_text())
            require(exact['status'] == 'ok' and exact['strict_increasing'] is True, 'invalid iterator scan')
            require(exact['estimated_pending_compaction_bytes'] == 0, 'scan endpoint has pending bytes')
            for field in ('key_size_mismatch_count', 'value_size_mismatch_count', 'key_encoding_mismatch_count', 'outside_domain_count'):
                require(exact[field] == 0, 'malformed data: '+field)
            row.update({k: exact[k] for k in ('exact_unique_keys', 'delta', 'fidelity_matches', 'strict_increasing',
                                             'non_increasing_count', 'sst_entry_sum', 'levels', 'estimated_pending_compaction_bytes')})
            row.update(relative_error=exact['delta']/case['unique_count'],
                       descriptor_minus_materialized=row['stage1_live_descriptor_entries']-row['stage2_live_sst_keys_written'],
                       physical_entry_excess=exact['sst_entry_sum']-exact['exact_unique_keys'],
                       final_l3_sst_count=sum(r['sst_count'] for r in exact['levels'] if r['level']==3))
            row['fixture_qualified'] = (row['materialization_l3_range_overlap_pairs'] > 0 and row['final_l3_sst_count'] > 0
                                        and (variant != 'run3_f2' or row['physical_entry_excess'] > 0))
            row['status'] = 'validated'
            self.event('VALIDATED', label+f" U={row['exact_unique_keys']} delta={row['delta']} fixture={row['fixture_qualified']}")
        except BaseException as error:
            barrier.abort()
            row.update(status='failed', error=str(error), traceback=traceback.format_exc())
            self.event('CASE_FAILED', label+': '+str(error))
        finally:
            save(log/'result.json', row)
        return row

    def execute(self):
        root = self.root/'pilot'
        root.mkdir()
        self.storage_probe('pilot')
        matrix = self.traces('pilot', shared.cases(SIZE_GIB))
        save(root/'trace_manifest.json', matrix)
        barrier = threading.Barrier(12)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            jobs = [pool.submit(self.load_pair_member, c, v, barrier) for c in matrix for v in VARIANTS]
            rows = [job.result() for job in jobs]
        save(root/'results.json', rows)
        fields = ['case_id', 'variant', 'status', 'expected_unique_keys'] + shared.COUNTS + [
            'exact_unique_keys', 'delta', 'relative_error', 'descriptor_minus_materialized', 'sst_entry_sum',
            'physical_entry_excess', 'final_l3_sst_count', 'materialization_l3_range_overlap_pairs', 'fixture_qualified', 'error']
        with (root/'summary.tsv').open('w', newline='') as output:
            writer = csv.DictWriter(output, fields, delimiter='\t', extrasaction='ignore')
            writer.writeheader(); writer.writerows(rows)
        comparisons = []
        for case in matrix:
            pair = {r['variant']:r for r in rows if r['case_id']==case['case_id']}
            comparisons.append(dict(case_id=case['case_id'], expected_unique_keys=case['unique_count'],
                                    trace_sha256=case['trace_sha256'], reference=pair['run3_f2'], candidate=pair['candidate_f2']))
        save(root/'comparison.json', comparisons)
        valid = sum(r['status']=='validated' for r in rows)
        fixtures = sum(r.get('fixture_qualified', False) for r in rows)
        report = ['# 4-GiB discrete fidelity candidate pilot', '',
                  f'{valid}/12 loads and strict readonly scans validated; {fixtures}/12 L3 fixtures qualified.', '',
                  'One matrix of six verified trace pairs, with all twelve F2 loads started concurrently. '
                  'The reference is frozen seq-fixed run3; the candidate is frozen after the external build/regression gate. '
                  'No new baseline loads are included; expected N comes from each independently verified trace.', '',
                  '| Case | Variant | Expected U | D | M | U | U error | L3 range overlaps | Fixture |',
                  '|---|---|---:|---:|---:|---:|---:|---:|---|']
        for r in rows:
            report.append(f"| {r['case_id']} | {r['variant']} | {r['expected_unique_keys']} | {r.get('stage1_live_descriptor_entries','')} | {r.get('stage2_live_sst_keys_written','')} | {r.get('exact_unique_keys','')} | {r.get('relative_error','')} | {r.get('materialization_l3_range_overlap_pairs','')} | {r.get('fixture_qualified',False)} |")
        report += ['', 'D/M are initial live descriptor/SST entry totals; U is the complete clean iterator count after strict ordering passes. '
                   'L3 overlap counts use materialization bounds, with final L3 presence checked separately. '
                   'Reference physical entries must exceed U to qualify the duplicate-version fixture; a candidate may remove that excess.', '',
                   'Cardinality differences are measurements, not automatically fatal. A structurally valid scan does not establish exact key/value fidelity. '
                   'Concurrent scheduling can change approximation outcomes; timing is operational only. '
                   'The 16-MiB memtable/SST and 64-MiB L1 base qualify overlap at 4 GiB and differ from the 100-GiB campaign sizes. '
                   'Original run3 DBs, traces, binaries, and results are preserved.']
        (root/'REPORT.md').write_text('\n'.join(report)+'\n')
        manifest = json.loads((self.root/'manifest.json').read_text())
        final_hashes = {p.name:sha(p) for p in self.bin.iterdir()}
        save(root/'final_binary_sha256.json', final_hashes)
        require(final_hashes == manifest['binary_sha256'], 'frozen binary changed during pilot')
        require(valid == 12, f'only {valid}/12 cases validated; all evidence and DBs retained')
        require(fixtures == 12, f'only {fixtures}/12 L3 fixtures qualified; inspect before interpreting pilot')
        (root/'COMPLETED').write_text(shared.now()+'\n')


def args_valid_candidate(args):
    return (args.candidate_binary is not None and args.candidate_binary.is_file()
            and os.access(args.candidate_binary, os.X_OK)
            and args.candidate_sha256 is not None
            and re.fullmatch(r'[0-9a-f]{64}', args.candidate_sha256) is not None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--candidate-binary', type=Path)
    parser.add_argument('--candidate-sha256')
    parser.add_argument('--execute', action='store_true', help='Run only after the external candidate build/regression gate passed')
    args = parser.parse_args()
    require(re.fullmatch(r'fidelity_discrete_20260908_pilot[A-Za-z0-9_-]*', args.run_id), 'use a fresh fidelity_discrete_20260908_pilot* run ID')
    if args.candidate_binary:
        args.candidate_binary = args.candidate_binary.resolve()
    if not args.execute:
        print(json.dumps(plan(args), indent=2, sort_keys=True))
        return
    require(args_valid_candidate(args), '--execute requires an executable candidate and approved SHA-256')
    args.phase, args.size_gib, args.artifact_root, args.db_root = 'pilot', SIZE_GIB, None, None
    pilot = Pilot(args)
    def interrupted(signum, frame):
        pilot.interrupt()
        raise KeyboardInterrupt()
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    shared.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with (shared.ARTIFACTS/'fidelity_dataset_sweep.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            pilot.prepare()
            pilot.execute()
            save(pilot.root/'status.json', dict(status='completed', finished=shared.now()))
        except BaseException as error:
            pilot.interrupt()
            if pilot.owns_root:
                save(pilot.root/'status.json', dict(status='failed', error=str(error), traceback=traceback.format_exc(), finished=shared.now()))
            raise


if __name__ == '__main__':
    main()
