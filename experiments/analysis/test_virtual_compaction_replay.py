#!/usr/bin/env python3
"""Deterministic file-only replay qualification; never opens/creates a DB."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess


def capture(root, name, inputs, output_chunk=7, gp=()):
    path = root / name
    path.mkdir()
    truth = sorted(set().union(*map(set, inputs)))
    outputs = [truth[i:i + output_chunk] for i in range(0, len(truth), output_chunk)]
    lines = ['schema\tvcomp_real_input_v1', 'job\t1', 'cf_id\t0', 'start_level\t0',
             'output_level\t1', 'target_sst_size\t' + str(output_chunk * 67),
             'key_size\t24', 'value_size\t43', 'plr_error\t8',
             'input_files\t' + str(len(inputs)), 'output_files\t' + str(len(outputs)),
             'binary_encoding\tuint64_le']
    for lo, hi in gp:
        lines.append('gp\t{}\t{}'.format(lo, hi))
    for kind, files, level in [('input', inputs, 0), ('output', outputs, 1)]:
        for index, keys in enumerate(files):
            keys = sorted(set(keys))
            filename = '{}_{}.u64'.format(kind, index)
            (path / filename).write_bytes(b''.join(struct.pack('<Q', key) for key in keys))
            lines.append('\t'.join(map(str, (kind, index, level, len(keys) * 67,
                                            len(keys), len(keys), keys[0], keys[-1], filename))))
    lines.append('status\tok')
    manifest = path / 'manifest.tsv'
    manifest.write_text('\n'.join(lines) + '\n')
    return manifest, len(truth)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--replay', type=Path, required=True)
    parser.add_argument('--legacy', type=Path, required=True)
    parser.add_argument('--artifact-root', type=Path, required=True)
    parser.add_argument('--fixture-root', type=Path, required=True)
    parser.add_argument('--real-capture', type=Path)
    args = parser.parse_args()
    args.artifact_root.mkdir(parents=True, exist_ok=False)
    args.fixture_root.mkdir(parents=True, exist_ok=False)
    runs = []

    def run(name, manifest, variant, extra=(), legacy=False, ok=True):
        output = args.artifact_root / (name + '.json')
        binary = args.legacy if legacy else args.replay
        argv = [str(binary), '--capture', str(manifest), '--output', str(output),
                '--variant', variant, '--samples', '512', '--buckets', '8', '--plr-error', '8',
                '--work-dir', str(args.fixture_root / 'tmp')] + list(extra)
        completed = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        (args.artifact_root / (name + '.stderr.log')).write_text(completed.stderr)
        result = json.loads(output.read_text())
        runs.append({'name': name, 'argv': argv, 'exit_code': completed.returncode,
                     'status': result['status'], 'output_sha256': hashlib.sha256(output.read_bytes()).hexdigest()})
        assert (completed.returncode == 0) == ok, (name, completed.returncode, result)
        assert (result['status'] == 'ok') == ok, (name, result)
        if ok:
            assert result['invariants']['required_pass']
        return result

    fixtures = {
        'dense5': capture(args.fixture_root, 'dense5', [[0, 1, 2, 3, 4]], 2),
        'dense3_overlap': capture(args.fixture_root, 'dense3_overlap', [[0, 1, 2], [1, 2]], 2),
        'gapped': capture(args.fixture_root, 'gapped', [list(range(0, 1000, 7)), list(range(0, 1000, 11))], 13, ((100, 200), (700, 900))),
        'hot': capture(args.fixture_root, 'hot', [list(range(64)) + list(range(100 + i, 1000, 4)) for i in range(4)], 31),
    }
    for name, (manifest, n) in fixtures.items():
        for variant in ('legacy', 'discrete_raw', 'discrete_certified'):
            measured = run(name + '_' + variant, manifest, variant)
            assert measured['actual_unique_entries'] == n
            if variant != 'legacy':
                assert measured['stages']['split']['generated_U'] == n
                assert measured['stages']['split']['missing_original_keys'] == 0
                assert measured['stages']['split']['normalized_ecdf_ks'] == 0
            streamed = run(name + '_' + variant + '_stream', manifest, variant, ['--stream-merged'])
            expected = dict(measured['stages']['merged'])
            actual = dict(streamed['stages']['merged'])
            expected.pop('materializer_source'); actual.pop('materializer_source')
            assert expected == actual, (name, variant, 'streaming helper disagreement')
        archived = run(name + '_archived', manifest, 'legacy', legacy=True)
        assert archived['implementation'] == 'archived_pre_discrete_sources'
        if name == 'dense5':
            assert archived['stages']['split']['generated_U'] == 4
            assert archived['invariants']['capacity_violations'] > 0
            assert archived['status'] == 'ok', 'known legacy fidelity defect is a measurement'

    manifest, n = fixtures['gapped']
    for samples, buckets, error in [(32, 4, 1), (1024, 16, 4), (2048, 8, 8), (4096, 8, 8)]:
        result = run('budget_{}_{}_{}'.format(samples, buckets, error), manifest, 'discrete_certified',
                     ['--samples', str(samples), '--buckets', str(buckets), '--plr-error', str(error)])
        assert result['effective_samples'] == samples and result['effective_buckets'] == buckets
    for label, extra in [('zero_samples', ['--samples', '0']), ('bad_buckets', ['--buckets', 'junk']),
                         ('negative_error', ['--plr-error', '-1']), ('buffer_limit', ['--max-buffer-keys', '2'])]:
        run(label, manifest, 'discrete_certified', extra, ok=False)

    for label, transform in [
        ('cf', lambda text: text.replace('cf_id\t0', 'cf_id\t1')),
        ('count_scalar', lambda text: text.replace('input_files\t1', 'input_files\t7')),
        ('incomplete', lambda text: text.replace('status\tok\n', '')),
        ('traversal', lambda text: text.replace('input_0.u64', '../input_0.u64')),
        ('output_level', lambda text: text.replace('output_level\t1', 'output_level\t2')),
    ]:
        bad, _ = capture(args.fixture_root, 'bad_' + label, [[0, 1, 2, 3, 4]], 2)
        bad.write_text(transform(bad.read_text()))
        run('bad_' + label, bad, 'discrete_certified', ok=False)
    bad, _ = capture(args.fixture_root, 'bad_order', [[0, 1, 2, 3, 4]], 2)
    (bad.parent / 'input_0.u64').write_bytes(struct.pack('<QQQQQ', 0, 2, 1, 3, 4))
    run('bad_order', bad, 'discrete_certified', ok=False)
    bad, _ = capture(args.fixture_root, 'bad_union', [[0, 1, 2, 3, 4]], 2)
    (bad.parent / 'input_0.u64').write_bytes(struct.pack('<QQQQQ', 0, 1, 2, 3, 5))
    bad.write_text(bad.read_text().replace('\t5\t0\t4\tinput_0.u64', '\t5\t0\t5\tinput_0.u64'))
    result = run('bad_union', bad, 'discrete_certified', ok=False)
    assert 'union mismatch' in result['error']
    if args.real_capture:
        for variant in ('legacy', 'discrete_raw', 'discrete_certified'):
            run('real_' + variant, args.real_capture, variant)
        run('real_archived_legacy', args.real_capture, 'legacy', legacy=True)
        complete = run('real_complete', args.real_capture, 'discrete_certified', ['--samples', '8192'])
        assert complete['stages']['split']['missing_original_keys'] == complete['stages']['split']['invented_keys'] == 0
        assert complete['stages']['split']['normalized_ecdf_ks'] == 0
    report = {'status': 'PASS', 'runs': runs, 'run_count': len(runs), 'fixture_root': str(args.fixture_root),
              'actual_database_io': False, 'raw_key_fixture_files_created': True,
              'binary_sha256': {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (args.replay, args.legacy)}}
    (args.artifact_root / 'validation.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS: {} replay/negative/streaming checks; real capture {}'.format(len(runs), bool(args.real_capture)))


if __name__ == '__main__':
    main()
