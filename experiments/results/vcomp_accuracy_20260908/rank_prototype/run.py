#!/usr/bin/env python3
"""Build two isolated actual-function probes; never alter production sources."""
import csv
import difflib
import hashlib
import json
import pathlib
import shlex
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

source_paths = [ROOT / 'db/virtual_compaction' / name for name in
                ['virtual_sst.cc', 'virtual_sst.h', 'plr_model.cc', 'plr_model.h']]
before = {str(p): sha(p) for p in source_paths}
for p in source_paths:
    (HERE / ('original_' + p.name)).write_bytes(p.read_bytes())
config = (ROOT / 'make_config.mk').read_text()
(HERE / 'make_config.mk.snapshot').write_text(config)
flags_line = next(x for x in config.splitlines() if x.startswith('PLATFORM_CXXFLAGS='))
flags = shlex.split(flags_line.split('=', 1)[1])
original = (HERE / 'original_virtual_sst.cc').read_text()
candidate = original.replace(
    'static_cast<double>(kmv_total_entries) / 2.0}});',
    '0.0}});')
candidate = candidate.replace(
    'static_cast<long double>(kmv_total_entries) /',
    'static_cast<long double>(kmv_total_entries > 0 ? kmv_total_entries - 1 : 0) /')
if candidate == original:
    raise RuntimeError('candidate replacement did not apply')
(HERE / 'candidate_virtual_sst.cc').write_text(candidate)
(HERE / 'candidate.diff').write_text(''.join(difflib.unified_diff(
    original.splitlines(True), candidate.splitlines(True),
    fromfile='original_virtual_sst.cc', tofile='candidate_virtual_sst.cc')))
commands = []
def run(argv, name):
    commands.append(argv)
    (HERE / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    with (HERE / name).open('w') as out:
        completed = subprocess.run(argv, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
    if completed.returncode:
        raise RuntimeError(f'{name}: exit {completed.returncode}')

run(['g++-11', '--version'], 'compiler_version.txt')
for variant in ['original', 'candidate']:
    run(['g++-11', *flags, '-O2', '-DNDEBUG', '-I' + str(ROOT),
         '-I' + str(ROOT / 'include'), str(HERE / 'probe.cc'),
         str(HERE / (variant + '_virtual_sst.cc')),
         str(HERE / 'original_plr_model.cc'), '-o', str(HERE / (variant + '_probe'))],
        variant + '.build.log')
    run([str(HERE / (variant + '_probe')),
         str(HERE / (variant + '.tsv')), str(HERE / (variant + '.files.tsv'))],
        variant + '.run.log')

summary = {'scope': 'Isolated actual functions, not DB workloads or production change',
           'candidate': 'N-1 global rank span and singleton intercept zero; descriptor count unchanged',
           'complete_sketch_budget': 4096, 'range_buckets': 8,
           'datasets': 'dense 1/3/5 + singleton fallback + 10 random + 10 gapped',
           'random_seed_base': 2026090800, 'plr_errors': [0, 8], 'target_entries': [2, 7, 16],
           'caveat': 'Uses actual MaterializeKeys helper, not DB SST-writing streaming loop',
           'variants': {}}
for variant in ['original', 'candidate']:
    with (HERE / (variant + '.tsv')).open() as f:
        rows = list(csv.DictReader(f, delimiter='\t'))
    groups = {}
    for group, selected in [('all', rows), ('dense', [r for r in rows if r['dataset'].startswith('dense_')]),
                            ('random_gapped', [r for r in rows if r['dataset'].startswith(('random_', 'gapped_'))])]:
        groups[group] = {'cases': len(selected),
            'cardinality_loss_cases': sum(int(r['materialized_unique']) < int(r['truth_unique']) for r in selected),
            'sum_entry_mismatch_cases': sum(r['split_entries'] != r['merged_entries'] for r in selected),
            'total_cardinality_loss': sum(int(r['truth_unique']) - int(r['materialized_unique']) for r in selected)}
        for col in ['capacity_bad_files', 'sibling_overlap_pairs', 'range_count_bad_files',
                    'bucket_overlap_pairs', 'local_duplicate_keys', 'truth_outside_output_ranges']:
            groups[group][col + '_cases'] = sum(int(r[col]) > 0 for r in selected)
            groups[group][col + '_sum'] = sum(int(r[col]) for r in selected)
    summary['variants'][variant] = groups
after = {str(p): sha(p) for p in source_paths}
summary['production_sources_unchanged'] = before == after
summary['source_hashes_before'] = before
summary['source_hashes_after'] = after
(HERE / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
manifest = {p.name: sha(p) for p in sorted(HERE.iterdir()) if p.is_file() and p.name != 'sha256.json'}
(HERE / 'sha256.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(summary, indent=2))
