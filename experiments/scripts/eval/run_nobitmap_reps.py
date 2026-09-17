#!/usr/bin/env python3
"""no-bitmap F2Load 12점 x N회 반복: 점 하나씩 로딩 -> YCSB C(zipfian) -> 결과 수집 -> DB 삭제.

세션이 끊겨도 살아남도록 setsid + nohup 으로 띄운다. 이미 결과가 있는 셀은 건너뛰므로
죽었다 다시 띄우면 이어서 진행한다.
"""
import argparse, json, os, shutil, subprocess, sys, time
from pathlib import Path

REPO = Path('/home/smrc/virtual_compaction/vcomp')
ART = REPO / 'experiments/artifacts'
TRACE = '/work/vcomp/exp/eval_20260909_night1/traces'
NB = dict(vcomp_exact_membership=False, vcomp_global_unique_keys=False,
          vcomp_global_unique_keys_deep_first=False)

# (point, target_gib, key, value, num, overrides)  -- 과거 no-bitmap 캠페인 manifest 그대로
POINTS = [
    ('E1-100B',    98,  24,    76, 1048576000, dict(NB, num=1048576000)),
    ('E3-u25',   1000,  24,  1000, 1048576000, dict(NB, load_trace_file=f'{TRACE}/e3_u25.vload')),
    ('E7-lz4',   1000,  24,  1000, 1048576000, dict(NB, compression_type='lz4')),
    ('E3-u50',   1000,  24,  1000, 1048576000, dict(NB, load_trace_file=f'{TRACE}/e3_u50.vload')),
    ('E4-256m4', 1000,  24,  1000, 1048576000, dict(NB, max_bytes_for_level_base=268435456,
                                                   max_bytes_for_level_multiplier=4)),
    ('E4-1024m10',1000, 24,  1000, 1048576000, dict(NB, max_bytes_for_level_base=1073741824,
                                                   max_bytes_for_level_multiplier=10)),
    ('P0fix-r1', 1000,  24,  1000, 1048576000, dict(NB)),
    ('E3-u100',  1000,  24,  1000, 1048576000, dict(NB, load_trace_file=f'{TRACE}/e3_u100.vload',
                                                   vcomp_global_unique_keys=True,
                                                   vcomp_global_unique_keys_deep_first=True,
                                                   vcomp_phase1_shards=32)),
    ('E2-2T',    2000,  24,  1000, 2097152000, dict(NB)),
    ('E2-4T',    4000,  24,  1000, 4194304000, dict(NB)),
    ('E2-8T',    8000,  24,  1000, 8388608000, dict(NB)),
    ('E1-10KB', 10000,  24, 10216, 1048576000, dict(NB, num=1048576000)),
]

def now(): return time.strftime('%Y-%m-%dT%H:%M:%S%z')

def log(f, msg):
    line = f'[{now()}] {msg}'
    print(line, flush=True)
    with open(f, 'a') as fh: fh.write(line + '\n')

def stub_binary(run_id):
    """run_eval_campaign 이 db_bench 를 385 MB씩 복사하지 않도록 심볼릭 링크를 미리 놓는다."""
    b = ART / run_id / 'bin'
    b.mkdir(parents=True, exist_ok=True)
    t = b / 'f2_db_bench'
    if not t.exists():
        t.symlink_to(REPO / 'db_bench')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--reps', type=int, default=5)
    ap.add_argument('--run-id', default='nobitmap_reps_20260916')
    ap.add_argument('--db-root', default='/work/vcomp/exp/nbreps_work')
    ap.add_argument('--duration', type=int, default=300)
    ap.add_argument('--env', action='append', default=[], help='로딩 env KEY=VAL')
    a = ap.parse_args()

    out = ART / a.run_id
    out.mkdir(parents=True, exist_ok=True)
    LOG = out / 'driver.log'
    dbroot = Path(a.db_root); dbroot.mkdir(parents=True, exist_ok=True)

    (out / 'plan.json').write_text(json.dumps(dict(
        created=now(), reps=a.reps, duration=a.duration, env=a.env, db_root=str(dbroot),
        points=[dict(point=p, target_gib=g, key_size=k, value_size=v, num=n, overrides=o)
                for p, g, k, v, n, o in POINTS]), indent=2))

    log(LOG, f'START reps={a.reps} points={len(POINTS)} env={a.env or "(기본)"}')
    for rep in range(1, a.reps + 1):
        for point, gib, key, value, num, ov in POINTS:
            cell = out / f'rep{rep}' / point
            if (cell / 'ycsb.json').exists():
                log(LOG, f'SKIP rep{rep}/{point} (이미 완료)'); continue
            cell.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(dbroot).free
            log(LOG, f'BEGIN rep{rep}/{point}  free={free/1e12:.2f} TB')

            load_id = f'{a.run_id}_r{rep}_{point}_load'
            ycsb_id = f'{a.run_id}_r{rep}_{point}_ycsbc'
            stub_binary(load_id)
            qf = cell / 'queue.json'
            qf.write_text(json.dumps([[point, 'f2load', gib, key, value, None, ov]]))

            t0 = time.time()
            cmd = [sys.executable, str(REPO / 'experiments/scripts/eval/run_eval_campaign.py'),
                   '--run-id', load_id, '--db-root', str(dbroot), '--execute',
                   '--queue-file', str(qf)]
            for kv in a.env: cmd += ['--env', kv]
            rc = subprocess.run(cmd, cwd=REPO).returncode
            load_s = time.time() - t0
            db = dbroot / f'{point}_f2load'
            if rc != 0 or not (db / 'CURRENT').exists():
                log(LOG, f'LOAD-FAIL rep{rep}/{point} rc={rc} — 건너뜀')
                shutil.rmtree(db, ignore_errors=True)
                (cell / 'load_failed.json').write_text(json.dumps(dict(rc=rc, seconds=load_s)))
                continue
            db_bytes = sum(f.stat().st_size for f in db.iterdir() if f.is_file())
            log(LOG, f'LOADED rep{rep}/{point} {load_s:.1f}s  db={db_bytes/1e12:.2f} TB')

            t1 = time.time()
            rc2 = subprocess.run(
                [sys.executable, str(REPO / 'experiments/scripts/eval/run_ycsb_matrix.py'),
                 '--run-id', ycsb_id, '--db-root', str(dbroot),
                 '--dbs', f'{point}_f2load:{num}:{key}:{value}',
                 '--workloads', 'c', '--distribution', 'zipfian',
                 '--duration', str(a.duration), '--cache-gib', '50',
                 '--copy-mode', 'hardlink'], cwd=REPO).returncode
            ycsb_s = time.time() - t1

            src = ART / ycsb_id / f'{point}_f2load__workloadc'
            res = {}
            if (src / 'result.json').exists():
                res = json.loads((src / 'result.json').read_text())
                for name in ('result.json', 'stdout_stderr.log', 'argv.json'):
                    if (src / name).exists(): shutil.copy2(src / name, cell / name)
            lsrc = ART / load_id / f'{point}_f2load' / 'stdout_stderr.log'
            if lsrc.exists(): shutil.copy2(lsrc, cell / 'load_stdout_stderr.log')

            (cell / 'ycsb.json').write_text(json.dumps(dict(
                rep=rep, point=point, load_seconds=round(load_s, 1), ycsb_seconds=round(ycsb_s, 1),
                db_bytes=db_bytes, load_rc=rc, ycsb_rc=rc2, finished_at=now(),
                ops_per_sec=res.get('ops_per_sec'),
                filter_probes_per_read=res.get('filter_probes_per_read'),
                positive_lookup_pct=res.get('positive_lookup_pct'),
                p50_us=res.get('p50_us'), p99_us=res.get('p99_us'), p999_us=res.get('p999_us'),
                source_identity_ssts=res.get('source_identity_ssts')), indent=1))
            log(LOG, f'DONE rep{rep}/{point} ycsb={ycsb_s:.1f}s '
                     f'ops={res.get("ops_per_sec")} fpr={res.get("filter_probes_per_read")} '
                     f'pos={res.get("positive_lookup_pct")}')

            shutil.rmtree(db, ignore_errors=True)
            shutil.rmtree(ART / ycsb_id / f'{point}_f2load__workloadc' / 'report.rep',
                          ignore_errors=True)
            log(LOG, f'PURGED rep{rep}/{point}  free={shutil.disk_usage(dbroot).free/1e12:.2f} TB')
        log(LOG, f'=== rep{rep} 완료 ===')
    log(LOG, 'ALL DONE')

if __name__ == '__main__':
    main()
