#!/usr/bin/env python3
"""Recover SST survival counts from preserved completed loading logs.

For these normal, successful table-building paths, TABLE_SYNC_MICROS counts
one flush output and COMPACTION_OUTFILE_SYNC_MICROS counts one compaction
output. File-open counts are a cross-check, not the general definition of
files created. Validate the method against the independently event-counted
1 TB common baseline before reporting the preserved 8 TB result.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re

EXP = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def counts(log):
    text = log.read_text(errors='replace')
    def count(name):
        values = re.findall(r'^' + re.escape(name) +
                            r'\b[^\n]*?COUNT\s*:\s*(\d+)', text, re.M)
        assert len(values) == 1, 'expected one final statistic: ' + name
        return int(values[0])
    assert len(re.findall(r'^fillrandom\s+:', text, re.M)) == 1
    assert 'finished with status (OK)' in text
    pending = re.findall(r'Estimated pending compaction bytes:\s*(\d+)', text)
    assert pending and int(pending[-1]) == 0
    assert count('rocksdb.no.file.errors') == 0
    flush = count('rocksdb.table.sync.micros')
    compaction = count('rocksdb.compaction.outfile.sync.micros')
    total = flush + compaction
    assert total == count('rocksdb.no.file.opens'), 'file-open cross-check failed'
    final = re.findall(r'^\s*([0-6])\s+(\d+)\s+(\d+)\s*$',
                       text.rsplit('Level Files Size(MB)', 1)[1], re.M)
    assert len(final) == 7 and {int(r[0]) for r in final} == set(range(7))
    remaining = sum(int(r[1]) for r in final)
    assert 0 < remaining <= total
    return dict(flush_sst_count=flush, compaction_output_sst_count=compaction,
                created_sst_count=total, final_sst_count=remaining,
                retained_sst_pct=100 * remaining / total,
                log=str(log), log_sha256=sha(log))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output-dir', required=True, type=Path)
    args = ap.parse_args()
    control_path = EXP / 'results/paper_ch23_common_260907_f2_completion1/loads.json'
    control = json.loads(control_path.read_text())['baseline_1kb']
    control_counts = counts(Path(control['log_dir']) / 'phase1/bench.out')
    for key in ('created_sst_count', 'final_sst_count', 'retained_sst_pct'):
        assert control_counts[key] == control[key], 'event-counted control mismatch: ' + key
    raw = EXP / 'artifacts/log_loads/exp_260822_paper_bg_91b_8tb_direct'
    target = counts(raw / 'preserved/bench.first_complete.out')
    rows = [dict(case='baseline_8tb_91b', dataset_gib=8000, key_bytes=48,
                 value_bytes=43, **target),
            dict(case='control_baseline_1tb_1kb', dataset_gib=1000, key_bytes=24,
                 value_bytes=1000, **control_counts)]
    args.output_dir.mkdir(parents=True, exist_ok=False)
    with (args.output_dir / 'summary.tsv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)
    provenance = dict(rows=rows, parser_sha256=sha(Path(__file__)),
        method='Final levelstats count divided by flush plus compaction output sync histogram counts',
        control_loads=str(control_path), control_loads_sha256=sha(control_path),
        control_check='Matches independently counted table_file_creation events',
        run_provenance=(raw / 'PROVENANCE.md').read_text(),
        run_preservation=(raw / 'PRESERVATION.md').read_text(),
        limitation='Counts do not establish the fraction of loading wall time spent on intermediate SSTs.')
    (args.output_dir / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(json.dumps(target, indent=2))


if __name__ == '__main__':
    main()
