#!/usr/bin/env python3
"""Read-only integrity and observation checks; no original paths or binaries needed."""
import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_json(relative):
    return json.loads((ROOT / relative).read_text())


def read_jsonl(relative):
    return [json.loads(line) for line in (ROOT / relative).read_text().splitlines()
            if line.strip()]


def sha256(relative):
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def check_csv(relative, rows, subset=False):
    with (ROOT / relative).open(newline='') as stream:
        actual = list(csv.DictReader(stream))
    require(len(actual) == len(rows), relative + ': row count')
    expected_fields = set().union(*(row.keys() for row in rows))
    for index, (recorded, expected) in enumerate(zip(actual, rows)):
        if not subset:
            require(set(recorded) == expected_fields, relative + ': field names')
        for name, value in recorded.items():
            original = expected.get(name)
            encoded = ('' if original is None else json.dumps(original)
                       if isinstance(original, (list, dict)) else str(original))
            require(value == encoded, '{} row {} field {}'.format(relative, index, name))


def main():
    inventory = read_json('ORIGIN_INVENTORY.json')
    manifest = read_json('bundle_manifest.json')
    byte_count = 0
    seen = set()
    for item in inventory['included_files']:
        relative = item['bundle_path']
        require(relative not in seen, 'Duplicate inventory path: ' + relative)
        seen.add(relative)
        local = (ROOT / relative).resolve()
        try:
            local.relative_to(ROOT)
        except ValueError:
            raise RuntimeError('Inventory path outside bundle: ' + relative)
        require(local.stat().st_size == item['size_bytes'], 'Size mismatch: ' + relative)
        require(sha256(relative) == item['sha256'], 'SHA-256 mismatch: ' + relative)
        byte_count += item['size_bytes']

    native_manifest = read_json('native/manifest.json')
    chain_manifest = read_json('chain/manifest.json')
    for original in (native_manifest, chain_manifest):
        require(original['library_sha256'] == manifest['library_sha256'], 'Library provenance mismatch')
        require(original['compile_exit_code'] == original['exit_code'] == 0, 'Original process failed')
        require(original['library_unchanged'], 'Library changed during original measurement')
    require(inventory['measured_static_library_sha256'] == manifest['library_sha256'], 'Inventory library hash')
    require(sha256('native/probe_source.cc') == native_manifest['source_sha256'], 'Native source provenance')
    for original, digest in native_manifest['core_source_sha256'].items():
        if original.startswith('db/'):
            require(sha256('source_snapshot/' + original) == digest, 'Core source provenance: ' + original)
    for original, digest in chain_manifest['source_sha256'].items():
        local = ('chain/' + Path(original).name) if original.startswith('tools/') else ('source_snapshot/' + original)
        require(sha256(local) == digest, 'Chain source provenance: ' + original)

    native = read_jsonl('native/probe.jsonl')
    pipeline = [row for row in native if row['record_type'] == 'pipeline']
    trials = [row for row in native if row['record_type'] == 'single_merge_hash_offset']
    require(len(native) == 356 and len(pipeline) == 100 and len(trials) == 256, 'Native observation counts')
    for row in pipeline:
        require(row['kmv_union_estimate'] == row['merged_target'], 'Merge target changed estimate')
        require(row['raw_split_planned_entries'] == row['this_path_target'], 'Split entry sum changed target')
        if row['all_input_global_sketches_complete']:
            require(row['kmv_union_estimate'] == row['true_union'], 'Complete initial sketch estimate')
        require(row['trimmed_split_library_unique_vs_stream_disagree_files'] == 0, 'Native helper/reference disagreement')
    check_csv('native/pipeline_results.csv', pipeline)
    check_csv('native/hash_offset_trials.csv', trials)
    check_csv('native/hash_offset_summary.csv', read_json('native/hash_offset_summary.json'))

    chain = read_jsonl('chain/probe.jsonl')
    require(len(chain) == 36, 'Chain observation count')
    groups = {}
    for row in chain:
        require(row['isolated_union_estimate'] == row['merged_target'] == row['output_descriptor_entries'], 'Chain target mismatch')
        require(row['raw_outputs_planned_entries'] == row['merged_target'], 'Chain split entry sum')
        require(row['output_descriptor_entries'] <= row['input_descriptor_entries'], 'Chain count cap')
        require(row['trimmed_outputs_diagnostic_only_library_unique_vs_stream_disagree_files'] == 0, 'Chain helper/reference disagreement')
        if row['kmv_samples'] == 16384:
            for side in ('input', 'output'):
                for kind in ('global', 'range'):
                    require(row[side + '_' + kind + '_incomplete_sketches'] == 0, 'Complete-control sketch flag')
        groups.setdefault((row['case'], row['kmv_samples']), []).append(row)
    require(len(groups) == 9, 'Chain dataset/budget groups')
    for key, rows in groups.items():
        ordered = sorted(rows, key=lambda row: row['round'])
        require([row['round'] for row in ordered] == [1, 2, 3, 4], 'Chain round sequence: ' + str(key))
        require(len(set(row['true_original_union'] for row in ordered)) == 1, 'Immutable original oracle')
        for previous, current in zip(ordered, ordered[1:]):
            require(previous['output_descriptor_entries'] == current['input_descriptor_entries'], 'Chain carried descriptor count')
    check_csv('chain/results.csv', chain)
    check_csv('chain/stage_summary.csv', chain, subset=True)

    expected_counts = {'native_pipeline': len(pipeline), 'native_single_merge_offset_trials': len(trials), 'chain_rounds': len(chain)}
    require(manifest['observations'] == expected_counts, 'Bundle observation totals')
    print('PASS: {} archived files ({} bytes), SHA-256/source/build provenance, '.format(len(seen), byte_count)
          + '100 pipeline + 256 isolated estimate + 36 chain records, CSV agreement and stage invariants; no writes.')


if __name__ == '__main__':
    main()
