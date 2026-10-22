#!/usr/bin/env python3
"""로딩 키 분포 skew 스윕: 기본 설정에서 zipf alpha만 바꿔 baseline/F2Load fidelity를 비교한다.

지금까지의 chapter 3 격자는 로딩 키를 uniform으로만 만들었다(`fillrandom`, 또는
`zipf_alpha=0` 트레이스). 이 러너는 나머지를 전부 고정한 채 `--zipf-alpha`만 바꾼
로딩 트레이스를 만들고, 각 alpha에서 baseline과 F2Load를 각각 N회 로딩해 YCSB A/C를
측정한다.

단일 변수 조작임을 생성기 수준에서 보장한다: unique_ratio와 seed가 같으면 트레이스의
distinct key 집합은 alpha와 무관하게 동일하고(affine 매핑이 seed에만 의존), 달라지는
것은 각 키가 몇 번 쓰이는지의 분포뿐이다.

프로토콜은 `run_nobitmap_reps.py`와 같다 -- 한 셀씩 로딩 -> 즉시 측정 -> 결과 수집 ->
DB 삭제. 따라서 디스크에는 항상 DB 하나만 존재한다. 이전 캠페인과 달리 baseline과
F2Load를 같은 rep 안에서 인접하게 돌려서, 머신 상태 변화가 두 arm에 같이 실리도록 한다.

세션이 끊겨도 살아남도록 setsid + nohup으로 띄운다. 이미 `ycsb.json`이 있는 셀은
건너뛰므로 죽었다 다시 띄우면 이어서 진행한다.
"""
import argparse, json, os, re, shutil, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ART = REPO / 'experiments/artifacts'
TRACES = Path(os.environ.get('VCOMP_TRACE_ROOT', '/work/vcomp/load_traces'))

# no-bitmap F2Load 구성 -- chapter 3 격자가 쓰는 것과 같다.
NB = dict(vcomp_exact_membership=False, vcomp_global_unique_keys=False,
          vcomp_global_unique_keys_deep_first=False)

# --modes 이름 -> (run_eval_campaign의 mode, 로딩 override).
# 'f2load'는 이전 캠페인과의 호환을 위해 no-bitmap을 뜻한다.
MODES = {
    'baseline': ('baseline', NB),
    'f2load': ('f2load', NB),
    'f2load_nobitmap': ('f2load', NB),
    'f2load_plan': ('f2load', dict(NB, vcomp_exact_membership=True,
                                   vcomp_membership_plan=True)),
    'f2load_noplan': ('f2load', dict(NB, vcomp_exact_membership=True,
                                     vcomp_membership_plan=False)),
}

# 기본 설정 = P0 지점. 여기서 zipf alpha만 움직인다.
KEY_SIZE, VALUE_SIZE = 24, 1000
UNIQUE_RATIO = 0.6321205588285577   # 1 - exp(-1), fillrandom의 기대 unique 비율
TRACE_SEED = 12345678


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def log(f, msg):
    line = f'[{now()}] {msg}'
    print(line, flush=True)
    with open(f, 'a') as fh:
        fh.write(line + '\n')


def alpha_tag(alpha):
    return f'a{alpha:g}'.replace('.', 'p')


def trace_path(gib, alpha):
    return TRACES / f'skew_{gib}gib_{KEY_SIZE}x{VALUE_SIZE}_{alpha_tag(alpha)}_s{TRACE_SEED}.vload'


def ensure_trace(gib, alpha, logfile):
    """alpha별 로딩 트레이스를 만든다(이미 있으면 재사용). 사이드카로 검증한다."""
    out = trace_path(gib, alpha)
    meta = Path(str(out) + '.meta.json')
    if out.exists() and meta.exists():
        d = json.loads(meta.read_text())
        want = out.stat().st_size == d['header_size'] + d['record_bytes'] * d['num_records']
        if want and abs(d['zipf_alpha'] - alpha) < 1e-12:
            log(logfile, f'TRACE reuse {out.name} ({d["num_records"]:,} recs, '
                         f'unique={d["unique_count"]:,})')
            return out, d
        log(logfile, f'TRACE stale {out.name} — 다시 만든다')
    log(logfile, f'TRACE build {out.name} alpha={alpha}')
    t0 = time.time()
    rc = subprocess.run([sys.executable, str(REPO / 'tools/generate_load_trace.py'), str(out),
                         '--target-db-gb', str(gib), '--key-size', str(KEY_SIZE),
                         '--value-size', str(VALUE_SIZE), '--unique-ratio', repr(UNIQUE_RATIO),
                         '--zipf-alpha', repr(alpha), '--seed', str(TRACE_SEED),
                         '--force'], cwd=REPO).returncode
    if rc != 0:
        raise RuntimeError(f'trace 생성 실패 alpha={alpha} rc={rc}')
    d = json.loads(meta.read_text())
    log(logfile, f'TRACE done  {out.name} {time.time()-t0:.0f}s '
                 f'{out.stat().st_size/1e9:.1f} GB unique={d["unique_count"]:,}')
    return out, d


def ssd_used_bytes(devs):
    """NVMe namespace utilization (NUSE): LBAs the drives still hold as written,
    i.e. not deallocated by TRIM. None if any device cannot be read."""
    total = 0
    for d in devs:
        try:
            out = subprocess.run(['sudo', '-n', 'nvme', 'id-ns', f'/dev/{d}'], capture_output=True,
                                 text=True, timeout=30).stdout
            nuse = next(int(l.split(':')[1].strip(), 0) for l in out.splitlines() if l.startswith('nuse'))
        except Exception:
            return None
        total += nuse * 512
    return total


def fstrim(target):
    t0 = time.time()
    r = subprocess.run(['sudo', '-n', 'fstrim', '-v', target], capture_output=True, text=True, timeout=1800)
    m = re.search(r'\((\d+) bytes\) trimmed', r.stdout)
    return dict(rc=r.returncode, trimmed_bytes=int(m.group(1)) if m else None,
                seconds=round(time.time() - t0, 1), stdout=r.stdout.strip(), stderr=r.stderr.strip())


def parse_override(kv):
    k, v = kv.split('=', 1)
    if v.lower() in ('true', 'false'):
        return k, v.lower() == 'true'
    try:
        return k, int(v)
    except ValueError:
        return k, v


def stub_binary(run_id, binary):
    """run_eval_campaign이 db_bench를 셀마다 385 MB씩 복사하지 않도록 링크를 미리 둔다."""
    b = ART / run_id / 'bin'
    b.mkdir(parents=True, exist_ok=True)
    t = b / 'f2_db_bench'
    if not t.exists():
        t.symlink_to(binary)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--reps', type=int, default=5)
    ap.add_argument('--alphas', default='0,0.99',
                    help='쉼표 구분 zipf alpha. 0은 uniform 대조군이며 반드시 포함할 것')
    ap.add_argument('--target-gib', type=int, default=1000)
    ap.add_argument('--run-id', default='loadskew_reps')
    ap.add_argument('--db-root', default='/work/vcomp/exp/skew_work')
    ap.add_argument('--duration', type=int, default=300)
    ap.add_argument('--workloads', default='ac')
    ap.add_argument('--cache-gib', type=int, default=50)
    ap.add_argument('--threads', type=int, default=48,
                    help='YCSB 측정 스레드, 로딩 background job, F2Load materialize worker를 \n                          모두 이 값으로 맞춘다. frozen 구성은 48이지만 online CPU가 적은 \n                          호스트에서는 코어 수로 낮춘다.')
    ap.add_argument('--modes', default='baseline,f2load',
                    help='쉼표 구분. ' + ', '.join(MODES))
    ap.add_argument('--mode-reps', default='',
                    help='mode=N,... 해당 mode는 rep N까지만 돈다 (예: f2load_nobitmap=2)')
    ap.add_argument('--binary', default=str(REPO / 'db_bench'),
                    help='로딩 바이너리. 캠페인 중 재빌드에 휩쓸리지 않게 고정 복사본을 줄 것')
    ap.add_argument('--env', action='append', default=[], help='로딩 env KEY=VAL')
    ap.add_argument('--load-override', action='append', default=[],
                    help='모든 mode의 로딩 옵션에 덮어쓸 KEY=VAL (예: vcomp_materialize_preallocate=false)')
    ap.add_argument('--fstrim', default='',
                    help='셀마다 로딩 직전에 이 마운트 지점을 fstrim (예: /work). 비우면 하지 않음')
    ap.add_argument('--trim-settle-max', type=int, default=900,
                    help='fstrim 뒤 SSD가 TRIM 처리를 마칠 때까지 기다리는 최대 초')
    ap.add_argument('--trim-settle-eps-gb', type=float, default=10.0,
                    help='30초 동안 SSD 사용량 감소가 이 값(GB) 미만이면 처리 완료로 본다')
    ap.add_argument('--nvme', default='nvme0n1,nvme1n1,nvme2n1,nvme3n1',
                    help='SSD 사용량(NUSE)을 기록할 NVMe namespace들. 비우면 기록하지 않음')
    a = ap.parse_args()

    alphas = [float(x) for x in a.alphas.split(',')]
    modes = a.modes.split(',')
    for m in modes:
        if m not in MODES:
            raise SystemExit(f'unknown mode {m}; choose from {", ".join(MODES)}')
    mode_reps = {k: int(v) for k, v in
                 (x.split('=') for x in a.mode_reps.split(',') if x)}
    load_ov = dict(parse_override(x) for x in a.load_override)
    nvme = [x for x in a.nvme.split(',') if x]
    binary = Path(a.binary).resolve()
    binary_sha = subprocess.check_output(['sha256sum', str(binary)], text=True).split()[0]
    num = a.target_gib * 1024**3 // (KEY_SIZE + VALUE_SIZE)

    out = ART / a.run_id
    out.mkdir(parents=True, exist_ok=True)
    LOG = out / 'driver.log'
    dbroot = Path(a.db_root)
    dbroot.mkdir(parents=True, exist_ok=True)
    TRACES.mkdir(parents=True, exist_ok=True)

    log(LOG, f'START reps={a.reps} alphas={alphas} modes={modes} gib={a.target_gib} '
             f'num={num:,} workloads={a.workloads} duration={a.duration}s '
             f'threads={a.threads} online_cpus={os.cpu_count()}')

    traces = {}
    for alpha in alphas:
        traces[alpha] = ensure_trace(a.target_gib, alpha, LOG)

    (out / 'plan.json').write_text(json.dumps(dict(
        created=now(), reps=a.reps, alphas=alphas, modes=modes, target_gib=a.target_gib,
        num_records=num, key_size=KEY_SIZE, value_size=VALUE_SIZE,
        unique_ratio=UNIQUE_RATIO, trace_seed=TRACE_SEED, workloads=a.workloads,
        duration=a.duration, cache_gib=a.cache_gib, threads=a.threads,
        db_root=str(dbroot), env=a.env, mode_reps=mode_reps,
        binary=str(binary), binary_sha256=binary_sha,
        mode_overrides={m: MODES[m][1] for m in modes}, load_overrides=load_ov,
        fstrim=a.fstrim, nvme=nvme,
        host=dict(online_cpus=os.cpu_count(),
                  smt=Path('/sys/devices/system/cpu/smt/control').read_text().strip()
                      if Path('/sys/devices/system/cpu/smt/control').exists() else None,
                  kernel_cmdline=Path('/proc/cmdline').read_text().strip()),
        ycsb_binary=os.environ.get('VCOMP_YCSB_BINARY'),
        traces={str(k): dict(path=str(v[0]), meta=v[1]) for k, v in traces.items()}), indent=2))

    for rep in range(1, a.reps + 1):
        for alpha in alphas:
            tr, meta = traces[alpha]
            # 같은 rep 안의 arm들을 인접하게 측정하되, 순서 효과가 한쪽에 몰리지 않게
            # 짝수 rep에서는 순서를 뒤집는다.
            order = modes if rep % 2 == 1 else list(reversed(modes))
            for mode in order:
                if rep > mode_reps.get(mode, a.reps):
                    continue
                point = f'{alpha_tag(alpha)}'
                cell = out / f'rep{rep}' / point / mode
                if (cell / 'ycsb.json').exists():
                    log(LOG, f'SKIP rep{rep}/{point}/{mode} (이미 완료)')
                    continue
                cell.mkdir(parents=True, exist_ok=True)
                free = shutil.disk_usage(dbroot).free
                log(LOG, f'BEGIN rep{rep}/{point}/{mode}  free={free/1e12:.2f} TB')

                load_id = f'{a.run_id}_r{rep}_{point}_{mode}_load'
                ycsb_id = f'{a.run_id}_r{rep}_{point}_{mode}_ycsb'
                # A cell without ycsb.json is (re)measured from scratch: the YCSB runner
                # reuses any result.json it finds, so leftovers from an interrupted or
                # discarded attempt would be reported as this attempt's numbers.
                for stale in (ART / load_id, ART / ycsb_id):
                    shutil.rmtree(stale, ignore_errors=True)
                stub_binary(load_id, binary)
                campaign_mode, mode_ov = MODES[mode]
                ov = dict(mode_ov, load_trace_file=str(tr),
                          max_background_jobs=a.threads,
                          vcomp_materialize_workers=a.threads, **load_ov)
                trim = None
                if a.fstrim:
                    # ext4 returns the blocks of deleted files to the free pool only when
                    # the journal commits, so a trim issued right after the previous cell's
                    # rm misses most of them. Commit first, trim, then wait until the drives
                    # finish deallocating (their utilization stops falling). If the drives
                    # still hold clearly more than the file system uses, trim again.
                    attempts = []
                    for attempt in range(3):
                        os.sync(); time.sleep(15)
                        t = fstrim(a.fstrim)
                        t0 = time.time(); prev = ssd_used_bytes(nvme) if nvme else None
                        while prev is not None and time.time() - t0 < a.trim_settle_max:
                            time.sleep(30)
                            cur = ssd_used_bytes(nvme)
                            if cur is None or prev - cur < a.trim_settle_eps_gb * 1e9:
                                prev = cur
                                break
                            prev = cur
                        t['settle_seconds'] = round(time.time() - t0, 1)
                        t['ssd_used_after'] = prev
                        t['fs_used'] = shutil.disk_usage(a.fstrim).used
                        attempts.append(t)
                        gap = None if prev is None else prev - t['fs_used']
                        log(LOG, f"FSTRIM rep{rep}/{point}/{mode} try{attempt + 1} rc={t['rc']} "
                                 f"trimmed={(t['trimmed_bytes'] or 0)/1e12:.2f} TB, settled {t['settle_seconds']}s, "
                                 f"ssd_used={'n/a' if prev is None else f'{prev/1e12:.2f} TB'} "
                                 f"fs_used={t['fs_used']/1e12:.2f} TB")
                        if gap is None or gap < 0.2e12:
                            break
                    trim = dict(attempts=attempts, settle_seconds=sum(x['settle_seconds'] for x in attempts))
                ssd_before = ssd_used_bytes(nvme) if nvme else None
                qf = cell / 'queue.json'
                qf.write_text(json.dumps(
                    [[f'{point}_{mode}', campaign_mode, a.target_gib, KEY_SIZE, VALUE_SIZE,
                      None, ov]]))

                t0 = time.time()
                cmd = [sys.executable, str(REPO / 'experiments/scripts/eval/run_eval_campaign.py'),
                       '--run-id', load_id, '--db-root', str(dbroot), '--execute',
                       '--queue-file', str(qf)]
                for kv in a.env:
                    cmd += ['--env', kv]
                rc = subprocess.run(cmd, cwd=REPO).returncode
                load_s = time.time() - t0
                db = dbroot / f'{point}_{mode}_{campaign_mode}'
                if rc != 0 or not (db / 'CURRENT').exists():
                    log(LOG, f'LOAD-FAIL rep{rep}/{point}/{mode} rc={rc} — 건너뜀')
                    shutil.rmtree(db, ignore_errors=True)
                    (cell / 'load_failed.json').write_text(
                        json.dumps(dict(rc=rc, seconds=load_s)))
                    continue
                db_bytes = sum(f.stat().st_size for f in db.iterdir() if f.is_file())
                ssd_after_load = ssd_used_bytes(nvme) if nvme else None
                fmt_ssd = lambda b: 'n/a' if b is None else f'{b/1e12:.2f} TB'
                log(LOG, f'LOADED rep{rep}/{point}/{mode} {load_s:.1f}s db={db_bytes/1e12:.2f} TB '
                         f'ssd_used before={fmt_ssd(ssd_before)} after={fmt_ssd(ssd_after_load)}')

                t1 = time.time()
                rc2 = subprocess.run(
                    [sys.executable, str(REPO / 'experiments/scripts/eval/run_ycsb_matrix.py'),
                     '--run-id', ycsb_id, '--db-root', str(dbroot),
                     '--dbs', f'{db.name}:{num}:{KEY_SIZE}:{VALUE_SIZE}',
                     '--workloads', a.workloads, '--distribution', 'zipfian',
                     '--duration', str(a.duration), '--cache-gib', str(a.cache_gib),
                     '--threads', str(a.threads),
                     '--copy-mode', 'hardlink'], cwd=REPO).returncode
                ycsb_s = time.time() - t1
                ssd_after_ycsb = ssd_used_bytes(nvme) if nvme else None

                cells = {}
                for w in a.workloads:
                    src = ART / ycsb_id / f'{db.name}__workload{w}'
                    if (src / 'result.json').exists():
                        cells[w] = json.loads((src / 'result.json').read_text())
                        wd = cell / f'workload{w}'
                        wd.mkdir(exist_ok=True)
                        for name in ('result.json', 'stdout_stderr.log', 'argv.json'):
                            if (src / name).exists():
                                shutil.copy2(src / name, wd / name)
                lsrc = ART / load_id / db.name / 'stdout_stderr.log'
                if lsrc.exists():
                    shutil.copy2(lsrc, cell / 'load_stdout_stderr.log')

                (cell / 'ycsb.json').write_text(json.dumps(dict(
                    rep=rep, alpha=alpha, point=point, mode=mode,
                    trace=str(tr), unique_count=meta['unique_count'],
                    load_seconds=round(load_s, 1), ycsb_seconds=round(ycsb_s, 1),
                    db_bytes=db_bytes, load_rc=rc, ycsb_rc=rc2, finished_at=now(),
                    fstrim=trim, ssd_used_before_load=ssd_before,
                    ssd_used_after_load=ssd_after_load, ssd_used_after_ycsb=ssd_after_ycsb,
                    workloads={w: dict(
                        ops_per_sec=c.get('ops_per_sec'),
                        filter_probes_per_read=c.get('filter_probes_per_read'),
                        positive_lookup_pct=c.get('positive_lookup_pct'),
                        p50_us=c.get('p50_us'), p99_us=c.get('p99_us'),
                        p999_us=c.get('p999_us'),
                        source_identity_ssts=c.get('source_identity_ssts'))
                        for w, c in cells.items()}), indent=1))
                got = {w: c.get('ops_per_sec') for w, c in cells.items()}
                log(LOG, f'DONE rep{rep}/{point}/{mode} ycsb={ycsb_s:.1f}s ops={got}')

                shutil.rmtree(db, ignore_errors=True)
                for w in a.workloads:
                    shutil.rmtree(ART / ycsb_id / f'{db.name}__workload{w}' / 'report.rep',
                                  ignore_errors=True)
                log(LOG, f'PURGED rep{rep}/{point}/{mode} '
                         f'free={shutil.disk_usage(dbroot).free/1e12:.2f} TB')
        log(LOG, f'=== rep{rep} 완료 ===')
    log(LOG, 'ALL DONE')


if __name__ == '__main__':
    main()
