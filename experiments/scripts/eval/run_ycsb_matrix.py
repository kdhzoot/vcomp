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
from ch23_common import BASE, command, stage_db, db_identity, deep_copy_db   # noqa: E402

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
    bench = workload + (',waitforcompaction' if FIXED_OPS else '') + ',stats,levelstats'
    return dict(BASE, num=num, key_size=key, value_size=value, db=str(db),
                benchmarks=bench,
                use_existing_db=True, readonly=False,
                disable_auto_compactions=False, memtablerep='skip_list',
                threads=48, duration=(0 if FIXED_OPS else duration),
                # fixed-work: --reads는 스레드당 op 수(ycsb_operationcount_) → 총 FIXED_OPS
                reads=((FIXED_OPS + 47) // 48 if FIXED_OPS else 10000000000),
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


COPY_MODE = 'hardlink'
DROP_CACHES = False
FIXED_OPS = 0


def run_cell(src, staging, out, workload, duration, cache, num, key, value, dist):
    out.mkdir(parents=True, exist_ok=True)
    if staging.exists():
        shutil.rmtree(staging)
    copy_secs = None
    dropped = False
    if COPY_MODE == 'deep':
        identity, copy_secs = deep_copy_db(src, staging)
    else:
        identity = stage_db(src, staging)
    if DROP_CACHES:
        # 딥카피/링크가 남긴 page cache를 비운 상태에서 측정. SST 읽기는 O_DIRECT라
        # 결과에 직접 영향은 없지만 dirty page·메모리 압력 변수를 제거한다.
        subprocess.run(['sync'], check=False)
        dropped = subprocess.run('echo 3 | sudo -n tee /proc/sys/vm/drop_caches >/dev/null',
                                 shell=True).returncode == 0
    argv = command(PROFILER, options(staging, out, workload, duration, cache,
                                     num, key, value, dist))
    (out / 'argv.json').write_text(json.dumps(argv, indent=1))
    start = time.time()
    # 러너가 stdout을 폴링해 워크로드 종료/drain 종료 시각을 기록한다
    # (db_bench의 waitforcompaction은 인라인 실행이라 자체 시간 보고가 없음).
    marks = {}
    with (out / 'stdout_stderr.log').open('wb') as f:
        proc = subprocess.Popen(argv, stdout=f, stderr=subprocess.STDOUT)
        seen = 0
        while True:
            rc = proc.poll()
            try:
                data = (out / 'stdout_stderr.log').read_bytes()
            except OSError:
                data = b''
            if len(data) > seen:
                chunk = data[seen:].decode(errors='replace'); seen = len(data)
                if 'run_end' not in marks and re.search(workload + r'\s*:\s*[\d.]+\s*micros/op', chunk):
                    marks['run_end'] = time.time()
                if 'drain_end' not in marks and 'waitforcompaction(' in chunk and 'finished' in chunk:
                    marks['drain_end'] = time.time()
            if rc is not None:
                break
            time.sleep(0.2)
    # staging의 RocksDB LOG를 보존 (compaction 타임라인 분석용)
    try:
        shutil.copy2(str(staging / 'LOG'), str(out / 'LOG'))
    except OSError:
        pass
    text = (out / 'stdout_stderr.log').read_text(errors='replace')
    c = counters(text)
    m = re.search(workload + r'\s*:\s*([\d.]+)\s*micros/op\s*(\d+)\s*ops/sec', text)
    m_run = re.search(workload + r'\s*:\s*[\d.]+\s*micros/op\s*\d+\s*ops/sec\s*([\d.]+)\s*seconds\s*(\d+)\s*operations', text)
    m_drain = re.search(r'waitforcompaction\s*:\s*[\d.]+\s*micros/op\s*\d+\s*ops/sec\s*([\d.]+)\s*seconds', text)
    run_s = float(m_run.group(1)) if m_run else None
    ops_total = int(m_run.group(2)) if m_run else None
    drain_s = float(m_drain.group(1)) if m_drain else None
    if drain_s is None and 'run_end' in marks and 'drain_end' in marks:
        drain_s = round(marks['drain_end'] - marks['run_end'], 1)   # 러너 폴링 기준(±0.2 s)
    total_s = (run_s + drain_s) if (run_s is not None and drain_s is not None) else run_s
    pct = re.search(r'Percentiles:\s*P50:\s*([\d.]+)\s*P75:\s*([\d.]+)\s*'
                    r'P99:\s*([\d.]+)\s*P99\.9:\s*([\d.]+)', text)
    reads = c.get('rocksdb.number.keys.read', 0)
    probes = (c.get('rocksdb.bloom.filter.useful', 0) +
              c.get('rocksdb.bloom.filter.full.positive', 0))
    # positive lookup: RocksDB가 어느 레벨에서든 key를 찾은 횟수 / Get 호출 수.
    # (number.keys.read는 Get 호출 수이므로 그 자체는 found 비율이 아님)
    found_keys = sum(c.get('rocksdb.' + k, 0)
                     for k in ('memtable.hit', 'l0.hit', 'l1.hit', 'l2andup.hit'))
    gets = c.get('rocksdb.number.keys.read', 0)

    res = dict(source=str(src), copy_mode=COPY_MODE, copy_seconds=copy_secs, dropped_caches=dropped,
               fixed_ops=FIXED_OPS or None, ops_total=ops_total, run_seconds=run_s, drain_seconds=drain_s,
               total_seconds_incl_drain=total_s,
               ops_per_sec_incl_drain=(ops_total / total_s if (ops_total and total_s) else None), found_keys=found_keys, gets=gets,
               positive_lookup_pct=(100.0 * found_keys / gets if gets else None), workload=workload, distribution=dist, exit_code=rc,
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
    p.add_argument('--fixed-ops', type=int, default=0,
                   help='총 op 수 고정(fixed-work). >0이면 duration=0, reads=총/48, 벤치에 waitforcompaction 추가(drain 포함 시간 기록)')
    p.add_argument('--drop-caches', action='store_true',
                   help='staging 뒤 sync + echo 3 > /proc/sys/vm/drop_caches (sudo -n 필요)')
    p.add_argument('--copy-mode', default='hardlink', choices=['hardlink', 'deep'],
                   help='deep: SST를 실제 복사(병렬 cp) — 물리 배치 차이 제거; 셀 종료 후 삭제')
    a = p.parse_args()
    global COPY_MODE, DROP_CACHES, FIXED_OPS; COPY_MODE = a.copy_mode; DROP_CACHES = a.drop_caches; FIXED_OPS = a.fixed_ops
    root = ART / a.run_id
    root.mkdir(parents=True, exist_ok=True)
    (root / 'manifest.json').write_text(json.dumps(dict(
        created=now(), binary=str(PROFILER), binary_sha256=sha(PROFILER),
        workloads=a.workloads, duration=a.duration, cache_gib=a.cache_gib, fixed_ops=a.fixed_ops, copy_mode=a.copy_mode, drop_caches=a.drop_caches,
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
