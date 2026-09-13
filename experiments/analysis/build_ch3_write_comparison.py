#!/usr/bin/env python3
"""Flush write, compaction write and throughput for a fixed write volume.

Both states run the same YCSB A for the same operation count rather than the
same wall time, and the tree is drained with waitforcompaction before the
statistics are read. Equal operation counts make flush write identical by
construction, so compaction write is the whole of the difference, and draining
first means the number covers work the window would otherwise have deferred.

Flush-only has no row: it stops writes at DB open, so it never runs the
workload.
"""
import csv
from pathlib import Path
import re

EXP = Path(__file__).resolve().parents[1]
RUN = EXP / 'results/ch3_write_fixed_260911'
OUT = EXP / 'results/paper_ch3_write_comparison.tsv'
STATES = [('baseline', 'Incremental construction'), ('fillseq', 'Fillseq')]
WORKLOAD = 'workloada'
FIELDS = ['state', 'label', 'operations', 'keys_written', 'measured_sec',
          'throughput_ops_sec', 'flush_write_bytes', 'flush_write_gb',
          'compaction_write_bytes', 'compaction_write_gb',
          'compaction_write_per_key_bytes', 'write_amplification',
          'throughput_vs_baseline', 'compaction_write_vs_baseline']


def parse(path):
    text = path.read_bytes().decode('latin-1')

    def ticker(name):
        values = re.findall(r'^' + re.escape(name) + r' COUNT\s*:\s*(\d+)',
                            text, re.M)
        if not values:
            raise SystemExit('missing ticker {} in {}'.format(name, path))
        return int(values[-1])

    m = re.search(r'^' + WORKLOAD + r'\s+:\s+[\d.]+ micros/op\s+([\d.]+) ops/sec'
                  r'\s+([\d.]+) seconds\s+(\d+) operations', text, re.M)
    if not m:
        raise SystemExit('missing benchmark line in ' + str(path))
    return dict(throughput_ops_sec=float(m.group(1)),
                measured_sec=float(m.group(2)),
                operations=int(m.group(3)),
                keys_written=ticker('rocksdb.number.keys.written'),
                flush_write_bytes=ticker('rocksdb.flush.write.bytes'),
                compaction_write_bytes=ticker('rocksdb.compact.write.bytes'))


def main():
    rows = []
    for state, label in STATES:
        d = parse(RUN / state / 'bench.out')
        d.update(state=state, label=label,
                 flush_write_gb=round(d['flush_write_bytes'] / 1e9, 1),
                 compaction_write_gb=round(d['compaction_write_bytes'] / 1e9, 1),
                 compaction_write_per_key_bytes=round(
                     d['compaction_write_bytes'] / d['keys_written'], 1),
                 write_amplification=round(
                     d['compaction_write_bytes'] / d['flush_write_bytes'], 2))
        rows.append(d)
    base = rows[0]
    for r in rows:
        r['throughput_vs_baseline'] = round(
            r['throughput_ops_sec'] / base['throughput_ops_sec'], 3)
        r['compaction_write_vs_baseline'] = round(
            r['compaction_write_bytes'] / base['compaction_write_bytes'], 3)
    with open(OUT, 'w', newline='') as f:
        w = csv.DictWriter(f, FIELDS, delimiter='\t')
        w.writeheader()
        w.writerows(rows)
    print('wrote ' + str(OUT))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
