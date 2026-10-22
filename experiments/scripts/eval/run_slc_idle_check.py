#!/usr/bin/env python3
"""SLC 캐시 확인: TRIM 직후 F2Load로 로딩한 DB를, 시간 간격을 두고 YCSB C로 여러 번 읽는다.

가설: TRIM 직후 비어 있는 SSD에 F2Load가 DB 전체(드라이브당 약 200 GB)를 수십 초 만에 쓰면
데이터가 SLC 캐시(990 PRO 2TB: 드라이브당 최대 226 GB)에 남고, 그래서 바로 이어지는
YCSB C의 파일 읽기가 빠르다(Get 파일 읽기 p50 약 61 us vs baseline 115 us).
SSD는 한가할 때 SLC 데이터를 TLC로 옮기므로, 로딩 뒤 아무것도 하지 않고 기다렸다 다시 읽으면
파일 읽기 시간이 baseline 쪽으로 올라가야 한다.

절차: TRIM(run_loadskew_reps.py와 같은 방식) -> F2Load 로딩 1회 -> C 즉시 ->
[대기 -> C]를 --idle-min 목록대로 반복 -> DB 삭제. A처럼 쓰기가 섞인 워크로드는 돌리지 않는다.
"""
import argparse, json, os, re, shutil, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_loadskew_reps as R   # noqa: E402  (helpers: TRIM, NUSE, trace, campaign queue)

REPO, ART = R.REPO, R.ART


def stat(text, name):
    m = re.findall(rf'rocksdb\.{re.escape(name)}\.micros P50 : ([\d.]+) P95 : ([\d.]+) '
                   rf'P99 : ([\d.]+) P100 : ([\d.]+) COUNT : (\d+) SUM : (\d+)', text)
    if not m:
        return None
    p50, p95, p99, p100, n, s = m[-1]
    return dict(p50=float(p50), p95=float(p95), p99=float(p99), count=int(n),
                avg=int(s) / int(n) if int(n) else None)


def level_avg(text):
    last = text.rsplit('** File Read Latency Histogram By Level', 1)[-1]
    return {int(L): float(av) for L, c, av in
            re.findall(r'Level (\d+) read latency histogram \(micros\):\s*\nCount: (\d+) Average: ([\d.]+)', last)}


def trim(a, nvme, log):
    attempts = []
    for attempt in range(3):
        os.sync(); time.sleep(15)
        t = R.fstrim(a.fstrim)
        t0 = time.time(); prev = R.ssd_used_bytes(nvme)
        while prev is not None and time.time() - t0 < a.trim_settle_max:
            time.sleep(30)
            cur = R.ssd_used_bytes(nvme)
            if cur is None or prev - cur < a.trim_settle_eps_gb * 1e9:
                prev = cur
                break
            prev = cur
        t.update(settle_seconds=round(time.time() - t0, 1), ssd_used_after=prev,
                 fs_used=shutil.disk_usage(a.fstrim).used)
        attempts.append(t)
        log(f"FSTRIM try{attempt + 1} rc={t['rc']} trimmed={(t['trimmed_bytes'] or 0)/1e12:.2f} TB, "
            f"settled {t['settle_seconds']}s, ssd_used={'n/a' if prev is None else f'{prev/1e12:.2f} TB'} "
            f"fs_used={t['fs_used']/1e12:.2f} TB")
        if prev is None or prev - t['fs_used'] < 0.2e12:
            break
    return attempts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--alpha', type=float, default=0.0)
    ap.add_argument('--mode', default='f2load_nobitmap', choices=sorted(R.MODES))
    ap.add_argument('--target-gib', type=int, default=1000)
    ap.add_argument('--db-root', required=True)
    ap.add_argument('--binary', required=True)
    ap.add_argument('--load-override', action='append', default=[])
    ap.add_argument('--threads', type=int, default=16)
    ap.add_argument('--cache-gib', type=int, default=50)
    ap.add_argument('--duration', type=int, default=300)
    ap.add_argument('--idle-min', default='30,30,60',
                    help='C 사이에 아무것도 하지 않고 기다리는 분. 첫 C는 로딩 직후')
    ap.add_argument('--fstrim', default='/work')
    ap.add_argument('--trim-settle-max', type=int, default=900)
    ap.add_argument('--trim-settle-eps-gb', type=float, default=10.0)
    ap.add_argument('--nvme', default='nvme0n1,nvme1n1,nvme2n1,nvme3n1')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    out = ART / a.run_id
    out.mkdir(parents=True, exist_ok=True)
    LOG = out / 'driver.log'
    log = lambda m: R.log(LOG, m)
    nvme = [d for d in a.nvme.split(',') if d]
    binary = Path(a.binary).resolve()
    idle = [int(x) for x in a.idle_min.split(',') if x]
    load_ov = dict(R.parse_override(kv) for kv in a.load_override)
    num = a.target_gib * (1 << 30) // (R.KEY_SIZE + R.VALUE_SIZE)
    dbroot = Path(a.db_root)
    dbroot.mkdir(parents=True, exist_ok=True)
    tr, meta = R.ensure_trace(a.target_gib, a.alpha, LOG)
    point = R.alpha_tag(a.alpha)
    campaign_mode, mode_ov = R.MODES[a.mode]
    ov = dict(mode_ov, load_trace_file=str(tr), max_background_jobs=a.threads,
              vcomp_materialize_workers=a.threads, **load_ov)
    plan = dict(created=R.now(), alpha=a.alpha, mode=a.mode, target_gib=a.target_gib,
                binary=str(binary),
                binary_sha256=subprocess.check_output(['sha256sum', str(binary)], text=True).split()[0],
                load_overrides=load_ov, threads=a.threads, cache_gib=a.cache_gib, duration=a.duration,
                idle_min=idle, fstrim=a.fstrim, ycsb_binary=os.environ.get('VCOMP_YCSB_BINARY'))
    (out / 'plan.json').write_text(json.dumps(plan, indent=1))
    log(f'START alpha={a.alpha} mode={a.mode} idle_min={idle} threads={a.threads} binary_sha={plan["binary_sha256"][:12]}')
    if a.dry_run:
        log(f'DRY-RUN queue ov={ov}')
        return

    trims = trim(a, nvme, log) if a.fstrim else []
    load_id = f'{a.run_id}_load'
    shutil.rmtree(ART / load_id, ignore_errors=True)
    R.stub_binary(load_id, binary)
    qf = out / 'queue.json'
    qf.write_text(json.dumps([[f'{point}_{a.mode}', campaign_mode, a.target_gib, R.KEY_SIZE, R.VALUE_SIZE, None, ov]]))
    ssd_before = R.ssd_used_bytes(nvme)
    t0 = time.time()
    rc = subprocess.run([sys.executable, str(REPO / 'experiments/scripts/eval/run_eval_campaign.py'),
                         '--run-id', load_id, '--db-root', str(dbroot), '--execute', '--queue-file', str(qf)],
                        cwd=REPO).returncode
    load_s = time.time() - t0
    t_loaded = time.time()
    db = dbroot / f'{point}_{a.mode}_{campaign_mode}'
    if rc != 0 or not (db / 'CURRENT').exists():
        log(f'LOAD-FAIL rc={rc}')
        shutil.rmtree(db, ignore_errors=True)
        return
    lsrc = ART / load_id / db.name / 'stdout_stderr.log'
    if lsrc.exists():
        shutil.copy2(lsrc, out / 'load_stdout_stderr.log')
    db_bytes = sum(f.stat().st_size for f in db.iterdir() if f.is_file())
    ssd_after = R.ssd_used_bytes(nvme)
    log(f'LOADED {load_s:.1f}s db={db_bytes/1e12:.2f} TB ssd_used before={ssd_before/1e12:.2f} TB after={ssd_after/1e12:.2f} TB')

    runs = []
    for i, wait in enumerate([0] + idle):
        if wait:
            log(f'IDLE {wait} min')
            time.sleep(wait * 60)
        since = (time.time() - t_loaded) / 60
        ssd = R.ssd_used_bytes(nvme)
        ycsb_id = f'{a.run_id}_c{i}'
        shutil.rmtree(ART / ycsb_id, ignore_errors=True)
        log(f'C{i} start ({since:.1f} min after load)')
        rc2 = subprocess.run([sys.executable, str(REPO / 'experiments/scripts/eval/run_ycsb_matrix.py'),
                              '--run-id', ycsb_id, '--db-root', str(dbroot),
                              '--dbs', f'{db.name}:{num}:{R.KEY_SIZE}:{R.VALUE_SIZE}',
                              '--workloads', 'c', '--distribution', 'zipfian', '--duration', str(a.duration),
                              '--cache-gib', str(a.cache_gib), '--threads', str(a.threads),
                              '--copy-mode', 'hardlink'], cwd=REPO).returncode
        src = ART / ycsb_id / f'{db.name}__workloadc'
        wd = out / f'c{i}'
        wd.mkdir(exist_ok=True)
        for name in ('result.json', 'stdout_stderr.log', 'argv.json'):
            if (src / name).exists():
                shutil.copy2(src / name, wd / name)
        shutil.rmtree(src / 'report.rep', ignore_errors=True)
        res = json.loads((wd / 'result.json').read_text()) if (wd / 'result.json').exists() else {}
        text = (wd / 'stdout_stderr.log').read_text(errors='replace') if (wd / 'stdout_stderr.log').exists() else ''
        g = stat(text, 'read.block.get')
        run = dict(i=i, idle_before_min=wait, minutes_after_load=round(since, 1), rc=rc2,
                   ssd_used=ssd, ops_per_sec=res.get('ops_per_sec'), p99_us=res.get('p99_us'),
                   get_file_read=g, level_avg_us=level_avg(text))
        runs.append(run)
        gs = 'n/a' if not g else f"p50={g['p50']:.1f} p99={g['p99']:.1f} avg={g['avg']:.1f} us"
        log(f"C{i} done ops={run['ops_per_sec']} get_file_read {gs}  L5 avg={run['level_avg_us'].get(5)}")
        (out / 'result.json').write_text(json.dumps(dict(
            plan=plan, fstrim=trims, load_seconds=round(load_s, 1), db_bytes=db_bytes,
            ssd_used_before_load=ssd_before, ssd_used_after_load=ssd_after, runs=runs), indent=1))

    shutil.rmtree(db, ignore_errors=True)
    log(f'PURGED free={shutil.disk_usage(dbroot).free/1e12:.2f} TB')
    log('ALL DONE')


if __name__ == '__main__':
    main()
