#!/usr/bin/env python3
"""Read retained VCOMP job logs and reconcile descriptor entry accounting."""
import argparse
import collections
import csv
import datetime
import hashlib
import json
from pathlib import Path
import re


def digest(data):
    return hashlib.sha256(data).hexdigest()


def analyze_case(run, dbroot, case):
    fidelity_path = case / 'fidelity/fidelity.json'
    fidelity_bytes = fidelity_path.read_bytes()
    fidelity = json.loads(fidelity_bytes)
    jobs, outputs, provenance = {}, collections.defaultdict(list), []
    for path in sorted((dbroot / 'full/db' / case.name).glob('LOG*')):
        data = path.read_bytes()
        provenance.append(dict(path=str(path), bytes=len(data), sha256=digest(data)))
        for line_number, line in enumerate(data.decode(errors='replace').splitlines(), 1):
            tag = next((tag for tag in ('VCOMP_WRITE_JOB', 'VCOMP_WRITE_OUTPUT')
                        if tag + ' ' in line), None)
            if tag is None:
                continue
            fields = dict(re.findall(r'([a-z_]+)=([^ ]+)', line.split(tag + ' ', 1)[1]))
            row = {key: int(value) for key, value in fields.items()
                   if re.fullmatch(r'-?\d+', value)}
            row.update(log_path=str(path), line_number=line_number)
            if tag == 'VCOMP_WRITE_JOB':
                if row['job'] in jobs:
                    raise ValueError('Duplicate job record: ' + str(row))
                jobs[row['job']] = row
            else:
                outputs[row['job']].append(row)
    if not jobs or set(outputs) != set(jobs):
        raise ValueError('Missing job/output log records: ' + case.name)

    level_pairs = collections.defaultdict(
        lambda: dict(jobs=0, input_entries=0, merged_entries=0,
                     estimated_dedup_entries=0, output_files=0,
                     infeasible_outputs=0, sibling_overlaps=0))
    violations = []
    split_mismatches = []
    for job_id, job in sorted(jobs.items()):
        children = sorted(outputs[job_id], key=lambda row: row['out_idx'])
        if [child['out_idx'] for child in children] != list(range(job['output_files'])):
            raise ValueError('Incomplete output list: ' + case.name + ':' + str(job_id))
        child_sum = sum(child['entries'] for child in children)
        if child_sum != job['output_entries']:
            split_mismatches.append(dict(job=job_id, merged=job['output_entries'],
                                         split_sum=child_sum))
        if job['input_entries'] - job['output_entries'] != job['dedup_entries']:
            raise ValueError('Inconsistent job accounting: ' + str(job_id))
        level = 'L{}->L{}'.format(job['start_level'], job['output_level'])
        aggregate = level_pairs[level]
        aggregate['jobs'] += 1
        aggregate['input_entries'] += job['input_entries']
        aggregate['merged_entries'] += job['output_entries']
        aggregate['estimated_dedup_entries'] += job['dedup_entries']
        aggregate['output_files'] += len(children)
        for index, child in enumerate(children):
            capacity = max(0, child['key_max'] - child['key_min'] + 1)
            bad_capacity = child['entries'] > capacity
            overlap = index > 0 and child['key_min'] <= children[index - 1]['key_max']
            aggregate['infeasible_outputs'] += bad_capacity
            aggregate['sibling_overlaps'] += overlap
            if bad_capacity or overlap:
                violations.append(dict(child, capacity=capacity,
                                       capacity_shortfall=max(0, child['entries'] - capacity),
                                       sibling_overlap=overlap))
    dedup_sum = sum(job['dedup_entries'] for job in jobs.values())
    observed_decrease = (fidelity['flush_local_unique_entries'] -
                         fidelity['stage1_live_descriptor_entries'])
    return dict(
        case=case.name, input_operations=fidelity['input_operations'],
        flush_local_unique_entries=fidelity['flush_local_unique_entries'],
        descriptor_entries=fidelity['stage1_live_descriptor_entries'],
        estimated_dedup_sum=dedup_sum, observed_descriptor_decrease=observed_decrease,
        ledger_closes=(dedup_sum == observed_decrease), jobs=len(jobs),
        split_sum_mismatches=split_mismatches,
        output_files=sum(len(children) for children in outputs.values()),
        infeasible_outputs=sum(row['infeasible_outputs'] for row in level_pairs.values()),
        sibling_overlaps=sum(row['sibling_overlaps'] for row in level_pairs.values()),
        level_pairs=dict(level_pairs), violations=violations,
        fidelity_source=dict(path=str(fidelity_path), sha256=digest(fidelity_bytes)),
        log_sources=provenance)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run = args.run_root.resolve()
    manifest = json.loads((run / 'manifest.json').read_text())
    status = json.loads((run / 'status.json').read_text())
    if not status.get('finished'):
        raise ValueError('Analyze only finished campaigns; active logs are not stable')
    cases = sorted((run / 'full/cases').glob('*_f2load'))
    if len(cases) != 6:
        raise ValueError('Expected six full F2Load cases')
    rows = [analyze_case(run, Path(manifest['db_root']), case) for case in cases]
    args.output.mkdir(parents=True, exist_ok=False)
    report = dict(created=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  run_root=str(run), script_sha256=digest(Path(__file__).read_bytes()),
                  interpretation='Entry-count accounting and logged descriptor invariants; '
                                 'not exact per-job key-set fidelity or causal attribution '
                                 'to a particular estimator formula', cases=rows)
    (args.output / 'entry_ledger.json').write_text(json.dumps(report, indent=2) + '\n')
    fields = ['case', 'jobs', 'output_files', 'estimated_dedup_sum',
              'observed_descriptor_decrease', 'ledger_closes',
              'infeasible_outputs', 'sibling_overlaps']
    with (args.output / 'summary.tsv').open('w', newline='') as output:
        writer = csv.DictWriter(output, fieldnames=fields, delimiter='\t', extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(json.dumps({field: row[field] for field in fields}))


if __name__ == '__main__':
    main()
