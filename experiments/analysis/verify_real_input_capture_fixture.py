#!/usr/bin/env python3
"""Independently validate the small real-DB capture fixtures; no DB writes."""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify_job(manifest):
    lines = [line.split('\t') for line in manifest.read_text().splitlines()]
    require(['schema', 'vcomp_real_input_v1'] in lines, 'wrong schema')
    require(lines[-1] == ['status', 'ok'], 'incomplete capture')
    for field, value in (('cf_id', '0'), ('key_size', '24'),
                         ('value_size', '43'), ('plr_error', '8')):
        require([field, value] in lines, 'unexpected fixture scalar: ' + field)
    groups = {'input': [], 'output': []}
    records = []
    for row in lines:
        if row[0] not in groups:
            continue
        require(len(row) == 9, 'invalid file record')
        physical, unique, low, high = map(int, row[4:8])
        source = (manifest.parent / row[8]).resolve()
        require(source.parent == manifest.parent.resolve(), 'unexpected path')
        data = source.read_bytes()
        require(len(data) == 8 * unique, 'wrong binary length')
        keys = [item[0] for item in struct.iter_unpack('<Q', data)]
        require(physical >= unique > 0, 'invalid physical/unique count')
        require(all(a < b for a, b in zip(keys, keys[1:])), 'not sorted unique')
        require((keys[0], keys[-1]) == (low, high), 'wrong key extrema')
        groups[row[0]].append(set(keys))
        records.append({'kind': row[0], 'file': row[1], 'physical_entries': physical,
                        'unique_entries': unique, 'sha256': hashlib.sha256(data).hexdigest()})
    require(groups['input'] and groups['output'], 'missing files')
    inputs = set().union(*groups['input'])
    outputs = set().union(*groups['output'])
    require(inputs == outputs, 'actual Put-only compaction changed union')
    return {'manifest': str(manifest), 'unique': len(outputs), 'files': records,
            'keys': outputs}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = {'status': 'ok', 'cases': {}}
    for case in ('put', 'delete', 'padding', 'snapshot'):
        base = args.root / case / 'capture'
        manifests = sorted(base.glob('**/manifest.tsv'))
        failures = sorted(set(base.glob('**/failed.txt')) |
                          set(base.glob('*.failed.txt')))
        if case == 'put':
            require(manifests and not failures, 'valid fixture not captured cleanly')
            require(all((p / 'manifest.tsv').exists()
                        for p in base.glob('cf*_job_*') if p.is_dir()),
                    'incomplete valid fixture job')
            jobs = [verify_job(p) for p in manifests]
            truth = set(range(0, 4096, 2)) | set(range(1024, 3072))
            # Manual compaction can produce multiple jobs; inspect their union
            # and also require one job to contain the complete overlapping input.
            require(any(job['keys'] == truth for job in jobs), 'full fixture job missing')
            for job in jobs:
                del job['keys']
            result['cases'][case] = {'status': 'valid', 'jobs': jobs}
        else:
            require(failures, 'unsupported fixture did not leave failed capture: ' + case)
            reason = {'delete': ('deletion or merge', 'plain Put'),
                      'padding': ('padding',), 'snapshot': ('snapshots',)}[case]
            reasons = [p.read_text() for p in failures]
            require(any(any(token in text for token in reason) for text in reasons),
                    'fixture failed for an unrelated reason: ' + case)
            result['cases'][case] = {'status': 'rejected',
                                     'failures': [str(p) for p in failures],
                                     'reasons': reasons}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': 'ok', 'valid_fixture': 1, 'unsupported_fixtures_rejected': 3}))


if __name__ == '__main__':
    main()
