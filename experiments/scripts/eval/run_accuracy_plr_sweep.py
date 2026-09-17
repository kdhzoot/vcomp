#!/usr/bin/env python3
"""Replay every captured compaction at several PLR error bounds.

Section 5.6 shows that relaxing the error bound leaves the final LSM-tree state
unchanged. That is an end-to-end result: errors in individual compactions could
still grow and cancel out across the loading path. This replays the 1000 GiB
capture set at each error bound and measures the per-compaction prediction error
directly, with the KMV budget held at the default 512/8.

Captures are read-only; the replay tool opens no database.
"""
import argparse
import concurrent.futures
import csv
import json
import os
from pathlib import Path
import statistics as st
import subprocess
import tempfile

CAPTURES = Path('/work/vcomp/exp/real_input_accuracy_accuracy_1000gib_260913_run1/captures')
REPLAY = Path('/tmp/claude-1000/-home-smrc-virtual-compaction/'
              'ed5849c9-da3a-497a-8c76-743d615c0e52/scratchpad/virtual_compaction_replay')
OUT = Path(__file__).resolve().parents[2] / 'results'
ERROR_BOUNDS = (2, 4, 8, 16, 32)
SAMPLES, BUCKETS = 512, 8


def actual(manifest):
    """Output-file count, bytes and entries as the real compaction produced them."""
    files, size, entries = 0, 0, 0
    for line in manifest.read_text().splitlines():
        parts = line.split('\t')
        if parts[0] == 'output':
            files += 1
            size += int(parts[3])
            entries += int(parts[5])
    return files, size, entries


def one(job_dir, plr_error, work_root):
    manifest = job_dir / 'manifest.tsv'
    with tempfile.TemporaryDirectory(dir=work_root) as tmp:
        out = Path(tmp) / 'result.json'
        argv = [str(REPLAY), '--manifest', str(manifest), '--output', str(out),
                '--variant', 'plr', '--samples', str(SAMPLES), '--buckets', str(BUCKETS),
                '--plr-error', str(plr_error), '--max-buffer-keys', '50000000',
                '--work-dir', tmp]
        env = dict(os.environ, VCOMP_KMV_SAMPLES=str(SAMPLES),
                   VCOMP_KMV_RANGE_BUCKETS=str(BUCKETS))
        proc = subprocess.run(argv, env=env, capture_output=True, text=True)
        if not out.is_file():
            return None
        r = json.loads(out.read_text())
    a_files, a_bytes, a_entries = actual(manifest)
    split = r.get('stages', {}).get('split', {})
    merged = r.get('stages', {}).get('merged', {})
    p_files = r.get('metadata', {}).get('split', {}).get('files')
    p_entries = split.get('generated_U')
    if p_files is None or p_entries is None or not a_entries:
        return None
    bounds = merged.get('actual_output_boundaries') or []
    mass = [abs(b['range_mass_error_over_truth_N']) for b in bounds]
    return dict(job=int(r['job']), plr_error=plr_error,
                output_level=r.get('output_level'),
                plr_segments=r.get('metadata', {}).get('merged', {}).get('plr_segments'),
                actual_output_files=a_files, predicted_output_files=p_files,
                output_file_delta=p_files - a_files,
                actual_entries=a_entries, predicted_entries=p_entries,
                entry_error_pct=100.0 * (p_entries - a_entries) / a_entries,
                rank_ks=merged.get('normalized_ecdf_ks'),
                rank_count_sup=merged.get('count_sup_over_truth_N'),
                boundary_mass_error=(sum(mass) / len(mass)) if mass else None)


def pct(values, q):
    values = sorted(values)
    if not values:
        return None
    i = min(len(values) - 1, int(round(q * (len(values) - 1))))
    return values[i]


def summarize(rows, plr_error):
    n = len(rows)
    exact = sum(1 for r in rows if r['output_file_delta'] == 0)
    within1 = sum(1 for r in rows if abs(r['output_file_delta']) <= 1)
    ent = [abs(r['entry_error_pct']) for r in rows]
    ks = [r['rank_ks'] for r in rows if r['rank_ks'] is not None]
    sup = [r['rank_count_sup'] for r in rows if r['rank_count_sup'] is not None]
    mass = [r['boundary_mass_error'] for r in rows if r['boundary_mass_error'] is not None]
    seg = [r['plr_segments'] for r in rows if r['plr_segments'] is not None]
    return dict(plr_error=plr_error, jobs=n,
                merged_plr_segments_mean=round(st.mean(seg), 1) if seg else None,
                output_count_exact_pct=round(100.0 * exact / n, 3),
                output_count_within1_pct=round(100.0 * within1 / n, 3),
                entry_error_p50_pct=round(pct(ent, 0.50), 4),
                entry_error_p95_pct=round(pct(ent, 0.95), 4),
                rank_ks_p50=round(pct(ks, 0.50), 8) if ks else None,
                rank_ks_p95=round(pct(ks, 0.95), 8) if ks else None,
                rank_count_sup_p50=round(pct(sup, 0.50), 8) if sup else None,
                rank_count_sup_p95=round(pct(sup, 0.95), 8) if sup else None,
                boundary_mass_error_p50=round(pct(mass, 0.50), 8) if mass else None,
                boundary_mass_error_p95=round(pct(mass, 0.95), 8) if mass else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='replay only the first N jobs')
    ap.add_argument('--workers', type=int, default=24)
    ap.add_argument('--bounds', default=','.join(map(str, ERROR_BOUNDS)))
    ap.add_argument('--out-prefix', default='accuracy_plr_sweep_260916')
    args = ap.parse_args()

    bounds = [int(x) for x in args.bounds.split(',')]
    jobs = sorted(CAPTURES.iterdir(), key=lambda p: int(p.name.rsplit('_', 1)[1]))
    if args.limit:
        jobs = jobs[:args.limit]
    print('captures: {} jobs, bounds {}'.format(len(jobs), bounds), flush=True)

    OUT.mkdir(exist_ok=True)
    per_job, summaries = [], []
    with tempfile.TemporaryDirectory(dir='/work/vcomp/exp') as work_root:
        for e in bounds:
            rows = []
            with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
                for r in pool.map(lambda j: one(j, e, work_root), jobs):
                    if r:
                        rows.append(r)
            per_job.extend(rows)
            s = summarize(rows, e)
            summaries.append(s)
            print('  eps={:>2}  n={:<6} segs={:<9} rankKS p50={:.6f} p95={:.6f}  bmass p95={:.6f}  '
                  'outputs exact={:.2f}%'.format(
                      e, s['jobs'], s['merged_plr_segments_mean'], s['rank_ks_p50'] or 0,
                      s['rank_ks_p95'] or 0, s['boundary_mass_error_p95'] or 0,
                      s['output_count_exact_pct']), flush=True)

    with (OUT / (args.out_prefix + '_jobs.tsv')).open('w', newline='') as h:
        w = csv.DictWriter(h, fieldnames=list(per_job[0]), delimiter='\t')
        w.writeheader(); w.writerows(per_job)
    with (OUT / (args.out_prefix + '_summary.tsv')).open('w', newline='') as h:
        w = csv.DictWriter(h, fieldnames=list(summaries[0]), delimiter='\t')
        w.writeheader(); w.writerows(summaries)
    print('wrote {}_jobs.tsv ({} rows) and {}_summary.tsv'.format(
        args.out_prefix, len(per_job), args.out_prefix))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
