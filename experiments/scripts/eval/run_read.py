#!/usr/bin/env python3
"""평가 계획용 YCSB 실행기.

옵션은 프로젝트 정본을 그대로 쓴다: experiments/lib/ch23_common.py 의 BASE 위에
experiments/scripts/read/run_ycsb_alternatives.py 의 YCSB 오버라이드를 얹는다.
차이는 대상 DB 목록을 임의로 받는다는 것뿐이다(원격 도구는 §3.1 시스템 비교용으로
DB 경로가 고정되어 있다).

YCSB 워크로드는 vcomp-prof 빌드에만 있으므로 그 바이너리를 쓴다.
"""
import argparse, datetime, hashlib, json, re, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'experiments/lib'))
from ch23_common import BASE, command                      # noqa: E402

ART = REPO / 'experiments/artifacts'
PROFILER = REPO.parent / 'vcomp-prof' / 'db_bench'


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def options(db, log, workload, duration, cache_size, num, key, value, dist):
    """run_ycsb_alternatives.options() 와 동일한 구성."""
    return dict(BASE, num=num, key_size=key, value_size=value, db=str(db),
                benchmarks=workload + ',stats,levelstats',
                use_existing_db=True, readonly=False,
                disable_auto_compactions=False, memtablerep='skip_list',
                threads=48, duration=duration, reads=10000000000,
                seed=87654321, ops_between_duration_checks=1,
                cache_type='lru_cache', cache_size=cache_size,
                cache_index_and_filter_blocks=True,
                pin_l0_filter_and_index_blocks_in_cache=False,
                pin_top_level_index_and_filter=False,
                open_files=-1, stats_level=3, histogram=True, perf_level=3,
                report_interval_seconds=1, report_file=str(log / 'report.rep'),
                stats_interval_seconds=30, stats_per_interval=1,
                ycsb_requestdistribution=dist,
                ycsb_minscanlength=1, ycsb_maxscanlength=100,
                ycsb_scanlengthdistribution='uniform')


def run_one(binary, db, out, workload, duration, cache_pct, num, key, value, dist):
    out.mkdir(parents=True, exist_ok=True)
    db_bytes = sum(f.stat().st_size for f in Path(db).glob('*.sst'))
    cache = max(1, db_bytes * cache_pct // 100)
    argv = command(binary, options(db, out, workload, duration, cache,
                                   num, key, value, dist))
    (out / 'argv.json').write_text(json.dumps(argv, indent=1))
    start = time.time()
    with (out / 'stdout_stderr.log').open('wb') as f:
        rc = subprocess.call(argv, stdout=f, stderr=subprocess.STDOUT)
    text = (out / 'stdout_stderr.log').read_text(errors='replace')
    m = re.search(workload + r'\s*:\s*([\d.]+)\s*micros/op\s*(\d+)\s*ops/sec', text)
    pct = re.search(r'Percentiles:\s*P50:\s*([\d.]+)\s*P75:\s*([\d.]+)\s*'
                    r'P99:\s*([\d.]+)\s*P99\.9:\s*([\d.]+)', text)
    got = re.search(r'reads\s+(\d+)\s+in\s+(\d+)\s+found', text)
    res = dict(db=str(db), workload=workload, distribution=dist, exit_code=rc,
               elapsed_sec=time.time() - start, cache_bytes=cache,
               cache_pct=cache_pct, db_sst_bytes=db_bytes, num_keys=num,
               duration=duration, finished_at=now(),
               micros_per_op=float(m.group(1)) if m else None,
               ops_per_sec=int(m.group(2)) if m else None,
               p50_us=float(pct.group(1)) if pct else None,
               p75_us=float(pct.group(2)) if pct else None,
               p99_us=float(pct.group(3)) if pct else None,
               p999_us=float(pct.group(4)) if pct else None,
               reads_found=int(got.group(1)) if got else None,
               reads_issued=int(got.group(2)) if got else None)
    (out / 'result.json').write_text(json.dumps(res, indent=1))
    return res


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-id', required=True)
    p.add_argument('--db-root', default='/work/vcomp/exp/eval_20260909_night1')
    p.add_argument('--dbs', required=True)
    p.add_argument('--workload', default='workloadc')
    p.add_argument('--distribution', default='zipfian')
    p.add_argument('--duration', type=int, default=300)
    p.add_argument('--cache-pct', type=int, default=10)
    p.add_argument('--num', type=int, default=1048576000)
    p.add_argument('--key-size', type=int, default=24)
    p.add_argument('--value-size', type=int, default=1000)
    a = p.parse_args()
    root = ART / a.run_id
    root.mkdir(parents=True, exist_ok=True)
    (root / 'manifest.json').write_text(json.dumps(dict(
        created=now(), binary=str(PROFILER), binary_sha256=sha(PROFILER),
        profiler_commit=subprocess.check_output(
            ['git', '-C', str(PROFILER.parent), 'rev-parse', 'HEAD'], text=True).strip(),
        workload=a.workload, distribution=a.distribution, duration=a.duration,
        cache_pct=a.cache_pct, base_options=BASE), indent=2, default=str))
    results = []
    for name in a.dbs.split(','):
        db = Path(a.db_root) / name
        out = root / name
        if not db.exists():
            print(f'  없음: {db}', flush=True)
            continue
        if (out / 'result.json').exists():
            results.append(json.loads((out / 'result.json').read_text()))
            print(f'  건너뜀: {name}', flush=True)
            continue
        print(f'[{now()}] {name} 시작', flush=True)
        r = run_one(PROFILER, db, out, a.workload, a.duration, a.cache_pct,
                    a.num, a.key_size, a.value_size, a.distribution)
        print(f'[{now()}] {name}: {r["ops_per_sec"]} ops/s, p50 {r["p50_us"]}, '
              f'p99 {r["p99_us"]}', flush=True)
        results.append(r)
        (root / 'results.json').write_text(json.dumps(results, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
