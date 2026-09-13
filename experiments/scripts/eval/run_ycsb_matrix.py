#!/usr/bin/env python3
"""YCSB A~F 매트릭스. 원본 DB는 하드링크 staging 으로 보존한다.

옵션은 ch23_common.BASE + run_ycsb_alternatives.options() 구성을 그대로 쓴다.
쓰기가 섞인 워크로드(A/B/D/E/F)는 DB를 변형하므로 셀마다 새로 staging 하고
측정이 끝나면 staging 사본만 지운다(원본 SST 는 하드링크라 그대로 남는다).
"""
import argparse, datetime, hashlib, json, re, shutil, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'experiments/lib'))
from ch23_common import BASE, command, stage_db, db_identity   # noqa: E402

ART = REPO / 'experiments/artifacts'
PROFILER = REPO.parent / 'vcomp-prof' / 'db_bench'
GIB = 1024 ** 3


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for c in iter(lambda: f.read(8 << 20), b''):
            h.update(c)
    return h.hexdigest()


def counters(text):
    c = {m.group(1): int(m.group(2))
         for m in re.finditer(r'^(rocksdb\.[\w.]+) COUNT : (\d+)', text, re.M)}
    for m in re.finditer(r'^(rocksdb\.[\w.]+) P50 : [\d.]+.*?COUNT : (\d+) SUM : (\d+)',
                         text, re.M):
        c[m.group(1) + '.count'] = int(m.group(2))
    return c


def options(db, log, workload, duration, cache, num, key, value, dist):
    return dict(BASE, num=num, key_size=key, value_size=value, db=str(db),
                benchmarks=workload + ',stats,levelstats',
                use_existing_db=True, readonly=False,
                disable_auto_compactions=False, memtablerep='skip_list',
                threads=48, duration=duration, reads=10000000000,
                seed=87654321, ops_between_duration_checks=1,
                cache_type='lru_cache', cache_size=cache,
                cache_index_and_filter_blocks=True,
                pin_l0_filter_and_index_blocks_in_cache=False,
                pin_top_level_index_and_filter=False,
                open_files=-1, stats_level=3, histogram=True, perf_level=3,
                report_interval_seconds=1, report_file=str(log / 'report.rep'),
                stats_interval_seconds=30, stats_per_interval=1,
                ycsb_requestdistribution=dist or
                ('latest' if workload == 'workloadd' else 'zipfian'),
                ycsb_minscanlength=1, ycsb_maxscanlength=100,
                ycsb_scanlengthdistribution='uniform')


def run_cell(src, staging, out, workload, duration, cache, num, key, value, dist):
    out.mkdir(parents=True, exist_ok=True)
    if staging.exists():
        shutil.rmtree(staging)
    identity = stage_db(src, staging)
    argv = command(PROFILER, options(staging, out, workload, duration, cache,
                                     num, key, value, dist))
    (out / 'argv.json').write_text(json.dumps(argv, indent=1))
    start = time.time()
    with (out / 'stdout_stderr.log').open('wb') as f:
        rc = subprocess.call(argv, stdout=f, stderr=subprocess.STDOUT)
    text = (out / 'stdout_stderr.log').read_text(errors='replace')
    c = counters(text)
    m = re.search(workload + r'\s*:\s*([\d.]+)\s*micros/op\s*(\d+)\s*ops/sec', text)
    pct = re.search(r'Percentiles:\s*P50:\s*([\d.]+)\s*P75:\s*([\d.]+)\s*'
                    r'P99:\s*([\d.]+)\s*P99\.9:\s*([\d.]+)', text)
    reads = c.get('rocksdb.number.keys.read', 0)
    probes = (c.get('rocksdb.bloom.filter.useful', 0) +
              c.get('rocksdb.bloom.filter.full.positive', 0))
    res = dict(source=str(src), workload=workload, distribution=dist, exit_code=rc,
               elapsed_sec=time.time() - start, cache_bytes=cache, num_keys=num,
               finished_at=now(),
               ops_per_sec=int(m.group(2)) if m else None,
               micros_per_op=float(m.group(1)) if m else None,
               p50_us=float(pct.group(1)) if pct else None,
               p99_us=float(pct.group(3)) if pct else None,
               p999_us=float(pct.group(4)) if pct else None,
               keys_read=reads, keys_written=c.get('rocksdb.number.keys.written', 0),
               filter_probes=probes,
               filter_probes_per_read=probes / reads if reads else None,
               bloom_full_positive=c.get('rocksdb.bloom.filter.full.positive', 0),
               bloom_true_positive=c.get('rocksdb.bloom.filter.full.true.positive', 0),
               compact_write_bytes=c.get('rocksdb.compact.write.bytes', 0),
               compact_read_bytes=c.get('rocksdb.compact.read.bytes', 0),
               flush_write_bytes=c.get('rocksdb.flush.write.bytes', 0),
               bytes_read=c.get('rocksdb.bytes.read', 0),
               bytes_written=c.get('rocksdb.bytes.written', 0),
               sst_read_count=c.get('rocksdb.sst.read.micros.count', 0),
               source_identity_ssts=len(identity.get('ssts', [])))
    (out / 'result.json').write_text(json.dumps(res, indent=1))
    shutil.rmtree(staging, ignore_errors=True)     # staging 사본만 제거
    return res


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-id', required=True)
    p.add_argument('--db-root', default='/work/vcomp/exp/eval_20260909_night1')
    p.add_argument('--staging-root', default='/work/vcomp/exp/ycsb_staging')
    p.add_argument('--dbs', required=True,
                   help='이름:num:key:value 목록, 쉼표 구분 (key/value 생략 시 24/1000)')
    p.add_argument('--workloads', default='abcdef')
    p.add_argument('--duration', type=int, default=300)
    p.add_argument('--cache-gib', type=int, default=50)
    p.add_argument('--distribution', default=None)
    a = p.parse_args()
    root = ART / a.run_id
    root.mkdir(parents=True, exist_ok=True)
    (root / 'manifest.json').write_text(json.dumps(dict(
        created=now(), binary=str(PROFILER), binary_sha256=sha(PROFILER),
        workloads=a.workloads, duration=a.duration, cache_gib=a.cache_gib,
        staging='hard link (ch23_common.stage_db); 원본 보존',
        base_options=BASE), indent=2, default=str))
    results = []
    for spec in a.dbs.split(','):
        parts = spec.split(':')
        name = parts[0]
        num = int(parts[1]) if len(parts) > 1 and parts[1] else 1048576000
        key = int(parts[2]) if len(parts) > 2 and parts[2] else 24
        value = int(parts[3]) if len(parts) > 3 and parts[3] else 1000
        src = Path(a.db_root) / name
        if not src.exists():
            print(f'  없음: {src}', flush=True); continue
        for letter in a.workloads:
            workload = 'workload' + letter
            out = root / f'{name}__{workload}'
            if (out / 'result.json').exists():
                results.append(json.loads((out / 'result.json').read_text()))
                print(f'  건너뜀: {name} {workload}', flush=True); continue
            print(f'[{now()}] {name} {workload} 시작', flush=True)
            r = run_cell(src, Path(a.staging_root) / f'{name}__{workload}', out,
                         workload, a.duration, a.cache_gib * GIB, num, key, value,
                         a.distribution)
            print(f'[{now()}] {name} {workload}: {r["ops_per_sec"]} ops/s, '
                  f'compaction write {r["compact_write_bytes"]/1e9:.1f} GB', flush=True)
            results.append(r)
            (root / 'results.json').write_text(json.dumps(results, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
