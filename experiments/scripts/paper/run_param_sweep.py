#!/usr/bin/env python3
"""PLR error bound and KMV sample sweep: cost, accuracy inputs, and read behavior.

Nine configurations. The PLR axis sweeps the error bound at the default KMV
sample count; the KMV axis sweeps the sample count at the default error bound;
the default cell (8 / 512) belongs to both. Each configuration loads the common
1 TB, 1 KB dataset with exact membership and the memory profiler on, measures
YCSB C with Zipfian requests on a hard-link clone, then deletes the database.

KMV parameters are environment variables rather than db_bench flags, so they are
set per load; the binary is the same throughout.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

EXP = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(EXP / 'lib'), str(EXP / 'scripts/paper')]
from ch23_common import F2, GIB, HASHES, active_benchmarks, require, save_json, sha  # noqa: E402
import run_f2load_band_chain as band  # noqa: E402

RUNNER = EXP / 'scripts/read/run_ycsb_alternatives.py'
CACHE = 50 * GIB
DB_ROOT_BASE = Path('/work/vcomp/exp')
DEFAULT_PLR, DEFAULT_KMV, DEFAULT_BUCKETS = 8, 512, 8
PLR_AXIS = [2, 4, 8, 16, 32]
KMV_AXIS = [128, 256, 512, 1024, 2048]


def configs(plr_axis=None, kmv_axis=None):
    """One cell per axis point; a point on both axes is loaded once."""
    plr_axis = PLR_AXIS if plr_axis is None else plr_axis
    kmv_axis = KMV_AXIS if kmv_axis is None else kmv_axis
    seen, out = set(), []
    for plr in plr_axis:
        out.append(('plr', plr, DEFAULT_KMV)); seen.add((plr, DEFAULT_KMV))
    for kmv in kmv_axis:
        if (DEFAULT_PLR, kmv) not in seen:
            out.append(('kmv', DEFAULT_PLR, kmv))
    return out


def axis_arg(text):
    """Comma-separated axis points; an empty string disables the axis."""
    text = text.strip()
    return [] if not text else [int(x) for x in text.split(',')]


def name_of(plr, kmv):
    return 'plr{}_kmv{}'.format(plr, kmv)


def peak_from_profile(path):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line]
    if not rows:
        return {}
    top = max(rows, key=lambda r: r['rss'])
    return dict(peak_rss_hwm=max(r['hwm'] for r in rows), baseline_rss=rows[0]['base_rss'],
                peak_rss_sampled=top['rss'], peak_phase=top['phase'],
                peak_plr_segment_bytes=top['plr_segment_bytes'],
                peak_kmv_sample_bytes=top['kmv_sample_bytes'],
                peak_kmv_bucket_bytes=top['kmv_bucket_bytes'],
                peak_descriptor_total=top['descriptor_total'],
                peak_other_live=top['other_live'], samples=len(rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='260915')
    ap.add_argument('--plr-axis', type=axis_arg, default=None,
                    help='comma-separated PLR error bounds (default 2,4,8,16,32)')
    ap.add_argument('--kmv-axis', type=axis_arg, default=None,
                    help='comma-separated KMV sample counts; empty disables the axis')
    ap.add_argument('--no-ycsb', action='store_true',
                    help='load only; skip the YCSB C measurement on the clone')
    ap.add_argument('--no-bitmap', action='store_true',
                    help='load without the exact membership bitmap, so materialized '
                         'keys come from the PLR inverse alone')
    ap.add_argument('--ycsb-only', action='store_true',
                    help='measure YCSB C on databases a previous load already produced')
    ap.add_argument('--run-tag', default=None,
                    help='tag for the YCSB run id; defaults to --date')
    ap.add_argument('--keep-db', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    db_root = DB_ROOT_BASE / ('paramsweep_' + args.date)

    binary = F2.resolve()
    HASHES[str(binary)] = sha(binary)
    plan = configs(args.plr_axis, args.kmv_axis)
    root = EXP / 'artifacts/log_loads' / ('paramsweep_' + args.date)
    print('binary {} ({})'.format(binary, sha(binary)[:16]))
    for axis, plr, kmv in plan:
        print('  {:<4} plr_error_bound={:<3} kmv_samples={:<5} -> {}'.format(
            axis, plr, kmv, db_root / name_of(plr, kmv)))
    print('{} configurations, buckets fixed at {}'.format(len(plan), DEFAULT_BUCKETS))
    if args.dry_run:
        return 0
    require(not active_benchmarks(), 'another storage benchmark is active')
    require(not (args.ycsb_only and args.no_ycsb), '--ycsb-only and --no-ycsb are exclusive')
    tag = args.run_tag or args.date
    root.mkdir(parents=True, exist_ok=True)

    rows, failed = [], []
    for axis, plr, kmv in plan:
        name = name_of(plr, kmv)
        db, log = db_root / name, root / name
        print('=== {} (axis {}) ==='.format(name, axis), flush=True)
        if args.ycsb_only:
            row = json.loads((log / 'validated.json').read_text())
            require(db.is_dir(), 'database is missing: ' + str(db))
            print('  reusing load: {:.1f} s, {} ssts'.format(
                row['elapsed_sec'], row['final_sst_count']), flush=True)
        else:
            row = load_cell(name, db, log, binary, plr, kmv, axis, args.no_bitmap)
            if args.no_ycsb:
                rows.append(row)
                save_json(root / 'results.json', rows)
                print('  ycsb skipped', flush=True)
                continue

        bundle = band.bundle_for(row, root / (name + '_bundle'))
        run_id = 'ps_{}_{}'.format(name, tag)
        cmd = [sys.executable, str(RUNNER), '--run-id', run_id, '--systems', 'f2load',
               '--workloads', 'c', '--duration', '300', '--cache-size', str(CACHE),
               '--source-bundle', str(bundle)]
        print('  ' + ' '.join(cmd), flush=True)
        code = subprocess.call(cmd)
        if code == 0:
            cell = [r for r in json.loads(
                (EXP / 'artifacts/log_runs' / run_id / 'results.json').read_text())
                if r['phase'] == 'full'][0]
            row.update(ycsb_run_id=run_id, throughput_ops_sec=cell['throughput_ops_sec'],
                       avg_latency_us=cell['avg_latency_us'],
                       get_found_fraction=cell['get_found_fraction'],
                       data_cache_hit_fraction=cell['data_cache_hit_fraction'],
                       engine_keys_read=cell['engine_keys_read'],
                       bloom_filter_useful=cell['tickers']['rocksdb.bloom.filter.useful'],
                       bloom_filter_full_positive=cell['tickers']['rocksdb.bloom.filter.full.positive'])
            rows.append(row)
            save_json(log / ('validated.json' if not args.ycsb_only else 'validated_ycsb.json'), row)
        else:
            failed.append(name)
        save_json(root / ('results.json' if not args.ycsb_only else 'results_ycsb.json'), rows)
        if not args.keep_db and not args.ycsb_only:
            require(str(db.resolve()).startswith(str(db_root) + '/'), 'unsafe delete path')
            shutil.rmtree(db)
            print('  removed ' + str(db), flush=True)
        print('  free {} GiB'.format(shutil.disk_usage('/work').free // GIB), flush=True)
        if code != 0:
            print('  campaign failed, stopping', flush=True)
            break
    (root / ('COMPLETED.json' if not args.ycsb_only else 'COMPLETED_ycsb.json')).write_text(
        json.dumps(dict(cells=len(rows), failed=failed or None), indent=1) + '\n')
    print('done={} failed={}'.format(len(rows), failed or ['none']))
    return 1 if failed else 0


def load_cell(name, db, log, binary, plr, kmv, axis, no_bitmap=False):
    """Load one configuration and record its cost and memory profile."""
    profile = log / 'profile.jsonl'
    extra = {'plr_error_bound': plr,
             'vcomp_memory_profile_out': str(profile),
             'vcomp_memory_profile_interval_ms': 100}
    if not no_bitmap:
        extra.update(vcomp_exact_membership=True, vcomp_exact_membership_max_mb=1024)
    os.environ['VCOMP_KMV_SAMPLES'] = str(kmv)
    os.environ['VCOMP_KMV_RANGE_BUCKETS'] = str(DEFAULT_BUCKETS)
    row = band.load_one(name, db, log, binary, extra)
    row.update(axis=axis, plr_error_bound=plr, kmv_samples=kmv,
               kmv_range_buckets=DEFAULT_BUCKETS, **peak_from_profile(profile))
    save_json(log / 'validated.json', row)
    print('  load {:.1f} s, {} ssts, peak RSS {:.2f} GB'.format(
        row['elapsed_sec'], row['final_sst_count'],
        row.get('peak_rss_hwm', row['peak_rss_kb'] * 1024) / 1e9), flush=True)
    return row


if __name__ == '__main__':
    raise SystemExit(main())
