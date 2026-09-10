#!/usr/bin/env python3
"""Compare completed baseline-layout YCSB-C runs using saved evidence only.

No DB is opened. Historical baseline/F2Load measurements remain historical;
their counts and latencies are not rewritten as new repetitions.
"""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import re

from parse_ycsb_alternatives import parse_metrics

EXPERIMENTS = Path(__file__).resolve().parents[1]
REFERENCE_RUN = 'paper_alternatives_ycsb_cached0_260908_no_flush_run2'
BINARY_HASH = '20d67c38612cee9a34d9ef7114c81bc8366b2d4cf99c598b642e63f21ab74266'
NUMBER = r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
LEVEL_RE = re.compile(
    r'^\*\* Level (\d+) read latency histogram \(micros\):\s*\n'
    rf'Count:\s*(\d+)\s+Average:\s*({NUMBER})\s+StdDev:\s*({NUMBER})', re.M)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text())


def evidence(path):
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def parse_level_reads(text):
    """Use the last complete default-CF cumulative dump, never sum snapshots."""
    marker = '** File Read Latency Histogram By Level [default] **'
    position = text.rfind(marker)
    require(position >= 0, 'missing default-CF file-read histogram')
    section = text[position + len(marker):]
    next_cf = section.find('** File Read Latency Histogram By Level [')
    if next_cf >= 0:
        section = section[:next_cf]
    result = {}
    for match in LEVEL_RE.finditer(section):
        level = int(match.group(1))
        require(0 <= level <= 6 and level not in result,
                'unexpected/duplicate level in final file-read dump')
        result[level] = dict(count=int(match.group(2)),
                             average_us=float(match.group(3)),
                             stddev_us=float(match.group(4)))
    require(1 in result and result[1]['count'] > 0, 'missing/nonpositive L1 reads')
    return result


def make_row(label, run_id, measured, log, metadata_path, historical):
    require(measured['phase'] == 'full' and measured['workload'] == 'workloadc'
            and measured['status'] == 'ok', 'unvalidated/non-full C measurement')
    require(measured['binary_sha256'] == BINARY_HASH, 'unexpected YCSB executable')
    text = log.read_text(errors='replace')
    parsed = parse_metrics(text, 'workloadc')
    require(all(measured.get(key) == value for key, value in parsed.items()),
            'saved measurements differ from reparsed log: ' + label)
    gets = measured['engine_keys_read']
    require(gets > 0 and gets == measured['operations'] and
            measured['engine_keys_written'] == 0, 'not a pure Get workload')
    by_level = parse_level_reads(text)
    for entry in by_level.values():
        entry['count_per_get'] = entry['count'] / gets
        entry['weighted_latency_us_per_get'] = entry['count_per_get'] * entry['average_us']
    read = measured['operation_histograms']['read']
    row = dict(label=label, run_id=run_id, historical_reference=historical,
               system=measured['system'], source_db_dir=measured['source_db_dir'],
               binary_sha256=measured['binary_sha256'],
               throughput_ops_sec=measured['throughput_ops_sec'],
               measured_seconds=measured['measured_seconds'], gets=gets,
               get_found_fraction=measured['get_found_fraction'],
               operation_mean_us=read['average_us'], operation_p99_us=read['p99_us'],
               l1_file_reads=by_level[1]['count'],
               l1_file_reads_per_get=by_level[1]['count_per_get'],
               l1_file_read_average_us=by_level[1]['average_us'],
               l1_weighted_latency_us_per_get=by_level[1]['weighted_latency_us_per_get'],
               file_reads_by_level=by_level,
               final_levels=measured['final_levels'],
               source_evidence=dict(log=evidence(log), metrics=evidence(metadata_path)),
               l1_domain_coverage_pct=None, coverage_evidence=None)
    for kind in ('filter', 'index', 'data'):
        require(measured[kind + '_cache_miss'] is not None, 'missing block statistics')
        row[kind + '_cache_misses_per_get'] = measured[kind + '_cache_miss'] / gets
    for key in ('compaction_read_bytes', 'compaction_write_bytes',
                'flush_write_bytes', 'stall_micros'):
        row[key] = measured[key]
    return row


def add_coverage(rows, bundle):
    """Attach saved domain-union coverage only when source identity is grounded."""
    source_path = bundle / 'provenance/source_load_results.json'
    loads = read_json(source_path)
    for row in rows:
        if not row['historical_reference']:
            matches = [entry for entry in loads if entry['phase'] == 'full' and
                       entry['loading']['db_dir'] == row['source_db_dir']]
            require(len(matches) == 1 and matches[0]['coverage']['source_unchanged'],
                    'repeat coverage source mismatch')
            coverage = matches[0]['coverage']
            row['l1_domain_coverage_pct'] = coverage['l1_global_coverage_pct']
            row['coverage_evidence'] = dict(**evidence(source_path),
                source_record=matches[0]['name'], coverage=coverage)

    diagnostic = EXPERIMENTS / 'artifacts/coverage_dumps/ycsb_l1_coverage_260908'
    table = diagnostic / 'coverage.tsv'
    if not table.is_file():
        return ['Historical L1 coverage TSV unavailable; historical coverage omitted.']
    with table.open() as stream:
        coverage_rows = list(csv.DictReader(stream, delimiter='\t'))
    for row in rows:
        if not row['historical_reference']:
            continue
        system = row['system']
        identity = diagnostic / (system + '.source_identity.json')
        expected = (EXPERIMENTS / 'artifacts/log_runs' / REFERENCE_RUN /
                    'full/workloadc' / system / 'source_identity.json')
        require(identity.is_file() and expected.is_file() and
                read_json(identity) == read_json(expected),
                'historical coverage identity cannot be verified: ' + system)
        matches = [entry for entry in coverage_rows if entry['system'] == system
                   and entry['level'] == '1']
        require(len(matches) == 1 and matches[0]['exact_inclusive_L1'] == 'True'
                and matches[0]['domain_keys'] == '1048576000',
                'historical coverage definition mismatch')
        row['l1_domain_coverage_pct'] = float(matches[0]['domain_coverage_pct'])
        row['coverage_evidence'] = dict(**evidence(table), source_record=matches[0],
            diagnostic_identity=evidence(identity), reference_identity=evidence(expected))
    return []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    require(re.fullmatch(r'baseline_repeat_ycsb_c_[A-Za-z0-9_-]+', args.run_id),
            'unsafe/unrelated run ID')
    raw = EXPERIMENTS / 'artifacts/log_runs' / args.run_id
    bundle = EXPERIMENTS / 'results' / args.run_id
    completion = read_json(raw / 'COMPLETED.json')
    status = read_json(raw / 'status.json')
    require(completion.get('full_cells') == 2 and completion.get('valid_full') == 2
            and completion.get('source_identities_unchanged') is True
            and status.get('kind') == 'COMPLETED' and status.get('valid_full') == 2,
            'campaign is not completely validated')
    require(bundle.is_dir() and not bundle.is_symlink(), 'missing/unsafe result bundle')
    outputs = [bundle / 'comparison.json', bundle / 'comparison.tsv']
    require(not any(path.exists() or path.is_symlink() for path in outputs),
            'comparison outputs exist; never overwrite measurements')
    metrics_path = bundle / 'results.json'
    full = [row for row in read_json(metrics_path) if row['phase'] == 'full']
    require(len(full) == 2 and {row['system'] for row in full} ==
            {'baseline_repeat_01', 'baseline_repeat_02'}, 'unexpected full cells')
    rows = []
    reference = bundle / 'provenance/reference_ycsb_c'
    for system, label in (('baseline', 'original_baseline'), ('f2load', 'historical_f2load')):
        metadata = reference / system / 'validated.json'
        rows.append(make_row(label, REFERENCE_RUN, read_json(metadata),
                             reference / system / 'bench.log', metadata, True))
    for measured in sorted(full, key=lambda row: row['system']):
        log = bundle / 'evidence/full/workloadc' / measured['system'] / 'bench.log'
        rows.append(make_row(measured['system'], args.run_id, measured, log,
                             metrics_path, False))
    ordering = {'original_baseline': 0, 'baseline_repeat_01': 1,
                'baseline_repeat_02': 2, 'historical_f2load': 3}
    rows.sort(key=lambda row: ordering[row['label']])
    warnings = add_coverage(rows, bundle)
    for row in rows:
        row['throughput_relative_to_original_baseline'] = (
            row['throughput_ops_sec'] / rows[0]['throughput_ops_sec'])
    report = dict(schema_version=1, run_id=args.run_id, rows=rows, warnings=warnings,
        completion_evidence=evidence(raw / 'COMPLETED.json'),
        analysis_source=evidence(Path(__file__)),
        definitions=dict(coverage='inclusive L1 range union / full integer key domain',
            l1_reads='Final cumulative SST file-reader histogram count, not level hits.',
            histogram_scope='May include DB-open/background activity; not pure data-block I/O.',
            weighted_latency='File-reader average times count/Get; not wall-clock attribution.'),
        limitations=['One five-minute read per newly loaded DB; old references are historical.',
            'Identical seed and duration can consume different RNG-prefix lengths.',
            'Coverage does not by itself explain differences in per-read latency.',
            'Per-level file-read totals need not equal the global SST-read histogram.'])
    fields = [key for key in rows[0] if key not in
              ('file_reads_by_level', 'source_evidence', 'coverage_evidence', 'final_levels')]
    fields += ['final_levels_json']
    for level in range(7):
        fields += [key for key in (f'l{level}_file_reads_per_get',
                                  f'l{level}_file_read_mean_us') if key not in fields]
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields, delimiter='\t', extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        flat = dict(row, final_levels_json=json.dumps(row['final_levels'], sort_keys=True))
        for level in range(7):
            entry = row['file_reads_by_level'].get(level)
            flat[f'l{level}_file_reads_per_get'] = entry['count_per_get'] if entry else None
            flat[f'l{level}_file_read_mean_us'] = entry['average_us'] if entry else None
        writer.writerow(flat)
    # Exclusive creation protects prior results even if another analyzer races.
    with outputs[0].open('x') as target:
        target.write(json.dumps(report, indent=2, sort_keys=True) + '\n')
    with outputs[1].open('x', newline='') as target:
        target.write(stream.getvalue())
    print(json.dumps(dict(comparison_outputs=[str(path) for path in outputs], rows=len(rows))))


if __name__ == '__main__':
    main()
