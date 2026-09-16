#!/usr/bin/env python3
"""Compare the key sets of ten baseline and ten bitmap F2Load loadings.

Each database was reduced to a key-ID bitmap over the generator domain. The ten
baseline loadings draw from the same generator and seed, so their key set is the
reference; this script first checks that the ten baseline bitmaps are bit-identical
and then measures every F2Load set against that reference.
"""
import csv
import hashlib
import json
from pathlib import Path
import numpy as np

EXP = Path(__file__).resolve().parents[2]
RECORDS = EXP / 'artifacts/log_loads/keyset_compare_260915/keyset_records.json'
OUT = EXP / 'results'
DOMAIN = 1000 * (1024 ** 3) // 1024


def bits(path):
    return np.fromfile(path, dtype=np.uint64)


def popcount(a):
    return int(np.unpackbits(a.view(np.uint8)).sum())


def main():
    records = json.loads(RECORDS.read_text())
    base = [r for r in records if r['system'] == 'baseline']
    f2 = [r for r in records if r['system'] == 'f2load_bitmap']

    digests = {}
    reference = None
    for r in base:
        raw = Path(r['bitmap']).read_bytes()
        digests[r['arm']] = hashlib.sha256(raw).hexdigest()
        if reference is None:
            reference = np.frombuffer(raw, dtype=np.uint64)
    unique_digests = set(digests.values())
    print('baseline bitmaps: {} loadings, {} distinct digest(s)'.format(
        len(base), len(unique_digests)))
    if len(unique_digests) != 1:
        for arm, d in sorted(digests.items()):
            print('  {:<6} {}'.format(arm, d[:16]))
        raise SystemExit('baseline key sets are not identical; no single reference')
    truth = popcount(reference)
    print('reference key set: {:,} keys, {:.4f}% of the {:,} id domain'.format(
        truth, 100 * truth / DOMAIN, DOMAIN))

    rows = []
    for r in f2:
        a = bits(r['bitmap'])
        inter = popcount(np.bitwise_and(reference, a))
        got = popcount(a)
        missing, extra = truth - inter, got - inter
        rows.append(dict(
            arm=r['arm'], system=r['system'], final_sst_count=r['final_sst_count'],
            load_sec=r.get('load_sec'), entries=r['entries'],
            reference_keys=truth, f2load_keys=got, intersection_keys=inter,
            missing_keys=missing, extra_keys=extra,
            recall_pct=round(100 * inter / truth, 6),
            missing_pct=round(100 * missing / truth, 6),
            extra_pct=round(100 * extra / truth, 6),
            jaccard=round(inter / (truth + got - inter), 8),
            duplicate_entries=r['entries'] - got))
        print('  {:<5} keys {:,}  missing {:,} ({:.3f}%)  extra {:,}  jaccard {:.6f}'.format(
            r['arm'], got, missing, rows[-1]['missing_pct'], extra, rows[-1]['jaccard']))

    OUT.mkdir(exist_ok=True)
    path = OUT / 'keyset_compare_260915.tsv'
    with path.open('w', newline='') as h:
        w = csv.DictWriter(h, fieldnames=list(rows[0]), delimiter='\t')
        w.writeheader()
        w.writerows(rows)
    print('wrote {} ({} rows)'.format(path, len(rows)))

    summary = OUT / 'keyset_compare_260915_summary.tsv'
    metrics = ['f2load_keys', 'intersection_keys', 'missing_keys', 'extra_keys',
               'recall_pct', 'missing_pct', 'extra_pct', 'jaccard', 'final_sst_count']
    with summary.open('w', newline='') as h:
        w = csv.writer(h, delimiter='\t')
        w.writerow(['metric', 'n', 'mean', 'sd', 'min', 'max'])
        for m in metrics:
            v = np.array([r[m] for r in rows], dtype=float)
            w.writerow([m, len(v), round(v.mean(), 6), round(v.std(ddof=1), 6),
                        round(v.min(), 6), round(v.max(), 6)])
        w.writerow(['baseline_reference_keys', len(base), truth, 0, truth, truth])
    print('wrote {}'.format(summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
